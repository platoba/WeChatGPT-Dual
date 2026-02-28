"""Tests for Admin Dashboard API"""

import time
import json
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from services.admin_dashboard import (
    AdminDashboard,
    AdminConfig,
    ApiResponse,
)


# ---- Fixtures ----

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_stats.return_value = {
        "total_messages": 1000,
        "total_users": 50,
        "usage": {"total_requests": 500},
    }
    db.get_users.return_value = [
        {"user_id": "u1", "username": "alice", "total_messages": 100, "last_active": time.time()},
        {"user_id": "u2", "username": "bob", "total_messages": 50, "last_active": time.time() - 3600},
    ]

    @dataclass
    class MockUser:
        user_id: str = "u1"
        username: str = "alice"
        first_seen: float = 0.0
        last_active: float = 0.0
        total_messages: int = 100
        is_blocked: bool = False
        metadata: dict = None
        def __post_init__(self):
            if self.metadata is None:
                self.metadata = {}

    db.get_user.return_value = MockUser(
        first_seen=time.time() - 86400,
        last_active=time.time(),
    )
    db.get_messages.return_value = [
        {"user_id": "u1", "role": "user", "content": "hello", "created_at": time.time()},
    ]
    db.get_usage.return_value = [
        {"date": "2026-03-01", "engine": "openai", "requests": 100, "tokens": 5000},
    ]
    db.get_usage_summary.return_value = {
        "total_records": 30,
        "total_requests": 500,
        "total_tokens": 25000,
        "total_errors": 5,
    }
    db.block_user.return_value = True
    db.unblock_user.return_value = True
    return db


@pytest.fixture
def mock_engine_manager():
    em = MagicMock()
    em.primary_name = "openai"
    em.secondary_name = "claude"
    em.failover_enabled = True
    em._failover_count = 3
    em.engines = {"openai": MagicMock(), "claude": MagicMock()}
    em.switch_primary.return_value = True
    return em


@pytest.fixture
def mock_config():
    @dataclass
    class MockConfig:
        system_prompt: str = "You are helpful"
        primary_engine: str = "openai"
        bot_token: str = "secret-token"
        failover_enabled: bool = True
    return MockConfig()


@pytest.fixture
def dashboard(mock_db, mock_engine_manager, mock_config):
    return AdminDashboard(
        config=AdminConfig(api_key="admin-key", admin_users=["admin1"]),
        db=mock_db,
        engine_manager=mock_engine_manager,
        app_config=mock_config,
    )


# ---- Authentication Tests ----

class TestAuthentication:
    def test_valid_api_key(self, dashboard):
        assert dashboard.authenticate(api_key="admin-key") is True

    def test_invalid_api_key(self, dashboard):
        assert dashboard.authenticate(api_key="wrong-key") is False

    def test_valid_user_id(self, dashboard):
        assert dashboard.authenticate(user_id="admin1") is True

    def test_invalid_user_id(self, dashboard):
        assert dashboard.authenticate(user_id="random_user") is False

    def test_no_auth_configured_allows_all(self):
        dash = AdminDashboard(config=AdminConfig())
        assert dash.authenticate() is True

    def test_no_credentials_fails_when_configured(self, dashboard):
        assert dashboard.authenticate() is False


# ---- System Health Tests ----

class TestSystemHealth:
    def test_health_success(self, dashboard):
        result = dashboard.get_system_health()
        assert result.success is True
        assert result.data["status"] == "healthy"

    def test_health_has_components(self, dashboard):
        result = dashboard.get_system_health()
        assert "database" in result.data["components"]
        assert "engine" in result.data["components"]

    def test_health_db_stats(self, dashboard):
        result = dashboard.get_system_health()
        db_health = result.data["components"]["database"]
        assert db_health["status"] == "ok"
        assert db_health["total_messages"] == 1000

    def test_health_engine_info(self, dashboard):
        result = dashboard.get_system_health()
        eng = result.data["components"]["engine"]
        assert eng["primary"] == "openai"
        assert eng["secondary"] == "claude"

    def test_health_degraded_on_db_error(self, mock_engine_manager):
        bad_db = MagicMock()
        bad_db.get_stats.side_effect = Exception("DB down")
        dash = AdminDashboard(db=bad_db, engine_manager=mock_engine_manager)
        result = dash.get_system_health()
        assert result.data["status"] == "degraded"

    def test_health_no_db(self, mock_engine_manager):
        dash = AdminDashboard(engine_manager=mock_engine_manager)
        result = dash.get_system_health()
        assert result.success is True


# ---- User Management Tests ----

class TestUserManagement:
    def test_list_users(self, dashboard):
        result = dashboard.list_users()
        assert result.success is True
        assert len(result.data["users"]) == 2

    def test_list_users_with_limit(self, dashboard):
        result = dashboard.list_users(limit=1)
        assert result.success is True

    def test_get_user_detail(self, dashboard):
        result = dashboard.get_user_detail("u1")
        assert result.success is True
        assert result.data["user"]["user_id"] == "u1"
        assert "recent_messages" in result.data

    def test_get_user_not_found(self, dashboard, mock_db):
        mock_db.get_user.return_value = None
        result = dashboard.get_user_detail("nonexistent")
        assert result.success is False
        assert "not found" in result.error

    def test_block_user(self, dashboard):
        result = dashboard.block_user("u1")
        assert result.success is True
        assert result.data["blocked"] is True

    def test_unblock_user(self, dashboard):
        result = dashboard.unblock_user("u1")
        assert result.success is True

    def test_block_user_read_only(self):
        dash = AdminDashboard(config=AdminConfig(read_only=True))
        result = dash.block_user("u1")
        assert result.success is False
        assert "read-only" in result.error

    def test_list_users_no_db(self):
        dash = AdminDashboard()
        result = dash.list_users()
        assert result.success is False


