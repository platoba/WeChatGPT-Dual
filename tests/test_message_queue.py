"""Tests for services/message_queue.py"""

import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from services.message_queue import (
    MessageQueue, DeadLetterQueue, QueueMessage,
    Priority, MessageStatus,
)


# ─── QueueMessage ────────────────────────────────────────

class TestQueueMessage:
    def test_defaults(self):
        msg = QueueMessage(
            priority=2, timestamp=time.time(),
            message_id="m1", user_id="u1",
            channel="tg", content="hello",
        )
        assert msg.status == MessageStatus.PENDING
        assert msg.retry_count == 0
        assert msg.max_retries == 3

    def test_ordering(self):
        t = time.time()
        high = QueueMessage(priority=1, timestamp=t, message_id="h",
                            user_id="u", channel="c", content="x")
        low = QueueMessage(priority=3, timestamp=t, message_id="l",
                           user_id="u", channel="c", content="x")
        assert high < low

    def test_same_priority_older_first(self):
        m1 = QueueMessage(priority=2, timestamp=1.0, message_id="a",
                          user_id="u", channel="c", content="x")
        m2 = QueueMessage(priority=2, timestamp=2.0, message_id="b",
                          user_id="u", channel="c", content="x")
        assert m1 < m2


# ─── Priority ────────────────────────────────────────────

class TestPriority:
    def test_ordering(self):
        assert Priority.CRITICAL < Priority.HIGH < Priority.NORMAL < Priority.LOW < Priority.BULK

    def test_int_values(self):
        assert int(Priority.CRITICAL) == 0
        assert int(Priority.BULK) == 4


# ─── DeadLetterQueue ─────────────────────────────────────

class TestDeadLetterQueue:
    @pytest.fixture
    def dlq(self, tmp_path):
        return DeadLetterQueue(str(tmp_path / "test_dlq.db"))

    def test_add_and_list(self, dlq):
        msg = QueueMessage(
            priority=2, timestamp=time.time(),
            message_id="dead-1", user_id="u1",
            channel="tg", content="bad msg",
            error="timeout", retry_count=3,
        )
        dlq.add(msg)
        items = dlq.list_messages()
        assert len(items) == 1
        assert items[0]["message_id"] == "dead-1"
        assert items[0]["error"] == "timeout"

    def test_count(self, dlq):
        assert dlq.count() == 0
        msg = QueueMessage(
            priority=2, timestamp=time.time(),
            message_id="d1", user_id="u",
            channel="c", content="x", error="e",
        )
        dlq.add(msg)
        assert dlq.count() == 1

    def test_list_by_user(self, dlq):
        for i, uid in enumerate(["u1", "u2", "u1"]):
            msg = QueueMessage(
                priority=2, timestamp=time.time(),
                message_id=f"m{i}", user_id=uid,
                channel="c", content="x", error="e",
            )
            dlq.add(msg)
        assert len(dlq.list_messages(user_id="u1")) == 2
        assert len(dlq.list_messages(user_id="u2")) == 1

    def test_replay(self, dlq):
        msg = QueueMessage(
            priority=2, timestamp=time.time(),
            message_id="replay-1", user_id="u",
            channel="c", content="x", error="e",
        )
        dlq.add(msg)
        assert dlq.count() == 1
        result = dlq.replay("replay-1")
        assert result is not None
        assert result["message_id"] == "replay-1"
        assert dlq.count() == 0

    def test_replay_not_found(self, dlq):
        assert dlq.replay("nonexistent") is None

    def test_purge(self, dlq):
        msg = QueueMessage(
            priority=2, timestamp=time.time(),
            message_id="old", user_id="u",
            channel="c", content="x", error="e",
        )
        dlq.add(msg)
        assert dlq.purge(older_than_hours=0.0001) == 0  # too recent
        assert dlq.count() == 1

    def test_limit(self, dlq):
        for i in range(10):
            msg = QueueMessage(
                priority=2, timestamp=time.time(),
                message_id=f"m{i}", user_id="u",
                channel="c", content="x", error="e",
            )
            dlq.add(msg)
        assert len(dlq.list_messages(limit=3)) == 3


