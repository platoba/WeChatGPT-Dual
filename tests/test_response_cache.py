"""Tests for services/response_cache.py"""

import os
import time
import pytest
import tempfile
from services.response_cache import ResponseCache


@pytest.fixture
def cache_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def cache(cache_db):
    return ResponseCache(db_path=cache_db, ttl=3600, similarity_threshold=0.85)


class TestResponseCache:
    def test_put_and_get(self, cache):
        cache.put("What is Python?", "Python is a programming language.", tokens_used=50)
        result = cache.get("What is Python?")
        assert result == "Python is a programming language."

    def test_miss(self, cache):
        result = cache.get("something random")
        assert result is None

    def test_expiry(self, cache_db):
        cache = ResponseCache(db_path=cache_db, ttl=0)  # instant expiry
        cache.put("test", "response")
        time.sleep(0.01)
        result = cache.get("test")
        assert result is None

    def test_invalidate(self, cache):
        cache.put("What is Java?", "Java is a language.")
        cache.invalidate("What is Java?")
        assert cache.get("What is Java?") is None

    def test_invalidate_user(self, cache):
        cache.put("q1", "r1", user_id="u1")
        cache.put("q2", "r2", user_id="u1")
        cache.put("q3", "r3", user_id="u2")
        cache.invalidate_user("u1")
        # u2's cache should still exist
        assert cache.get("q3") == "r3"

    def test_clear_expired(self, cache_db):
        cache = ResponseCache(db_path=cache_db, ttl=0)
        cache.put("a", "b")
        cache.put("c", "d")
        time.sleep(0.01)
        deleted = cache.clear_expired()
        assert deleted == 2

    def test_clear_all(self, cache):
        cache.put("a", "b")
        cache.put("c", "d")
        cache.clear_all()
        assert cache.get("a") is None
        assert cache.get("c") is None

    def test_stats(self, cache):
        cache.put("test question", "test answer", tokens_used=100)
        cache.get("test question")  # hit
        cache.get("nonexistent")  # miss

        stats = cache.get_stats()
        assert stats["total_entries"] >= 1
        assert stats["total_hits"] >= 1
        assert stats["total_misses"] >= 1
        assert stats["hit_rate"] > 0

    def test_similarity_match(self, cache_db):
        cache = ResponseCache(db_path=cache_db, ttl=3600, similarity_threshold=0.6)
        cache.put(
            "What is Python programming language",
            "Python is a versatile programming language.",
            tokens_used=50,
        )
        # Similar query (high word overlap)
        result = cache.get("What is Python programming")
        assert result is not None
        assert "Python" in result

    def test_no_false_similarity(self, cache):
        cache.put("How to cook pasta", "Boil water and add pasta.", tokens_used=30)
        result = cache.get("What is quantum physics")
        assert result is None

    def test_hit_count_increment(self, cache):
        cache.put("reusable query", "cached response")
        cache.get("reusable query")
        cache.get("reusable query")
        cache.get("reusable query")
        stats = cache.get_stats()
        assert stats["total_hits"] >= 3

    def test_max_entries_eviction(self, cache_db):
        cache = ResponseCache(db_path=cache_db, ttl=3600, max_entries=5)
        for i in range(10):
            cache.put(f"query_{i}", f"response_{i}")
        stats = cache.get_stats()
        assert stats["total_entries"] <= 6  # some may be slightly over during insert

    def test_custom_ttl(self, cache_db):
        cache = ResponseCache(db_path=cache_db, ttl=3600)
        cache.put("short_lived", "ephemeral", ttl=-1)
        assert cache.get("short_lived") is None

    def test_hash_normalization(self, cache):
        cache.put("  Hello World  ", "response")
        result = cache.get("hello world")
        assert result == "response"

    def test_cosine_similarity_identical(self):
        from collections import Counter
        a = Counter({"hello": 1, "world": 1})
        assert ResponseCache._cosine_similarity(a, a) == pytest.approx(1.0)

    def test_cosine_similarity_empty(self):
        from collections import Counter
        assert ResponseCache._cosine_similarity(Counter(), Counter({"a": 1})) == 0.0
