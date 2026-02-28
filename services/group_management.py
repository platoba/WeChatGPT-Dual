"""
Group Management - Group chat features for WeChat + Telegram

Features:
- Welcome messages for new members (customizable per group)
- Group rules with enforcement
- Topic tracking and discussion threads
- Member activity stats and leaderboard
- Auto-kick inactive members (configurable)
- Scheduled announcements
- FAQ bot with keyword triggers
- Anti-flood protection
- Group-specific AI persona settings
"""

import json
import time
import sqlite3
import hashlib
import logging
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from contextlib import contextmanager
from collections import defaultdict

logger = logging.getLogger(__name__)

GROUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    name TEXT DEFAULT '',
    welcome_message TEXT DEFAULT '',
    rules TEXT DEFAULT '',
    ai_persona TEXT DEFAULT '',
    language TEXT DEFAULT 'zh',
    settings TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT DEFAULT '',
    role TEXT DEFAULT 'member',
    message_count INTEGER DEFAULT 0,
    last_active REAL DEFAULT 0,
    joined_at REAL NOT NULL,
    warned INTEGER DEFAULT 0,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_group ON group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_members_active ON group_members(last_active);

CREATE TABLE IF NOT EXISTS group_topics (
    topic_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    title TEXT NOT NULL,
    started_by TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    last_message REAL DEFAULT 0,
    is_pinned INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_group ON group_topics(group_id);

CREATE TABLE IF NOT EXISTS group_faq (
    faq_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    trigger_keywords TEXT NOT NULL,
    response TEXT NOT NULL,
    use_count INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faq_group ON group_faq(group_id);

CREATE TABLE IF NOT EXISTS group_announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    message TEXT NOT NULL,
    scheduled_at REAL DEFAULT 0,
    sent_at REAL DEFAULT 0,
    repeat_interval INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_announce_group ON group_announcements(group_id);

CREATE TABLE IF NOT EXISTS flood_tracker (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    window_start REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY (group_id, user_id)
);
"""

DEFAULT_SETTINGS = {
    'max_messages_per_minute': 10,
    'inactive_days_kick': 0,  # 0 = disabled
    'welcome_enabled': True,
    'faq_enabled': True,
    'flood_protection': True,
    'ai_enabled': True,
    'max_message_length': 4000,
    'min_interval_seconds': 1,
}


@dataclass
class GroupInfo:
    group_id: str
    platform: str
    name: str = ''
    welcome_message: str = ''
    rules: str = ''
    ai_persona: str = ''
    language: str = 'zh'
    settings: Dict = field(default_factory=dict)
    member_count: int = 0
    created_at: float = 0


@dataclass
class MemberInfo:
    group_id: str
    user_id: str
    username: str = ''
    role: str = 'member'
    message_count: int = 0
    last_active: float = 0
    joined_at: float = 0
    warned: int = 0


@dataclass
class TopicInfo:
    topic_id: str
    group_id: str
    title: str
    started_by: str = ''
    message_count: int = 0
    last_message: float = 0
    is_pinned: bool = False
    created_at: float = 0


@dataclass
class FloodCheckResult:
    is_flood: bool
    message_count: int
    limit: int
    action: str = 'pass'  # pass, warn, mute


class GroupManager:
    """Manage group chats across WeChat and Telegram."""

    def __init__(self, db_path: str = "groups.db"):
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
            conn.executescript(GROUP_SCHEMA)

    # ── Group CRUD ──

    def register_group(self, group_id: str, platform: str, name: str = '',
                       welcome_message: str = '', rules: str = '') -> GroupInfo:
        """Register or update a group."""
        now = time.time()
        settings = json.dumps(DEFAULT_SETTINGS)
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO groups (group_id, platform, name, welcome_message, rules, settings, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(group_id) DO UPDATE SET
                   name = ?, welcome_message = ?, rules = ?, updated_at = ?""",
                (group_id, platform, name, welcome_message, rules, settings, now, now,
                 name, welcome_message, rules, now)
            )
        return GroupInfo(group_id=group_id, platform=platform, name=name,
                         welcome_message=welcome_message, rules=rules,
                         settings=DEFAULT_SETTINGS, created_at=now)

    def get_group(self, group_id: str) -> Optional[GroupInfo]:
        """Get group info."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,)).fetchone()
            if not row:
                return None
            member_count = conn.execute(
                "SELECT COUNT(*) FROM group_members WHERE group_id = ?", (group_id,)
            ).fetchone()[0]
            return GroupInfo(
                group_id=row['group_id'],
                platform=row['platform'],
                name=row['name'],
                welcome_message=row['welcome_message'],
                rules=row['rules'],
                ai_persona=row['ai_persona'],
                language=row['language'],
                settings=json.loads(row['settings']) if row['settings'] else DEFAULT_SETTINGS,
                member_count=member_count,
                created_at=row['created_at'],
            )

    def update_settings(self, group_id: str, **kwargs) -> bool:
        """Update group settings."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT settings FROM groups WHERE group_id = ?", (group_id,)).fetchone()
            if not row:
                return False
            settings = json.loads(row['settings']) if row['settings'] else dict(DEFAULT_SETTINGS)
            settings.update(kwargs)
            conn.execute(
                "UPDATE groups SET settings = ?, updated_at = ? WHERE group_id = ?",
                (json.dumps(settings), time.time(), group_id)
            )
            return True

    def set_welcome(self, group_id: str, message: str) -> bool:
        """Set welcome message for new members."""
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE groups SET welcome_message = ?, updated_at = ? WHERE group_id = ?",
                (message, time.time(), group_id)
            )
            return result.rowcount > 0

    def set_rules(self, group_id: str, rules: str) -> bool:
        """Set group rules."""
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE groups SET rules = ?, updated_at = ? WHERE group_id = ?",
                (rules, time.time(), group_id)
            )
            return result.rowcount > 0

    def set_ai_persona(self, group_id: str, persona: str) -> bool:
        """Set AI persona for this group."""
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE groups SET ai_persona = ?, updated_at = ? WHERE group_id = ?",
                (persona, time.time(), group_id)
            )
            return result.rowcount > 0

    # ── Members ──

    def add_member(self, group_id: str, user_id: str, username: str = '',
                   role: str = 'member') -> MemberInfo:
        """Add a member to a group."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO group_members
                   (group_id, user_id, username, role, joined_at, last_active)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (group_id, user_id, username, role, now, now)
            )
        return MemberInfo(group_id=group_id, user_id=user_id, username=username,
                          role=role, joined_at=now, last_active=now)

    def remove_member(self, group_id: str, user_id: str) -> bool:
        """Remove a member from a group."""
        with self._get_conn() as conn:
            result = conn.execute(
                "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                (group_id, user_id)
            )
            return result.rowcount > 0

    def record_activity(self, group_id: str, user_id: str) -> None:
        """Record member activity (message sent)."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE group_members SET
                   message_count = message_count + 1,
                   last_active = ?
                   WHERE group_id = ? AND user_id = ?""",
                (now, group_id, user_id)
            )

    def get_leaderboard(self, group_id: str, limit: int = 10) -> List[MemberInfo]:
        """Get top active members."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM group_members
                   WHERE group_id = ?
                   ORDER BY message_count DESC
                   LIMIT ?""",
                (group_id, limit)
            ).fetchall()
            return [self._row_to_member(row) for row in rows]

    def get_inactive_members(self, group_id: str, inactive_days: int = 30) -> List[MemberInfo]:
        """Get members inactive for N days."""
        cutoff = time.time() - inactive_days * 86400
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM group_members
                   WHERE group_id = ? AND last_active < ? AND role = 'member'
                   ORDER BY last_active ASC""",
                (group_id, cutoff)
            ).fetchall()
            return [self._row_to_member(row) for row in rows]

    def get_welcome_message(self, group_id: str, username: str = '') -> Optional[str]:
        """Get formatted welcome message for a new member."""
        group = self.get_group(group_id)
        if not group or not group.welcome_message:
            return None
        settings = group.settings
        if not settings.get('welcome_enabled', True):
            return None
        msg = group.welcome_message.replace('{username}', username)
        msg = msg.replace('{group}', group.name)
        if group.rules:
            msg += f"\n\n📋 群规:\n{group.rules}"
        return msg

    # ── Flood Protection ──

    def check_flood(self, group_id: str, user_id: str) -> FloodCheckResult:
        """Check if user is flooding the group."""
        group = self.get_group(group_id)
        if not group:
            return FloodCheckResult(is_flood=False, message_count=0, limit=10)

        settings = group.settings
        if not settings.get('flood_protection', True):
            return FloodCheckResult(is_flood=False, message_count=0, limit=10)

        limit = settings.get('max_messages_per_minute', 10)
        now = time.time()
        window_start = now - 60

        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM flood_tracker WHERE group_id = ? AND user_id = ?",
                (group_id, user_id)
            ).fetchone()

            if row and row['window_start'] > window_start:
                count = row['message_count'] + 1
                conn.execute(
                    "UPDATE flood_tracker SET message_count = ? WHERE group_id = ? AND user_id = ?",
                    (count, group_id, user_id)
                )
            else:
                count = 1
                conn.execute(
                    """INSERT OR REPLACE INTO flood_tracker (group_id, user_id, window_start, message_count)
                       VALUES (?, ?, ?, 1)""",
                    (group_id, user_id, now)
                )

        is_flood = count > limit
        action = 'pass'
        if count > limit * 2:
            action = 'mute'
        elif is_flood:
            action = 'warn'

        return FloodCheckResult(
            is_flood=is_flood,
            message_count=count,
            limit=limit,
            action=action,
        )

    # ── Topics ──

    def create_topic(self, group_id: str, title: str, started_by: str = '') -> TopicInfo:
        """Create a discussion topic."""
        topic_id = hashlib.sha256(f"{group_id}:{title}:{time.time()}".encode()).hexdigest()[:12]
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO group_topics (topic_id, group_id, title, started_by, created_at, last_message)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (topic_id, group_id, title, started_by, now, now)
            )
        return TopicInfo(topic_id=topic_id, group_id=group_id, title=title,
                         started_by=started_by, created_at=now)

    def get_topics(self, group_id: str, limit: int = 20) -> List[TopicInfo]:
        """Get topics for a group, pinned first."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM group_topics WHERE group_id = ?
                   ORDER BY is_pinned DESC, last_message DESC LIMIT ?""",
                (group_id, limit)
            ).fetchall()
            return [TopicInfo(
                topic_id=r['topic_id'], group_id=r['group_id'], title=r['title'],
                started_by=r['started_by'], message_count=r['message_count'],
                last_message=r['last_message'], is_pinned=bool(r['is_pinned']),
                created_at=r['created_at']
            ) for r in rows]

    def pin_topic(self, topic_id: str, pinned: bool = True) -> bool:
        """Pin/unpin a topic."""
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE group_topics SET is_pinned = ? WHERE topic_id = ?",
                (1 if pinned else 0, topic_id)
            )
            return result.rowcount > 0

    # ── FAQ ──

    def add_faq(self, group_id: str, keywords: List[str], response: str,
                created_by: str = '') -> str:
        """Add a FAQ entry with trigger keywords."""
        faq_id = hashlib.sha256(f"{group_id}:{','.join(keywords)}".encode()).hexdigest()[:12]
        keywords_json = json.dumps(keywords)
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO group_faq
                   (faq_id, group_id, trigger_keywords, response, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (faq_id, group_id, keywords_json, response, created_by, time.time())
            )
        return faq_id

    def match_faq(self, group_id: str, message: str) -> Optional[str]:
        """Try to match a message against FAQ entries."""
        group = self.get_group(group_id)
        if not group:
            return None
        settings = group.settings
        if not settings.get('faq_enabled', True):
            return None

        message_lower = message.lower()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM group_faq WHERE group_id = ?", (group_id,)
            ).fetchall()

            for row in rows:
                keywords = json.loads(row['trigger_keywords'])
                for kw in keywords:
                    if kw.lower() in message_lower:
                        # Update use count
                        conn.execute(
                            "UPDATE group_faq SET use_count = use_count + 1 WHERE faq_id = ?",
                            (row['faq_id'],)
                        )
                        return row['response']
        return None

    def list_faq(self, group_id: str) -> List[Dict[str, Any]]:
        """List all FAQ entries for a group."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM group_faq WHERE group_id = ? ORDER BY use_count DESC",
                (group_id,)
            ).fetchall()
            return [
                {
                    'faq_id': r['faq_id'],
                    'keywords': json.loads(r['trigger_keywords']),
                    'response': r['response'],
                    'use_count': r['use_count'],
                }
                for r in rows
            ]

    def remove_faq(self, faq_id: str) -> bool:
        """Remove a FAQ entry."""
        with self._get_conn() as conn:
            result = conn.execute("DELETE FROM group_faq WHERE faq_id = ?", (faq_id,))
            return result.rowcount > 0

    # ── Announcements ──

    def schedule_announcement(self, group_id: str, message: str,
                               scheduled_at: float = 0, repeat_interval: int = 0,
                               created_by: str = '') -> int:
        """Schedule an announcement."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO group_announcements
                   (group_id, message, scheduled_at, repeat_interval, created_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (group_id, message, scheduled_at or time.time(), repeat_interval, created_by)
            )
            return cursor.lastrowid or 0

    def get_pending_announcements(self) -> List[Dict[str, Any]]:
        """Get announcements ready to be sent."""
        now = time.time()
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM group_announcements
                   WHERE is_active = 1 AND scheduled_at <= ? AND sent_at = 0
                   ORDER BY scheduled_at""",
                (now,)
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_sent(self, announcement_id: int) -> None:
        """Mark announcement as sent."""
        now = time.time()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT repeat_interval FROM group_announcements WHERE id = ?",
                (announcement_id,)
            ).fetchone()
            if row and row['repeat_interval'] > 0:
                # Reschedule
                conn.execute(
                    "UPDATE group_announcements SET sent_at = 0, scheduled_at = ? WHERE id = ?",
                    (now + row['repeat_interval'], announcement_id)
                )
            else:
                conn.execute(
                    "UPDATE group_announcements SET sent_at = ?, is_active = 0 WHERE id = ?",
                    (now, announcement_id)
                )

    # ── Stats ──

    def get_group_stats(self, group_id: str) -> Dict[str, Any]:
        """Get comprehensive group statistics."""
        with self._get_conn() as conn:
            member_count = conn.execute(
                "SELECT COUNT(*) FROM group_members WHERE group_id = ?", (group_id,)
            ).fetchone()[0]

            total_messages = conn.execute(
                "SELECT COALESCE(SUM(message_count), 0) FROM group_members WHERE group_id = ?",
                (group_id,)
            ).fetchone()[0]

            active_today = conn.execute(
                "SELECT COUNT(*) FROM group_members WHERE group_id = ? AND last_active >= ?",
                (group_id, time.time() - 86400)
            ).fetchone()[0]

            topic_count = conn.execute(
                "SELECT COUNT(*) FROM group_topics WHERE group_id = ?", (group_id,)
            ).fetchone()[0]

            faq_count = conn.execute(
                "SELECT COUNT(*) FROM group_faq WHERE group_id = ?", (group_id,)
            ).fetchone()[0]

            return {
                'group_id': group_id,
                'member_count': member_count,
                'total_messages': total_messages,
                'active_today': active_today,
                'topic_count': topic_count,
                'faq_count': faq_count,
                'avg_messages_per_member': round(total_messages / member_count, 1) if member_count > 0 else 0,
            }

    def _row_to_member(self, row) -> MemberInfo:
        return MemberInfo(
            group_id=row['group_id'],
            user_id=row['user_id'],
            username=row['username'],
            role=row['role'],
            message_count=row['message_count'],
            last_active=row['last_active'],
            joined_at=row['joined_at'],
            warned=row['warned'],
        )
