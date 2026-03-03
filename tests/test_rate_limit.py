"""
限流中间件测试
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from middleware import RateLimiter, TokenBucket


class TestTokenBucket:
    def test_initial_full(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=1.0)
        assert bucket.tokens == 10.0

    def test_consume_success(self):
        bucket = TokenBucket(capacity=5.0, refill_rate=1.0)
        assert bucket.consume(1.0)
        assert bucket.tokens < 5.0

    def test_consume_insufficient(self):
        bucket = TokenBucket(capacity=1.0, refill_rate=0.1)
        assert bucket.consume(1.0)  # drain it
        assert not bucket.consume(1.0)  # should fail

    def test_refill(self):
        bucket = TokenBucket(capacity=2.0, refill_rate=100.0)
        bucket.consume(2.0)
        time.sleep(0.05)
        assert bucket.consume(1.0)  # should refill

    def test_remaining(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=1.0)
        assert bucket.remaining > 0

    def test_retry_after(self):
        bucket = TokenBucket(capacity=1.0, refill_rate=1.0)
        bucket.consume(1.0)
        assert bucket.retry_after >= 0


class TestRateLimiter:
    def test_basic_allow(self):
        limiter = RateLimiter(user_rpm=10, global_rpm=100)
        result = limiter.check("user1")
        assert result.allowed

    def test_user_limit(self):
        limiter = RateLimiter(user_rpm=2, global_rpm=100)
        assert limiter.check("user1").allowed
        assert limiter.check("user1").allowed
        result = limiter.check("user1")
        assert not result.allowed
        assert result.limit_type == "user"

    def test_global_limit(self):
        limiter = RateLimiter(user_rpm=100, global_rpm=2)
        assert limiter.check("user1").allowed
        assert limiter.check("user2").allowed
        result = limiter.check("user3")
        assert not result.allowed
        assert result.limit_type == "global"

    def test_whitelist_bypass(self):
        limiter = RateLimiter(user_rpm=1, global_rpm=100, whitelist={"vip"})
        assert limiter.check("vip").allowed
        assert limiter.check("vip").allowed
        assert limiter.check("vip").allowed

    def test_add_remove_whitelist(self):
        limiter = RateLimiter(user_rpm=1)
        assert limiter.check("u1").allowed
        assert not limiter.check("u1").allowed  # blocked

        limiter.add_whitelist("u1")
        assert limiter.check("u1").allowed

        limiter.remove_whitelist("u1")

    def test_different_users_independent(self):
        limiter = RateLimiter(user_rpm=1, global_rpm=100)
        assert limiter.check("a").allowed
        assert limiter.check("b").allowed

    def test_stats(self):
        limiter = RateLimiter(user_rpm=1, global_rpm=100)
        limiter.check("u1")
        limiter.check("u1")
        stats = limiter.get_stats()
        assert stats["total_checks"] == 2
        assert stats["blocked_count"] == 1

    def test_cleanup(self):
        limiter = RateLimiter(user_rpm=10)
        limiter.check("u1")
        limiter.check("u2")
        # Cleanup with 0 max age should remove all
        removed = limiter.cleanup(max_age=0)
        assert removed == 2

    def test_retry_after_returned(self):
        limiter = RateLimiter(user_rpm=1, global_rpm=100)
        limiter.check("u1")
        result = limiter.check("u1")
        assert not result.allowed
        assert result.retry_after > 0