# ─── MessageQueue ────────────────────────────────────────

class TestMessageQueue:
    @pytest.fixture
    def mq(self, tmp_path):
        q = MessageQueue(
            max_size=100, max_workers=2,
            max_retries=2, dlq_path=str(tmp_path / "dlq.db"),
        )
        yield q
        if q.is_running:
            q.stop()

    def test_enqueue(self, mq):
        mid = mq.enqueue("u1", "hello")
        assert mid is not None
        assert mq.size == 1

    def test_enqueue_priority(self, mq):
        mq.enqueue("u1", "low", priority=Priority.LOW)
        mq.enqueue("u1", "critical", priority=Priority.CRITICAL)
        assert mq.size == 2

    def test_enqueue_full(self, tmp_path):
        mq = MessageQueue(max_size=2, dlq_path=str(tmp_path / "dlq.db"))
        mq.enqueue("u1", "a")
        mq.enqueue("u1", "b")
        mid = mq.enqueue("u1", "c")  # should be rejected
        assert mid is None
        assert mq.size == 2

    def test_start_stop(self, mq):
        assert not mq.is_running
        mq.set_handler(lambda msg: None)
        mq.start()
        assert mq.is_running
        mq.stop()
        assert not mq.is_running

    def test_processing(self, mq):
        processed = []
        mq.set_handler(lambda msg: processed.append(msg.content))
        mq.enqueue("u1", "hello")
        mq.enqueue("u1", "world")
        mq.start()
        time.sleep(0.5)
        mq.stop()
        assert len(processed) == 2
        assert set(processed) == {"hello", "world"}

    def test_priority_order(self, mq):
        order = []
        event = threading.Event()

        def handler(msg):
            order.append(msg.content)
            if len(order) >= 3:
                event.set()

        mq.set_handler(handler)
        mq.enqueue("u1", "low", priority=Priority.LOW)
        mq.enqueue("u1", "critical", priority=Priority.CRITICAL)
        mq.enqueue("u1", "normal", priority=Priority.NORMAL)
        mq.start()
        event.wait(timeout=2)
        mq.stop()
        assert order[0] == "critical"

    def test_retry_on_failure(self, mq):
        call_count = {"n": 0}

        def flaky_handler(msg):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                raise ValueError("temporary error")

        mq.set_handler(flaky_handler)
        mq.enqueue("u1", "retry-me")
        mq.start()
        time.sleep(3)
        mq.stop()
        assert call_count["n"] >= 2

    def test_dlq_after_max_retries(self, mq):
        def always_fail(msg):
            raise RuntimeError("permanent")

        mq.set_handler(always_fail)
        mq.enqueue("u1", "doomed")
        mq.start()
        time.sleep(4)
        mq.stop()
        assert mq.get_dlq().count() >= 1

    def test_stats(self, mq):
        stats = mq.get_stats()
        assert "queue_size" in stats
        assert "processed" in stats
        assert "failed" in stats
        assert "dlq_size" in stats
        assert stats["running"] is False

    def test_metadata(self, mq):
        mid = mq.enqueue("u1", "meta", metadata={"key": "value"})
        assert mid is not None

    def test_channel(self, mq):
        mid = mq.enqueue("u1", "wx", channel="wechat")
        assert mid is not None

    def test_double_start(self, mq):
        mq.set_handler(lambda msg: None)
        mq.start()
        mq.start()  # should be no-op
        assert mq.is_running
        mq.stop()

    def test_utilization(self, tmp_path):
        mq = MessageQueue(max_size=10, dlq_path=str(tmp_path / "dlq.db"))
        for i in range(5):
            mq.enqueue("u1", f"m{i}")
        stats = mq.get_stats()
        assert stats["utilization"] == 50.0
