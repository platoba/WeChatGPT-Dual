"""Tests for Webhook Security Service"""

import time
import pytest
from services.webhook_security import (
    WebhookSecurity,
    SecurityConfig,
    NonceStore,
    RateLimitTracker,
    SecurityEvent,
)


# ---- NonceStore Tests ----

class TestNonceStore:
    def test_new_nonce_accepted(self):
        store = NonceStore(ttl=60)
        assert store.check_and_store("nonce-1") is True

    def test_duplicate_nonce_rejected(self):
        store = NonceStore(ttl=60)
        store.check_and_store("nonce-1")
        assert store.check_and_store("nonce-1") is False

    def test_different_nonces_accepted(self):
        store = NonceStore(ttl=60)
        assert store.check_and_store("a") is True
        assert store.check_and_store("b") is True
        assert store.check_and_store("c") is True

    def test_expired_nonce_cleanup(self):
        store = NonceStore(ttl=1, max_size=4)
        store.check_and_store("old-1")
        store.check_and_store("old-2")
        # Simulate expiry
        for key in list(store._nonces.keys()):
            store._nonces[key] = time.time() - 10
        # Force cleanup by filling up
        store.check_and_store("new-1")
        store.check_and_store("new-2")
        store.check_and_store("new-3")
        assert store.size <= 5  # Old ones should be cleaned

    def test_size_tracking(self):
        store = NonceStore()
        assert store.size == 0
        store.check_and_store("a")
        assert store.size == 1
        store.check_and_store("b")
        assert store.size == 2


# ---- RateLimitTracker Tests ----

class TestRateLimitTracker:
    def test_under_limit_allowed(self):
        tracker = RateLimitTracker(max_requests=5, window=60)
        allowed, remaining = tracker.check("1.2.3.4")
        assert allowed is True
        assert remaining == 4

    def test_at_limit_blocked(self):
        tracker = RateLimitTracker(max_requests=3, window=60)
        tracker.check("1.2.3.4")
        tracker.check("1.2.3.4")
        tracker.check("1.2.3.4")
        allowed, remaining = tracker.check("1.2.3.4")
        assert allowed is False
        assert remaining == 0

    def test_different_ips_independent(self):
        tracker = RateLimitTracker(max_requests=2, window=60)
        tracker.check("1.1.1.1")
        tracker.check("1.1.1.1")
        # IP 1 exhausted
        allowed1, _ = tracker.check("1.1.1.1")
        assert allowed1 is False
        # IP 2 fresh
        allowed2, _ = tracker.check("2.2.2.2")
        assert allowed2 is True

    def test_get_count(self):
        tracker = RateLimitTracker(max_requests=10, window=60)
        assert tracker.get_count("1.2.3.4") == 0
        tracker.check("1.2.3.4")
        tracker.check("1.2.3.4")
        assert tracker.get_count("1.2.3.4") == 2

    def test_reset_single_ip(self):
        tracker = RateLimitTracker(max_requests=5, window=60)
        tracker.check("1.1.1.1")
        tracker.check("2.2.2.2")
        tracker.reset("1.1.1.1")
        assert tracker.get_count("1.1.1.1") == 0
        assert tracker.get_count("2.2.2.2") == 1

    def test_reset_all(self):
        tracker = RateLimitTracker(max_requests=5, window=60)
        tracker.check("1.1.1.1")
        tracker.check("2.2.2.2")
        tracker.reset()
        assert tracker.get_count("1.1.1.1") == 0
        assert tracker.get_count("2.2.2.2") == 0


# ---- WebhookSecurity Tests ----

