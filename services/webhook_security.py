"""
Webhook Security Service - 保护Webhook端点安全

Features:
- HMAC-SHA256 签名验证
- IP白名单过滤
- 请求重放攻击防护 (nonce + timestamp)
- 请求速率限制 (per-IP)
- 请求体大小限制
- 安全日志审计
"""

import time
import hmac
import hashlib
import logging
import threading
from typing import Optional, Dict, List, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """安全配置"""
    # HMAC
    secret_key: str = ""
    signature_header: str = "X-Signature-256"
    signature_algorithm: str = "sha256"

    # IP whitelist (empty = allow all)
    ip_whitelist: Set[str] = field(default_factory=set)
    ip_blacklist: Set[str] = field(default_factory=set)

    # Replay protection
    nonce_ttl: int = 300  # 5 minutes
    timestamp_tolerance: int = 300  # 5 minutes
    max_nonces: int = 10000

    # Rate limiting (per IP)
    rate_limit_requests: int = 60
    rate_limit_window: int = 60  # seconds

    # Body size
    max_body_size: int = 1_048_576  # 1MB

    # Audit
    log_failures: bool = True
    log_successes: bool = False


@dataclass
class SecurityEvent:
    """安全事件记录"""
    timestamp: float
    event_type: str  # "signature_fail", "ip_blocked", "replay", "rate_limit", "body_too_large", "ok"
    ip_address: str
    details: str = ""
    request_id: str = ""


class NonceStore:
    """Nonce存储 - 防重放攻击"""

    def __init__(self, ttl: int = 300, max_size: int = 10000):
        self.ttl = ttl
        self.max_size = max_size
        self._nonces: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, nonce: str) -> bool:
        """
        检查nonce是否已用过。
        返回True=新nonce(安全)，False=重复(重放攻击)
        """
        with self._lock:
            self._cleanup()

            if nonce in self._nonces:
                return False

            self._nonces[nonce] = time.time()
            return True

    def _cleanup(self):
        """清理过期nonce"""
        if len(self._nonces) < self.max_size // 2:
            return
        now = time.time()
        expired = [k for k, v in self._nonces.items() if now - v > self.ttl]
        for k in expired:
            del self._nonces[k]

    @property
    def size(self) -> int:
        return len(self._nonces)


class RateLimitTracker:
    """IP级别速率限制"""

    def __init__(self, max_requests: int = 60, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, ip: str) -> Tuple[bool, int]:
        """
        检查IP是否超过速率限制。
        返回 (allowed, remaining)
        """
        now = time.time()
        with self._lock:
            # Clean old entries
            cutoff = now - self.window
            self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

            current = len(self._requests[ip])
            remaining = max(0, self.max_requests - current)

            if current >= self.max_requests:
                return False, 0

            self._requests[ip].append(now)
            return True, remaining - 1

    def get_count(self, ip: str) -> int:
        """获取IP当前请求数"""
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]
            return len(self._requests[ip])

    def reset(self, ip: str = None):
        """重置计数"""
        with self._lock:
            if ip:
                self._requests.pop(ip, None)
            else:
                self._requests.clear()


