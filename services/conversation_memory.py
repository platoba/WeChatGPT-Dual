"""
Conversation Memory - Long-term semantic memory with vector search (RAG-lite)

Features:
- Store conversation snippets with embeddings (TF-IDF based, no external deps)
- Semantic search across past conversations
- Per-user memory isolation
- Memory importance scoring (recency + frequency + explicit saves)
- Auto-summarize old memories to save space
- Memory categories (facts, preferences, instructions, context)
- Forgetting curve: auto-decay unused memories
- Export/import memory per user
"""

import json
import math
import re
import time
import sqlite3
import hashlib
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import Counter
from contextlib import contextmanager

logger = logging.getLogger(__name__)

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'context',
    importance REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    last_accessed REAL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata TEXT DEFAULT '{}',
    tokens TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);

CREATE TABLE IF NOT EXISTS memory_terms (
    term TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    tf_idf REAL DEFAULT 0.0,
    PRIMARY KEY (term, memory_id),
    FOREIGN KEY (memory_id) REFERENCES memories(memory_id)
);
CREATE INDEX IF NOT EXISTS idx_terms_term ON memory_terms(term);

CREATE TABLE IF NOT EXISTS memory_stats (
    user_id TEXT PRIMARY KEY,
    total_memories INTEGER DEFAULT 0,
    total_searches INTEGER DEFAULT 0,
    total_saves INTEGER DEFAULT 0,
    last_search REAL DEFAULT 0,
    last_save REAL DEFAULT 0
);
"""

CATEGORIES = ['fact', 'preference', 'instruction', 'context', 'summary', 'important']
STOP_WORDS = set([
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'dare', 'ought',
    'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
    'as', 'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'between', 'out', 'off', 'over', 'under', 'again', 'further', 'then',
    'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'each',
    'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such', 'no',
    'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very',
    'just', 'because', 'but', 'and', 'or', 'if', 'while', 'this', 'that',
    'these', 'those', 'it', 'its', 'i', 'me', 'my', 'we', 'our', 'you',
    'your', 'he', 'him', 'his', 'she', 'her', 'they', 'them', 'their',
    '的', '了', '在', '是', '我', '你', '他', '她', '它', '们', '这', '那',
    '有', '和', '与', '就', '不', '也', '都', '而', '及', '但', '或', '把',
    '被', '让', '给', '从', '到', '对', '向', '于', '以', '为', '因', '如',
])

DECAY_HALF_LIFE = 7 * 86400  # 7 days
MAX_MEMORIES_PER_USER = 1000
SIMILARITY_THRESHOLD = 0.15


@dataclass
class Memory:
    memory_id: str
    user_id: str
    content: str
    category: str = 'context'
    importance: float = 0.5
    access_count: int = 0
    last_accessed: float = 0
    created_at: float = 0
    updated_at: float = 0
    metadata: Dict = field(default_factory=dict)
    tokens: List[str] = field(default_factory=list)
    relevance_score: float = 0.0


@dataclass
class SearchResult:
    memory: Memory
    score: float
    match_terms: List[str]


class ConversationMemory:
    """Long-term memory store with TF-IDF based semantic search."""

    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(MEMORY_SCHEMA)

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into meaningful terms (supports EN + CN).

        For Chinese text, generates n-grams (bigrams through 4-grams) to enable
        substring matching without a dictionary-based segmenter.
        """
        text = text.lower()
        # Split on non-alphanumeric (keep Chinese chars)
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', text)
        # For Chinese, generate n-grams for better search recall
        expanded = []
        for t in tokens:
            if re.match(r'^[\u4e00-\u9fff]+$', t):
                # Pure Chinese: generate n-grams (2,3,4) for search recall
                expanded.extend(self._chinese_ngrams(t))
            elif re.search(r'[\u4e00-\u9fff]', t):
                # Mixed Chinese+ASCII: split into Chinese and non-Chinese parts
                parts = re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+', t)
                for p in parts:
                    if re.match(r'^[\u4e00-\u9fff]+$', p):
                        expanded.extend(self._chinese_ngrams(p))
                    else:
                        expanded.append(p)
            else:
                expanded.append(t)
        # Remove stop words and very short tokens
        return [t for t in expanded if t not in STOP_WORDS and len(t) > 1]

    @staticmethod
    def _chinese_ngrams(text: str) -> List[str]:
        """Generate n-grams from Chinese text (bigrams through 4-grams + full string)."""
        results = []
        length = len(text)
        # Always include the full string if len >= 2
        if length >= 2:
            results.append(text)
        # Generate bigrams, trigrams, 4-grams (skip if same as full string)
        for n in range(2, min(5, length + 1)):
            for i in range(length - n + 1):
                gram = text[i:i + n]
                if gram != text:  # avoid duplicate of full string
                    results.append(gram)
        return results

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """Compute term frequency."""
        counts = Counter(tokens)
        total = len(tokens) if tokens else 1
        return {term: count / total for term, count in counts.items()}

    def _compute_idf(self, terms: List[str], conn) -> Dict[str, float]:
        """Compute inverse document frequency."""
        total_docs = conn.execute("SELECT COUNT(DISTINCT memory_id) FROM memories").fetchone()[0]
        if total_docs == 0:
            return {t: 1.0 for t in terms}
        idf = {}
        for term in terms:
            doc_count = conn.execute(
                "SELECT COUNT(DISTINCT memory_id) FROM memory_terms WHERE term = ?",
                (term,)
            ).fetchone()[0]
            idf[term] = math.log((total_docs + 1) / (doc_count + 1)) + 1
        return idf

    def _generate_id(self, content: str, user_id: str) -> str:
        h = hashlib.sha256(f"{user_id}:{content}:{time.time()}".encode()).hexdigest()[:16]
        return f"mem_{h}"

    def save(self, user_id: str, content: str, category: str = 'context',
             importance: float = 0.5, metadata: Optional[Dict] = None) -> Memory:
        """Save a memory snippet."""
        if category not in CATEGORIES:
            category = 'context'
        importance = max(0.0, min(1.0, importance))

        now = time.time()
        tokens = self._tokenize(content)
        memory_id = self._generate_id(content, user_id)

        memory = Memory(
            memory_id=memory_id,
            user_id=user_id,
            content=content,
            category=category,
            importance=importance,
            access_count=0,
            last_accessed=now,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
            tokens=tokens,
        )

        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO memories
                   (memory_id, user_id, content, category, importance,
                    access_count, last_accessed, created_at, updated_at, metadata, tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (memory.memory_id, memory.user_id, memory.content,
                 memory.category, memory.importance, 0, now, now, now,
                 json.dumps(memory.metadata), json.dumps(tokens))
            )

            # Compute TF-IDF and store terms
            tf = self._compute_tf(tokens)
            idf = self._compute_idf(list(tf.keys()), conn)

            for term, tf_val in tf.items():
                tf_idf = tf_val * idf.get(term, 1.0)
                conn.execute(
                    "INSERT OR REPLACE INTO memory_terms (term, memory_id, tf_idf) VALUES (?, ?, ?)",
                    (term, memory_id, tf_idf)
                )

            # Update stats
            conn.execute(
                """INSERT INTO memory_stats (user_id, total_memories, total_saves, last_save)
                   VALUES (?, 1, 1, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   total_memories = total_memories + 1,
                   total_saves = total_saves + 1,
                   last_save = ?""",
                (user_id, now, now)
            )

            # Enforce per-user limit
            self._enforce_limit(conn, user_id)

        logger.info(f"Saved memory {memory_id} for user {user_id}, category={category}")
        return memory

    def search(self, user_id: str, query: str, limit: int = 5,
               category: Optional[str] = None, min_importance: float = 0.0) -> List[SearchResult]:
        """Search memories by semantic similarity (TF-IDF cosine-like scoring)."""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        with self._get_conn() as conn:
            # Get matching memories
            placeholders = ','.join('?' * len(query_tokens))
            query_parts = [
                f"""SELECT m.*, SUM(mt.tf_idf) as match_score,
                    GROUP_CONCAT(mt.term) as matched_terms
                    FROM memories m
                    JOIN memory_terms mt ON m.memory_id = mt.memory_id
                    WHERE mt.term IN ({placeholders})
                    AND m.user_id = ?"""
            ]
            params = list(query_tokens) + [user_id]

            if category:
                query_parts.append("AND m.category = ?")
                params.append(category)
            if min_importance > 0:
                query_parts.append("AND m.importance >= ?")
                params.append(min_importance)

            query_parts.append("GROUP BY m.memory_id")
            query_parts.append("ORDER BY match_score DESC")
            query_parts.append(f"LIMIT {limit * 2}")  # fetch extra for re-ranking

            sql = ' '.join(query_parts)
            rows = conn.execute(sql, params).fetchall()

            results = []
            now = time.time()
            for row in rows:
                # Compute final score: TF-IDF match * importance * recency decay
                match_score = row['match_score']
                importance = row['importance']
                age = now - row['last_accessed']
                decay = math.exp(-0.693 * age / DECAY_HALF_LIFE)  # half-life decay

                final_score = match_score * (0.5 + 0.5 * importance) * (0.3 + 0.7 * decay)

                if final_score < SIMILARITY_THRESHOLD:
                    continue

                memory = Memory(
                    memory_id=row['memory_id'],
                    user_id=row['user_id'],
                    content=row['content'],
                    category=row['category'],
                    importance=row['importance'],
                    access_count=row['access_count'],
                    last_accessed=row['last_accessed'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                    tokens=json.loads(row['tokens']) if row['tokens'] else [],
                    relevance_score=final_score,
                )

                matched = row['matched_terms'].split(',') if row['matched_terms'] else []
                results.append(SearchResult(memory=memory, score=final_score, match_terms=matched))

            # Sort by final score and limit
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[:limit]

            # Update access counts
            for r in results:
                conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE memory_id = ?",
                    (now, r.memory.memory_id)
                )

            # Update search stats
            conn.execute(
                """INSERT INTO memory_stats (user_id, total_searches, last_search)
                   VALUES (?, 1, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   total_searches = total_searches + 1,
                   last_search = ?""",
                (user_id, now, now)
            )

        return results

    def get_user_memories(self, user_id: str, category: Optional[str] = None,
                          limit: int = 50, offset: int = 0) -> List[Memory]:
        """List memories for a user."""
        with self._get_conn() as conn:
            sql = "SELECT * FROM memories WHERE user_id = ?"
            params: list = [user_id]
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += " ORDER BY importance DESC, updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_memory(row) for row in rows]

    def update_importance(self, memory_id: str, importance: float) -> bool:
        """Update importance score of a memory."""
        importance = max(0.0, min(1.0, importance))
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE memories SET importance = ?, updated_at = ? WHERE memory_id = ?",
                (importance, time.time(), memory_id)
            )
            return result.rowcount > 0

    def delete(self, memory_id: str) -> bool:
        """Delete a specific memory."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM memory_terms WHERE memory_id = ?", (memory_id,))
            result = conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            return result.rowcount > 0

    def forget_user(self, user_id: str) -> int:
        """Delete all memories for a user (GDPR compliance)."""
        with self._get_conn() as conn:
            memory_ids = [row[0] for row in
                          conn.execute("SELECT memory_id FROM memories WHERE user_id = ?",
                                       (user_id,)).fetchall()]
            if memory_ids:
                placeholders = ','.join('?' * len(memory_ids))
                conn.execute(f"DELETE FROM memory_terms WHERE memory_id IN ({placeholders})", memory_ids)
            result = conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memory_stats WHERE user_id = ?", (user_id,))
            return result.rowcount

    def decay_memories(self, user_id: Optional[str] = None, threshold: float = 0.05) -> int:
        """Apply forgetting curve: remove memories below threshold after decay."""
        now = time.time()
        removed = 0
        with self._get_conn() as conn:
            sql = "SELECT memory_id, importance, last_accessed FROM memories"
            params: list = []
            if user_id:
                sql += " WHERE user_id = ?"
                params.append(user_id)

            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                age = now - row['last_accessed']
                decay = math.exp(-0.693 * age / DECAY_HALF_LIFE)
                effective_importance = row['importance'] * decay
                if effective_importance < threshold:
                    conn.execute("DELETE FROM memory_terms WHERE memory_id = ?",
                                 (row['memory_id'],))
                    conn.execute("DELETE FROM memories WHERE memory_id = ?",
                                 (row['memory_id'],))
                    removed += 1

        logger.info(f"Decayed {removed} memories below threshold {threshold}")
        return removed

    def get_stats(self, user_id: str) -> Dict[str, Any]:
        """Get memory statistics for a user."""
        with self._get_conn() as conn:
            stats_row = conn.execute(
                "SELECT * FROM memory_stats WHERE user_id = ?", (user_id,)
            ).fetchone()

            category_counts = {}
            rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories WHERE user_id = ? GROUP BY category",
                (user_id,)
            ).fetchall()
            for row in rows:
                category_counts[row['category']] = row['cnt']

            total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)
            ).fetchone()[0]

            return {
                'user_id': user_id,
                'total_memories': total,
                'categories': category_counts,
                'total_searches': stats_row['total_searches'] if stats_row else 0,
                'total_saves': stats_row['total_saves'] if stats_row else 0,
                'last_search': stats_row['last_search'] if stats_row else 0,
                'last_save': stats_row['last_save'] if stats_row else 0,
            }

    def export_memories(self, user_id: str) -> Dict[str, Any]:
        """Export all memories for a user as JSON-serializable dict."""
        memories = self.get_user_memories(user_id, limit=MAX_MEMORIES_PER_USER)
        return {
            'user_id': user_id,
            'exported_at': time.time(),
            'count': len(memories),
            'memories': [
                {
                    'content': m.content,
                    'category': m.category,
                    'importance': m.importance,
                    'created_at': m.created_at,
                    'metadata': m.metadata,
                }
                for m in memories
            ]
        }

    def import_memories(self, user_id: str, data: Dict[str, Any]) -> int:
        """Import memories from exported data."""
        imported = 0
        for item in data.get('memories', []):
            self.save(
                user_id=user_id,
                content=item['content'],
                category=item.get('category', 'context'),
                importance=item.get('importance', 0.5),
                metadata=item.get('metadata'),
            )
            imported += 1
        return imported

    def _enforce_limit(self, conn, user_id: str):
        """Remove oldest/least important memories if over limit."""
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        if count > MAX_MEMORIES_PER_USER:
            excess = count - MAX_MEMORIES_PER_USER
            to_delete = conn.execute(
                """SELECT memory_id FROM memories WHERE user_id = ?
                   ORDER BY importance ASC, last_accessed ASC LIMIT ?""",
                (user_id, excess)
            ).fetchall()

            for row in to_delete:
                conn.execute("DELETE FROM memory_terms WHERE memory_id = ?", (row[0],))
                conn.execute("DELETE FROM memories WHERE memory_id = ?", (row[0],))

    def _row_to_memory(self, row) -> Memory:
        return Memory(
            memory_id=row['memory_id'],
            user_id=row['user_id'],
            content=row['content'],
            category=row['category'],
            importance=row['importance'],
            access_count=row['access_count'],
            last_accessed=row['last_accessed'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
            tokens=json.loads(row['tokens']) if row['tokens'] else [],
        )