class TestSignatureVerification:
    def test_valid_signature(self):
        sec = WebhookSecurity(SecurityConfig(secret_key="test-secret"))
        payload = b'{"event": "test"}'
        sig = sec.compute_signature(payload)
        assert sec.verify_signature(payload, sig) is True

    def test_invalid_signature(self):
        sec = WebhookSecurity(SecurityConfig(secret_key="test-secret"))
        payload = b'{"event": "test"}'
        assert sec.verify_signature(payload, "sha256=badhex") is False

    def test_empty_signature_fails(self):
        sec = WebhookSecurity(SecurityConfig(secret_key="test-secret"))
        assert sec.verify_signature(b"data", "") is False

    def test_no_secret_skips_verification(self):
        sec = WebhookSecurity(SecurityConfig(secret_key=""))
        assert sec.verify_signature(b"data", "anything") is True

    def test_tampered_payload_fails(self):
        sec = WebhookSecurity(SecurityConfig(secret_key="secret"))
        payload = b'original'
        sig = sec.compute_signature(payload)
        assert sec.verify_signature(b'tampered', sig) is False

    def test_compute_signature_format(self):
        sec = WebhookSecurity(SecurityConfig(secret_key="key"))
        sig = sec.compute_signature(b"test")
        assert sig.startswith("sha256=")
        assert len(sig) == 71  # "sha256=" + 64 hex chars


class TestIPFiltering:
    def test_no_whitelist_allows_all(self):
        sec = WebhookSecurity(SecurityConfig())
        assert sec.check_ip("1.2.3.4") is True
        assert sec.check_ip("5.6.7.8") is True

    def test_whitelist_blocks_unknown(self):
        sec = WebhookSecurity(SecurityConfig(ip_whitelist={"1.2.3.4"}))
        assert sec.check_ip("1.2.3.4") is True
        assert sec.check_ip("5.6.7.8") is False

    def test_blacklist_blocks(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"evil.ip"}))
        assert sec.check_ip("evil.ip") is False
        assert sec.check_ip("good.ip") is True

    def test_blacklist_overrides_whitelist(self):
        sec = WebhookSecurity(SecurityConfig(
            ip_whitelist={"1.2.3.4"},
            ip_blacklist={"1.2.3.4"},
        ))
        assert sec.check_ip("1.2.3.4") is False

    def test_add_to_whitelist(self):
        sec = WebhookSecurity(SecurityConfig(ip_whitelist={"1.1.1.1"}))
        assert sec.check_ip("2.2.2.2") is False
        sec.add_to_whitelist("2.2.2.2")
        assert sec.check_ip("2.2.2.2") is True

    def test_add_to_blacklist(self):
        sec = WebhookSecurity(SecurityConfig())
        assert sec.check_ip("bad.ip") is True
        sec.add_to_blacklist("bad.ip")
        assert sec.check_ip("bad.ip") is False

    def test_remove_from_whitelist(self):
        sec = WebhookSecurity(SecurityConfig(ip_whitelist={"1.1.1.1", "2.2.2.2"}))
        sec.remove_from_whitelist("2.2.2.2")
        assert "2.2.2.2" not in sec.config.ip_whitelist

    def test_remove_from_blacklist(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"bad.ip"}))
        sec.remove_from_blacklist("bad.ip")
        assert sec.check_ip("bad.ip") is True

    def test_add_whitelist_removes_from_blacklist(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"1.1.1.1"}))
        sec.add_to_whitelist("1.1.1.1")
        assert "1.1.1.1" not in sec.config.ip_blacklist


class TestReplayProtection:
    def test_fresh_request_passes(self):
        sec = WebhookSecurity(SecurityConfig(timestamp_tolerance=300))
        safe, reason = sec.check_replay(nonce="unique-1", timestamp=time.time())
        assert safe is True

    def test_expired_timestamp_fails(self):
        sec = WebhookSecurity(SecurityConfig(timestamp_tolerance=60))
        old_time = time.time() - 120
        safe, reason = sec.check_replay(timestamp=old_time)
        assert safe is False
        assert "timestamp_expired" in reason

    def test_duplicate_nonce_fails(self):
        sec = WebhookSecurity()
        sec.check_replay(nonce="reuse-me")
        safe, reason = sec.check_replay(nonce="reuse-me")
        assert safe is False
        assert "nonce_reused" in reason

    def test_no_nonce_no_timestamp_passes(self):
        sec = WebhookSecurity()
        safe, reason = sec.check_replay()
        assert safe is True

    def test_future_timestamp_passes_within_tolerance(self):
        sec = WebhookSecurity(SecurityConfig(timestamp_tolerance=300))
        future = time.time() + 100
        safe, _ = sec.check_replay(timestamp=future)
        assert safe is True

    def test_far_future_timestamp_fails(self):
        sec = WebhookSecurity(SecurityConfig(timestamp_tolerance=60))
        far_future = time.time() + 120
        safe, _ = sec.check_replay(timestamp=far_future)
        assert safe is False


