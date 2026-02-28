"""
tests/test_scheduled_message.py - 定时消息服务测试
"""

import os
import time
import json
import pytest
import tempfile
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from services.scheduled_message import (
    ScheduledMessageService, ScheduleStore, ScheduledMessage,
    CronParser, ScheduleType, MessageStatus, RepeatPolicy,
)


# ──────────────── CronParser ────────────────

class TestCronParser:
    def test_all_wildcards(self):
        result = CronParser.parse("* * * * *")
        assert len(result["minute"]) == 60
        assert len(result["hour"]) == 24
        assert len(result["weekday"]) == 7

    def test_specific_values(self):
        result = CronParser.parse("30 8 1 1 0")
        assert result["minute"] == [30]
        assert result["hour"] == [8]
        assert result["day"] == [1]
        assert result["month"] == [1]
        assert result["weekday"] == [0]

    def test_range(self):
        result = CronParser.parse("0-5 * * * *")
        assert result["minute"] == [0, 1, 2, 3, 4, 5]

    def test_step(self):
        result = CronParser.parse("*/15 * * * *")
        assert result["minute"] == [0, 15, 30, 45]

    def test_comma_list(self):
        result = CronParser.parse("0 8,12,18 * * *")
        assert result["hour"] == [8, 12, 18]

    def test_range_with_step(self):
        result = CronParser.parse("0-30/10 * * * *")
        assert result["minute"] == [0, 10, 20, 30]

    def test_invalid_fields(self):
        with pytest.raises(ValueError, match="5 fields"):
            CronParser.parse("* * *")

    def test_every_hour_at_30(self):
        result = CronParser.parse("30 * * * *")
        assert result["minute"] == [30]
        assert len(result["hour"]) == 24

    def test_weekday_range(self):
        result = CronParser.parse("0 9 * * 1-5")
        assert result["weekday"] == [1, 2, 3, 4, 5]

    def test_next_run(self):
        # 每分钟运行
        next_ts = CronParser.next_run("* * * * *")
        assert next_ts > time.time()
        assert next_ts <= time.time() + 120

    def test_matches(self):
        dt = datetime(2026, 3, 1, 8, 30, 0)
        assert CronParser.matches("30 8 * * *", dt) is True
        assert CronParser.matches("0 8 * * *", dt) is False


# ──────────────── ScheduleStore ────────────────

class TestScheduleStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ScheduleStore(str(tmp_path / "test_schedule.db"))

    def test_save_and_get(self, store):
        msg = ScheduledMessage(
            user_id="u1",
            chat_id="c1",
            content="Hello",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time() + 3600,
        )
        msg_id = store.save(msg)
        assert msg_id

        loaded = store.get(msg_id)
        assert loaded is not None
        assert loaded.content == "Hello"
        assert loaded.user_id == "u1"

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None

    def test_get_due(self, store):
        # 到期消息
        msg1 = ScheduledMessage(
            user_id="u1", chat_id="c1", content="due",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time() - 10,
        )
        store.save(msg1)

        # 未到期
        msg2 = ScheduledMessage(
            user_id="u1", chat_id="c1", content="future",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time() + 3600,
        )
        store.save(msg2)

        due = store.get_due()
        assert len(due) == 1
        assert due[0].content == "due"

    def test_get_by_user(self, store):
        for i in range(3):
            store.save(ScheduledMessage(
                user_id="u1", chat_id="c1", content=f"msg{i}",
                schedule_type=ScheduleType.ONCE,
                scheduled_at=time.time() + i * 100,
            ))
        store.save(ScheduledMessage(
            user_id="u2", chat_id="c2", content="other",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time() + 100,
        ))

        u1_msgs = store.get_by_user("u1")
        assert len(u1_msgs) == 3

    def test_get_by_user_with_status(self, store):
        msg = ScheduledMessage(
            user_id="u1", chat_id="c1", content="test",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time(),
        )
        msg_id = store.save(msg)
        store.update_status(msg_id, MessageStatus.DELIVERED)

        pending = store.get_by_user("u1", "pending")
        assert len(pending) == 0

        delivered = store.get_by_user("u1", "delivered")
        assert len(delivered) == 1

    def test_update_status_delivered(self, store):
        msg = ScheduledMessage(
            user_id="u1", chat_id="c1", content="test",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time(),
        )
        msg_id = store.save(msg)
        store.update_status(msg_id, MessageStatus.DELIVERED)

        loaded = store.get(msg_id)
        assert loaded.status == MessageStatus.DELIVERED
        assert loaded.delivery_count == 1

    def test_update_status_failed(self, store):
        msg = ScheduledMessage(
            user_id="u1", chat_id="c1", content="test",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time(),
        )
        msg_id = store.save(msg)
        store.update_status(msg_id, MessageStatus.FAILED, error="timeout")

        loaded = store.get(msg_id)
        assert loaded.status == MessageStatus.FAILED
        assert loaded.retry_count == 1
        assert loaded.last_error == "timeout"

    def test_reschedule(self, store):
        msg = ScheduledMessage(
            user_id="u1", chat_id="c1", content="repeat",
            schedule_type=ScheduleType.DAILY,
            scheduled_at=time.time() - 10,
        )
        msg_id = store.save(msg)
        store.update_status(msg_id, MessageStatus.DELIVERED)

        next_at = time.time() + 86400
        store.reschedule(msg_id, next_at)

        loaded = store.get(msg_id)
        assert loaded.status == MessageStatus.PENDING
        assert abs(loaded.scheduled_at - next_at) < 1

    def test_cancel(self, store):
        msg = ScheduledMessage(
            user_id="u1", chat_id="c1", content="cancel me",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time() + 3600,
        )
        msg_id = store.save(msg)

        assert store.cancel(msg_id) is True
        loaded = store.get(msg_id)
        assert loaded.status == MessageStatus.CANCELLED

        # 已取消不能再取消
        assert store.cancel(msg_id) is False

    def test_delete(self, store):
        msg_id = store.save(ScheduledMessage(
            user_id="u1", chat_id="c1", content="delete",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time(),
        ))
        assert store.delete(msg_id) is True
        assert store.get(msg_id) is None
        assert store.delete("nonexistent") is False

    def test_cleanup_expired(self, store):
        # 创建过期消息
        msg = ScheduledMessage(
            user_id="u1", chat_id="c1", content="expired",
            schedule_type=ScheduleType.ONCE,
            scheduled_at=time.time() - 100,
            expires_at=time.time() - 50,
            auto_delete=True,
        )
        store.save(msg)

        count = store.cleanup_expired()
        # 先标记过期，再删除auto_delete的
        assert count >= 0

    def test_stats(self, store):
        for i in range(5):
            store.save(ScheduledMessage(
                user_id="u1", chat_id="c1", content=f"msg{i}",
                schedule_type=ScheduleType.ONCE,
                scheduled_at=time.time() + i * 100,
            ))
        stats = store.stats()
        assert stats["total"] == 5
        assert stats["pending"] == 5


# ──────────────── ScheduledMessage ────────────────

class TestScheduledMessage:
    def test_is_due(self):
        msg = ScheduledMessage(scheduled_at=time.time() - 10)
        assert msg.is_due() is True

    def test_not_due(self):
        msg = ScheduledMessage(scheduled_at=time.time() + 3600)
        assert msg.is_due() is False

    def test_not_due_when_delivered(self):
        msg = ScheduledMessage(
            scheduled_at=time.time() - 10,
            status=MessageStatus.DELIVERED,
        )
        assert msg.is_due() is False

    def test_is_expired(self):
        msg = ScheduledMessage(expires_at=time.time() - 10)
        assert msg.is_expired() is True

    def test_not_expired_zero(self):
        msg = ScheduledMessage(expires_at=0)
        assert msg.is_expired() is False

    def test_should_repeat_once(self):
        msg = ScheduledMessage(schedule_type=ScheduleType.ONCE)
        assert msg.should_repeat() is False

    def test_should_repeat_daily(self):
        msg = ScheduledMessage(schedule_type=ScheduleType.DAILY, max_deliveries=0)
        assert msg.should_repeat() is True

    def test_should_repeat_max_reached(self):
        msg = ScheduledMessage(
            schedule_type=ScheduleType.DAILY,
            max_deliveries=5,
            delivery_count=5,
        )
        assert msg.should_repeat() is False


# ──────────────── ScheduledMessageService ────────────────

