"""
Response Cache Service - 缓存AI响应以节省API成本

Features:
- SQLite-backed response cache with TTL
- Simple word-overlap similarity matching
- Cache hit/miss statistics tracking
- Per-user and global cache
- Auto-eviction of expired entries
"""

import time
import math
import sqlite3
import hashlib
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from contextlib import contextmanager
from collections import Counter

logger = logging.getLogger(__name__)

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS response_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT NOT NULL,
    query_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    user_id TEXT DEFAULT '',
    engine TEXT DEFAULT '',
    tokens_saved INTEGER DEFAULT 0,
    hit_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_hit_at REAL
);

CREATE INDEX IF NOT EXISTS idx_cache_hash ON response_cache(query_hash);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON response_cache(expires_at);

CREATE TABLE IF NOT EXISTS cache_stats (
    date TEXT NOT NULL,
    hits INTEGER DEFAULT 0,
    misses INTEGER DEFAULT 0,
    evictions INTEGER DEFAULT 0,
    tokens_saved INTEGER DEFAULT 0,
    PRIMARY KEY (date)
);
"""


@dataclass
class CacheEntry:
    """Cached response entry"""
    id: int
    query_hash: str
    query_text: str
    response_text: str
    user_id: str
    engine: str
    tokens_saved: int
    hit_count: int
    created_at: float
    expires_at: float
    last_hit_at: Optional[float]

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class ResponseCache:
    """
    SQLite-backed response cache with TTL and similarity matching.

    Usage:
        cache = ResponseCache("cache.db", ttl=3600)
        hit = cache.get("What is Python?")
        if not hit:
            response = call_ai(...)
            cache.put("What is Python?", response, tokens=150)
    """

    def __init__(
        self,
        db_path: str = "cache.db",
        ttl: int = 3600,
        max_entries: int = 10000,
        similarity_threshold: float = 0.85,
    ):
        self.db_path = db_path
        self.ttl = ttl
        self.max_entries = max_entries
        self.similarity_threshold = similarity_threshold
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(CACHE_SCHEMA)

    @staticmethod
    def _hash_query(query: str) -> str:
        """Create hash from normalized query"""
        normalized = query.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _tokenize(text: str) -> Counter:
        """Simple word tokenization for similarity"""
        words = text.strip().lower().split()
        return Counter(words)

    @staticmethod
    def _cosine_similarity(a: Counter, b: Counter) -> float:
        """Cosine similarity between two word count vectors"""
        if not a or not b:
            return 0.0
        common = set(a.keys()) & set(b.keys())
        dot = sum(a[w] * b[w] for w in common)
        mag_a = math.sqrt(sum(v * v for v in a.values()))
        mag_b = math.sqrt(sum(v * v for v in b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def get(self, query: str, user_id: str = "") -> Optional[str]:
        """
        Look up cached response for query.

        Args:
            query: The user's question/prompt
            user_id: Optional user filter

        Returns:
            Cached response text if found and valid, else None
        """
        query_hash = self._hash_query(query)
        now = time.time()

        with self._conn() as conn:
            # Exact hash match first
            rows = conn.execute(
                """SELECT * FROM response_cache
                   WHERE query_hash = ? AND expires_at > ?
                   ORDER BY hit_count DESC LIMIT 5""",
                (query_hash, now),
            ).fetchall()

            if rows:
                entry = rows[0]
                # Update hit count
                conn.execute(
                    "UPDATE response_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                    (now, entry["id"]),
                )
                self._record_stats(conn, hits=1, tokens_saved=entry["tokens_saved"])
                logger.debug(f"Cache hit (exact) for query: {query[:50]}...")
                return entry["response_text"]

            # Similarity match fallback
            candidates = conn.execute(
                """SELECT * FROM response_cache
                   WHERE expires_at > ?
                   ORDER BY created_at DESC LIMIT 100""",
                (now,),
            ).fetchall()

            query_tokens = self._tokenize(query)
            best_match = None
            best_sim = 0.0

            for row in candidates:
                sim = self._cosine_similarity(query_tokens, self._tokenize(row["query_text"]))
                if sim > best_sim and sim >= self.similarity_threshold:
                    best_sim = sim
                    best_match = row

            if best_match:
                conn.execute(
                    "UPDATE response_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                    (now, best_match["id"]),
                )
                self._record_stats(conn, hits=1, tokens_saved=best_match["tokens_saved"])
                logger.debug(f"Cache hit (sim={best_sim:.2f}) for query: {query[:50]}...")
                return best_match["response_text"]

            # Miss
            self._record_stats(conn, misses=1)
            return None

    def put(
        self,
        query: str,
        response: str,
        user_id: str = "",
        engine: str = "",
        tokens_used: int = 0,
        ttl: Optional[int] = None,
    ):
        """
        Store a response in cache.

        Args:
            query: The user's question/prompt
            response: The AI response
            user_id: User who triggered the query
            engine: Engine that generated the response
            tokens_used: Tokens consumed (for savings tracking)
            ttl: Custom TTL override (seconds)
        """
        now = time.time()
        expire = now + (ttl or self.ttl)
        query_hash = self._hash_query(query)

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO response_cache
                   (query_hash, query_text, response_text, user_id, engine, tokens_saved, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (query_hash, query, response, user_id, engine, tokens_used, now, expire),
            )

            # Evict if over capacity
            count = conn.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0]
            if count > self.max_entries:
                self._evict(conn, count - self.max_entries)

    def invalidate(self, query: str):
        """Remove cached response for a query"""
        query_hash = self._hash_query(query)
        with self._conn() as conn:
            conn.execute("DELETE FROM response_cache WHERE query_hash = ?", (query_hash,))

    def invalidate_user(self, user_id: str):
        """Remove all cached responses for a user"""
        with self._conn() as conn:
            conn.execute("DELETE FROM response_cache WHERE user_id = ?", (user_id,))

    def clear_expired(self) -> int:
        """Remove all expired entries"""
        now = time.time()
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM response_cache WHERE expires_at <= ?", (now,))
            deleted = cursor.rowcount
            if deleted > 0:
                self._record_stats(conn, evictions=deleted)
            return deleted

    def clear_all(self):
        """Clear entire cache"""
        with self._conn() as conn:
            conn.execute("DELETE FROM response_cache")

    def get_stats(self) -> Dict:
        """Get cache statistics"""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM response_cache WHERE expires_at <= ?",
                (time.time(),),
            ).fetchone()[0]

            daily = conn.execute(
                """SELECT SUM(hits) as total_hits, SUM(misses) as total_misses,
                          SUM(evictions) as total_evictions, SUM(tokens_saved) as total_tokens_saved
                   FROM cache_stats"""
            ).fetchone()

            total_hits = daily["total_hits"] or 0
            total_misses = daily["total_misses"] or 0
            total_reqs = total_hits + total_misses

            return {
                "total_entries": total,
                "expired_entries": expired,
                "active_entries": total - expired,
                "total_hits": total_hits,
                "total_misses": total_misses,
                "hit_rate": round(total_hits / total_reqs, 4) if total_reqs > 0 else 0,
                "tokens_saved": daily["total_tokens_saved"] or 0,
                "evictions": daily["total_evictions"] or 0,
            }

    def _evict(self, conn, count: int):
        """Evict oldest/least-used entries"""
        conn.execute(
            """DELETE FROM response_cache WHERE id IN (
                   SELECT id FROM response_cache
                   ORDER BY hit_count ASC, created_at ASC
                   LIMIT ?
               )""",
            (count,),
        )
        self._record_stats(conn, evictions=count)

    def _record_stats(self, conn, hits: int = 0, misses: int = 0, evictions: int = 0, tokens_saved: int = 0):
        """Record daily statistics"""
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

        existing = conn.execute("SELECT * FROM cache_stats WHERE date = ?", (date,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE cache_stats
                   SET hits = hits + ?, misses = misses + ?,
                       evictions = evictions + ?, tokens_saved = tokens_saved + ?
                   WHERE date = ?""",
                (hits, misses, evictions, tokens_saved, date),
            )
        else:
            conn.execute(
                "INSERT INTO cache_stats (date, hits, misses, evictions, tokens_saved) VALUES (?, ?, ?, ?, ?)",
                (date, hits, misses, evictions, tokens_saved),
            )
