"""
services/api_gateway.py - API 网关服务
统一API入口: 路由分发 + 认证鉴权 + 请求日志 + 限流 + 版本管理
"""

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────── 数据模型 ────────────────

class AuthMethod(Enum):
    NONE = "none"
    API_KEY = "api_key"
    HMAC_SHA256 = "hmac_sha256"
    BEARER_TOKEN = "bearer_token"


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


@dataclass
class ApiKey:
    key_id: str
    key_hash: str
    name: str
    scopes: List[str] = field(default_factory=list)
    rate_limit: int = 60  # requests per minute
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def has_scope(self, scope: str) -> bool:
        if "*" in self.scopes:
            return True
        # 支持通配符匹配: "chat:*" 匹配 "chat:read", "chat:write"
        for s in self.scopes:
            if s == scope:
                return True
            if s.endswith(":*"):
                prefix = s[:-2]
                if scope.startswith(prefix + ":"):
                    return True
        return False


@dataclass
class RouteConfig:
    path: str
    method: HttpMethod
    handler_name: str
    auth_required: bool = True
    required_scopes: List[str] = field(default_factory=list)
    rate_limit: Optional[int] = None  # per-route override
    version: str = "v1"
    description: str = ""
    deprecated: bool = False
    cache_ttl: int = 0  # seconds, 0 = no cache


@dataclass
class RequestContext:
    request_id: str
    path: str
    method: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None
    query_params: Dict[str, str] = field(default_factory=dict)
    client_ip: str = "127.0.0.1"
    timestamp: float = field(default_factory=time.time)
    api_key: Optional[ApiKey] = None
    user_agent: str = ""


@dataclass
class ApiResponse:
    status_code: int
    body: Dict[str, Any]
    headers: Dict[str, str] = field(default_factory=dict)
    request_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status_code,
            "request_id": self.request_id,
            "data": self.body,
        }


# ──────────────── 令牌桶限流 ────────────────

class TokenBucket:
    """线程安全令牌桶"""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    @property
    def available(self) -> int:
        with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            return int(min(self.capacity, self.tokens + elapsed * self.rate))


# ──────────────── API Key 存储 ────────────────

