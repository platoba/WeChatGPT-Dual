"""Tests for Conversation Export Service"""

import time
import json
import csv
import io
import pytest
from services.conversation_export import (
    ConversationExporter,
    ExportFormat,
    ExportFilter,
    ExportResult,
)


# ---- Fixtures ----

@pytest.fixture
def sample_messages():
    now = time.time()
    return [
        {
            "user_id": "user1",
            "role": "user",
            "content": "Hello, how are you?",
            "channel": "telegram",
            "engine": "",
            "tokens_used": 10,
            "latency": 0.0,
            "created_at": now - 300,
        },
        {
            "user_id": "user1",
            "role": "assistant",
            "content": "I'm doing great! How can I help you?",
            "channel": "telegram",
            "engine": "openai",
            "tokens_used": 25,
            "latency": 0.8,
            "created_at": now - 290,
        },
        {
            "user_id": "user2",
            "role": "user",
            "content": "你好世界",
            "channel": "wechat",
            "engine": "",
            "tokens_used": 8,
            "latency": 0.0,
            "created_at": now - 200,
        },
        {
            "user_id": "user2",
            "role": "assistant",
            "content": "你好！有什么我可以帮助你的吗？",
            "channel": "wechat",
            "engine": "claude",
            "tokens_used": 30,
            "latency": 1.2,
            "created_at": now - 190,
        },
        {
            "user_id": "user1",
            "role": "user",
            "content": "Tell me about Python",
            "channel": "telegram",
            "engine": "",
            "tokens_used": 12,
            "latency": 0.0,
            "created_at": now - 100,
        },
    ]


@pytest.fixture
def exporter():
    return ConversationExporter()


# ---- JSON Export Tests ----

