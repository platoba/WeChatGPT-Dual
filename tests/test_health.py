"""
健康检查测试
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.health import HealthChecker


class MockEngineManager:
    def get_status(self):
        return {
            "primary": "openai",
            "secondary": "claude",
            "failover_enabled": True,
            "failover_count": 0,
            "engines": {
                "openai": {"available": True, "model": "gpt-4o", "success_rate": 98.5},
                "claude": {"available": True, "model": "claude-3", "success_rate": 100.0},
            },
        }


class MockEngineManagerDegraded:
    def get_status(self):
        return {
            "primary": "openai",
            "secondary": "claude",
            "failover_enabled": True,
            "failover_count": 3,
            "engines": {
                "openai": {"available": False, "model": "gpt-4o", "success_rate": 50.0},
                "claude": {"available": True, "model": "claude-3", "success_rate": 100.0},
            },
        }


class TestHealthChecker:
    def test_healthy(self):
        checker = HealthChecker(engine_manager=MockEngineManager())
        result = checker.check_all()
        assert result["status"] == "healthy"
        assert "system" in result["checks"]
        assert "engines" in result["checks"]
        assert "memory" in result["checks"]
        assert "runtime" in result["checks"]

    def test_degraded(self):
        checker = HealthChecker(engine_manager=MockEngineManagerDegraded())
        result = checker.check_all()
        assert result["status"] == "degraded"
        assert result["checks"]["engines"]["status"] == "degraded"

    def test_no_engine(self):
        checker = HealthChecker()
        result = checker.check_all()
        assert result["status"] == "healthy"
        assert "engines" not in result["checks"]

    def test_uptime(self):
        checker = HealthChecker(start_time=time.time() - 3600)
        result = checker.check_all()
        assert result["uptime_seconds"] >= 3599

    def test_summary(self):
        checker = HealthChecker(engine_manager=MockEngineManager())
        summary = checker.get_summary()
        assert "🟢" in summary
        assert "系统健康" in summary

    def test_summary_degraded(self):
        checker = HealthChecker(engine_manager=MockEngineManagerDegraded())
        summary = checker.get_summary()
        assert "🟡" in summary

    def test_checks_counter(self):
        checker = HealthChecker()
        checker.check_all()
        checker.check_all()
        result = checker.check_all()
        assert result["checks"]["runtime"]["checks_run"] == 3

    def test_system_check(self):
        checker = HealthChecker()
        result = checker.check_all()
        sys_check = result["checks"]["system"]
        assert sys_check["status"] == "ok"
        assert "pid" in sys_check

    def test_memory_check(self):
        checker = HealthChecker()
        result = checker.check_all()
        mem = result["checks"]["memory"]
        assert mem["status"] in ("ok", "warning")