# ---- Engine Management Tests ----

class TestEngineManagement:
    def test_get_engine_status(self, dashboard):
        result = dashboard.get_engine_status()
        assert result.success is True
        assert result.data["primary"] == "openai"
        assert result.data["failover_count"] == 3

    def test_switch_engine(self, dashboard):
        result = dashboard.switch_engine("claude")
        assert result.success is True
        assert result.data["switched"] is True

    def test_switch_engine_unknown(self, dashboard, mock_engine_manager):
        mock_engine_manager.switch_primary.return_value = False
        result = dashboard.switch_engine("nonexistent")
        assert result.success is False

    def test_switch_engine_read_only(self):
        dash = AdminDashboard(config=AdminConfig(read_only=True))
        result = dash.switch_engine("claude")
        assert result.success is False

    def test_get_engine_no_manager(self):
        dash = AdminDashboard()
        result = dash.get_engine_status()
        assert result.success is False


# ---- Usage Stats Tests ----

class TestUsageStats:
    def test_get_usage(self, dashboard):
        result = dashboard.get_usage_stats(days=7)
        assert result.success is True
        assert "daily" in result.data
        assert "summary" in result.data

    def test_get_usage_no_db(self):
        dash = AdminDashboard()
        result = dash.get_usage_stats()
        assert result.success is False


# ---- Message Search Tests ----

class TestMessageSearch:
    def test_search_messages(self, dashboard):
        result = dashboard.search_messages()
        assert result.success is True
        assert result.data["count"] == 1

    def test_search_with_user_filter(self, dashboard):
        result = dashboard.search_messages(user_id="u1")
        assert result.success is True

    def test_search_no_db(self):
        dash = AdminDashboard()
        result = dash.search_messages()
        assert result.success is False

    def test_search_respects_max_limit(self, dashboard):
        dashboard.config.max_query_limit = 10
        result = dashboard.search_messages(limit=1000)
        assert result.success is True


# ---- Configuration Tests ----

class TestConfiguration:
    def test_get_config(self, dashboard):
        result = dashboard.get_config()
        assert result.success is True
        assert result.data["system_prompt"] == "You are helpful"

    def test_get_config_hides_secrets(self, dashboard):
        result = dashboard.get_config()
        assert result.data["bot_token"] == "***"

    def test_get_config_no_config(self):
        dash = AdminDashboard()
        result = dash.get_config()
        assert result.success is False

    def test_update_config(self, dashboard):
        dashboard.config.enable_config_edit = True
        result = dashboard.update_config({"system_prompt": "New prompt"})
        assert result.success is True
        assert result.data["count"] == 1

    def test_update_config_disabled(self, dashboard):
        result = dashboard.update_config({"system_prompt": "New"})
        assert result.success is False
        assert "disabled" in result.error

    def test_update_config_read_only(self):
        dash = AdminDashboard(config=AdminConfig(read_only=True))
        result = dash.update_config({"foo": "bar"})
        assert result.success is False

    def test_update_config_skips_secrets(self, dashboard):
        dashboard.config.enable_config_edit = True
        result = dashboard.update_config({"bot_token": "hacked!"})
        # Secret fields should be skipped
        assert result.data["count"] == 0


# ---- Action Log Tests ----

class TestActionLog:
    def test_action_logged_on_block(self, dashboard):
        dashboard.block_user("u1", admin_id="admin1")
        result = dashboard.get_action_log()
        assert result.success is True
        assert len(result.data["actions"]) >= 1
        assert result.data["actions"][-1]["action"] == "block_user"

    def test_action_logged_on_switch(self, dashboard):
        dashboard.switch_engine("claude", admin_id="admin1")
        result = dashboard.get_action_log()
        assert any(a["action"] == "switch_engine" for a in result.data["actions"])

    def test_action_log_limit(self, dashboard):
        for i in range(10):
            dashboard.block_user(f"u{i}")
        result = dashboard.get_action_log(limit=3)
        assert len(result.data["actions"]) == 3

    def test_action_log_admin_id(self, dashboard):
        dashboard.block_user("u1", admin_id="boss")
        result = dashboard.get_action_log()
        assert result.data["actions"][-1]["admin_id"] == "boss"


# ---- Dashboard Aggregate Tests ----

class TestDashboardAggregate:
    def test_get_dashboard(self, dashboard):
        result = dashboard.get_dashboard()
        assert result.success is True
        assert "health" in result.data
        assert "engine" in result.data
        assert "usage" in result.data
        assert "users" in result.data

    def test_dashboard_with_no_components(self):
        dash = AdminDashboard()
        result = dash.get_dashboard()
        assert result.success is True


# ---- ApiResponse Tests ----

class TestApiResponse:
    def test_success_response(self):
        r = ApiResponse(success=True, data={"key": "value"})
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"]["key"] == "value"

    def test_error_response(self):
        r = ApiResponse(success=False, error="Something broke")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "Something broke"

    def test_timestamp_auto_set(self):
        r = ApiResponse(success=True)
        assert r.timestamp > 0
