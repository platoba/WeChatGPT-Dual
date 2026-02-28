"""Tests for services/conversation_search.py"""

import os
import time
import pytest
import sqlite3
import tempfile
from services.conversation_search import ConversationSearch, SearchResult, SearchResponse


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def db_with_messages(db_path):
    """Create a database with messages table and sample data"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            channel TEXT DEFAULT 'telegram',
            engine TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            latency REAL DEFAULT 0.0,
            created_at REAL NOT NULL
        );
    """)

    now = time.time()
    messages = [
        ("user1", "user", "How do I write Python code for web scraping?", "telegram", now - 3600),
        ("user1", "assistant", "You can use BeautifulSoup or Scrapy for web scraping in Python.", "telegram", now - 3590),
        ("user1", "user", "Show me a requests example", "telegram", now - 3500),
        ("user1", "assistant", "Here is a simple requests example with error handling.", "telegram", now - 3490),
        ("user2", "user", "What is machine learning?", "wechat", now - 2000),
        ("user2", "assistant", "Machine learning is a subset of AI that learns from data.", "wechat", now - 1990),
        ("user2", "user", "Explain neural networks", "wechat", now - 1500),
        ("user2", "assistant", "Neural networks are computing systems inspired by biological neural networks.", "wechat", now - 1490),
        ("user1", "user", "How to deploy a Flask application?", "telegram", now - 1000),
        ("user1", "assistant", "You can deploy Flask with gunicorn and nginx.", "telegram", now - 990),
        ("user3", "user", "Translate hello to Chinese", "telegram", now - 500),
        ("user3", "assistant", "Hello in Chinese is 你好 (nǐ hǎo).", "telegram", now - 490),
    ]

    for user_id, role, content, channel, created_at in messages:
        conn.execute(
            "INSERT INTO messages (user_id, role, content, channel, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, role, content, channel, created_at),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def search(db_with_messages):
    s = ConversationSearch(db_with_messages)
    s.ensure_index()
    s.rebuild_index()
    return s


class TestSearchResult:
    def test_to_dict(self):
        r = SearchResult(
            message_id=1, user_id="u1", role="user",
            content="test", channel="telegram",
            created_at=1.0, snippet="test", rank=-1.0,
        )
        d = r.to_dict()
        assert d["message_id"] == 1
        assert d["user_id"] == "u1"
        assert d["rank"] == -1.0


class TestSearchResponse:
    def test_has_more_true(self):
        r = SearchResponse(
            query="test", total=50, results=[], took_ms=1.0, page=1, page_size=20
        )
        assert r.has_more is True

    def test_has_more_false(self):
        r = SearchResponse(
            query="test", total=5, results=[], took_ms=1.0, page=1, page_size=20
        )
        assert r.has_more is False

    def test_to_dict(self):
        r = SearchResponse(
            query="test", total=1, results=[], took_ms=1.23456, page=1, page_size=20
        )
        d = r.to_dict()
        assert d["query"] == "test"
        assert d["took_ms"] == 1.23


class TestConversationSearch:
    def test_ensure_index(self, db_with_messages):
        s = ConversationSearch(db_with_messages)
        result = s.ensure_index()
        assert result is True

    def test_ensure_index_no_messages_table(self, db_path):
        s = ConversationSearch(db_path)
        result = s.ensure_index()
        assert result is False

    def test_search_basic(self, search):
        results = search.search("Python")
        assert results.total > 0
        assert any("Python" in r.content or "python" in r.content.lower()
                    for r in results.results)

    def test_search_by_user(self, search):
        results = search.search("Python", user_id="user1")
        assert results.total > 0
        assert all(r.user_id == "user1" for r in results.results)

    def test_search_by_channel(self, search):
        results = search.search("machine learning", channel="wechat")
        assert results.total > 0
        assert all(r.channel == "wechat" for r in results.results)

    def test_search_by_role(self, search):
        results = search.search("Python", role="user")
        assert results.total > 0
        assert all(r.role == "user" for r in results.results)

    def test_search_no_results(self, search):
        results = search.search("nonexistent_xyzzy_term")
        assert results.total == 0
        assert len(results.results) == 0

    def test_search_empty_query(self, search):
        results = search.search("")
        assert results.total == 0

    def test_search_pagination(self, search):
        r1 = search.search("the", page=1, page_size=2)
        r2 = search.search("the", page=2, page_size=2)
        # Pages should return different results (if enough total)
        if r1.total > 2:
            ids1 = {r.message_id for r in r1.results}
            ids2 = {r.message_id for r in r2.results}
            assert ids1 != ids2

    def test_search_took_ms(self, search):
        results = search.search("Python")
        assert results.took_ms >= 0

    def test_search_user_history(self, search):
        results = search.search_user_history("user1", "Flask")
        assert len(results) > 0
        assert all(r.user_id == "user1" for r in results)

    def test_search_time_range(self, search):
        now = time.time()
        results = search.search("Python", since=now - 4000, until=now - 3000)
        assert results.total > 0

    def test_get_index_stats(self, search):
        stats = search.get_index_stats()
        assert "total_indexed" in stats
        assert stats["total_indexed"] == 12

    def test_rebuild_index(self, search):
        search.rebuild_index()
        stats = search.get_index_stats()
        assert stats["total_indexed"] == 12
        assert stats["last_rebuild"] is not None

    def test_sanitize_query_special_chars(self, search):
        # Should not crash with special characters
        results = search.search("hello!@#$%")
        # Just verify it doesn't raise
        assert isinstance(results, SearchResponse)

    def test_sanitize_query_empty(self):
        assert ConversationSearch._sanitize_query("") == ""
        assert ConversationSearch._sanitize_query("   ") == ""

    def test_sanitize_query_normal(self):
        assert ConversationSearch._sanitize_query("hello") == "hello"

    def test_search_chinese(self, search):
        results = search.search("你好")
        assert results.total >= 1

    def test_search_translate(self, search):
        results = search.search("Translate")
        assert results.total >= 1

    def test_multiple_search_terms(self, search):
        results = search.search("web scraping")
        assert results.total > 0
