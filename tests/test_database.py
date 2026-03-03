"""Tests for Database layer"""

import time
import pytest
from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(db_path=str(tmp_path / "test.db"))


class TestMessageLogging:
    def test_log_message(self, db):
        db.log_message("u1", "user", "Hello", channel="telegram")
        msgs = db.get_messages(user_id="u1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Hello"
        assert msgs[0]["role"] == "user"

    def test_log_multiple(self, db):
        db.log_message("u1", "user", "Q1")
        db.log_message("u1", "assistant", "A1", engine="openai", tokens_used=100)
        db.log_message("u1", "user", "Q2")
        msgs = db.get_messages(user_id="u1")
        assert len(msgs) == 3

    def test_filter_by_channel(self, db):
        db.log_message("u1", "user", "TG msg", channel="telegram")
        db.log_message("u1", "user", "WX msg", channel="wechat")
        tg = db.get_messages(channel="telegram")
        wx = db.get_messages(channel="wechat")
        assert len(tg) == 1
        assert len(wx) == 1

    def test_filter_by_time(self, db):
        db.log_message("u1", "user", "Old msg")
        future = time.time() + 100
        msgs = db.get_messages(since=future)
        assert len(msgs) == 0

    def test_limit(self, db):
        for i in range(10):
            db.log_message("u1", "user", f"msg {i}")
        msgs = db.get_messages(limit=5)
        assert len(msgs) == 5


class TestUsageTracking:
    def test_record_usage(self, db):
        db.record_usage("openai", tokens=100, latency=0.5)
        usage = db.get_usage(days=1)
        assert len(usage) >= 1
        assert usage[0]["tokens"] == 100

    def test_accumulate_usage(self, db):
        db.record_usage("openai", tokens=100, latency=0.5)
        db.record_usage("openai", tokens=200, latency=1.0)
        usage = db.get_usage(days=1)
        openai_rows = [u for u in usage if u["engine"] == "openai"]
        assert openai_rows[0]["tokens"] == 300
        assert openai_rows[0]["requests"] == 2

    def test_record_error(self, db):
        db.record_usage("claude", is_error=True)
        usage = db.get_usage(days=1)
        claude_rows = [u for u in usage if u["engine"] == "claude"]
        assert claude_rows[0]["errors"] == 1

    def test_usage_summary(self, db):
        db.record_usage("openai", tokens=100)
        db.record_usage("claude", tokens=200)
        summary = db.get_usage_summary()
        assert summary["total_tokens"] == 300
        assert summary["total_requests"] == 2


class TestUserManagement:
    def test_auto_create_user(self, db):
        db.log_message("new_user", "user", "Hello")
        user = db.get_user("new_user")
        assert user is not None
        assert user.total_messages == 1

    def test_user_message_count(self, db):
        db.log_message("u1", "user", "msg1")
        db.log_message("u1", "user", "msg2")
        db.log_message("u1", "user", "msg3")
        user = db.get_user("u1")
        assert user.total_messages == 3

    def test_get_users(self, db):
        db.log_message("u1", "user", "Hi")
        db.log_message("u2", "user", "Hi")
        users = db.get_users()
        assert len(users) == 2

    def test_block_user(self, db):
        db.log_message("u1", "user", "Hi")
        assert db.is_blocked("u1") is False
        db.block_user("u1")
        assert db.is_blocked("u1") is True

    def test_unblock_user(self, db):
        db.log_message("u1", "user", "Hi")
        db.block_user("u1")
        db.unblock_user("u1")
        assert db.is_blocked("u1") is False

    def test_blocked_users_hidden(self, db):
        db.log_message("u1", "user", "Hi")
        db.log_message("u2", "user", "Hi")
        db.block_user("u1")
        users = db.get_users()
        user_ids = [u["user_id"] for u in users]
        assert "u1" not in user_ids
        assert "u2" in user_ids

    def test_nonexistent_user(self, db):
        assert db.get_user("nobody") is None
        assert db.is_blocked("nobody") is False


class TestDatabaseStats:
    def test_stats(self, db):
        db.log_message("u1", "user", "Hi")
        db.log_message("u2", "user", "Hello")
        stats = db.get_stats()
        assert stats["total_messages"] == 2
        assert stats["total_users"] == 2
