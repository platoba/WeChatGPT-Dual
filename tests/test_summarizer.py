"""Tests for Summarizer"""

from context.summarizer import Summarizer
from tests.conftest import MockEngine


class TestSimpleSummarizer:
    def test_no_engine_simple_summarize(self):
        s = Summarizer(engine=None)
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ]
        result = s.summarize(messages)
        assert "用户问" in result
        assert "助手答" in result

    def test_empty_messages(self):
        s = Summarizer(engine=None)
        result = s.summarize([], "existing summary")
        assert result == "existing summary"

    def test_existing_summary_included(self):
        s = Summarizer(engine=None)
        messages = [{"role": "user", "content": "Hello"}]
        result = s.summarize(messages, "Previous summary")
        assert "Previous summary" in result

    def test_long_content_truncated(self):
        s = Summarizer(engine=None)
        long_text = "x" * 200
        messages = [{"role": "user", "content": long_text}]
        result = s.summarize(messages)
        assert "..." in result

    def test_max_10_entries(self):
        s = Summarizer(engine=None)
        messages = [
            {"role": "user", "content": f"msg {i}"} for i in range(20)
        ]
        result = s.summarize(messages)
        lines = [l for l in result.strip().split("\n") if l.strip()]
        assert len(lines) <= 10


class TestAISummarizer:
    def test_ai_summarize(self):
        engine = MockEngine(responses=["Condensed summary of conversation"])
        s = Summarizer(engine=engine)
        messages = [
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI is artificial intelligence."},
        ]
        result = s.summarize(messages)
        assert result == "Condensed summary of conversation"

    def test_ai_summarize_with_existing(self):
        engine = MockEngine(responses=["Updated summary"])
        s = Summarizer(engine=engine)
        messages = [{"role": "user", "content": "More questions"}]
        result = s.summarize(messages, "Old summary")
        assert result == "Updated summary"
        # Verify the prompt included the old summary
        assert "已有摘要" in engine.last_messages[0]["content"]

    def test_ai_fallback_on_failure(self):
        engine = MockEngine(should_fail=True)
        s = Summarizer(engine=engine)
        messages = [{"role": "user", "content": "Hello"}]
        result = s.summarize(messages)
        # Should fall back to simple summarize
        assert "用户问" in result
