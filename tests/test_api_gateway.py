"""
tests/test_api_gateway.py - API 网关测试
"""

import os
import time
import threading
import pytest

from services.api_gateway import (
    ApiGateway, ApiKeyStore, ApiKey, ApiResponse,
    RouteConfig, RequestContext, HttpMethod,
    TokenBucket, Router, ResponseCache, MiddlewareChain,
)

TEST_DB = "/tmp/test_api_gateway.db"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for f in [TEST_DB]:
        if os.path.exists(f):
            os.remove(f)


@pytest.fixture
def store():
    return ApiKeyStore(TEST_DB)


@pytest.fixture
def gateway():
    return ApiGateway(db_path=TEST_DB)


# ──────────────── TokenBucket ────────────────

class TestTokenBucket:
    def test_consume_within_limit(self):
        bucket = TokenBucket(rate=10, capacity=10)
        for _ in range(10):
            assert bucket.consume()

    def test_consume_exceeds_capacity(self):
        bucket = TokenBucket(rate=1, capacity=2)
        assert bucket.consume()
        assert bucket.consume()
        assert not bucket.consume()

    def test_refill_over_time(self):
        bucket = TokenBucket(rate=1000, capacity=10)
        for _ in range(10):
            bucket.consume()
        assert not bucket.consume()
        time.sleep(0.02)
        assert bucket.consume()

    def test_available_property(self):
        bucket = TokenBucket(rate=10, capacity=10)
        assert bucket.available == 10
        bucket.consume(5)
        assert bucket.available == 5

    def test_consume_multiple_tokens(self):
        bucket = TokenBucket(rate=10, capacity=10)
        assert bucket.consume(5)
        assert bucket.consume(5)
        assert not bucket.consume(1)

    def test_concurrent_consume(self):
        bucket = TokenBucket(rate=0, capacity=100)
        consumed = {"count": 0}
        lock = threading.Lock()

        def consume_one():
            if bucket.consume():
                with lock:
                    consumed["count"] += 1

        threads = [threading.Thread(target=consume_one) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert consumed["count"] == 100


# ──────────────── ApiKeyStore ────────────────

class TestApiKeyStore:
    def test_generate_key(self, store):
        raw, key = store.generate_key("test-app")
        assert raw.startswith("wgpt_")
        assert key.name == "test-app"
        assert key.enabled
        assert "*" in key.scopes

    def test_get_by_raw(self, store):
        raw, key = store.generate_key("test-app")
        found = store.get_by_raw(raw)
        assert found is not None
        assert found.key_id == key.key_id

    def test_get_by_hash(self, store):
        raw, key = store.generate_key("test-app")
        found = store.get_by_hash(store.hash_key(raw))
        assert found is not None
        assert found.name == "test-app"

    def test_get_nonexistent(self, store):
        assert store.get_by_raw("wgpt_nonexistent") is None

    def test_revoke(self, store):
        raw, key = store.generate_key("test-app")
        assert store.revoke(key.key_id)
        found = store.get_by_raw(raw)
        assert found is not None
        assert not found.enabled

    def test_revoke_nonexistent(self, store):
        assert not store.revoke("nonexistent")

    def test_list_keys(self, store):
        store.generate_key("app1")
        store.generate_key("app2")
        keys = store.list_keys()
        assert len(keys) == 2

    def test_list_keys_exclude_disabled(self, store):
        _, key1 = store.generate_key("app1")
        store.generate_key("app2")
        store.revoke(key1.key_id)
        keys = store.list_keys(include_disabled=False)
        assert len(keys) == 1

    def test_list_keys_include_disabled(self, store):
        _, key1 = store.generate_key("app1")
        store.generate_key("app2")
        store.revoke(key1.key_id)
        keys = store.list_keys(include_disabled=True)
        assert len(keys) == 2

    def test_generate_with_scopes(self, store):
        raw, key = store.generate_key("scoped", scopes=["chat:read", "chat:write"])
        assert key.scopes == ["chat:read", "chat:write"]
        found = store.get_by_raw(raw)
        assert found.scopes == ["chat:read", "chat:write"]

    def test_generate_with_expiry(self, store):
        raw, key = store.generate_key("expiring", expires_in=3600)
        assert key.expires_at is not None
        assert not key.is_expired()

    def test_expired_key(self, store):
        raw, key = store.generate_key("expired", expires_in=-1)
        found = store.get_by_raw(raw)
        assert found.is_expired()

    def test_generate_with_metadata(self, store):
        raw, key = store.generate_key("meta", metadata={"env": "prod"})
        found = store.get_by_raw(raw)
        assert found.metadata == {"env": "prod"}

    def test_log_request(self, store):
        ctx = RequestContext(
            request_id="test-123", path="/api/v1/chat",
            method="POST", client_ip="1.2.3.4",
        )
        store.log_request(ctx, 200, 15.5)
        stats = store.get_request_stats(1)
        assert stats["total_requests"] == 1

    def test_request_stats(self, store):
        for i in range(5):
            ctx = RequestContext(
                request_id=f"req-{i}", path="/api/v1/chat",
                method="POST",
            )
            status = 200 if i < 3 else 500
            store.log_request(ctx, status, 10.0 + i)

        stats = store.get_request_stats(1)
        assert stats["total_requests"] == 5
        assert stats["error_count"] == 2
        assert 0.3 < stats["error_rate"] < 0.5

    def test_request_stats_by_path(self, store):
        for path in ["/api/chat", "/api/chat", "/api/health"]:
            ctx = RequestContext(request_id="r", path=path, method="GET")
            store.log_request(ctx, 200, 5.0)

        stats = store.get_request_stats(1)
        assert len(stats["top_paths"]) == 2
        assert stats["top_paths"][0]["path"] == "/api/chat"

    def test_hash_key_deterministic(self):
        h1 = ApiKeyStore.hash_key("test_key")
        h2 = ApiKeyStore.hash_key("test_key")
        assert h1 == h2

    def test_hash_key_different_inputs(self):
        h1 = ApiKeyStore.hash_key("key1")
        h2 = ApiKeyStore.hash_key("key2")
        assert h1 != h2


# ──────────────── ApiKey Model ────────────────

class TestApiKey:
    def test_has_scope_wildcard(self):
        key = ApiKey(key_id="1", key_hash="h", name="t", scopes=["*"])
        assert key.has_scope("anything")

    def test_has_scope_exact(self):
        key = ApiKey(key_id="1", key_hash="h", name="t", scopes=["chat:read"])
        assert key.has_scope("chat:read")
        assert not key.has_scope("chat:write")

    def test_has_scope_prefix_wildcard(self):
        key = ApiKey(key_id="1", key_hash="h", name="t", scopes=["chat:*"])
        assert key.has_scope("chat:read")
        assert key.has_scope("chat:write")
        assert not key.has_scope("admin:read")

    def test_not_expired_no_expiry(self):
        key = ApiKey(key_id="1", key_hash="h", name="t")
        assert not key.is_expired()

    def test_expired(self):
        key = ApiKey(key_id="1", key_hash="h", name="t", expires_at=1.0)
        assert key.is_expired()

    def test_not_expired_future(self):
        key = ApiKey(key_id="1", key_hash="h", name="t", expires_at=time.time() + 9999)
        assert not key.is_expired()


# ──────────────── Router ────────────────

class TestRouter:
    def test_simple_match(self):
        router = Router()
        config = RouteConfig(path="/api/v1/chat", method=HttpMethod.POST, handler_name="chat")
        router.add_route(config, lambda ctx: "ok")
        match = router.match("/api/v1/chat", "POST")
        assert match is not None
        route, params, handler = match
        assert route.handler_name == "chat"
        assert params == {}

    def test_path_params(self):
        router = Router()
        config = RouteConfig(
            path="/api/v1/users/{user_id}/messages/{msg_id}",
            method=HttpMethod.GET, handler_name="get_msg",
        )
        router.add_route(config, lambda ctx, **kw: kw)
        match = router.match("/api/v1/users/abc/messages/123", "GET")
        assert match is not None
        _, params, _ = match
        assert params == {"user_id": "abc", "msg_id": "123"}

    def test_no_match_wrong_method(self):
        router = Router()
        config = RouteConfig(path="/api/health", method=HttpMethod.GET, handler_name="health")
        router.add_route(config, lambda ctx: "ok")
        assert router.match("/api/health", "POST") is None

    def test_no_match_wrong_path(self):
        router = Router()
        config = RouteConfig(path="/api/health", method=HttpMethod.GET, handler_name="health")
        router.add_route(config, lambda ctx: "ok")
        assert router.match("/api/unknown", "GET") is None

    def test_list_routes(self):
        router = Router()
        for path, method in [("/a", "GET"), ("/b", "POST")]:
            config = RouteConfig(path=path, method=HttpMethod(method), handler_name=path)
            router.add_route(config, lambda ctx: None)
        routes = router.list_routes()
        assert len(routes) == 2

    def test_multiple_routes_same_path_diff_method(self):
        router = Router()
        router.add_route(
            RouteConfig(path="/api/item", method=HttpMethod.GET, handler_name="get_item"),
            lambda ctx: "get",
        )
        router.add_route(
            RouteConfig(path="/api/item", method=HttpMethod.POST, handler_name="create_item"),
            lambda ctx: "post",
        )
        get_match = router.match("/api/item", "GET")
        post_match = router.match("/api/item", "POST")
        assert get_match[0].handler_name == "get_item"
        assert post_match[0].handler_name == "create_item"


# ──────────────── ResponseCache ────────────────

class TestResponseCache:
    def test_set_and_get(self):
        cache = ResponseCache()
        resp = ApiResponse(status_code=200, body={"msg": "ok"})
        cache.set("/test", "GET", resp, ttl=60)
        found = cache.get("/test", "GET")
        assert found is not None
        assert found.body == {"msg": "ok"}

    def test_miss(self):
        cache = ResponseCache()
        assert cache.get("/test", "GET") is None

    def test_expired(self):
        cache = ResponseCache()
        resp = ApiResponse(status_code=200, body={"msg": "ok"})
        cache.set("/test", "GET", resp, ttl=-1)
        # TTL -1 means already expired
        assert cache.get("/test", "GET") is None

    def test_zero_ttl_no_cache(self):
        cache = ResponseCache()
        resp = ApiResponse(status_code=200, body={"msg": "ok"})
        cache.set("/test", "GET", resp, ttl=0)
        assert cache.get("/test", "GET") is None

    def test_clear(self):
        cache = ResponseCache()
        resp = ApiResponse(status_code=200, body={})
        cache.set("/a", "GET", resp, ttl=60)
        cache.set("/b", "GET", resp, ttl=60)
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0

    def test_max_size_eviction(self):
        cache = ResponseCache(max_size=2)
        for i in range(3):
            resp = ApiResponse(status_code=200, body={"i": i})
            cache.set(f"/path{i}", "GET", resp, ttl=60)
        assert cache.size <= 2

    def test_different_methods_different_keys(self):
        cache = ResponseCache()
        r1 = ApiResponse(status_code=200, body={"method": "GET"})
        r2 = ApiResponse(status_code=200, body={"method": "POST"})
        cache.set("/test", "GET", r1, ttl=60)
        cache.set("/test", "POST", r2, ttl=60)
        assert cache.get("/test", "GET").body["method"] == "GET"
        assert cache.get("/test", "POST").body["method"] == "POST"


# ──────────────── MiddlewareChain ────────────────

class TestMiddlewareChain:
    def test_empty_chain(self):
        chain = MiddlewareChain()
        ctx = RequestContext(request_id="r", path="/", method="GET")
        assert chain.execute(ctx) is None

    def test_passthrough(self):
        chain = MiddlewareChain()
        chain.add(lambda ctx: None)
        ctx = RequestContext(request_id="r", path="/", method="GET")
        assert chain.execute(ctx) is None

    def test_block(self):
        chain = MiddlewareChain()
        chain.add(lambda ctx: ApiResponse(status_code=403, body={"error": "blocked"}))
        ctx = RequestContext(request_id="r", path="/", method="GET")
        result = chain.execute(ctx)
        assert result.status_code == 403

    def test_order_matters(self):
        chain = MiddlewareChain()
        calls = []
        chain.add(lambda ctx: (calls.append(1), None)[-1])
        chain.add(lambda ctx: (calls.append(2), None)[-1])
        ctx = RequestContext(request_id="r", path="/", method="GET")
        chain.execute(ctx)
        assert calls == [1, 2]

    def test_early_return(self):
        chain = MiddlewareChain()
        calls = []
        chain.add(lambda ctx: ApiResponse(status_code=200, body={"early": True}))
        chain.add(lambda ctx: (calls.append("should not run"), None)[-1])
        ctx = RequestContext(request_id="r", path="/", method="GET")
        result = chain.execute(ctx)
        assert result.status_code == 200
        assert len(calls) == 0


# ──────────────── ApiGateway (集成) ────────────────

class TestApiGateway:
    def test_route_not_found(self, gateway):
        ctx = RequestContext(request_id="r1", path="/unknown", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 404

    def test_auth_required_no_key(self, gateway):
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {"reply": "hi"},
        )
        ctx = RequestContext(request_id="r1", path="/api/chat", method="POST")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 401

    def test_auth_with_valid_key(self, gateway):
        raw, key = gateway.create_key("test-app")
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {"reply": "hi"},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/chat", method="POST",
            headers={"authorization": f"Bearer {raw}"},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200
        assert resp.body == {"reply": "hi"}

    def test_auth_with_x_api_key(self, gateway):
        raw, _ = gateway.create_key("test-app")
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {"ok": True},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/chat", method="POST",
            headers={"x-api-key": raw},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200

    def test_auth_invalid_key(self, gateway):
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/chat", method="POST",
            headers={"authorization": "Bearer wgpt_invalid"},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 401

    def test_auth_revoked_key(self, gateway):
        raw, key = gateway.create_key("test-app")
        gateway.revoke_key(key.key_id)
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/chat", method="POST",
            headers={"authorization": f"Bearer {raw}"},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 403

    def test_auth_expired_key(self, gateway):
        raw, key = gateway.create_key("test-app", expires_in=-1)
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/chat", method="POST",
            headers={"authorization": f"Bearer {raw}"},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 403

    def test_no_auth_required(self, gateway):
        gateway.add_route(
            RouteConfig(path="/api/health", method=HttpMethod.GET,
                        handler_name="health", auth_required=False),
            lambda ctx: {"status": "ok"},
        )
        ctx = RequestContext(request_id="r1", path="/api/health", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200

    def test_scope_check(self, gateway):
        raw, _ = gateway.create_key("limited", scopes=["chat:read"])
        gateway.add_route(
            RouteConfig(path="/api/admin", method=HttpMethod.POST,
                        handler_name="admin", auth_required=True,
                        required_scopes=["admin:write"]),
            lambda ctx: {},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/admin", method="POST",
            headers={"authorization": f"Bearer {raw}"},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 403
        assert "Missing scope" in resp.body["error"]

    def test_scope_wildcard_passes(self, gateway):
        raw, _ = gateway.create_key("admin", scopes=["*"])
        gateway.add_route(
            RouteConfig(path="/api/admin", method=HttpMethod.POST,
                        handler_name="admin", auth_required=True,
                        required_scopes=["admin:write"]),
            lambda ctx: {"admin": True},
        )
        ctx = RequestContext(
            request_id="r1", path="/api/admin", method="POST",
            headers={"authorization": f"Bearer {raw}"},
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200

    def test_rate_limiting(self, gateway):
        raw, _ = gateway.create_key("test", rate_limit=2)
        gateway.add_route(
            RouteConfig(path="/api/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True),
            lambda ctx: {"ok": True},
        )

        for i in range(3):
            ctx = RequestContext(
                request_id=f"r{i}", path="/api/chat", method="POST",
                headers={"authorization": f"Bearer {raw}"},
            )
            resp = gateway.handle_request(ctx)
            if i < 2:
                assert resp.status_code == 200
            else:
                assert resp.status_code == 429

    def test_path_params_passed_to_handler(self, gateway):
        def get_user(ctx, user_id=None):
            return {"user_id": user_id}

        gateway.add_route(
            RouteConfig(path="/api/users/{user_id}", method=HttpMethod.GET,
                        handler_name="get_user", auth_required=False),
            get_user,
        )
        ctx = RequestContext(request_id="r1", path="/api/users/abc123", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200
        assert resp.body["user_id"] == "abc123"

    def test_deprecated_route_header(self, gateway):
        gateway.add_route(
            RouteConfig(path="/api/old", method=HttpMethod.GET,
                        handler_name="old", auth_required=False, deprecated=True),
            lambda ctx: {"old": True},
        )
        ctx = RequestContext(request_id="r1", path="/api/old", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == "true"

    def test_response_caching(self, gateway):
        call_count = {"n": 0}

        def handler(ctx):
            call_count["n"] += 1
            return {"count": call_count["n"]}

        gateway.add_route(
            RouteConfig(path="/api/cached", method=HttpMethod.GET,
                        handler_name="cached", auth_required=False, cache_ttl=300),
            handler,
        )

        ctx1 = RequestContext(request_id="r1", path="/api/cached", method="GET")
        resp1 = gateway.handle_request(ctx1)
        assert resp1.body["count"] == 1
        assert resp1.headers.get("X-Cache") == "MISS"

        ctx2 = RequestContext(request_id="r2", path="/api/cached", method="GET")
        resp2 = gateway.handle_request(ctx2)
        assert resp2.body["count"] == 1  # cached
        assert resp2.headers.get("X-Cache") == "HIT"

    def test_middleware_blocks_request(self, gateway):
        def ip_blocker(ctx):
            if ctx.client_ip == "evil.ip":
                return ApiResponse(status_code=403, body={"error": "blocked"})
            return None

        gateway.middleware.add(ip_blocker)
        gateway.add_route(
            RouteConfig(path="/api/test", method=HttpMethod.GET,
                        handler_name="test", auth_required=False),
            lambda ctx: {"ok": True},
        )

        ctx = RequestContext(
            request_id="r1", path="/api/test", method="GET", client_ip="evil.ip"
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 403

    def test_middleware_passes(self, gateway):
        gateway.middleware.add(lambda ctx: None)
        gateway.add_route(
            RouteConfig(path="/api/test", method=HttpMethod.GET,
                        handler_name="test", auth_required=False),
            lambda ctx: {"ok": True},
        )
        ctx = RequestContext(request_id="r1", path="/api/test", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200

    def test_handler_exception(self, gateway):
        def bad_handler(ctx):
            raise RuntimeError("boom")

        gateway.add_route(
            RouteConfig(path="/api/boom", method=HttpMethod.GET,
                        handler_name="boom", auth_required=False),
            bad_handler,
        )
        ctx = RequestContext(request_id="r1", path="/api/boom", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 500

    def test_handler_returns_api_response(self, gateway):
        def custom_handler(ctx):
            return ApiResponse(
                status_code=201,
                body={"created": True},
                headers={"Location": "/api/items/1"},
            )

        gateway.add_route(
            RouteConfig(path="/api/items", method=HttpMethod.POST,
                        handler_name="create", auth_required=False),
            custom_handler,
        )
        ctx = RequestContext(request_id="r1", path="/api/items", method="POST")
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 201
        assert resp.body["created"]

    def test_list_routes(self, gateway):
        gateway.add_route(
            RouteConfig(path="/a", method=HttpMethod.GET, handler_name="a",
                        auth_required=False, description="Route A"),
            lambda ctx: {},
        )
        gateway.add_route(
            RouteConfig(path="/b", method=HttpMethod.POST, handler_name="b",
                        auth_required=True),
            lambda ctx: {},
        )
        routes = gateway.list_routes()
        assert len(routes) == 2

    def test_get_stats(self, gateway):
        stats = gateway.get_stats(1)
        assert stats["total_requests"] == 0

    def test_request_id_in_response(self, gateway):
        gateway.add_route(
            RouteConfig(path="/api/test", method=HttpMethod.GET,
                        handler_name="test", auth_required=False),
            lambda ctx: {"ok": True},
        )
        ctx = RequestContext(request_id="unique-id-123", path="/api/test", method="GET")
        resp = gateway.handle_request(ctx)
        assert resp.request_id == "unique-id-123"

    def test_api_response_to_dict(self):
        resp = ApiResponse(status_code=200, body={"msg": "ok"}, request_id="r1")
        d = resp.to_dict()
        assert d["status"] == 200
        assert d["request_id"] == "r1"
        assert d["data"]["msg"] == "ok"

    def test_hmac_verify(self):
        secret = "my_secret"
        payload = '{"msg":"hello"}'
        import hmac as hmac_mod
        sig = hmac_mod.new(secret.encode(), payload.encode(), "sha256").hexdigest()
        assert ApiGateway.verify_hmac(secret, payload, sig)
        assert not ApiGateway.verify_hmac(secret, payload, "invalid_sig")

    def test_full_flow_create_key_and_call(self, gateway):
        """完整流程: 创建key → 注册路由 → 调用 → 检查统计"""
        raw, key = gateway.create_key("full-test", scopes=["chat:*"], rate_limit=100)

        gateway.add_route(
            RouteConfig(path="/api/v1/chat", method=HttpMethod.POST,
                        handler_name="chat", auth_required=True,
                        required_scopes=["chat:write"]),
            lambda ctx: {"reply": "Hello!"},
        )

        ctx = RequestContext(
            request_id="flow-1", path="/api/v1/chat", method="POST",
            headers={"authorization": f"Bearer {raw}"},
            body='{"message": "Hi"}',
        )
        resp = gateway.handle_request(ctx)
        assert resp.status_code == 200
        assert resp.body["reply"] == "Hello!"

    def test_route_rate_limit_override(self, gateway):
        raw, _ = gateway.create_key("test", rate_limit=100)
        gateway.add_route(
            RouteConfig(path="/api/limited", method=HttpMethod.POST,
                        handler_name="limited", auth_required=True,
                        rate_limit=1),
            lambda ctx: {"ok": True},
        )

        for i in range(2):
            ctx = RequestContext(
                request_id=f"r{i}", path="/api/limited", method="POST",
                headers={"authorization": f"Bearer {raw}"},
            )
            resp = gateway.handle_request(ctx)
            if i == 0:
                assert resp.status_code == 200
            else:
                assert resp.status_code == 429

    def test_no_auth_rate_limit_by_ip(self, gateway):
        gw = ApiGateway(db_path=TEST_DB + ".ip", default_rate_limit=2)
        gw.add_route(
            RouteConfig(path="/api/open", method=HttpMethod.GET,
                        handler_name="open", auth_required=False),
            lambda ctx: {"ok": True},
        )

        for i in range(3):
            ctx = RequestContext(
                request_id=f"r{i}", path="/api/open", method="GET",
                client_ip="1.2.3.4",
            )
            resp = gw.handle_request(ctx)
            if i < 2:
                assert resp.status_code == 200
            else:
                assert resp.status_code == 429

        import os
        os.remove(TEST_DB + ".ip")
