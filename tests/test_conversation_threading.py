"""Tests for ConversationThreading."""

import json
import time
import pytest

from services.conversation_threading import (
    ConversationThreading, ConversationThread, ThreadMessage,
    ThreadContext, ThreadState,
)


@pytest.fixture
def svc(tmp_path):
    db = str(tmp_path / "threads_test.db")
    return ConversationThreading(db_path=db)


class TestCreateThread:
    def test_create_basic(self, svc):
        thread = svc.create_thread("conv1", "user1", "Python Help")
        assert thread.thread_id
        assert thread.conversation_id == "conv1"
        assert thread.user_id == "user1"
        assert thread.title == "Python Help"
        assert thread.state == "active"
        assert thread.message_count == 0

    def test_create_with_topic(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Debug Session",
            topic="Fixing async bug",
        )
        assert thread.topic == "Fixing async bug"

    def test_create_with_tags(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Coding",
            tags=["python", "async"],
        )
        assert thread.tags == ["python", "async"]

    def test_create_with_system_prompt(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Expert Mode",
            system_prompt="You are a Python expert.",
        )
        ctx = svc.get_thread_context(thread.thread_id)
        assert ctx is not None
        assert ctx.system_prompt == "You are a Python expert."

    def test_create_child_thread(self, svc):
        parent = svc.create_thread("conv1", "user1", "Main Topic")
        child = svc.create_thread(
            "conv1", "user1", "Sub Topic",
            parent_thread_id=parent.thread_id,
        )
        assert child.parent_thread_id == parent.thread_id

    def test_create_with_metadata(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Test",
            metadata={"source": "telegram", "priority": 1},
        )
        assert thread.metadata["source"] == "telegram"


