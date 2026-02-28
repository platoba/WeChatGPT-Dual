"""
对话线程服务 - 支持话题分支和线程管理

Features:
- Create conversation threads (topic branching)
- Thread-scoped context isolation
- Thread tagging and labeling
- Thread state management (active/paused/archived/closed)
- Thread summary generation
- Thread search and filtering
- Thread merge (combine related threads)
- Thread statistics per user/conversation
- SQLite persistence
"""

import json
import time
import sqlite3
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager
from enum import Enum

logger = logging.getLogger(__name__)

THREAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_threads (
    thread_id TEXT PRIMARY KEY,
    parent_thread_id TEXT DEFAULT '',
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT DEFAULT '',
    state TEXT DEFAULT 'active',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    message_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    closed_at REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_thread_conv ON conversation_threads(conversation_id);
CREATE INDEX IF NOT EXISTS idx_thread_user ON conversation_threads(user_id);
CREATE INDEX IF NOT EXISTS idx_thread_state ON conversation_threads(state);
CREATE INDEX IF NOT EXISTS idx_thread_parent ON conversation_threads(parent_thread_id);
CREATE INDEX IF NOT EXISTS idx_thread_updated ON conversation_threads(updated_at);

CREATE TABLE IF NOT EXISTS thread_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES conversation_threads(thread_id)
);

CREATE INDEX IF NOT EXISTS idx_tmsg_thread ON thread_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_tmsg_time ON thread_messages(created_at);

