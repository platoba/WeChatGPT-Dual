"""Tests for KnowledgeStore (TF-IDF RAG)"""

import os
import json
import pytest
from knowledge.store import KnowledgeStore, Document


class TestKnowledgeStoreBasic:
    def test_init(self, knowledge_store):
        assert knowledge_store.chunk_size == 100
        assert knowledge_store.top_k == 3
        stats = knowledge_store.get_stats()
        assert stats["total_chunks"] == 0

    def test_add_document(self, knowledge_store):
        ids = knowledge_store.add_document(
            "Python is a great programming language.",
            source="test.md",
        )
        assert len(ids) == 1
        assert knowledge_store.get_stats()["total_chunks"] == 1

    def test_add_long_document_chunks(self, knowledge_store):
        long_text = "Python programming. " * 20  # > 100 chars
        ids = knowledge_store.add_document(long_text, source="long.md")
        assert len(ids) > 1

    def test_add_with_custom_id(self, knowledge_store):
        ids = knowledge_store.add_document("test", doc_id="custom_doc")
        assert ids[0] == "custom_doc"


class TestKnowledgeStoreSearch:
    def test_search_empty(self, knowledge_store):
        results = knowledge_store.search("anything")
        assert results == []

    def test_search_finds_relevant(self, knowledge_store):
        knowledge_store.add_document("Python是一门编程语言", source="py.md")
        knowledge_store.add_document("Django是Python的Web框架", source="django.md")
        knowledge_store.add_document("美食烹饪指南", source="food.md")

        results = knowledge_store.search("Python编程")
        assert len(results) > 0
        # Python-related docs should score higher
        top_doc = results[0][0]
        assert "Python" in top_doc.content or "编程" in top_doc.content

    def test_search_text(self, knowledge_store):
        knowledge_store.add_document("AI人工智能技术", source="ai.md")
        text = knowledge_store.search_text("人工智能")
        assert "AI" in text or "人工智能" in text

    def test_search_text_empty(self, knowledge_store):
        text = knowledge_store.search_text("nothing")
        assert text == ""

    def test_search_top_k(self, knowledge_store):
        for i in range(10):
            knowledge_store.add_document(f"Document about topic {i}", source=f"doc{i}.md")
        results = knowledge_store.search("topic", top_k=2)
        assert len(results) <= 2


class TestKnowledgeStoreRemove:
    def test_remove_document(self, knowledge_store):
        knowledge_store.add_document("test content", doc_id="to_remove")
        assert knowledge_store.get_stats()["total_chunks"] == 1
        result = knowledge_store.remove_document("to_remove")
        assert result is True
        assert knowledge_store.get_stats()["total_chunks"] == 0

    def test_remove_nonexistent(self, knowledge_store):
        result = knowledge_store.remove_document("nonexistent")
        assert result is False

    def test_remove_chunked_document(self, knowledge_store):
        long_text = "Content " * 50
        knowledge_store.add_document(long_text, doc_id="chunked")
        initial = knowledge_store.get_stats()["total_chunks"]
        assert initial > 1
        knowledge_store.remove_document("chunked")
        assert knowledge_store.get_stats()["total_chunks"] == 0

    def test_clear(self, knowledge_store):
        knowledge_store.add_document("doc1")
        knowledge_store.add_document("doc2")
        knowledge_store.clear()
        assert knowledge_store.get_stats()["total_chunks"] == 0


class TestKnowledgeStorePersistence:
    def test_save_and_load(self, tmp_path):
        store_dir = str(tmp_path / "persist_kb")
        store1 = KnowledgeStore(store_dir=store_dir)
        store1.add_document("Persistent data", source="test.md")

        # Create new instance from same dir
        store2 = KnowledgeStore(store_dir=store_dir)
        assert store2.get_stats()["total_chunks"] == 1
        results = store2.search("Persistent")
        assert len(results) == 1


class TestKnowledgeStoreTokenizer:
    def test_chinese_tokenization(self, knowledge_store):
        knowledge_store.add_document("机器学习深度学习自然语言处理")
        results = knowledge_store.search("深度学习")
        assert len(results) > 0

    def test_english_tokenization(self, knowledge_store):
        knowledge_store.add_document("machine learning deep learning NLP")
        results = knowledge_store.search("deep learning")
        assert len(results) > 0

    def test_mixed_language(self, knowledge_store):
        knowledge_store.add_document("Python机器学习框架TensorFlow")
        results = knowledge_store.search("Python框架")
        assert len(results) > 0


class TestKnowledgeStoreStats:
    def test_stats(self, knowledge_store):
        knowledge_store.add_document("test", source="a.md")
        knowledge_store.add_document("test2", source="b.md")
        stats = knowledge_store.get_stats()
        assert stats["total_chunks"] == 2
        assert "a.md" in stats["sources"]
        assert "b.md" in stats["sources"]
        assert stats["total_chars"] > 0