class ApiKeyStore:
    """SQLite API Key 管理"""

    def __init__(self, db_path: str = "api_gateway.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    scopes TEXT DEFAULT '[]',
                    rate_limit INTEGER DEFAULT 60,
                    enabled INTEGER DEFAULT 1,
                    created_at REAL,
                    expires_at REAL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    key_id TEXT,
                    path TEXT NOT NULL,
                    method TEXT NOT NULL,
                    status_code INTEGER,
                    client_ip TEXT,
                    user_agent TEXT,
                    latency_ms REAL,
                    error TEXT,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_request_log_ts ON request_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_request_log_key ON request_log(key_id);
                CREATE INDEX IF NOT EXISTS idx_request_log_path ON request_log(path);
            """)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def generate_key(self, name: str, scopes: Optional[List[str]] = None,
                     rate_limit: int = 60, expires_in: Optional[int] = None,
                     metadata: Optional[Dict] = None) -> Tuple[str, ApiKey]:
        """生成新 API key, 返回 (raw_key, ApiKey)"""
        raw_key = f"wgpt_{uuid.uuid4().hex}"
        key_id = str(uuid.uuid4())[:8]
        key_hash = self.hash_key(raw_key)
        expires_at = time.time() + expires_in if expires_in else None

        api_key = ApiKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            scopes=scopes or ["*"],
            rate_limit=rate_limit,
            enabled=True,
            expires_at=expires_at,
            metadata=metadata or {},
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO api_keys 
                   (key_id, key_hash, name, scopes, rate_limit, enabled, created_at, expires_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (api_key.key_id, api_key.key_hash, api_key.name,
                 json.dumps(api_key.scopes), api_key.rate_limit,
                 1, api_key.created_at, api_key.expires_at,
                 json.dumps(api_key.metadata)),
            )

        return raw_key, api_key

    def get_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()

        if not row:
            return None

        return ApiKey(
            key_id=row[0], key_hash=row[1], name=row[2],
            scopes=json.loads(row[3]), rate_limit=row[4],
            enabled=bool(row[5]), created_at=row[6],
            expires_at=row[7], metadata=json.loads(row[8]),
        )

    def get_by_raw(self, raw_key: str) -> Optional[ApiKey]:
        return self.get_by_hash(self.hash_key(raw_key))

    def revoke(self, key_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET enabled = 0 WHERE key_id = ?", (key_id,)
            )
            return cursor.rowcount > 0

    def list_keys(self, include_disabled: bool = False) -> List[ApiKey]:
        with sqlite3.connect(self.db_path) as conn:
            if include_disabled:
                rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM api_keys WHERE enabled = 1 ORDER BY created_at DESC"
                ).fetchall()

        return [
            ApiKey(
                key_id=r[0], key_hash=r[1], name=r[2],
                scopes=json.loads(r[3]), rate_limit=r[4],
                enabled=bool(r[5]), created_at=r[6],
                expires_at=r[7], metadata=json.loads(r[8]),
            )
            for r in rows
        ]

    def log_request(self, ctx: RequestContext, status_code: int,
                    latency_ms: float, error: Optional[str] = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO request_log
                   (request_id, key_id, path, method, status_code, client_ip, 
                    user_agent, latency_ms, error, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ctx.request_id, ctx.api_key.key_id if ctx.api_key else None,
                 ctx.path, ctx.method, status_code, ctx.client_ip,
                 ctx.user_agent, latency_ms, error, ctx.timestamp),
            )

    def get_request_stats(self, hours: int = 24) -> Dict[str, Any]:
        since = time.time() - hours * 3600
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE timestamp > ?", (since,)
            ).fetchone()[0]

            errors = conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE timestamp > ? AND status_code >= 400",
                (since,),
            ).fetchone()[0]

            avg_latency = conn.execute(
                "SELECT AVG(latency_ms) FROM request_log WHERE timestamp > ?", (since,)
            ).fetchone()[0]

            by_path = conn.execute(
                """SELECT path, COUNT(*), AVG(latency_ms) 
                   FROM request_log WHERE timestamp > ?
                   GROUP BY path ORDER BY COUNT(*) DESC LIMIT 10""",
                (since,),
            ).fetchall()

            by_status = conn.execute(
                """SELECT status_code, COUNT(*)
                   FROM request_log WHERE timestamp > ?
                   GROUP BY status_code ORDER BY COUNT(*) DESC""",
                (since,),
            ).fetchall()

        return {
            "total_requests": total,
            "error_count": errors,
            "error_rate": errors / total if total > 0 else 0,
            "avg_latency_ms": round(avg_latency or 0, 2),
            "top_paths": [{"path": p, "count": c, "avg_ms": round(l or 0, 2)} for p, c, l in by_path],
            "status_distribution": {str(s): c for s, c in by_status},
            "period_hours": hours,
        }


# ──────────────── 路由引擎 ────────────────

class Router:
    """URL路由匹配器，支持路径参数"""

    def __init__(self):
        self._routes: List[Tuple[str, RouteConfig]] = []
        self._handlers: Dict[str, Callable] = {}
        self._compiled: List[Tuple[re.Pattern, RouteConfig]] = []

    def add_route(self, config: RouteConfig, handler: Callable):
        # 将路径参数转为正则: /api/v1/chat/{user_id} → /api/v1/chat/(?P<user_id>[^/]+)
        pattern = re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', config.path)
        pattern = f"^{pattern}$"
        self._compiled.append((re.compile(pattern), config))
        self._handlers[config.handler_name] = handler

    def match(self, path: str, method: str) -> Optional[Tuple[RouteConfig, Dict[str, str], Callable]]:
        for regex, config in self._compiled:
            if config.method.value != method.upper():
                continue
            m = regex.match(path)
            if m:
                handler = self._handlers.get(config.handler_name)
                if handler:
                    return config, m.groupdict(), handler
        return None

    def list_routes(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": config.path,
                "method": config.method.value,
                "version": config.version,
                "auth_required": config.auth_required,
                "scopes": config.required_scopes,
                "description": config.description,
                "deprecated": config.deprecated,
            }
            for _, config in self._compiled
        ]


# ──────────────── 响应缓存 ────────────────

class ResponseCache:
    """简单内存缓存"""

    def __init__(self, max_size: int = 1000):
        self._cache: Dict[str, Tuple[float, ApiResponse]] = {}
        self._max_size = max_size
        self._lock = threading.Lock()

    def _make_key(self, path: str, method: str, params: str = "") -> str:
        return f"{method}:{path}:{params}"

    def get(self, path: str, method: str, params: str = "") -> Optional[ApiResponse]:
        key = self._make_key(path, method, params)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, response = entry
            if time.time() > expires_at:
                del self._cache[key]
                return None
            return response

    def set(self, path: str, method: str, response: ApiResponse,
            ttl: int, params: str = ""):
        if ttl <= 0:
            return
        key = self._make_key(path, method, params)
        with self._lock:
            if len(self._cache) >= self._max_size:
                # 清除过期条目
                now = time.time()
                expired = [k for k, (e, _) in self._cache.items() if now > e]
                for k in expired:
                    del self._cache[k]
                # 还满则删最旧的
                if len(self._cache) >= self._max_size:
                    oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                    del self._cache[oldest_key]

            self._cache[key] = (time.time() + ttl, response)

    def clear(self):
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# ──────────────── Middleware ────────────────

class MiddlewareChain:
    """中间件管道"""

    def __init__(self):
        self._middlewares: List[Callable] = []

    def add(self, middleware: Callable):
        self._middlewares.append(middleware)

    def execute(self, ctx: RequestContext) -> Optional[ApiResponse]:
        """按顺序执行中间件，任一返回 ApiResponse 则中断"""
        for mw in self._middlewares:
            result = mw(ctx)
            if result is not None:
                return result
        return None


# ──────────────── API 网关 ────────────────

class ApiGateway:
    """
    统一API网关
    - 路由分发 (含路径参数)
    - API Key 认证 + HMAC 签名验证
    - 权限作用域
    - 每key/每路由限流 (令牌桶)
    - 请求日志 + 统计
    - 响应缓存
    - 中间件管道
    - API版本管理
    """

    def __init__(self, store: Optional[ApiKeyStore] = None,
                 db_path: str = "api_gateway.db",
                 default_rate_limit: int = 60):
        self.store = store or ApiKeyStore(db_path)
        self.router = Router()
        self.cache = ResponseCache()
        self.middleware = MiddlewareChain()
        self.default_rate_limit = default_rate_limit
        self._rate_limiters: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    # ──── 路由注册 ────

    def route(self, path: str, method: str = "GET", **kwargs):
        """装饰器注册路由"""
        def decorator(func):
            config = RouteConfig(
                path=path,
                method=HttpMethod(method.upper()),
                handler_name=func.__name__,
                **kwargs,
            )
            self.router.add_route(config, func)
            return func
        return decorator

    def add_route(self, config: RouteConfig, handler: Callable):
        self.router.add_route(config, handler)

    # ──── 认证 ────

    def _authenticate(self, ctx: RequestContext) -> Optional[ApiResponse]:
        """从 header 提取并验证 API key"""
        auth_header = ctx.headers.get("authorization", "")

        if auth_header.startswith("Bearer "):
            raw_key = auth_header[7:]
        elif "x-api-key" in ctx.headers:
            raw_key = ctx.headers["x-api-key"]
        else:
            return ApiResponse(
                status_code=401,
                body={"error": "Missing authentication"},
                request_id=ctx.request_id,
            )

        api_key = self.store.get_by_raw(raw_key)
        if api_key is None:
            return ApiResponse(
                status_code=401,
                body={"error": "Invalid API key"},
                request_id=ctx.request_id,
            )

        if not api_key.enabled:
            return ApiResponse(
                status_code=403,
                body={"error": "API key revoked"},
                request_id=ctx.request_id,
            )

        if api_key.is_expired():
            return ApiResponse(
                status_code=403,
                body={"error": "API key expired"},
                request_id=ctx.request_id,
            )

        ctx.api_key = api_key
        return None

    # ──── HMAC 签名验证 ────

    @staticmethod
    def verify_hmac(secret: str, payload: str, signature: str,
                    algorithm: str = "sha256") -> bool:
        expected = hmac.new(
            secret.encode(), payload.encode(), algorithm
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ──── 限流 ────

    def _get_bucket(self, key: str, rate_limit: int) -> TokenBucket:
        with self._lock:
            if key not in self._rate_limiters:
                self._rate_limiters[key] = TokenBucket(
                    rate=rate_limit / 60.0,
                    capacity=rate_limit,
                )
            return self._rate_limiters[key]

    def _check_rate_limit(self, ctx: RequestContext,
                          route: RouteConfig) -> Optional[ApiResponse]:
        # 确定限流key和速率
        if ctx.api_key:
            limit_key = f"key:{ctx.api_key.key_id}"
            limit = route.rate_limit or ctx.api_key.rate_limit
        else:
            limit_key = f"ip:{ctx.client_ip}"
            limit = route.rate_limit or self.default_rate_limit

        bucket = self._get_bucket(limit_key, limit)
        if not bucket.consume():
            return ApiResponse(
                status_code=429,
                body={"error": "Rate limit exceeded", "retry_after": 60},
                headers={"Retry-After": "60"},
                request_id=ctx.request_id,
            )
        return None

    # ──── 权限检查 ────

    def _check_scope(self, ctx: RequestContext,
                     route: RouteConfig) -> Optional[ApiResponse]:
        if not route.required_scopes:
            return None
        if ctx.api_key is None:
            return None  # 无key时权限由上层决定

        for scope in route.required_scopes:
            if not ctx.api_key.has_scope(scope):
                return ApiResponse(
                    status_code=403,
                    body={"error": f"Missing scope: {scope}"},
                    request_id=ctx.request_id,
                )
        return None

    # ──── 主处理流程 ────

    def handle_request(self, ctx: RequestContext) -> ApiResponse:
        """处理一个请求"""
        start = time.time()
        error_msg = None

        try:
            # 1. 中间件
            mw_result = self.middleware.execute(ctx)
            if mw_result is not None:
                mw_result.request_id = ctx.request_id
                return mw_result

            # 2. 路由匹配
            match = self.router.match(ctx.path, ctx.method)
            if match is None:
                return ApiResponse(
                    status_code=404,
                    body={"error": "Route not found"},
                    request_id=ctx.request_id,
                )

            route, path_params, handler = match

            # 3. 弃用警告
            headers = {}
            if route.deprecated:
                headers["Deprecation"] = "true"
                headers["Sunset"] = "Check documentation for migration guide"

            # 4. 认证
            if route.auth_required:
                auth_err = self._authenticate(ctx)
                if auth_err is not None:
                    return auth_err

            # 5. 权限
            scope_err = self._check_scope(ctx, route)
            if scope_err is not None:
                return scope_err

            # 6. 限流
            rate_err = self._check_rate_limit(ctx, route)
            if rate_err is not None:
                return rate_err

            # 7. 缓存检查 (仅 GET)
            if ctx.method == "GET" and route.cache_ttl > 0:
                cached = self.cache.get(ctx.path, ctx.method)
                if cached is not None:
                    cached.request_id = ctx.request_id
                    cached.headers["X-Cache"] = "HIT"
                    return cached

            # 8. 执行handler
            result = handler(ctx, **path_params)
            if isinstance(result, ApiResponse):
                response = result
            elif isinstance(result, dict):
                response = ApiResponse(
                    status_code=200,
                    body=result,
                    request_id=ctx.request_id,
                )
            else:
                response = ApiResponse(
                    status_code=200,
                    body={"result": result},
                    request_id=ctx.request_id,
                )

            response.request_id = ctx.request_id
            response.headers.update(headers)

            # 9. 缓存存储
            if ctx.method == "GET" and route.cache_ttl > 0:
                response.headers["X-Cache"] = "MISS"
                self.cache.set(ctx.path, ctx.method, response, route.cache_ttl)

            return response

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Request {ctx.request_id} error: {e}")
            return ApiResponse(
                status_code=500,
                body={"error": "Internal server error"},
                request_id=ctx.request_id,
            )
        finally:
            latency = (time.time() - start) * 1000
            try:
                status = 500 if error_msg else 200
                self.store.log_request(ctx, status, latency, error_msg)
            except Exception:
                pass

    # ──── 便捷方法 ────

    def create_key(self, name: str, **kwargs) -> Tuple[str, ApiKey]:
        return self.store.generate_key(name, **kwargs)

    def revoke_key(self, key_id: str) -> bool:
        return self.store.revoke(key_id)

    def list_routes(self) -> List[Dict[str, Any]]:
        return self.router.list_routes()

    def get_stats(self, hours: int = 24) -> Dict[str, Any]:
        return self.store.get_request_stats(hours)
