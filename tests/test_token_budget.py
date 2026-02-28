"""Tests for services/token_budget.py"""

import os
import time
import pytest
import tempfile
from services.token_budget import TokenBudgetManager, BudgetStatus, UsageReport


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def mgr(db_path):
    return TokenBudgetManager(db_path)


class TestBudgetStatus:
    def test_to_dict(self):
        s = BudgetStatus(
            user_id="u1",
            daily_used=500, daily_limit=1000, daily_remaining=500, daily_pct=50.0,
            monthly_used=5000, monthly_limit=30000, monthly_remaining=25000, monthly_pct=16.67,
            is_unlimited=False, is_over_budget=False, alerts=[],
        )
        d = s.to_dict()
        assert d["user_id"] == "u1"
        assert d["daily"]["used"] == 500
        assert d["monthly"]["pct"] == 16.7
        assert d["is_unlimited"] is False


class TestUsageReport:
    def test_to_dict(self):
        r = UsageReport(
            user_id="u1", period="daily",
            entries=[], total_tokens=100, total_requests=5,
            avg_tokens_per_request=20.0,
        )
        d = r.to_dict()
        assert d["avg_tokens_per_request"] == 20.0


class TestTokenBudgetManager:
    def test_check_budget_no_limits(self, mgr):
        allowed, msg = mgr.check_budget("u1")
        assert allowed is True
        assert msg == "ok"

    def test_record_and_check(self, mgr):
        mgr.record_usage("u1", 500)
        allowed, msg = mgr.check_budget("u1", daily_limit=1000)
        assert allowed is True

    def test_daily_budget_exceeded(self, mgr):
        mgr.record_usage("u1", 1000)
        allowed, msg = mgr.check_budget("u1", daily_limit=1000)
        assert allowed is False
        assert "Daily" in msg

    def test_monthly_budget_exceeded(self, mgr):
        mgr.record_usage("u1", 50000)
        allowed, msg = mgr.check_budget("u1", monthly_limit=50000)
        assert allowed is False
        assert "Monthly" in msg

    def test_record_zero_tokens(self, mgr):
        mgr.record_usage("u1", 0)
        status = mgr.get_status("u1", daily_limit=1000)
        assert status.daily_used == 0

    def test_record_negative_tokens(self, mgr):
        mgr.record_usage("u1", -10)
        status = mgr.get_status("u1", daily_limit=1000)
        assert status.daily_used == 0

    def test_cumulative_usage(self, mgr):
        mgr.record_usage("u1", 100)
        mgr.record_usage("u1", 200)
        mgr.record_usage("u1", 300)
        status = mgr.get_status("u1", daily_limit=1000)
        assert status.daily_used == 600

    def test_get_status_basic(self, mgr):
        mgr.record_usage("u1", 500)
        status = mgr.get_status("u1", daily_limit=1000, monthly_limit=30000)
        assert status.daily_used == 500
        assert status.daily_limit == 1000
        assert status.daily_remaining == 500
        assert status.daily_pct == 50.0
        assert status.is_over_budget is False

    def test_get_status_over_budget(self, mgr):
        mgr.record_usage("u1", 1500)
        status = mgr.get_status("u1", daily_limit=1000)
        assert status.is_over_budget is True
        assert "⛔" in status.alerts[0]

    def test_get_status_warning_alert(self, mgr):
        mgr.record_usage("u1", 850)
        status = mgr.get_status("u1", daily_limit=1000)
        assert len(status.alerts) >= 1
        assert "⚠️" in status.alerts[0]

    def test_get_status_unlimited(self, mgr):
        mgr.set_override("u1", unlimited=True)
        allowed, msg = mgr.check_budget("u1", daily_limit=100)
        assert allowed is True
        assert msg == "unlimited"

    def test_get_status_no_limits(self, mgr):
        status = mgr.get_status("u1")
        assert status.daily_remaining == -1
        assert status.monthly_remaining == -1

    def test_set_override(self, mgr):
        mgr.set_override("u1", daily_override=5000, reason="VIP user")
        allowed, msg = mgr.check_budget("u1", daily_limit=100)
        # Now the override limit of 5000 applies
        mgr.record_usage("u1", 200)
        allowed2, _ = mgr.check_budget("u1", daily_limit=100)
        assert allowed2 is True

    def test_set_override_unlimited(self, mgr):
        mgr.set_override("u1", unlimited=True)
        mgr.record_usage("u1", 999999)
        allowed, msg = mgr.check_budget("u1", daily_limit=100)
        assert allowed is True

    def test_remove_override(self, mgr):
        mgr.set_override("u1", unlimited=True)
        result = mgr.remove_override("u1")
        assert result is True
        # Now should respect normal limits
        mgr.record_usage("u1", 200)
        allowed, _ = mgr.check_budget("u1", daily_limit=100)
        assert allowed is False

    def test_remove_override_nonexistent(self, mgr):
        result = mgr.remove_override("unknown")
        assert result is False

    def test_get_usage_report(self, mgr):
        mgr.record_usage("u1", 100)
        mgr.record_usage("u1", 200)
        report = mgr.get_usage_report("u1", period="daily")
        assert report.total_tokens == 300
        assert report.total_requests == 2
        assert report.avg_tokens_per_request == 150.0

    def test_get_usage_report_empty(self, mgr):
        report = mgr.get_usage_report("unknown")
        assert report.total_tokens == 0
        assert report.total_requests == 0

    def test_get_top_users(self, mgr):
        mgr.record_usage("u1", 500)
        mgr.record_usage("u2", 1000)
        mgr.record_usage("u3", 200)
        top = mgr.get_top_users(period="daily")
        assert len(top) == 3
        assert top[0]["user_id"] == "u2"
        assert top[0]["tokens_used"] == 1000

    def test_get_top_users_monthly(self, mgr):
        mgr.record_usage("u1", 5000)
        top = mgr.get_top_users(period="monthly")
        assert len(top) >= 1

    def test_daily_key(self):
        key = TokenBudgetManager._daily_key()
        assert len(key) == 10
        assert key.count("-") == 2

    def test_monthly_key(self):
        key = TokenBudgetManager._monthly_key()
        assert len(key) == 7
        assert key.count("-") == 1

    def test_multiple_users_independent(self, mgr):
        mgr.record_usage("u1", 500)
        mgr.record_usage("u2", 200)
        s1 = mgr.get_status("u1", daily_limit=1000)
        s2 = mgr.get_status("u2", daily_limit=1000)
        assert s1.daily_used == 500
        assert s2.daily_used == 200

    def test_override_monthly(self, mgr):
        mgr.set_override("u1", monthly_override=50000)
        mgr.record_usage("u1", 40000)
        status = mgr.get_status("u1", monthly_limit=10000)
        # Override should apply: 50000 not 10000
        assert status.monthly_limit == 50000
        assert status.is_over_budget is False
