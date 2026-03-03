"""Tests for EngineManager (dual engine + failover)"""

import pytest
from engines.base import EngineError, EngineTimeoutError, EngineRateLimitError
from engines.engine_manager import EngineManager


class TestEngineManagerBasic:
    def test_init(self, engine_manager, mock_engine, mock_secondary):
        assert engine_manager.primary_name == "primary"
        assert engine_manager.secondary_name == "secondary"
        assert engine_manager.failover_enabled is True

    def test_primary_property(self, engine_manager, mock_engine):
        assert engine_manager.primary is mock_engine

    def test_secondary_property(self, engine_manager, mock_secondary):
        assert engine_manager.secondary is mock_secondary

    def test_list_engines(self, engine_manager):
        engines = engine_manager.list_engines()
        assert "primary" in engines
        assert "secondary" in engines

    def test_get_engine(self, engine_manager, mock_engine):
        assert engine_manager.get_engine("primary") is mock_engine
        assert engine_manager.get_engine("nonexistent") is None


class TestEngineManagerChat:
    def test_normal_chat(self, engine_manager):
        messages = [{"role": "user", "content": "Hello"}]
        response = engine_manager.chat(messages)
        assert response.content == "Mock response"
        assert response.engine_name == "primary"
        assert response.from_failover is False

    def test_failover_on_error(self, engine_manager, mock_engine):
        mock_engine.should_fail = True
        mock_engine.fail_error = EngineError("Primary down")

        messages = [{"role": "user", "content": "Hello"}]
        response = engine_manager.chat(messages)
        assert response.content == "Secondary response"
        assert response.from_failover is True

    def test_failover_on_timeout(self, engine_manager, mock_engine):
        mock_engine.should_fail = True
        mock_engine.fail_error = EngineTimeoutError("timeout")

        messages = [{"role": "user", "content": "Hello"}]
        response = engine_manager.chat(messages)
        assert response.content == "Secondary response"
        assert response.from_failover is True

    def test_failover_on_rate_limit(self, engine_manager, mock_engine):
        mock_engine.should_fail = True
        mock_engine.fail_error = EngineRateLimitError("429")

        messages = [{"role": "user", "content": "Hello"}]
        response = engine_manager.chat(messages)
        assert response.content == "Secondary response"

    def test_failover_disabled(self, mock_engine, mock_secondary):
        mgr = EngineManager(
            primary=mock_engine,
            secondary=mock_secondary,
            failover_enabled=False,
        )
        mock_engine.should_fail = True

        with pytest.raises(EngineError):
            mgr.chat([{"role": "user", "content": "Hello"}])

    def test_both_engines_fail(self, engine_manager, mock_engine, mock_secondary):
        mock_engine.should_fail = True
        mock_secondary.should_fail = True

        with pytest.raises(EngineError, match="Both engines failed"):
            engine_manager.chat([{"role": "user", "content": "Hello"}])

    def test_primary_unavailable_triggers_failover(self, engine_manager, mock_engine):
        mock_engine.set_available(False)

        messages = [{"role": "user", "content": "Hello"}]
        response = engine_manager.chat(messages)
        assert response.from_failover is True

    def test_secondary_unavailable_raises(self, engine_manager, mock_engine, mock_secondary):
        mock_engine.should_fail = True
        mock_secondary.set_available(False)

        with pytest.raises(EngineError, match="Both engines unavailable"):
            engine_manager.chat([{"role": "user", "content": "Hello"}])


class TestEngineManagerSwitch:
    def test_switch_primary(self, engine_manager):
        assert engine_manager.primary_name == "primary"
        result = engine_manager.switch_primary("secondary")
        assert result is True
        assert engine_manager.primary_name == "secondary"
        assert engine_manager.secondary_name == "primary"

    def test_switch_to_current(self, engine_manager):
        result = engine_manager.switch_primary("primary")
        assert result is True
        assert engine_manager.primary_name == "primary"

    def test_switch_invalid(self, engine_manager):
        result = engine_manager.switch_primary("nonexistent")
        assert result is False
        assert engine_manager.primary_name == "primary"


class TestEngineManagerStatus:
    def test_get_status(self, engine_manager):
        status = engine_manager.get_status()
        assert status["primary"] == "primary"
        assert status["secondary"] == "secondary"
        assert status["failover_enabled"] is True
        assert status["failover_count"] == 0
        assert "primary" in status["engines"]
        assert "secondary" in status["engines"]

    def test_failover_count_increments(self, engine_manager, mock_engine):
        mock_engine.should_fail = True
        engine_manager.chat([{"role": "user", "content": "test"}])
        status = engine_manager.get_status()
        assert status["failover_count"] == 1
