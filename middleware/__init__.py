"""
令牌桶限流中间件
支持: 每用户每分钟消息限制、全局限制、白名单
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, Set


@dataclass
class TokenBucket:
    """令牌桶"""
    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.time)

    def __post_init__(self):
        self.tokens = self.capacity

    def consume(self, amount: float = 1.0) -> bool:
        """尝试消耗令牌，返回是否成功"""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_rate,
        )
        self.last_refill = now

        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    @property
    def remaining(self) -> float:
        now = time.time()
        elapsed = now - self.last_refill
        return min(self.capacity, self.tokens + elapsed * self.refill_rate)

    @property
    def retry_after(self) -> float:
        """需要等待多少秒才能获得1个令牌"""
        deficit = 1.0 - self.remaining
        if deficit <= 0:
            return 0.0
        return deficit / self.refill_rate


@dataclass
class RateLimitResult:
    """限流检查结果"""
    allowed: bool
    remaining: float = 0.0
    retry_after: float = 0.0
    limit_type: str = ""  # "user" or "global"


class RateLimiter:
    """
    用户级 + 全局级限流器

    Args:
        user_rpm: 每用户每分钟最大消息数
        global_rpm: 全局每分钟最大消息数
        whitelist: 不受限流的用户ID集合
    """

    def __init__(
        self,
        user_rpm: int = 20,
        global_rpm: int = 200,
        whitelist: Optional[Set[str]] = None,
    ):
        self.user_rpm = user_rpm
        self.global_rpm = global_rpm
        self.whitelist = whitelist or set()
        self._user_buckets: Dict[str, TokenBucket] = {}
        self._global_bucket = TokenBucket(
            capacity=float(global_rpm),
            refill_rate=global_rpm / 60.0,
        )
        self._lock = threading.Lock()
        self._blocked_count = 0
        self._total_checks = 0

    def _get_user_bucket(self, user_id: str) -> TokenBucket:
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = TokenBucket(
                capacity=float(self.user_rpm),
                refill_rate=self.user_rpm / 60.0,
            )
        return self._user_buckets[user_id]

    def check(self, user_id: str) -> RateLimitResult:
        """
        检查用户是否被限流

        Returns:
            RateLimitResult with allowed=True if OK
        """
        with self._lock:
            self._total_checks += 1

            # 白名单直接放行
            if user_id in self.whitelist:
                return RateLimitResult(allowed=True, remaining=float(self.user_rpm))

            # 全局限流
            if not self._global_bucket.consume():
                self._blocked_count += 1
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=self._global_bucket.retry_after,
                    limit_type="global",
                )

            # 用户限流
            bucket = self._get_user_bucket(user_id)
            if not bucket.consume():
                self._blocked_count += 1
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=bucket.retry_after,
                    limit_type="user",
                )

            return RateLimitResult(
                allowed=True,
                remaining=bucket.remaining,
            )

    def add_whitelist(self, user_id: str):
        self.whitelist.add(user_id)

    def remove_whitelist(self, user_id: str):
        self.whitelist.discard(user_id)

    def get_stats(self) -> dict:
        """获取限流统计"""
        return {
            "user_rpm": self.user_rpm,
            "global_rpm": self.global_rpm,
            "whitelist_count": len(self.whitelist),
            "active_users": len(self._user_buckets),
            "total_checks": self._total_checks,
            "blocked_count": self._blocked_count,
            "block_rate": (
                round(self._blocked_count / self._total_checks * 100, 1)
                if self._total_checks > 0
                else 0
            ),
        }

    def cleanup(self, max_age: float = 3600):
        """清理长期不活跃的用户桶"""
        now = time.time()
        expired = [
            uid for uid, bucket in self._user_buckets.items()
            if now - bucket.last_refill > max_age
        ]
        for uid in expired:
            del self._user_buckets[uid]
        return len(expired)
