"""Tests for ContextManager"""

import pytest
from context.manager import ContextManager, ConversationContext


class TestContextManagerBasic:
    def test_init(self, context_manager):
        assert context_manager.max_history == 10
        assert context_manager.default_system_prompt == "You are a test assistant."

    def test_add_and_get_messages(self, context_manager):
        context_manager.add_message("u1", "user", "Hello")
        context_manager.add_message("u1", "assistant", "Hi there!")
        msgs = context_manager.get_messages("u1")
        assert len(msgs) == 3  # system + user + assistant
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "Hello"
        assert msgs[2]["content"] == "Hi there!"

    def test_separate_users(self, context_manager):
        context_manager.add_message("u1", "user", "Question from u1")
        context_manager.add_message("u2", "user", "Question from u2")
        msgs1 = context_manager.get_messages("u1")
        msgs2 = context_manager.get_messages("u2")
        assert any("u1" in m["content"] for m in msgs1)
        assert any("u2" in m["content"] for m in msgs2)

    def test_clear(self, context_manager):
        context_manager.add_message("u1", "user", "Hello")
        context_manager.clear("u1")
        msgs = context_manager.get_messages("u1")
        # After clear, only system prompt
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_clear_nonexistent(self, context_manager):
        # Should not raise
        context_manager.clear("nonexistent")


class TestContextManagerHistory:
    def test_truncation_without_summarizer(self):
        cm = ContextManager(max_history=5, summary_threshold=100)
        for i in range(10):
            cm.add_message("u1", "user", f"msg {i}")
        msgs = cm.get_messages("u1", include_system=False)
        assert len(msgs) == 5

    def test_auto_summary_with_summarizer(self):
        summarizer = type("MockSummarizer", (), {
            "summarize": lambda self, msgs, existing: "Summary of conversation"
        })()
        cm = ContextManager(
            max_history=20,
            summary_threshold=4,
            summary_keep_recent=2,
            default_system_prompt="Test",
            summarizer=summarizer,
        )
        for i in range(5):
            cm.add_message("u1", "user", f"msg {i}")
        msgs = cm.get_messages("u1")
        # System prompt should contain summary
        assert "Summary" in msgs[0]["content"]


class TestContextManagerSystemPrompt:
    def test_default_system_prompt(self, context_manager):
        prompt = context_manager.get_system_prompt("u1")
        assert prompt == "You are a test assistant."

    def test_custom_system_prompt(self, context_manager):
        context_manager.set_system_prompt("u1", "You are a translator.")
        prompt = context_manager.get_system_prompt("u1")
        assert prompt == "You are a translator."
        msgs = context_manager.get_messages("u1")
        assert msgs[0]["content"] == "You are a translator."

    def test_get_messages_without_system(self, context_manager):
        context_manager.add_message("u1", "user", "Hello")
        msgs = context_manager.get_messages("u1", include_system=False)
        assert all(m["role"] != "system" for m in msgs)


class TestContextManagerInject:
    def test_inject_context(self, context_manager):
        context_manager.inject_context("u1", "Relevant info about Python")
        msgs = context_manager.get_messages("u1", include_system=False)
        assert any("Relevant info" in m["content"] for m in msgs)


class TestContextManagerStats:
    def test_stats(self, context_manager):
        context_manager.add_message("u1", "user", "Hello")
        context_manager.add_message("u1", "assistant", "Hi")
        stats = context_manager.get_stats("u1")
        assert stats["message_count"] == 2
        assert stats["total_messages"] == 2
        assert stats["has_summary"] is False

    def test_user_count(self, context_manager):
        assert context_manager.get_user_count() == 0
        context_manager.add_message("u1", "user", "Hi")
        context_manager.add_message("u2", "user", "Hi")
        assert context_manager.get_user_count() == 2

    def test_all_user_ids(self, context_manager):
        context_manager.add_message("user_a", "user", "Hi")
        context_manager.add_message("user_b", "user", "Hi")
        ids = context_manager.get_all_user_ids()
        assert "user_a" in ids
        assert "user_b" in ids
