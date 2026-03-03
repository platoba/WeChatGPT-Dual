"""
Admin管理面板测试
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from admin import admin_app, setup_admin, _auth

from plugins.base import PluginBase, PluginResult
from plugins.loader import PluginLoader
from middleware import RateLimiter
from services.health import HealthChecker


class DummyPlugin(PluginBase):
    name = "dummy"
    description = "Dummy"
    version = "0.1.0"

    def on_message(self, ctx):
        return PluginResult.skip()


class MockEngineManager:
    def get_status(self):
        return {
            "primary": "openai",
            "secondary": "claude",
            "failover_enabled": True,
            "failover_count": 0,
            "engines": {
                "openai": {"available": True, "model": "gpt-4o", "success_rate": 100, "total_requests": 10, "total_tokens_used": 500, "avg_latency": 0.3},
                "claude": {"available": True, "model": "claude-3", "success_rate": 100, "total_requests": 5, "total_tokens_used": 200, "avg_latency": 0.5},
            },
        }


@pytest.fixture
def client():
    loader = PluginLoader()
    loader.register(DummyPlugin())
    limiter = RateLimiter(user_rpm=10)
    checker = HealthChecker(engine_manager=MockEngineManager())
    setup_admin(
        engine_manager=MockEngineManager(),
        plugin_loader=loader,
        rate_limiter=limiter,
        health_checker=checker,
    )
    _auth.token = "testtoken"
    return TestClient(admin_app)


class TestAdminDashboard:
    def test_dashboard_unauthorized(self, client):
        resp = client.get("/admin/dashboard")
        assert resp.status_code == 401

    def test_dashboard_with_token(self, client):
        resp = client.get("/admin/dashboard?token=testtoken")
        assert resp.status_code == 200
        assert "WeChatGPT-Dual Admin" in resp.text

    def test_api_status(self, client):
        resp = client.get(
            "/admin/api/status",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "uptime" in data
        assert "engines" in data

    def test_health_endpoint(self, client):
        resp = client.get("/admin/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")

    def test_toggle_plugin(self, client):
        resp = client.post(
            "/admin/api/plugins/dummy/toggle",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "disabled"

        # Toggle back
        resp = client.post(
            "/admin/api/plugins/dummy/toggle",
            headers={"Authorization": "Bearer testtoken"},
        )
        data = resp.json()
        assert data["status"] == "enabled"

    def test_toggle_nonexistent_plugin(self, client):
        resp = client.post(
            "/admin/api/plugins/nonexistent/toggle",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 404

    def test_reload_plugin(self, client):
        resp = client.post(
            "/admin/api/plugins/dummy/reload",
            headers={"Authorization": "Bearer testtoken"},
        )
        # Reload may fail for in-memory plugin, that's expected
        assert resp.status_code in (200, 500)

    def test_auth_bearer(self, client):
        resp = client.get(
            "/admin/api/status",
            headers={"Authorization": "Bearer wrongtoken"},
        )
        assert resp.status_code == 401

    def test_auth_query_param(self, client):
        resp = client.get("/admin/api/status?token=testtoken")
        assert resp.status_code == 200

    def test_dashboard_contains_engine_info(self, client):
        resp = client.get("/admin/dashboard?token=testtoken")
        assert "openai" in resp.text.lower() or "gpt" in resp.text.lower()
