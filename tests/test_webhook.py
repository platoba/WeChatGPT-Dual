"""Tests for Webhook API"""

import pytest
from fastapi.testclient import TestClient
from webhook import app, set_handler
from tests.conftest import MockEngine
from engines.engine_manager import EngineManager
from context.manager import ContextManager
from knowledge.store import KnowledgeStore
from commands.handler import CommandHandler
from wechat.handler import WeChatHandler


@pytest.fixture
def api_client(tmp_path):
    """Create test client with mock handlers"""
    primary = MockEngine(name="openai", model="gpt-4o-mini")
    secondary = MockEngine(name="claude", model="claude-3", responses=["Claude response"])
    em = EngineManager(primary=primary, secondary=secondary)
    cm = ContextManager(default_system_prompt="Test")
    ks = KnowledgeStore(store_dir=str(tmp_path / "kb"))
    ch = CommandHandler(engine_manager=em, context_manager=cm, knowledge_store=ks)
    wh = WeChatHandler(engine_manager=em, context_manager=cm, knowledge_store=ks, command_handler=ch)

    set_handler(wh, em)
    return TestClient(app)


class TestHealthEndpoint:
    def test_health(self, api_client):
        r = api_client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "3.0.0"
        assert data["engine_available"] is True


class TestStatsEndpoint:
    def test_stats(self, api_client):
        r = api_client.get("/stats")
        assert r.status_code == 200
        data = r.json()
        assert "engine_status" in data


class TestWebhookEndpoint:
    def test_wechat_webhook(self, api_client):
        r = api_client.post("/webhook/wechat", json={
            "msg_id": "m1",
            "sender_id": "u1",
            "content": "Hello",
            "msg_type": "text",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["has_reply"] is True
        assert "Mock response" in data["reply"]

    def test_webhook_image(self, api_client):
        r = api_client.post("/webhook/wechat", json={
            "msg_id": "m2",
            "sender_id": "u1",
            "msg_type": "image",
        })
        assert r.status_code == 200
        assert r.json()["has_reply"] is False


class TestChatAPI:
    def test_chat(self, api_client):
        r = api_client.post("/api/chat", json={
            "message": "What is Python?",
            "user_id": "test_user",
        })
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        assert data["engine"] == "openai"

    def test_chat_with_system_prompt(self, api_client):
        r = api_client.post("/api/chat", json={
            "message": "Translate",
            "system_prompt": "You are a translator",
        })
        assert r.status_code == 200


class TestEngineAPI:
    def test_list_engines(self, api_client):
        r = api_client.get("/api/engines")
        assert r.status_code == 200
        data = r.json()
        assert "primary" in data
        assert "engines" in data

    def test_switch_engine(self, api_client):
        r = api_client.post("/api/engine/switch/claude")
        assert r.status_code == 200
        assert r.json()["primary"] == "claude"

    def test_switch_invalid(self, api_client):
        r = api_client.post("/api/engine/switch/nonexistent")
        assert r.status_code == 400