class TestJsonExport:
    def test_valid_json(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        data = json.loads(result.content)
        assert "messages" in data
        assert "title" in data
        assert "exported_at" in data

    def test_message_count(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        data = json.loads(result.content)
        assert data["total_messages"] == 5
        assert len(data["messages"]) == 5

    def test_message_fields(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        data = json.loads(result.content)
        msg = data["messages"][0]
        assert "user_id" in msg
        assert "role" in msg
        assert "content" in msg
        assert "created_at_iso" in msg

    def test_chinese_content(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert "你好世界" in result.content

    def test_custom_title(self, exporter, sample_messages):
        result = exporter.export(
            sample_messages, ExportFormat.JSON, title="My Export"
        )
        data = json.loads(result.content)
        assert data["title"] == "My Export"

    def test_empty_messages(self, exporter):
        result = exporter.export([], ExportFormat.JSON)
        data = json.loads(result.content)
        assert data["total_messages"] == 0


# ---- CSV Export Tests ----

class TestCsvExport:
    def test_valid_csv(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.CSV)
        reader = csv.reader(io.StringIO(result.content))
        rows = list(reader)
        assert len(rows) == 6  # header + 5 messages

    def test_csv_header(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.CSV)
        reader = csv.reader(io.StringIO(result.content))
        header = next(reader)
        assert "timestamp" in header
        assert "user_id" in header
        assert "content" in header

    def test_csv_content(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.CSV)
        assert "Hello, how are you?" in result.content


# ---- HTML Export Tests ----

class TestHtmlExport:
    def test_valid_html(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.HTML)
        assert "<!DOCTYPE html>" in result.content
        assert "</html>" in result.content

    def test_has_styles(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.HTML)
        assert "<style>" in result.content

    def test_user_bubbles(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.HTML)
        assert "user-bubble" in result.content
        assert "assistant-bubble" in result.content

    def test_html_escape(self, exporter):
        messages = [{
            "user_id": "test",
            "role": "user",
            "content": "<script>alert('xss')</script>",
            "created_at": time.time(),
        }]
        result = exporter.export(messages, ExportFormat.HTML)
        assert "<script>" not in result.content
        assert "&lt;script&gt;" in result.content

    def test_stats_in_html(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.HTML)
        assert "5 messages" in result.content

    def test_title_in_html(self, exporter, sample_messages):
        result = exporter.export(
            sample_messages, ExportFormat.HTML, title="Test Chat"
        )
        assert "Test Chat" in result.content


# ---- Markdown Export Tests ----

class TestMarkdownExport:
    def test_has_title(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.MARKDOWN)
        assert result.content.startswith("# ")

    def test_has_user_markers(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.MARKDOWN)
        assert "👤" in result.content
        assert "🤖" in result.content

    def test_content_preserved(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.MARKDOWN)
        assert "Hello, how are you?" in result.content
        assert "你好世界" in result.content

    def test_date_headers(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.MARKDOWN)
        assert "## " in result.content  # Date headers

    def test_engine_shown(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.MARKDOWN)
        assert "openai" in result.content or "claude" in result.content


# ---- ExportResult Tests ----

class TestExportResult:
    def test_result_format(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert result.format == ExportFormat.JSON

    def test_result_filename(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert result.filename.endswith(".json")

    def test_result_filename_csv(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.CSV)
        assert result.filename.endswith(".csv")

    def test_result_filename_html(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.HTML)
        assert result.filename.endswith(".html")

    def test_result_filename_md(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.MARKDOWN)
        assert result.filename.endswith(".md")

    def test_result_message_count(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert result.message_count == 5

    def test_result_user_count(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert result.user_count == 2

    def test_result_date_range(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert "~" in result.date_range

    def test_result_size(self, exporter, sample_messages):
        result = exporter.export(sample_messages, ExportFormat.JSON)
        assert result.size_bytes > 0


# ---- Filter Tests ----

class TestFilters:
    def test_filter_by_user(self, exporter, sample_messages):
        filter_ = ExportFilter(user_id="user1")
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert all(m["user_id"] == "user1" for m in data["messages"])

    def test_filter_by_channel(self, exporter, sample_messages):
        filter_ = ExportFilter(channel="wechat")
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert all(m["channel"] == "wechat" for m in data["messages"])

    def test_filter_by_role(self, exporter, sample_messages):
        filter_ = ExportFilter(role="assistant")
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert all(m["role"] == "assistant" for m in data["messages"])

    def test_filter_by_engine(self, exporter, sample_messages):
        filter_ = ExportFilter(engine="claude")
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert len(data["messages"]) == 1
        assert data["messages"][0]["engine"] == "claude"

    def test_filter_by_search_text(self, exporter, sample_messages):
        filter_ = ExportFilter(search_text="python")
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert len(data["messages"]) == 1

    def test_filter_by_min_tokens(self, exporter, sample_messages):
        filter_ = ExportFilter(min_tokens=20)
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert all(m["tokens_used"] >= 20 for m in data["messages"])

    def test_filter_limit(self, exporter, sample_messages):
        filter_ = ExportFilter(limit=2)
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert len(data["messages"]) <= 2

    def test_filter_since(self, exporter, sample_messages):
        cutoff = time.time() - 150
        filter_ = ExportFilter(since=cutoff)
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert all(m["created_at"] >= cutoff for m in data["messages"])

    def test_filter_until(self, exporter, sample_messages):
        cutoff = time.time() - 250
        filter_ = ExportFilter(until=cutoff)
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        assert all(m["created_at"] <= cutoff for m in data["messages"])

    def test_combined_filters(self, exporter, sample_messages):
        filter_ = ExportFilter(user_id="user1", channel="telegram", role="user")
        result = exporter.export(sample_messages, ExportFormat.JSON, filter_=filter_)
        data = json.loads(result.content)
        for m in data["messages"]:
            assert m["user_id"] == "user1"
            assert m["channel"] == "telegram"
            assert m["role"] == "user"


# ---- Batch Export Tests ----

class TestBatchExport:
    def test_export_by_user(self, exporter, sample_messages):
        results = exporter.export_by_user(sample_messages, ExportFormat.JSON)
        assert "user1" in results
        assert "user2" in results
        assert results["user1"].message_count == 3
        assert results["user2"].message_count == 2


# ---- Summary Tests ----

class TestExportSummary:
    def test_summary_counts(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert summary["total_messages"] == 5
        assert summary["unique_users"] == 2

    def test_summary_by_role(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert summary["by_role"]["user"] == 3
        assert summary["by_role"]["assistant"] == 2

    def test_summary_by_channel(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert "telegram" in summary["by_channel"]
        assert "wechat" in summary["by_channel"]

    def test_summary_tokens(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert summary["total_tokens"] == 85  # 10+25+8+30+12

    def test_summary_latency(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert summary["avg_latency"] > 0

    def test_summary_date_range(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert summary["date_range"]["start"] is not None
        assert summary["date_range"]["end"] is not None

    def test_summary_empty(self, exporter):
        summary = exporter.export_summary([])
        assert summary["total"] == 0

    def test_summary_hourly(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert len(summary["hourly_distribution"]) > 0

    def test_summary_engines(self, exporter, sample_messages):
        summary = exporter.export_summary(sample_messages)
        assert "openai" in summary["by_engine"] or "claude" in summary["by_engine"]
