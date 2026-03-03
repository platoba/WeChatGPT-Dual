"""
Database layer - SQLite persistence for usage tracking, message logging, and user management
"""

import os
import time
import sqlite3
import json
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "wechatgpt.db")

SCHEMA = """
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

CREATE TABLE IF NOT EXISTS usage_daily (
    date TEXT NOT NULL,
    engine TEXT NOT NULL,
    requests INTEGER DEFAULT 0,
    tokens INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    avg_latency REAL DEFAULT 0.0,
    PRIMARY KEY (date, engine)
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT DEFAULT '',
    first_seen REAL NOT NULL,
    last_active REAL NOT NULL,
    total_messages INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active);
"""


@dataclass
class UserRecord:
    user_id: str
    username: str
    first_seen: float
    last_active: float
    total_messages: int
    is_blocked: bool
    metadata: Dict


class Database:
    """SQLite database for persistence"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

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

    # ---- Message Logging ----

    def log_message(
        self,
        user_id: str,
        role: str,
        content: str,
        channel: str = "telegram",
        engine: str = "",
        tokens_used: int = 0,
        latency: float = 0.0,
    ):
        """Log a message"""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO messages (user_id, role, content, channel, engine, tokens_used, latency, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, role, content, channel, engine, tokens_used, latency, now),
            )
            # Update user record
            self._touch_user(conn, user_id)

    def get_messages(
        self,
        user_id: str = None,
        channel: str = None,
        limit: int = 50,
        since: float = None,
    ) -> List[Dict]:
        """Query message history"""
        query = "SELECT * FROM messages WHERE 1=1"
        params = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if channel:
            query += " AND channel = ?"
            params.append(channel)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    # ---- Usage Tracking ----

    def record_usage(
        self,
        engine: str,
        tokens: int = 0,
        latency: float = 0.0,
        is_error: bool = False,
    ):
        """Record engine usage for daily aggregation"""
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM usage_daily WHERE date = ? AND engine = ?",
                (date, engine),
            ).fetchone()

            if row:
                new_requests = row["requests"] + 1
                new_tokens = row["tokens"] + tokens
                new_errors = row["errors"] + (1 if is_error else 0)
                # Running average latency
                if not is_error and latency > 0:
                    old_avg = row["avg_latency"]
                    ok_count = row["requests"] - row["errors"]
                    new_avg = (old_avg * ok_count + latency) / (ok_count + 1) if ok_count >= 0 else latency
                else:
                    new_avg = row["avg_latency"]

                conn.execute(
                    """UPDATE usage_daily
                       SET requests = ?, tokens = ?, errors = ?, avg_latency = ?
                       WHERE date = ? AND engine = ?""",
                    (new_requests, new_tokens, new_errors, round(new_avg, 3), date, engine),
                )
            else:
                conn.execute(
                    """INSERT INTO usage_daily (date, engine, requests, tokens, errors, avg_latency)
                       VALUES (?, ?, 1, ?, ?, ?)""",
                    (date, engine, tokens, 1 if is_error else 0, round(latency, 3)),
                )

    def get_usage(self, days: int = 7) -> List[Dict]:
        """Get daily usage for the last N days"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM usage_daily
                   ORDER BY date DESC, engine
                   LIMIT ?""",
                (days * 5,),  # up to 5 engines per day
            ).fetchall()
            return [dict(r) for r in rows]

    def get_usage_summary(self) -> Dict:
        """Get total usage summary"""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) as total_records,
                     SUM(requests) as total_requests,
                     SUM(tokens) as total_tokens,
                     SUM(errors) as total_errors
                   FROM usage_daily"""
            ).fetchone()
            return dict(row) if row else {}

    # ---- User Management ----

    def _touch_user(self, conn, user_id: str, username: str = ""):
        """Create or update user record"""
        now = time.time()
        existing = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE users
                   SET last_active = ?, total_messages = total_messages + 1
                   WHERE user_id = ?""",
                (now, user_id),
            )
        else:
            conn.execute(
                """INSERT INTO users (user_id, username, first_seen, last_active, total_messages)
                   VALUES (?, ?, ?, ?, 1)""",
                (user_id, username, now, now),
            )

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        """Get user record"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                return None
            return UserRecord(
                user_id=row["user_id"],
                username=row["username"],
                first_seen=row["first_seen"],
                last_active=row["last_active"],
                total_messages=row["total_messages"],
                is_blocked=bool(row["is_blocked"]),
                metadata=json.loads(row["metadata"]),
            )

    def get_users(self, limit: int = 50) -> List[Dict]:
        """List users by activity"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM users
                   WHERE is_blocked = 0
                   ORDER BY last_active DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def block_user(self, user_id: str) -> bool:
        """Block a user"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET is_blocked = 1 WHERE user_id = ?",
                (user_id,),
            )
            return conn.total_changes > 0

    def unblock_user(self, user_id: str) -> bool:
        """Unblock a user"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET is_blocked = 0 WHERE user_id = ?",
                (user_id,),
            )
            return conn.total_changes > 0

    def is_blocked(self, user_id: str) -> bool:
        """Check if user is blocked"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT is_blocked FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return bool(row["is_blocked"]) if row else False

    # ---- Stats ----

    def get_stats(self) -> Dict:
        """Get overall database stats"""
        with self._conn() as conn:
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            usage = self.get_usage_summary()
            return {
                "total_messages": msg_count,
                "total_users": user_count,
                "usage": usage,
            }