class TestBodySize:
    def test_small_body_passes(self):
        sec = WebhookSecurity(SecurityConfig(max_body_size=1024))
        assert sec.check_body_size(b"hello") is True

    def test_oversized_body_fails(self):
        sec = WebhookSecurity(SecurityConfig(max_body_size=10))
        assert sec.check_body_size(b"x" * 11) is False

    def test_exact_size_passes(self):
        sec = WebhookSecurity(SecurityConfig(max_body_size=5))
        assert sec.check_body_size(b"12345") is True


class TestValidatePipeline:
    def test_full_valid_request(self):
        config = SecurityConfig(
            secret_key="secret",
            rate_limit_requests=100,
            max_body_size=10000,
        )
        sec = WebhookSecurity(config)
        body = b'{"ok": true}'
        sig = sec.compute_signature(body)
        valid, reason = sec.validate_request(
            body=body,
            ip="1.2.3.4",
            signature=sig,
            nonce="uniq-1",
            timestamp=time.time(),
        )
        assert valid is True
        assert reason == "ok"

    def test_blocked_ip(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"bad.ip"}))
        valid, reason = sec.validate_request(body=b"x", ip="bad.ip")
        assert valid is False
        assert reason == "ip_blocked"

    def test_rate_limited(self):
        sec = WebhookSecurity(SecurityConfig(rate_limit_requests=2, rate_limit_window=60))
        sec.validate_request(body=b"x", ip="1.1.1.1")
        sec.validate_request(body=b"x", ip="1.1.1.1")
        valid, reason = sec.validate_request(body=b"x", ip="1.1.1.1")
        assert valid is False
        assert reason == "rate_limited"

    def test_oversized_body(self):
        sec = WebhookSecurity(SecurityConfig(max_body_size=5))
        valid, reason = sec.validate_request(body=b"x" * 10, ip="1.1.1.1")
        assert valid is False
        assert reason == "body_too_large"

    def test_bad_signature(self):
        sec = WebhookSecurity(SecurityConfig(secret_key="secret"))
        valid, reason = sec.validate_request(
            body=b"data", ip="1.1.1.1", signature="sha256=badbadbad"
        )
        assert valid is False
        assert reason == "signature_invalid"

    def test_replay_detected(self):
        sec = WebhookSecurity()
        sec.validate_request(body=b"x", ip="1.1.1.1", nonce="same")
        valid, reason = sec.validate_request(body=b"x", ip="1.1.1.1", nonce="same")
        assert valid is False
        assert "replay" in reason


class TestAuditLog:
    def test_events_logged(self):
        sec = WebhookSecurity(SecurityConfig(
            ip_blacklist={"bad.ip"},
            log_failures=True,
        ))
        sec.validate_request(body=b"x", ip="bad.ip")
        events = sec.get_events()
        assert len(events) == 1
        assert events[0].event_type == "ip_blocked"

    def test_filter_by_type(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"bad"}, rate_limit_requests=1))
        sec.validate_request(body=b"x", ip="bad")
        sec.validate_request(body=b"x", ip="good")
        sec.validate_request(body=b"x", ip="good")
        events = sec.get_events(event_type="rate_limited")
        assert len(events) == 1

    def test_filter_by_ip(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"a", "b"}))
        sec.validate_request(body=b"x", ip="a")
        sec.validate_request(body=b"x", ip="b")
        events = sec.get_events(ip="a")
        assert len(events) == 1

    def test_stats(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"attacker"}))
        for _ in range(5):
            sec.validate_request(body=b"x", ip="attacker")
        stats = sec.get_stats()
        assert stats["total_events"] == 5
        assert "attacker" in stats["top_blocked_ips"]

    def test_reset(self):
        sec = WebhookSecurity(SecurityConfig(ip_blacklist={"x"}))
        sec.validate_request(body=b"x", ip="x")
        sec.reset()
        assert len(sec.get_events()) == 0
        assert sec.get_stats()["total_events"] == 0
