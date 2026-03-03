"""Tests for Engine base classes and stats"""

import time
import pytest
from engines.base import (
    ChatResponse, EngineStats,
    EngineError, EngineTimeoutError, EngineRateLimitError,
)


class TestEngineStats:
    def test_initial_state(self):
        stats = EngineStats()
        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
        assert stats.total_tokens_used == 0
        assert stats.avg_latency == 0.0
        assert stats.success_rate == 1.0
        assert stats.is_rate_limited is False

    def test_record_success(self):
        stats = EngineStats()
        stats.record_success(1.5, 200)
        assert stats.total_requests == 1
        assert stats.successful_requests == 1
        assert stats.total_tokens_used == 200
        assert stats.avg_latency == 1.5

    def test_record_multiple_successes(self):
        stats = EngineStats()
        stats.record_success(1.0, 100)
        stats.record_success(2.0, 200)
        assert stats.total_requests == 2
        assert stats.avg_latency == 1.5
        assert stats.total_tokens_used == 300
        assert stats.success_rate == 1.0

    def test_record_failure(self):
        stats = EngineStats()
        stats.record_failure("Connection error")
        assert stats.total_requests == 1
        assert stats.failed_requests == 1
        assert stats.last_error == "Connection error"
        assert stats.last_error_time is not None

    def test_success_rate_mixed(self):
        stats = EngineStats()
        stats.record_success(1.0, 100)
        stats.record_failure("err")
        assert stats.success_rate == 0.5

    def test_rate_limited(self):
        stats = EngineStats()
        assert stats.is_rate_limited is False
        stats.set_rate_limited(2)
        assert stats.is_rate_limited is True
        # Wait for cooldown
        time.sleep(0.1)
        stats.rate_limited_until = time.time() - 1
        assert stats.is_rate_limited is False

    def test_to_dict(self):
        stats = EngineStats()
        stats.record_success(1.0, 100)
        d = stats.to_dict()
        assert d["total_requests"] == 1
        assert d["successful_requests"] == 1
        assert d["success_rate"] == 100.0
        assert d["avg_latency"] == 1.0
        assert d["is_rate_limited"] is False


class TestChatResponse:
    def test_basic(self):
        r = ChatResponse(
            content="Hello!",
            engine_name="openai",
            model="gpt-4o",
            tokens_used=50,
            latency=0.3,
        )
        assert r.content == "Hello!"
        assert r.engine_name == "openai"
        assert r.from_failover is False

    def test_failover_flag(self):
        r = ChatResponse(content="Hi", engine_name="claude", model="claude-3")
        r.from_failover = True
        assert r.from_failover is True


class TestEngineErrors:
    def test_engine_error(self):
        with pytest.raises(EngineError):
            raise EngineError("test error")

    def test_timeout_error_is_engine_error(self):
        with pytest.raises(EngineError):
            raise EngineTimeoutError("timeout")

    def test_rate_limit_error_is_engine_error(self):
        with pytest.raises(EngineError):
            raise EngineRateLimitError("rate limited")