CREATE TABLE IF NOT EXISTS thread_context (
    thread_id TEXT PRIMARY KEY,
    system_prompt TEXT DEFAULT '',
    context_summary TEXT DEFAULT '',
    pinned_messages TEXT DEFAULT '[]',
    variables TEXT DEFAULT '{}',
    updated_at REAL NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES conversation_threads(thread_id)
);
"""


class ThreadState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"
    CLOSED = "closed"


@dataclass
class ThreadMessage:
    thread_id: str
    message_id: str
    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ThreadContext:
    thread_id: str
    system_prompt: str = ""
    context_summary: str = ""
    pinned_messages: List[str] = field(default_factory=list)
    variables: Dict[str, str] = field(default_factory=dict)
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationThread:
    thread_id: str
    parent_thread_id: str
    conversation_id: str
    user_id: str
    title: str
    topic: str
    state: str
    tags: List[str]
    metadata: Dict[str, Any]
    message_count: int
    created_at: float
    updated_at: float
    closed_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_active(self) -> bool:
        return self.state == ThreadState.ACTIVE

    def is_closed(self) -> bool:
        return self.state in (ThreadState.CLOSED, ThreadState.ARCHIVED)


class ConversationThreading:
    """Manage threaded conversations within chats."""

    def __init__(self, db_path: str = "threads.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
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
        with self._get_conn() as conn:
            conn.executescript(THREAD_SCHEMA)

    def _generate_id(self) -> str:
        import hashlib
        return hashlib.sha256(f"thread-{time.time()}-{id(self)}".encode()).hexdigest()[:16]

    def create_thread(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        topic: str = "",
        parent_thread_id: str = "",
        system_prompt: str = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationThread:
        """Create a new conversation thread."""
        now = time.time()
        thread = ConversationThread(
            thread_id=self._generate_id(),
            parent_thread_id=parent_thread_id,
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            topic=topic,
            state=ThreadState.ACTIVE,
            tags=tags or [],
            metadata=metadata or {},
            message_count=0,
            created_at=now,
            updated_at=now,
            closed_at=0,
        )
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO conversation_threads
                   (thread_id, parent_thread_id, conversation_id, user_id, title,
                    topic, state, tags, metadata, message_count, created_at, updated_at, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread.thread_id, thread.parent_thread_id,
                    thread.conversation_id, thread.user_id, thread.title,
                    thread.topic, thread.state,
                    json.dumps(thread.tags), json.dumps(thread.metadata),
                    0, now, now, 0,
                ),
            )
            # Create thread context
            conn.execute(
                """INSERT INTO thread_context (thread_id, system_prompt, updated_at)
                   VALUES (?, ?, ?)""",
                (thread.thread_id, system_prompt, now),
            )

        logger.info(f"Created thread '{title}' in conversation {conversation_id}")
        return thread

    def get_thread(self, thread_id: str) -> Optional[ConversationThread]:
        """Get a thread by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return self._row_to_thread(row) if row else None

    def add_message(
        self,
        thread_id: str,
        message_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ThreadMessage:
        """Add a message to a thread."""
        now = time.time()
        msg = ThreadMessage(
            thread_id=thread_id,
            message_id=message_id,
            role=role,
            content=content,
            metadata=metadata or {},
            created_at=now,
        )
        with self._get_conn() as conn:
            # Verify thread exists and is active
            thread = conn.execute(
                "SELECT state FROM conversation_threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if not thread:
                raise ValueError(f"Thread {thread_id} not found")
            if thread["state"] in ("closed", "archived"):
                raise ValueError(f"Thread {thread_id} is {thread['state']}")

            conn.execute(
                """INSERT INTO thread_messages
                   (thread_id, message_id, role, content, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    thread_id, message_id, role, content,
                    json.dumps(metadata or {}), now,
                ),
            )
            conn.execute(
                """UPDATE conversation_threads
                   SET message_count = message_count + 1, updated_at = ?
                   WHERE thread_id = ?""",
                (now, thread_id),
            )
        return msg

    def get_messages(
        self,
        thread_id: str,
        limit: int = 50,
        offset: int = 0,
        since: Optional[float] = None,
    ) -> List[ThreadMessage]:
        """Get messages from a thread."""
        query = "SELECT * FROM thread_messages WHERE thread_id = ?"
        params: list = [thread_id]
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            ThreadMessage(
                thread_id=r["thread_id"],
                message_id=r["message_id"],
                role=r["role"],
                content=r["content"],
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_thread_context(self, thread_id: str) -> Optional[ThreadContext]:
        """Get thread-specific context."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM thread_context WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if not row:
            return None
        return ThreadContext(
            thread_id=row["thread_id"],
            system_prompt=row["system_prompt"],
            context_summary=row["context_summary"],
            pinned_messages=json.loads(row["pinned_messages"]) if row["pinned_messages"] else [],
            variables=json.loads(row["variables"]) if row["variables"] else {},
            updated_at=row["updated_at"],
        )

    def update_context(
        self,
        thread_id: str,
        system_prompt: Optional[str] = None,
        context_summary: Optional[str] = None,
        pinned_messages: Optional[List[str]] = None,
        variables: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Update thread context."""
        updates = []
        params: list = []
        if system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(system_prompt)
        if context_summary is not None:
            updates.append("context_summary = ?")
            params.append(context_summary)
        if pinned_messages is not None:
            updates.append("pinned_messages = ?")
            params.append(json.dumps(pinned_messages))
        if variables is not None:
            updates.append("variables = ?")
            params.append(json.dumps(variables))
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(thread_id)
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE thread_context SET {', '.join(updates)} WHERE thread_id = ?",
                params,
            )
            return cursor.rowcount > 0

    def change_state(
        self,
        thread_id: str,
        new_state: str,
    ) -> bool:
        """Change thread state."""
        valid_states = [s.value for s in ThreadState]
        if new_state not in valid_states:
            raise ValueError(f"Invalid state: {new_state}. Valid: {valid_states}")
        now = time.time()
        closed_at = now if new_state in ("closed", "archived") else 0
        with self._get_conn() as conn:
            cursor = conn.execute(
                """UPDATE conversation_threads
                   SET state = ?, updated_at = ?, closed_at = ?
                   WHERE thread_id = ?""",
                (new_state, now, closed_at, thread_id),
            )
            return cursor.rowcount > 0

    def list_threads(
        self,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        state: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ConversationThread]:
        """List threads with filters."""
        where_parts = []
        params: list = []
        if conversation_id:
            where_parts.append("conversation_id = ?")
            params.append(conversation_id)
        if user_id:
            where_parts.append("user_id = ?")
            params.append(user_id)
        if state:
            where_parts.append("state = ?")
            params.append(state)
        if tag:
            where_parts.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
        query = f"SELECT * FROM conversation_threads{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def search_threads(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConversationThread]:
        """Search threads by title/topic."""
        sql = "SELECT * FROM conversation_threads WHERE (title LIKE ? OR topic LIKE ?)"
        params: list = [f"%{query}%", f"%{query}%"]
        if conversation_id:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def get_child_threads(self, parent_thread_id: str) -> List[ConversationThread]:
        """Get child threads (branched from a parent)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM conversation_threads WHERE parent_thread_id = ? ORDER BY created_at ASC",
                (parent_thread_id,),
            ).fetchall()
        return [self._row_to_thread(r) for r in rows]

    def merge_threads(
        self,
        source_thread_id: str,
        target_thread_id: str,
    ) -> bool:
        """Merge source thread messages into target thread."""
        with self._get_conn() as conn:
            # Get source thread
            source = conn.execute(
                "SELECT * FROM conversation_threads WHERE thread_id = ?",
                (source_thread_id,),
            ).fetchone()
            if not source:
                raise ValueError(f"Source thread {source_thread_id} not found")

            target = conn.execute(
                "SELECT * FROM conversation_threads WHERE thread_id = ?",
                (target_thread_id,),
            ).fetchone()
            if not target:
                raise ValueError(f"Target thread {target_thread_id} not found")

            # Move messages
            conn.execute(
                "UPDATE thread_messages SET thread_id = ? WHERE thread_id = ?",
                (target_thread_id, source_thread_id),
            )

            # Update message count
            msg_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM thread_messages WHERE thread_id = ?",
                (target_thread_id,),
            ).fetchone()["cnt"]

            now = time.time()
            conn.execute(
                "UPDATE conversation_threads SET message_count = ?, updated_at = ? WHERE thread_id = ?",
                (msg_count, now, target_thread_id),
            )

            # Archive source
            conn.execute(
                "UPDATE conversation_threads SET state = 'archived', closed_at = ?, updated_at = ? WHERE thread_id = ?",
                (now, now, source_thread_id),
            )

        logger.info(f"Merged thread {source_thread_id} into {target_thread_id}")
        return True

    def update_tags(
        self,
        thread_id: str,
        tags: List[str],
    ) -> bool:
        """Update thread tags."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE conversation_threads SET tags = ?, updated_at = ? WHERE thread_id = ?",
                (json.dumps(tags), time.time(), thread_id),
            )
            return cursor.rowcount > 0

    def update_title(self, thread_id: str, title: str) -> bool:
        """Update thread title."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE conversation_threads SET title = ?, updated_at = ? WHERE thread_id = ?",
                (title, time.time(), thread_id),
            )
            return cursor.rowcount > 0

    def get_stats(
        self,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get thread statistics."""
        where_parts = []
        params: list = []
        if conversation_id:
            where_parts.append("conversation_id = ?")
            params.append(conversation_id)
        if user_id:
            where_parts.append("user_id = ?")
            params.append(user_id)
        where = " WHERE " + " AND ".join(where_parts) if where_parts else ""

        with self._get_conn() as conn:
            row = conn.execute(
                f"""SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN state='active' THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN state='paused' THEN 1 ELSE 0 END) as paused,
                    SUM(CASE WHEN state='archived' THEN 1 ELSE 0 END) as archived,
                    SUM(CASE WHEN state='closed' THEN 1 ELSE 0 END) as closed,
                    SUM(message_count) as total_messages,
                    AVG(message_count) as avg_messages
                    FROM conversation_threads{where}""",
                params,
            ).fetchone()

        return {
            "total_threads": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "paused": int(row["paused"] or 0),
            "archived": int(row["archived"] or 0),
            "closed": int(row["closed"] or 0),
            "total_messages": int(row["total_messages"] or 0),
            "avg_messages_per_thread": round(float(row["avg_messages"] or 0), 1),
        }

    def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread and its messages."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM thread_messages WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM thread_context WHERE thread_id = ?", (thread_id,))
            cursor = conn.execute(
                "DELETE FROM conversation_threads WHERE thread_id = ?", (thread_id,)
            )
            return cursor.rowcount > 0

    def pin_message(self, thread_id: str, message_id: str) -> bool:
        """Pin a message in thread context."""
        ctx = self.get_thread_context(thread_id)
        if not ctx:
            return False
        if message_id not in ctx.pinned_messages:
            ctx.pinned_messages.append(message_id)
            return self.update_context(thread_id, pinned_messages=ctx.pinned_messages)
        return True

    def unpin_message(self, thread_id: str, message_id: str) -> bool:
        """Unpin a message from thread context."""
        ctx = self.get_thread_context(thread_id)
        if not ctx:
            return False
        if message_id in ctx.pinned_messages:
            ctx.pinned_messages.remove(message_id)
            return self.update_context(thread_id, pinned_messages=ctx.pinned_messages)
        return False

    def _row_to_thread(self, row: sqlite3.Row) -> ConversationThread:
        return ConversationThread(
            thread_id=row["thread_id"],
            parent_thread_id=row["parent_thread_id"],
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            title=row["title"],
            topic=row["topic"],
            state=row["state"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            message_count=row["message_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            closed_at=row["closed_at"],
        )
