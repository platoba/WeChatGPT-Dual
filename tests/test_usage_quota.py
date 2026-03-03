"""Tests for services/usage_quota.py"""

import os
import pytest
import tempfile
from services.usage_quota import UsageQuota


@pytest.fixture
def quota_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def quota(quota_db):
    return UsageQuota(db_path=quota_db)


class TestUsageQuota:
    def test_check_new_user(self, quota):
        assert quota.check("new_user", tokens_needed=100) is True

    def test_consume_and_check(self, quota):
        quota.consume("u1", tokens_used=500)
        status = quota.get_status("u1")
        assert status.daily_used == 500
        assert status.monthly_used == 500

    def test_quota_exhaustion(self, quota):
        # Free tier: 1000/day
        quota.consume("u1", tokens_used=999)
        assert quota.check("u1", tokens_needed=2) is False
        assert quota.check("u1", tokens_needed=1) is True

    def test_set_tier(self, quota):
        assert quota.set_tier("u1", "premium") is True
        status = quota.get_status("u1")
        assert status.tier == "premium"
        assert status.daily_limit == 100000

    def test_invalid_tier(self, quota):
        assert quota.set_tier("u1", "nonexistent") is False

    def test_admin_bypass(self, quota):
        quota.set_admin("admin1")
        quota.consume("admin1", tokens_used=999999)
        assert quota.check("admin1", tokens_needed=999999) is True

    def test_status_format(self, quota):
        quota.consume("u1", tokens_used=250)
        status = quota.get_status("u1")
        msg = status.format_message()
        assert "250" in msg
        assert "配额状态" in msg

    def test_status_to_dict(self, quota):
        quota.consume("u1", tokens_used=100)
        status = quota.get_status("u1")
        d = status.to_dict()
        assert d["daily"]["used"] == 100
        assert d["monthly"]["used"] == 100
        assert d["is_exhausted"] is False

    def test_daily_pct(self, quota):
        quota.consume("u1", tokens_used=500)
        status = quota.get_status("u1")
        assert status.daily_pct == 50.0

    def test_history(self, quota):
        quota.consume("u1", tokens_used=100, engine="openai", action="chat")
        quota.consume("u1", tokens_used=200, engine="claude", action="image")
        history = quota.get_history("u1")
        assert len(history) == 2
        assert history[0]["tokens_used"] == 200  # most recent first

    def test_top_users(self, quota):
        quota.consume("u1", tokens_used=500)
        quota.consume("u2", tokens_used=1000)
        quota.consume("u3", tokens_used=200)
        top = quota.get_top_users(limit=2)
        assert len(top) == 2
        assert top[0]["user_id"] == "u2"

    def test_total_used_accumulates(self, quota):
        quota.consume("u1", tokens_used=100)
        quota.consume("u1", tokens_used=200)
        quota.consume("u1", tokens_used=300)
        status = quota.get_status("u1")
        assert status.total_used == 600

    def test_multiple_users_independent(self, quota):
        quota.consume("u1", tokens_used=500)
        quota.consume("u2", tokens_used=200)
        s1 = quota.get_status("u1")
        s2 = quota.get_status("u2")
        assert s1.daily_used == 500
        assert s2.daily_used == 200

    def test_is_exhausted_flag(self, quota):
        quota.consume("u1", tokens_used=1000)
        status = quota.get_status("u1")
        assert status.is_exhausted is True
        assert status.daily_remaining == 0

    def test_get_all_tiers(self, quota):
        tiers = quota.get_all_tiers()
        assert "free" in tiers
        assert "premium" in tiers
        assert "unlimited" in tiers
        assert tiers["free"]["daily"] == 1000

    def test_set_admin_remove(self, quota):
        quota.set_admin("u1", is_admin=True)
        status = quota.get_status("u1")
        assert status.is_admin is True

        quota.set_admin("u1", is_admin=False)
        status = quota.get_status("u1")
        assert status.is_admin is False