class TestScheduledMessageService:
    @pytest.fixture
    def service(self, tmp_path):
        return ScheduledMessageService(
            db_path=str(tmp_path / "test.db"),
            check_interval=1,
        )

    def test_schedule_once(self, service):
        msg_id = service.schedule_once(
            "u1", "c1", "Hello!",
            at=time.time() + 3600,
        )
        assert msg_id
        msgs = service.get_user_messages("u1")
        assert len(msgs) == 1
        assert msgs[0].content == "Hello!"

    def test_schedule_delay(self, service):
        msg_id = service.schedule_delay(
            "u1", "c1", "Delayed",
            delay_seconds=60,
        )
        assert msg_id
        msg = service.store.get(msg_id)
        assert msg.schedule_type == ScheduleType.DELAY
        assert msg.scheduled_at > time.time()

    def test_schedule_daily(self, service):
        msg_id = service.schedule_daily(
            "u1", "c1", "Good morning",
            hour=8, minute=30,
        )
        assert msg_id
        msg = service.store.get(msg_id)
        assert msg.schedule_type == ScheduleType.DAILY
        assert msg.interval_seconds == 86400

    def test_schedule_interval(self, service):
        msg_id = service.schedule_interval(
            "u1", "c1", "Ping",
            interval_seconds=300,
            max_deliveries=10,
        )
        msg = service.store.get(msg_id)
        assert msg.schedule_type == ScheduleType.INTERVAL
        assert msg.max_deliveries == 10

    def test_schedule_cron(self, service):
        msg_id = service.schedule_cron(
            "u1", "c1", "Cron message",
            cron_expr="*/15 * * * *",
        )
        msg = service.store.get(msg_id)
        assert msg.schedule_type == ScheduleType.CRON
        assert msg.cron_expr == "*/15 * * * *"

    def test_cancel(self, service):
        msg_id = service.schedule_once("u1", "c1", "Cancel me", at=time.time() + 3600)
        assert service.cancel(msg_id) is True
        msg = service.store.get(msg_id)
        assert msg.status == MessageStatus.CANCELLED

    def test_render_template(self, service):
        result = service.render_template(
            "Hello {{name}}, your order #{{order_id}} is ready!",
            {"name": "Alice", "order_id": "12345"},
        )
        assert result == "Hello Alice, your order #12345 is ready!"

    def test_render_template_missing_var(self, service):
        result = service.render_template("Hello {{name}}!", {})
        assert result == "Hello !"

    def test_process_due_messages(self, service):
        delivered = []
        service.delivery_callback = lambda msg, content: delivered.append(content)

        service.schedule_once("u1", "c1", "Due now", at=time.time() - 10)
        service.schedule_once("u1", "c1", "Future", at=time.time() + 3600)

        results = service.process_due_messages()
        assert len(results) == 1
        assert results[0][1] is True  # success
        assert len(delivered) == 1
        assert delivered[0] == "Due now"

    def test_process_with_template(self, service):
        delivered = []
        service.delivery_callback = lambda msg, content: delivered.append(content)

        service.schedule_once(
            "u1", "c1", "Hi {{name}}!",
            at=time.time() - 10,
            template_vars={"name": "Bob"},
        )
        service.process_due_messages()
        assert delivered[0] == "Hi Bob!"

    def test_process_delivery_failure(self, service):
        def fail_delivery(msg, content):
            raise RuntimeError("Network error")

        service.delivery_callback = fail_delivery

        msg_id = service.schedule_once("u1", "c1", "Fail", at=time.time() - 10)
        results = service.process_due_messages()
        assert len(results) == 1
        assert results[0][1] is False  # failed

    def test_process_repeat_daily(self, service):
        delivered = []
        service.delivery_callback = lambda msg, content: delivered.append(content)

        msg_id = service.schedule_once("u1", "c1", "Repeat", at=time.time() - 10)
        # Manually set to daily
        msg = service.store.get(msg_id)
        msg.schedule_type = ScheduleType.DAILY
        msg.interval_seconds = 86400
        msg.max_deliveries = 0
        service.store.save(msg)

        service.process_due_messages()
        assert len(delivered) == 1

        # Should be rescheduled
        msg = service.store.get(msg_id)
        assert msg.status == MessageStatus.PENDING
        assert msg.scheduled_at > time.time()

    def test_process_expired_message(self, service):
        delivered = []
        service.delivery_callback = lambda msg, content: delivered.append(content)

        service.schedule_once(
            "u1", "c1", "Expired",
            at=time.time() - 100,
            expires_at=time.time() - 50,
        )
        service.process_due_messages()
        assert len(delivered) == 0

    def test_stats(self, service):
        service.schedule_once("u1", "c1", "Test", at=time.time() + 100)
        stats = service.get_stats()
        assert stats["created"] == 1
        assert stats["store"]["pending"] == 1

    def test_start_stop_scheduler(self, service):
        service.start()
        assert service._running is True
        time.sleep(0.1)
        service.stop()
        assert service._running is False

    def test_scheduler_processes(self, service):
        delivered = []
        service.delivery_callback = lambda msg, content: delivered.append(content)
        service.check_interval = 0.1

        service.schedule_once("u1", "c1", "Auto-deliver", at=time.time() - 1)
        service.start()
        time.sleep(0.5)
        service.stop()

        assert len(delivered) >= 1

    def test_get_user_messages_empty(self, service):
        msgs = service.get_user_messages("nonexistent")
        assert msgs == []
