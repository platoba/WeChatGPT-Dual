"""
对话导出测试
"""

import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import ConversationExporter


class MockContextManager:
    """Mock context manager for export tests"""

    def __init__(self, messages=None):
        self._messages = messages or []

    def get_messages(self, user_id):
        return list(self._messages)


class TestConversationExporter:
    @pytest.fixture
    def sample_messages(self):
        return [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well!"},
        ]

    @pytest.fixture
    def exporter(self, sample_messages):
        ctx = MockContextManager(sample_messages)
        return ConversationExporter(ctx)

    def test_export_markdown(self, exporter):
        result = exporter.export("u1", fmt="markdown")
        assert result is not None
        assert result.format == "markdown"
        assert "Hello!" in result.content
        assert "# Chat Export" in result.content

    def test_export_json(self, exporter):
        result = exporter.export("u1", fmt="json")
        assert result is not None
        data = json.loads(result.content)
        assert data["user_id"] == "u1"
        assert len(data["messages"]) == 4  # system excluded by default

    def test_export_csv(self, exporter):
        result = exporter.export("u1", fmt="csv")
        assert result is not None
        assert "role,content" in result.content
        assert "user" in result.content

    def test_export_include_system(self, exporter):
        result = exporter.export("u1", fmt="json", include_system=True)
        data = json.loads(result.content)
        assert len(data["messages"]) == 5

    def test_export_empty(self):
        ctx = MockContextManager([])
        exporter = ConversationExporter(ctx)
        result = exporter.export("u1")
        assert result is None

    def test_export_system_only(self):
        ctx = MockContextManager([{"role": "system", "content": "sys"}])
        exporter = ConversationExporter(ctx)
        result = exporter.export("u1")
        assert result is None

    def test_get_formats(self, exporter):
        fmts = exporter.get_formats()
        assert "json" in fmts
        assert "markdown" in fmts
        assert "csv" in fmts

    def test_export_metadata(self, exporter):
        result = exporter.export("u1", fmt="markdown")
        assert result.user_id == "u1"
        assert result.exported_at > 0
        assert len(result.messages) > 0
