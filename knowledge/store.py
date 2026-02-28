"""
知识库存储 - 基于TF-IDF的轻量级RAG检索
不依赖外部向量数据库，纯Python实现
"""

import os
import json
import math
import re
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class Document:
    """文档块"""
    id: str
    content: str
    metadata: Dict = field(default_factory=dict)
    source: str = ""


class KnowledgeStore:
    """
    知识库存储和检索
    - TF-IDF向量检索（无外部依赖）
    - 支持文档导入、删除
    - 持久化存储
    """

    def __init__(
        self,
        store_dir: str = "knowledge_store",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        top_k: int = 3,
    ):
        self.store_dir = store_dir
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k

        self.documents: Dict[str, Document] = {}
        self._idf_cache: Dict[str, float] = {}
        self._dirty = True

        os.makedirs(store_dir, exist_ok=True)
        self._load()

    def _tokenize(self, text: str) -> List[str]:
        """简单分词（支持中英文）"""
        # 英文按空格和标点分词
        text = text.lower()
        # 中文按字分词 + 英文按词分词
        tokens = []
        current_word = []
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                if current_word:
                    tokens.append(''.join(current_word))
                    current_word = []
                tokens.append(char)
            elif char.isalnum():
                current_word.append(char)
            else:
                if current_word:
                    tokens.append(''.join(current_word))
                    current_word = []
        if current_word:
            tokens.append(''.join(current_word))
        return [t for t in tokens if len(t) > 0]

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """计算词频"""
        counter = Counter(tokens)
        total = len(tokens)
        if total == 0:
            return {}
        return {word: count / total for word, count in counter.items()}

    def _compute_idf(self) -> Dict[str, float]:
        """计算逆文档频率"""
        if not self._dirty and self._idf_cache:
            return self._idf_cache

        n_docs = len(self.documents)
        if n_docs == 0:
            return {}

        doc_freq = defaultdict(int)
        for doc in self.documents.values():
            tokens = set(self._tokenize(doc.content))
            for token in tokens:
                doc_freq[token] += 1

        self._idf_cache = {
            word: math.log((n_docs + 1) / (freq + 1)) + 1
            for word, freq in doc_freq.items()
        }
        self._dirty = False
        return self._idf_cache

    def _tfidf_vector(
        self, text: str
    ) -> Dict[str, float]:
        """计算TF-IDF向量"""
        tokens = self._tokenize(text)
        tf = self._compute_tf(tokens)
        idf = self._compute_idf()

        return {
            word: tf_val * idf.get(word, 1.0)
            for word, tf_val in tf.items()
        }

    def _cosine_similarity(
        self, vec1: Dict[str, float], vec2: Dict[str, float]
    ) -> float:
        """余弦相似度"""
        common = set(vec1.keys()) & set(vec2.keys())
        if not common:
            return 0.0

        dot = sum(vec1[w] * vec2[w] for w in common)
        norm1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
        norm2 = math.sqrt(sum(v ** 2 for v in vec2.values()))

        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def add_document(
        self,
        content: str,
        source: str = "",
        metadata: Optional[Dict] = None,
        doc_id: Optional[str] = None,
    ) -> List[str]:
        """
        添加文档（自动分块）
        Returns: 生成的文档块ID列表
        """
        chunks = self._chunk_text(content)
        ids = []
        for i, chunk in enumerate(chunks):
            chunk_id = doc_id or f"doc_{len(self.documents)}"
            if len(chunks) > 1:
                chunk_id = f"{chunk_id}_chunk{i}"

            doc = Document(
                id=chunk_id,
                content=chunk,
                metadata=metadata or {},
                source=source,
            )
            self.documents[chunk_id] = doc
            ids.append(chunk_id)

        self._dirty = True
        self._save()
        return ids

    def _chunk_text(self, text: str) -> List[str]:
        """文本分块"""
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size

            # 尝试在句子边界分割
            if end < len(text):
                for sep in ['。', '！', '？', '\n', '.', '!', '?']:
                    last_sep = text[start:end].rfind(sep)
                    if last_sep > self.chunk_size // 2:
                        end = start + last_sep + 1
                        break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end - self.chunk_overlap

        return chunks

    def search(
        self, query: str, top_k: Optional[int] = None
    ) -> List[Tuple[Document, float]]:
        """
        检索最相关的文档块

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            [(Document, score), ...] 按相似度降序
        """
        if not self.documents:
            return []

        k = top_k or self.top_k
        query_vec = self._tfidf_vector(query)

        results = []
        for doc in self.documents.values():
            doc_vec = self._tfidf_vector(doc.content)
            score = self._cosine_similarity(query_vec, doc_vec)
            if score > 0:
                results.append((doc, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:k]

    def search_text(
        self, query: str, top_k: Optional[int] = None
    ) -> str:
        """检索并返回拼接文本（直接可用于注入上下文）"""
        results = self.search(query, top_k)
        if not results:
            return ""

        parts = []
        for doc, score in results:
            source_info = f" (来源: {doc.source})" if doc.source else ""
            parts.append(f"{doc.content}{source_info}")

        return "\n\n---\n\n".join(parts)

    def remove_document(self, doc_id: str) -> bool:
        """删除文档"""
        # 删除精确匹配和chunk匹配
        to_remove = [
            k for k in self.documents
            if k == doc_id or k.startswith(f"{doc_id}_chunk")
        ]
        for k in to_remove:
            del self.documents[k]

        if to_remove:
            self._dirty = True
            self._save()
            return True
        return False

    def clear(self) -> None:
        """清空知识库"""
        self.documents.clear()
        self._idf_cache.clear()
        self._dirty = True
        self._save()

    def get_stats(self) -> dict:
        """获取知识库统计"""
        return {
            "total_chunks": len(self.documents),
            "sources": list(
                set(d.source for d in self.documents.values() if d.source)
            ),
            "total_chars": sum(
                len(d.content) for d in self.documents.values()
            ),
        }

    def _save(self) -> None:
        """持久化保存"""
        data = {}
        for doc_id, doc in self.documents.items():
            data[doc_id] = {
                "content": doc.content,
                "metadata": doc.metadata,
                "source": doc.source,
            }
        path = os.path.join(self.store_dir, "documents.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        """从磁盘加载"""
        path = os.path.join(self.store_dir, "documents.json")
        if not os.path.exists(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for doc_id, info in data.items():
                self.documents[doc_id] = Document(
                    id=doc_id,
                    content=info["content"],
                    metadata=info.get("metadata", {}),
                    source=info.get("source", ""),
                )
            self._dirty = True
        except (json.JSONDecodeError, KeyError):
            pass
