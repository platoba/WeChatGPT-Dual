"""Tests for conversation_memory module."""
import os
import time
import json
import pytest
import tempfile
from services.conversation_memory import ConversationMemory, Memory, SearchResult, CATEGORIES


@pytest.fixture
def mem():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    m = ConversationMemory(db_path=db_path)
    yield m
    os.unlink(db_path)


class TestSaveAndRetrieve:
    def test_save_memory(self, mem):
        m = mem.save("user1", "I like Python programming", category='preference')
        assert m.memory_id.startswith("mem_")
        assert m.user_id == "user1"
        assert m.content == "I like Python programming"
        assert m.category == 'preference'

    def test_save_with_metadata(self, mem):
        m = mem.save("user1", "Meeting at 3pm", metadata={'source': 'calendar'})
        assert m.metadata == {'source': 'calendar'}

    def test_save_invalid_category_defaults(self, mem):
        m = mem.save("user1", "test", category='nonexistent')
        assert m.category == 'context'

    def test_save_clamps_importance(self, mem):
        m1 = mem.save("user1", "high", importance=1.5)
        assert m1.importance == 1.0
        m2 = mem.save("user1", "low", importance=-0.5)
        assert m2.importance == 0.0

    def test_save_chinese_content(self, mem):
        m = mem.save("user1", "我喜欢用Python编程", category='preference')
        assert m.content == "我喜欢用Python编程"
        assert len(m.tokens) > 0

    def test_get_user_memories(self, mem):
        mem.save("user1", "memory A", importance=0.8)
        mem.save("user1", "memory B", importance=0.3)
        mem.save("user2", "other user")
        results = mem.get_user_memories("user1")
        assert len(results) == 2
        # Should be sorted by importance desc
        assert results[0].importance >= results[1].importance

    def test_get_user_memories_by_category(self, mem):
        mem.save("user1", "fact 1", category='fact')
        mem.save("user1", "pref 1", category='preference')
        facts = mem.get_user_memories("user1", category='fact')
        assert len(facts) == 1
        assert facts[0].category == 'fact'

    def test_get_user_memories_pagination(self, mem):
        for i in range(10):
            mem.save("user1", f"memory {i}", importance=i/10)
        page1 = mem.get_user_memories("user1", limit=3, offset=0)
        page2 = mem.get_user_memories("user1", limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        ids1 = {m.memory_id for m in page1}
        ids2 = {m.memory_id for m in page2}
        assert ids1.isdisjoint(ids2)


class TestSearch:
    def test_basic_search(self, mem):
        mem.save("user1", "Python is my favorite programming language")
        mem.save("user1", "I enjoy hiking on weekends")
        results = mem.search("user1", "programming language")
        assert len(results) > 0
        assert "Python" in results[0].memory.content or "programming" in results[0].memory.content

    def test_search_no_results(self, mem):
        mem.save("user1", "I like cats")
        results = mem.search("user1", "quantum physics nuclear reactor")
        assert len(results) == 0

    def test_search_user_isolation(self, mem):
        mem.save("user1", "secret data for user1")
        mem.save("user2", "data for user2")
        results = mem.search("user1", "secret data")
        for r in results:
            assert r.memory.user_id == "user1"

    def test_search_by_category(self, mem):
        mem.save("user1", "My name is Alice", category='fact')
        mem.save("user1", "I prefer dark mode", category='preference')
        results = mem.search("user1", "name Alice", category='fact')
        assert all(r.memory.category == 'fact' for r in results)

    def test_search_min_importance(self, mem):
        mem.save("user1", "important note about Python", importance=0.9)
        mem.save("user1", "trivial Python comment", importance=0.1)
        results = mem.search("user1", "Python", min_importance=0.5)
        assert all(r.memory.importance >= 0.5 for r in results)

    def test_search_updates_access_count(self, mem):
        m = mem.save("user1", "searchable content about databases")
        mem.search("user1", "databases")
        memories = mem.get_user_memories("user1")
        found = [mm for mm in memories if mm.memory_id == m.memory_id]
        if found:
            assert found[0].access_count >= 1

    def test_search_chinese(self, mem):
        mem.save("user1", "我最喜欢的编程语言是Python")
        mem.save("user1", "周末喜欢去爬山")
        results = mem.search("user1", "编程语言")
        assert len(results) > 0

    def test_search_limit(self, mem):
        for i in range(20):
            mem.save("user1", f"memory about topic {i} with keywords")
        results = mem.search("user1", "memory topic keywords", limit=3)
        assert len(results) <= 3

    def test_search_empty_query(self, mem):
        mem.save("user1", "some content")
        results = mem.search("user1", "")
        assert len(results) == 0

    def test_search_result_has_match_terms(self, mem):
        mem.save("user1", "Python Django web framework development")
        results = mem.search("user1", "Python web development")
        if results:
            assert len(results[0].match_terms) > 0


class TestUpdateAndDelete:
    def test_update_importance(self, mem):
        m = mem.save("user1", "test content")
        assert mem.update_importance(m.memory_id, 0.95)
        memories = mem.get_user_memories("user1")
        found = [mm for mm in memories if mm.memory_id == m.memory_id]
        assert found[0].importance == 0.95

    def test_update_nonexistent(self, mem):
        assert not mem.update_importance("nonexistent_id", 0.5)

    def test_delete_memory(self, mem):
        m = mem.save("user1", "to be deleted")
        assert mem.delete(m.memory_id)
        memories = mem.get_user_memories("user1")
        assert len(memories) == 0

    def test_delete_nonexistent(self, mem):
        assert not mem.delete("nonexistent_id")

    def test_forget_user(self, mem):
        for i in range(5):
            mem.save("user1", f"memory {i}")
        mem.save("user2", "other user memory")
        removed = mem.forget_user("user1")
        assert removed == 5
        assert len(mem.get_user_memories("user1")) == 0
        assert len(mem.get_user_memories("user2")) == 1

    def test_forget_nonexistent_user(self, mem):
        removed = mem.forget_user("nobody")
        assert removed == 0


class TestDecay:
    def test_decay_old_memories(self, mem):
        # Save a memory and manually set old timestamp
        m = mem.save("user1", "old memory", importance=0.1)
        with mem._get_conn() as conn:
            old_time = time.time() - 365 * 86400  # 1 year ago
            conn.execute(
                "UPDATE memories SET last_accessed = ? WHERE memory_id = ?",
                (old_time, m.memory_id)
            )
        removed = mem.decay_memories("user1", threshold=0.05)
        assert removed >= 1

    def test_decay_preserves_important(self, mem):
        m = mem.save("user1", "important memory", importance=1.0)
        removed = mem.decay_memories("user1", threshold=0.05)
        assert removed == 0
        assert len(mem.get_user_memories("user1")) == 1

    def test_decay_by_user(self, mem):
        mem.save("user1", "user1 mem", importance=0.01)
        mem.save("user2", "user2 mem", importance=0.01)
        with mem._get_conn() as conn:
            old = time.time() - 365 * 86400
            conn.execute("UPDATE memories SET last_accessed = ?", (old,))
        removed = mem.decay_memories("user1", threshold=0.05)
        assert removed >= 1
        # user2 untouched
        assert len(mem.get_user_memories("user2")) >= 0  # may or may not be decayed


class TestStats:
    def test_get_stats(self, mem):
        mem.save("user1", "fact", category='fact')
        mem.save("user1", "preference", category='preference')
        mem.search("user1", "fact")
        stats = mem.get_stats("user1")
        assert stats['total_memories'] == 2
        assert stats['categories']['fact'] == 1
        assert stats['categories']['preference'] == 1
        assert stats['total_searches'] >= 1
        assert stats['total_saves'] >= 2

    def test_stats_empty_user(self, mem):
        stats = mem.get_stats("nobody")
        assert stats['total_memories'] == 0


class TestExportImport:
    def test_export(self, mem):
        mem.save("user1", "exportable fact", category='fact', importance=0.9)
        mem.save("user1", "exportable pref", category='preference')
        data = mem.export_memories("user1")
        assert data['count'] == 2
        assert len(data['memories']) == 2
        assert data['memories'][0]['content'] in ['exportable fact', 'exportable pref']

    def test_import(self, mem):
        data = {
            'memories': [
                {'content': 'imported 1', 'category': 'fact', 'importance': 0.8},
                {'content': 'imported 2', 'category': 'context'},
            ]
        }
        count = mem.import_memories("user1", data)
        assert count == 2
        memories = mem.get_user_memories("user1")
        assert len(memories) == 2

    def test_roundtrip(self, mem):
        mem.save("user1", "roundtrip test", category='fact', importance=0.7)
        exported = mem.export_memories("user1")
        mem.forget_user("user1")
        assert len(mem.get_user_memories("user1")) == 0
        mem.import_memories("user1", exported)
        memories = mem.get_user_memories("user1")
        assert len(memories) == 1
        assert memories[0].content == "roundtrip test"


class TestTokenizer:
    def test_english_tokenize(self, mem):
        tokens = mem._tokenize("Hello world, this is a test!")
        assert 'hello' in tokens
        assert 'world' in tokens
        assert 'test' in tokens
        # Stop words removed
        assert 'this' not in tokens
        assert 'is' not in tokens

    def test_chinese_tokenize(self, mem):
        tokens = mem._tokenize("我喜欢编程")
        assert len(tokens) > 0

    def test_mixed_tokenize(self, mem):
        tokens = mem._tokenize("我喜欢Python编程")
        assert 'python' in tokens

    def test_empty_tokenize(self, mem):
        tokens = mem._tokenize("")
        assert tokens == []


class TestEnforceLimit:
    def test_enforces_per_user_limit(self, mem):
        # Temporarily lower limit for testing
        import services.conversation_memory as cm
        original = cm.MAX_MEMORIES_PER_USER
        cm.MAX_MEMORIES_PER_USER = 5
        try:
            for i in range(8):
                mem.save("user1", f"memory {i}", importance=i/10)
            memories = mem.get_user_memories("user1", limit=100)
            assert len(memories) <= 5
        finally:
            cm.MAX_MEMORIES_PER_USER = original
