"""
对话搜索服务 - SQLite FTS5全文检索

Features:
- FTS5 full-text search index on conversation messages
- Search by keyword, user, date range, channel
- Highlighted search results with snippet extraction
- Automatic index maintenance (insert/rebuild)
"""

import re
import time
import sqlite3
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from contextlib import contextmanager

logger = logging.getLogger(__name__)

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    user_id,
    role,
    content,
    channel,
    content='messages',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS search_index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Triggers to auto-sync FTS with messages table
FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, user_id, role, content, channel)
    VALUES (new.id, new.user_id, new.role, new.content, new.channel);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, user_id, role, content, channel)
    VALUES ('delete', old.id, old.user_id, old.role, old.content, old.channel);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, user_id, role, content, channel)
    VALUES ('delete', old.id, old.user_id, old.role, old.content, old.channel);
    INSERT INTO messages_fts(rowid, user_id, role, content, channel)
    VALUES (new.id, new.user_id, new.role, new.content, new.channel);
END;
"""


@dataclass
class SearchResult:
    """A single search result"""

    message_id: int
    user_id: str
    role: str
    content: str
    channel: str
    created_at: float
    snippet: str
    rank: float

    def to_dict(self) -> Dict:
        return {
            "message_id": self.message_id,
            "user_id": self.user_id,
            "role": self.role,
            "content": self.content,
            "channel": self.channel,
            "created_at": self.created_at,
            "snippet": self.snippet,
            "rank": self.rank,
        }


@dataclass
class SearchResponse:
    """Search response with pagination"""

    query: str
    total: int
    results: List[SearchResult]
    took_ms: float
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total

    def to_dict(self) -> Dict:
        return {
            "query": self.query,
            "total": self.total,
            "results": [r.to_dict() for r in self.results],
            "took_ms": round(self.took_ms, 2),
            "page": self.page,
            "page_size": self.page_size,
            "has_more": self.has_more,
        }


class ConversationSearch:
    """
    Full-text search service for conversation messages.

    Uses SQLite FTS5 for fast keyword search with ranking.
    Automatically syncs with the messages table via triggers.

    Usage:
        search = ConversationSearch("bot.db")
        search.ensure_index()
        results = search.search("python code", user_id="123")
    """

    def __init__(self, db_path: str = "wechatgpt.db"):
        self.db_path = db_path

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

    def ensure_index(self):
        """Create FTS index and triggers if not exists"""
        with self._conn() as conn:
            # Check if messages table exists
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
            ).fetchone()
            if not tables:
                logger.warning("Messages table not found, skipping FTS setup")
                return False

            conn.executescript(FTS_SCHEMA)
            try:
                conn.executescript(FTS_TRIGGERS)
            except sqlite3.OperationalError:
                # Triggers might already exist in a different form
                pass

            # Record setup time
            conn.execute(
                "INSERT OR REPLACE INTO search_index_meta (key, value) VALUES (?, ?)",
                ("last_setup", str(time.time())),
            )
            logger.info("FTS5 search index initialized")
            return True

    def rebuild_index(self):
        """Rebuild the FTS index from scratch"""
        with self._conn() as conn:
            # Clear and repopulate
            conn.execute("DELETE FROM messages_fts")
            conn.execute(
                """INSERT INTO messages_fts(rowid, user_id, role, content, channel)
                   SELECT id, user_id, role, content, channel FROM messages"""
            )
            conn.execute(
                "INSERT OR REPLACE INTO search_index_meta (key, value) VALUES (?, ?)",
                ("last_rebuild", str(time.time())),
            )
        logger.info("FTS index rebuilt successfully")

    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        channel: Optional[str] = None,
        role: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SearchResponse:
        """
        Search conversations by keyword.

        Args:
            query: Search query (FTS5 syntax supported)
            user_id: Filter by user
            channel: Filter by channel (telegram/wechat)
            role: Filter by role (user/assistant)
            since: Only messages after this timestamp
            until: Only messages before this timestamp
            page: Page number (1-indexed)
            page_size: Results per page

        Returns:
            SearchResponse with ranked results
        """
        start = time.time()

        # Sanitize query for FTS5
        safe_query = self._sanitize_query(query)
        if not safe_query:
            return SearchResponse(
                query=query, total=0, results=[], took_ms=0, page=page, page_size=page_size
            )

        offset = (page - 1) * page_size

        # Build WHERE clause for filtering
        filters = []
        params = []

        if user_id:
            filters.append("m.user_id = ?")
            params.append(user_id)
        if channel:
            filters.append("m.channel = ?")
            params.append(channel)
        if role:
            filters.append("m.role = ?")
            params.append(role)
        if since:
            filters.append("m.created_at >= ?")
            params.append(since)
        if until:
            filters.append("m.created_at <= ?")
            params.append(until)

        where_clause = " AND ".join(filters) if filters else "1=1"

        with self._conn() as conn:
            # Count total
            count_sql = f"""
                SELECT COUNT(*) FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                WHERE messages_fts MATCH ?
                AND {where_clause}
            """
            total = conn.execute(count_sql, [safe_query] + params).fetchone()[0]

            # Fetch results with rank
            search_sql = f"""
                SELECT
                    m.id as message_id,
                    m.user_id,
                    m.role,
                    m.content,
                    m.channel,
                    m.created_at,
                    snippet(messages_fts, 2, '<b>', '</b>', '...', 32) as snippet,
                    rank
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                WHERE messages_fts MATCH ?
                AND {where_clause}
                ORDER BY rank
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(
                search_sql, [safe_query] + params + [page_size, offset]
            ).fetchall()

        results = [
            SearchResult(
                message_id=row["message_id"],
                user_id=row["user_id"],
                role=row["role"],
                content=row["content"],
                channel=row["channel"],
                created_at=row["created_at"],
                snippet=row["snippet"] or "",
                rank=row["rank"],
            )
            for row in rows
        ]

        took_ms = (time.time() - start) * 1000

        return SearchResponse(
            query=query,
            total=total,
            results=results,
            took_ms=took_ms,
            page=page,
            page_size=page_size,
        )

    def search_user_history(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> List[SearchResult]:
        """Quick search within a single user's history"""
        response = self.search(query, user_id=user_id, page_size=limit)
        return response.results

    def get_popular_terms(self, limit: int = 20) -> List[Tuple[str, int]]:
        """
        Get most frequent terms across all messages.
        Returns list of (term, count) tuples.
        """
        with self._conn() as conn:
            # Use FTS5 vocab table
            try:
                rows = conn.execute(
                    """SELECT term, doc as doc_count
                       FROM messages_fts_vocab
                       WHERE col = 'content'
                       ORDER BY doc DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
                return [(row["term"], row["doc_count"]) for row in rows]
            except sqlite3.OperationalError:
                # vocab table might not be available
                return []

    def get_index_stats(self) -> Dict:
        """Get FTS index statistics"""
        with self._conn() as conn:
            try:
                total_docs = conn.execute(
                    "SELECT COUNT(*) FROM messages_fts"
                ).fetchone()[0]

                meta = {}
                for row in conn.execute("SELECT * FROM search_index_meta").fetchall():
                    meta[row["key"]] = row["value"]

                return {
                    "total_indexed": total_docs,
                    "last_setup": meta.get("last_setup"),
                    "last_rebuild": meta.get("last_rebuild"),
                }
            except sqlite3.OperationalError:
                return {"error": "FTS index not initialized"}

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """
        Sanitize a user query for FTS5.
        Remove special FTS operators that could cause errors.
        """
        if not query or not query.strip():
            return ""

        # Remove FTS5 special characters that are not part of words
        # Keep basic operators: AND, OR, NOT, quotes for phrases
        sanitized = query.strip()

        # Escape special chars that break FTS5
        sanitized = re.sub(r'[^\w\s"\'*\-]', " ", sanitized, flags=re.UNICODE)

        # Collapse whitespace
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        # If it looks like a simple word search, wrap in quotes for exact phrase
        if " " not in sanitized and not any(c in sanitized for c in '"*'):
            return sanitized

        return sanitized