class TestGetThread:
    def test_get_existing(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        fetched = svc.get_thread(thread.thread_id)
        assert fetched is not None
        assert fetched.title == "Test"

    def test_get_nonexistent(self, svc):
        assert svc.get_thread("nonexistent") is None


class TestMessages:
    def test_add_message(self, svc):
        thread = svc.create_thread("conv1", "user1", "Chat")
        msg = svc.add_message(thread.thread_id, "msg1", "user", "Hello!")
        assert msg.thread_id == thread.thread_id
        assert msg.content == "Hello!"
        # Check message count updated
        t = svc.get_thread(thread.thread_id)
        assert t.message_count == 1

    def test_add_multiple_messages(self, svc):
        thread = svc.create_thread("conv1", "user1", "Chat")
        svc.add_message(thread.thread_id, "msg1", "user", "Hi")
        svc.add_message(thread.thread_id, "msg2", "assistant", "Hello!")
        svc.add_message(thread.thread_id, "msg3", "user", "How are you?")
        t = svc.get_thread(thread.thread_id)
        assert t.message_count == 3

    def test_add_message_to_closed_thread(self, svc):
        thread = svc.create_thread("conv1", "user1", "Chat")
        svc.change_state(thread.thread_id, "closed")
        with pytest.raises(ValueError, match="closed"):
            svc.add_message(thread.thread_id, "msg1", "user", "test")

    def test_add_message_to_nonexistent(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.add_message("fake_id", "msg1", "user", "test")

    def test_get_messages(self, svc):
        thread = svc.create_thread("conv1", "user1", "Chat")
        svc.add_message(thread.thread_id, "msg1", "user", "First")
        svc.add_message(thread.thread_id, "msg2", "assistant", "Second")
        msgs = svc.get_messages(thread.thread_id)
        assert len(msgs) == 2
        assert msgs[0].content == "First"
        assert msgs[1].content == "Second"

    def test_get_messages_pagination(self, svc):
        thread = svc.create_thread("conv1", "user1", "Chat")
        for i in range(10):
            svc.add_message(thread.thread_id, f"msg{i}", "user", f"Message {i}")
        msgs = svc.get_messages(thread.thread_id, limit=3)
        assert len(msgs) == 3
        msgs2 = svc.get_messages(thread.thread_id, limit=3, offset=3)
        assert len(msgs2) == 3
        assert msgs[0].content != msgs2[0].content

    def test_message_metadata(self, svc):
        thread = svc.create_thread("conv1", "user1", "Chat")
        svc.add_message(
            thread.thread_id, "msg1", "user", "Test",
            metadata={"tokens": 150},
        )
        msgs = svc.get_messages(thread.thread_id)
        assert msgs[0].metadata["tokens"] == 150


class TestThreadContext:
    def test_get_context(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Test",
            system_prompt="Be helpful",
        )
        ctx = svc.get_thread_context(thread.thread_id)
        assert ctx.system_prompt == "Be helpful"
        assert ctx.pinned_messages == []

    def test_update_context(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.update_context(
            thread.thread_id,
            system_prompt="New prompt",
            context_summary="We discussed X",
            variables={"topic": "Python"},
        )
        ctx = svc.get_thread_context(thread.thread_id)
        assert ctx.system_prompt == "New prompt"
        assert ctx.context_summary == "We discussed X"
        assert ctx.variables["topic"] == "Python"

    def test_update_context_partial(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Test",
            system_prompt="Original",
        )
        svc.update_context(thread.thread_id, context_summary="Summary")
        ctx = svc.get_thread_context(thread.thread_id)
        assert ctx.system_prompt == "Original"  # unchanged
        assert ctx.context_summary == "Summary"

    def test_update_context_empty(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        assert not svc.update_context(thread.thread_id)

    def test_context_nonexistent(self, svc):
        assert svc.get_thread_context("fake_id") is None


class TestStateManagement:
    def test_change_to_paused(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        assert svc.change_state(thread.thread_id, "paused")
        t = svc.get_thread(thread.thread_id)
        assert t.state == "paused"

    def test_change_to_closed(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.change_state(thread.thread_id, "closed")
        t = svc.get_thread(thread.thread_id)
        assert t.state == "closed"
        assert t.closed_at > 0

    def test_change_to_archived(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.change_state(thread.thread_id, "archived")
        t = svc.get_thread(thread.thread_id)
        assert t.state == "archived"
        assert t.is_closed()

    def test_invalid_state(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        with pytest.raises(ValueError, match="Invalid state"):
            svc.change_state(thread.thread_id, "invalid")

    def test_is_active(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        assert thread.is_active()
        assert not thread.is_closed()


class TestListThreads:
    def test_list_by_conversation(self, svc):
        svc.create_thread("conv1", "user1", "Thread A")
        svc.create_thread("conv1", "user1", "Thread B")
        svc.create_thread("conv2", "user1", "Thread C")
        threads = svc.list_threads(conversation_id="conv1")
        assert len(threads) == 2

    def test_list_by_user(self, svc):
        svc.create_thread("conv1", "user1", "T1")
        svc.create_thread("conv1", "user2", "T2")
        threads = svc.list_threads(user_id="user1")
        assert len(threads) == 1

    def test_list_by_state(self, svc):
        t1 = svc.create_thread("conv1", "user1", "Active")
        t2 = svc.create_thread("conv1", "user1", "Paused")
        svc.change_state(t2.thread_id, "paused")
        threads = svc.list_threads(state="paused")
        assert len(threads) == 1
        assert threads[0].state == "paused"

    def test_list_by_tag(self, svc):
        svc.create_thread("conv1", "user1", "T1", tags=["python", "help"])
        svc.create_thread("conv1", "user1", "T2", tags=["java"])
        threads = svc.list_threads(tag="python")
        assert len(threads) == 1

    def test_list_pagination(self, svc):
        for i in range(10):
            svc.create_thread("conv1", "user1", f"Thread {i}")
        threads = svc.list_threads(limit=3)
        assert len(threads) == 3
        threads2 = svc.list_threads(limit=3, offset=3)
        assert len(threads2) == 3


class TestSearchThreads:
    def test_search_by_title(self, svc):
        svc.create_thread("conv1", "user1", "Python debugging help")
        svc.create_thread("conv1", "user1", "Java performance")
        results = svc.search_threads("Python")
        assert len(results) == 1
        assert "Python" in results[0].title

    def test_search_by_topic(self, svc):
        svc.create_thread("conv1", "user1", "Help", topic="async programming")
        svc.create_thread("conv1", "user1", "Other", topic="database design")
        results = svc.search_threads("async")
        assert len(results) == 1

    def test_search_case_insensitive(self, svc):
        svc.create_thread("conv1", "user1", "Python Help")
        results = svc.search_threads("python")
        assert len(results) == 1

    def test_search_with_conversation_filter(self, svc):
        svc.create_thread("conv1", "user1", "Python")
        svc.create_thread("conv2", "user1", "Python")
        results = svc.search_threads("Python", conversation_id="conv1")
        assert len(results) == 1


class TestChildThreads:
    def test_get_children(self, svc):
        parent = svc.create_thread("conv1", "user1", "Main")
        svc.create_thread("conv1", "user1", "Branch A", parent_thread_id=parent.thread_id)
        svc.create_thread("conv1", "user1", "Branch B", parent_thread_id=parent.thread_id)
        children = svc.get_child_threads(parent.thread_id)
        assert len(children) == 2

    def test_no_children(self, svc):
        thread = svc.create_thread("conv1", "user1", "Lonely")
        children = svc.get_child_threads(thread.thread_id)
        assert len(children) == 0


class TestMergeThreads:
    def test_merge(self, svc):
        t1 = svc.create_thread("conv1", "user1", "Source")
        t2 = svc.create_thread("conv1", "user1", "Target")
        svc.add_message(t1.thread_id, "m1", "user", "From source")
        svc.add_message(t2.thread_id, "m2", "user", "From target")
        svc.merge_threads(t1.thread_id, t2.thread_id)
        # Source archived
        source = svc.get_thread(t1.thread_id)
        assert source.state == "archived"
        # Target has all messages
        target = svc.get_thread(t2.thread_id)
        assert target.message_count == 2
        msgs = svc.get_messages(t2.thread_id)
        assert len(msgs) == 2

    def test_merge_nonexistent_source(self, svc):
        t = svc.create_thread("conv1", "user1", "Target")
        with pytest.raises(ValueError, match="Source thread"):
            svc.merge_threads("fake", t.thread_id)

    def test_merge_nonexistent_target(self, svc):
        t = svc.create_thread("conv1", "user1", "Source")
        with pytest.raises(ValueError, match="Target thread"):
            svc.merge_threads(t.thread_id, "fake")


class TestUpdateThread:
    def test_update_tags(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test", tags=["old"])
        assert svc.update_tags(thread.thread_id, ["new", "updated"])
        t = svc.get_thread(thread.thread_id)
        assert t.tags == ["new", "updated"]

    def test_update_title(self, svc):
        thread = svc.create_thread("conv1", "user1", "Old Title")
        assert svc.update_title(thread.thread_id, "New Title")
        t = svc.get_thread(thread.thread_id)
        assert t.title == "New Title"


class TestPinMessages:
    def test_pin_message(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.add_message(thread.thread_id, "msg1", "user", "Important!")
        assert svc.pin_message(thread.thread_id, "msg1")
        ctx = svc.get_thread_context(thread.thread_id)
        assert "msg1" in ctx.pinned_messages

    def test_pin_duplicate(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.pin_message(thread.thread_id, "msg1")
        svc.pin_message(thread.thread_id, "msg1")  # duplicate
        ctx = svc.get_thread_context(thread.thread_id)
        assert ctx.pinned_messages.count("msg1") == 1

    def test_unpin_message(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.pin_message(thread.thread_id, "msg1")
        assert svc.unpin_message(thread.thread_id, "msg1")
        ctx = svc.get_thread_context(thread.thread_id)
        assert "msg1" not in ctx.pinned_messages

    def test_unpin_nonexistent(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        assert not svc.unpin_message(thread.thread_id, "msg999")

    def test_pin_nonexistent_thread(self, svc):
        assert not svc.pin_message("fake_id", "msg1")


class TestDeleteThread:
    def test_delete(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        svc.add_message(thread.thread_id, "msg1", "user", "Test")
        assert svc.delete_thread(thread.thread_id)
        assert svc.get_thread(thread.thread_id) is None
        assert svc.get_messages(thread.thread_id) == []

    def test_delete_nonexistent(self, svc):
        assert not svc.delete_thread("fake_id")


class TestStats:
    def test_empty_stats(self, svc):
        stats = svc.get_stats()
        assert stats["total_threads"] == 0

    def test_stats_by_conversation(self, svc):
        svc.create_thread("conv1", "user1", "T1")
        svc.create_thread("conv1", "user1", "T2")
        svc.create_thread("conv2", "user1", "T3")
        stats = svc.get_stats(conversation_id="conv1")
        assert stats["total_threads"] == 2

    def test_stats_with_states(self, svc):
        t1 = svc.create_thread("conv1", "user1", "Active")
        t2 = svc.create_thread("conv1", "user1", "Paused")
        t3 = svc.create_thread("conv1", "user1", "Closed")
        svc.change_state(t2.thread_id, "paused")
        svc.change_state(t3.thread_id, "closed")
        stats = svc.get_stats()
        assert stats["active"] == 1
        assert stats["paused"] == 1
        assert stats["closed"] == 1

    def test_stats_message_counts(self, svc):
        t = svc.create_thread("conv1", "user1", "Chat")
        for i in range(5):
            svc.add_message(t.thread_id, f"m{i}", "user", f"msg{i}")
        stats = svc.get_stats()
        assert stats["total_messages"] == 5


class TestDataclasses:
    def test_thread_to_dict(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        d = thread.to_dict()
        assert "thread_id" in d
        assert d["title"] == "Test"

    def test_message_to_dict(self, svc):
        thread = svc.create_thread("conv1", "user1", "Test")
        msg = svc.add_message(thread.thread_id, "msg1", "user", "Hi")
        d = msg.to_dict()
        assert d["content"] == "Hi"
        assert d["role"] == "user"

    def test_context_to_dict(self, svc):
        thread = svc.create_thread(
            "conv1", "user1", "Test",
            system_prompt="Be helpful",
        )
        ctx = svc.get_thread_context(thread.thread_id)
        d = ctx.to_dict()
        assert d["system_prompt"] == "Be helpful"