class WebhookSecurity:
    """Webhook安全网关"""

    def __init__(self, config: SecurityConfig = None):
        self.config = config or SecurityConfig()
        self._nonce_store = NonceStore(
            ttl=self.config.nonce_ttl,
            max_size=self.config.max_nonces,
        )
        self._rate_limiter = RateLimitTracker(
            max_requests=self.config.rate_limit_requests,
            window=self.config.rate_limit_window,
        )
        self._events: List[SecurityEvent] = []
        self._events_lock = threading.Lock()
        self._max_events = 1000

    # ---- Signature Verification ----

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """
        验证HMAC-SHA256签名

        Args:
            payload: 请求原始body
            signature: 请求头中的签名值 (格式: sha256=xxx 或纯hex)
        """
        if not self.config.secret_key:
            return True  # No secret = skip verification

        if not signature:
            return False

        # Strip prefix (e.g. "sha256=")
        if "=" in signature and not signature.startswith("sha256="):
            return False
        clean_sig = signature.replace("sha256=", "")

        expected = hmac.new(
            self.config.secret_key.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, clean_sig)

    def compute_signature(self, payload: bytes) -> str:
        """计算HMAC-SHA256签名"""
        return "sha256=" + hmac.new(
            self.config.secret_key.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

    # ---- IP Filtering ----

    def check_ip(self, ip: str) -> bool:
        """检查IP是否允许"""
        # Blacklist takes priority
        if ip in self.config.ip_blacklist:
            return False
        # If whitelist is set, must be in it
        if self.config.ip_whitelist:
            return ip in self.config.ip_whitelist
        return True

    def add_to_whitelist(self, ip: str):
        """添加IP到白名单"""
        self.config.ip_whitelist.add(ip)
        self.config.ip_blacklist.discard(ip)

    def add_to_blacklist(self, ip: str):
        """添加IP到黑名单"""
        self.config.ip_blacklist.add(ip)
        self.config.ip_whitelist.discard(ip)

    def remove_from_whitelist(self, ip: str):
        self.config.ip_whitelist.discard(ip)

    def remove_from_blacklist(self, ip: str):
        self.config.ip_blacklist.discard(ip)

    # ---- Replay Protection ----

    def check_replay(self, nonce: str = None, timestamp: float = None) -> Tuple[bool, str]:
        """
        检查请求是否为重放攻击

        Returns:
            (safe, reason) - True=安全，False=可疑重放
        """
        # Check timestamp freshness
        if timestamp is not None:
            now = time.time()
            diff = abs(now - timestamp)
            if diff > self.config.timestamp_tolerance:
                return False, f"timestamp_expired (diff={diff:.0f}s)"

        # Check nonce uniqueness
        if nonce is not None:
            if not self._nonce_store.check_and_store(nonce):
                return False, "nonce_reused"

        return True, "ok"

    # ---- Rate Limiting ----

    def check_rate_limit(self, ip: str) -> Tuple[bool, int]:
        """检查速率限制"""
        return self._rate_limiter.check(ip)

    # ---- Body Size ----

    def check_body_size(self, body: bytes) -> bool:
        """检查请求体大小"""
        return len(body) <= self.config.max_body_size

    # ---- Full Validation Pipeline ----

    def validate_request(
        self,
        body: bytes,
        ip: str,
        signature: str = None,
        nonce: str = None,
        timestamp: float = None,
        request_id: str = "",
    ) -> Tuple[bool, str]:
        """
        完整请求验证管线

        Returns:
            (valid, reason)
        """
        # 1. Body size
        if not self.check_body_size(body):
            self._log_event("body_too_large", ip, f"size={len(body)}", request_id)
            return False, "body_too_large"

        # 2. IP check
        if not self.check_ip(ip):
            self._log_event("ip_blocked", ip, "", request_id)
            return False, "ip_blocked"

        # 3. Rate limit
        allowed, remaining = self.check_rate_limit(ip)
        if not allowed:
            self._log_event("rate_limited", ip, "", request_id)
            return False, "rate_limited"

        # 4. Signature
        if self.config.secret_key and signature is not None:
            if not self.verify_signature(body, signature):
                self._log_event("signature_fail", ip, "", request_id)
                return False, "signature_invalid"

        # 5. Replay protection
        safe, reason = self.check_replay(nonce, timestamp)
        if not safe:
            self._log_event("replay", ip, reason, request_id)
            return False, f"replay:{reason}"

        # All good
        if self.config.log_successes:
            self._log_event("ok", ip, f"remaining={remaining}", request_id)
        return True, "ok"

    # ---- Audit Log ----

    def _log_event(self, event_type: str, ip: str, details: str, request_id: str = ""):
        """记录安全事件"""
        event = SecurityEvent(
            timestamp=time.time(),
            event_type=event_type,
            ip_address=ip,
            details=details,
            request_id=request_id,
        )
        with self._events_lock:
            self._events.append(event)
            # Trim
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

        if event_type != "ok" and self.config.log_failures:
            logger.warning(
                f"Security event: {event_type} from {ip} - {details}"
            )

    def get_events(
        self,
        event_type: str = None,
        ip: str = None,
        limit: int = 50,
        since: float = None,
    ) -> List[SecurityEvent]:
        """查询安全事件"""
        with self._events_lock:
            events = list(self._events)

        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if ip:
            events = [e for e in events if e.ip_address == ip]
        if since:
            events = [e for e in events if e.timestamp >= since]

        return events[-limit:]

    def get_stats(self) -> Dict:
        """获取安全统计"""
        with self._events_lock:
            events = list(self._events)

        by_type = defaultdict(int)
        by_ip = defaultdict(int)
        for e in events:
            by_type[e.event_type] += 1
            if e.event_type != "ok":
                by_ip[e.ip_address] += 1

        return {
            "total_events": len(events),
            "by_type": dict(by_type),
            "top_blocked_ips": dict(
                sorted(by_ip.items(), key=lambda x: -x[1])[:10]
            ),
            "nonce_store_size": self._nonce_store.size,
        }

    def reset(self):
        """重置所有状态"""
        with self._events_lock:
            self._events.clear()
        self._nonce_store = NonceStore(
            ttl=self.config.nonce_ttl,
            max_size=self.config.max_nonces,
        )
        self._rate_limiter.reset()
