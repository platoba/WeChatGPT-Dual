"""
Usage Quota Service - 用户使用配额管理

Features:
- Per-user daily/monthly token quotas
- Tier system: free / basic / premium / unlimited
- Quota checking before API call
- Usage tracking with auto-reset on period boundary
- Admin override
- Quota exhaustion notification
"""

import time
import sqlite3
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, date

logger = logging.getLogger(__name__)

QUOTA_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_quotas (
    user_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL DEFAULT 'free',
    daily_limit INTEGER NOT NULL DEFAULT 1000,
    monthly_limit INTEGER NOT NULL DEFAULT 30000,
    daily_used INTEGER DEFAULT 0,
    monthly_used INTEGER DEFAULT 0,
    total_used INTEGER DEFAULT 0,
    last_daily_reset TEXT,
    last_monthly_reset TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tokens_used INTEGER NOT NULL,
    engine TEXT DEFAULT '',
    action TEXT DEFAULT 'chat',
    recorded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quota_history_user ON quota_history(user_id);
CREATE INDEX IF NOT EXISTS idx_quota_history_time ON quota_history(recorded_at);
"""

# Tier definitions
TIERS = {
    "free": {"daily": 1000, "monthly": 30000, "label": "🆓 Free"},
    "basic": {"daily": 10000, "monthly": 300000, "label": "⭐ Basic"},
    "premium": {"daily": 100000, "monthly": 3000000, "label": "💎 Premium"},
    "unlimited": {"daily": 999999999, "monthly": 999999999, "label": "♾️ Unlimited"},
}


@dataclass
class QuotaStatus:
    """Current quota status for a user"""
    user_id: str
    tier: str
    daily_limit: int
    monthly_limit: int
    daily_used: int
    monthly_used: int
    daily_remaining: int
    monthly_remaining: int
    is_exhausted: bool
    is_admin: bool
    total_used: int

    @property
    def daily_pct(self) -> float:
        if self.daily_limit <= 0:
            return 0
        return round(self.daily_used / self.daily_limit * 100, 1)

    @property
    def monthly_pct(self) -> float:
        if self.monthly_limit <= 0:
            return 0
        return round(self.monthly_used / self.monthly_limit * 100, 1)

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "tier": self.tier,
            "daily": {
                "limit": self.daily_limit,
                "used": self.daily_used,
                "remaining": self.daily_remaining,
                "pct": self.daily_pct,
            },
            "monthly": {
                "limit": self.monthly_limit,
                "used": self.monthly_used,
                "remaining": self.monthly_remaining,
                "pct": self.monthly_pct,
            },
            "is_exhausted": self.is_exhausted,
            "is_admin": self.is_admin,
            "total_used": self.total_used,
        }

    def format_message(self) -> str:
        """Format quota status as a readable message"""
        tier_info = TIERS.get(self.tier, TIERS["free"])
        lines = [
            f"📊 配额状态 — {tier_info['label']}",
            "",
            f"📅 今日: {self.daily_used:,} / {self.daily_limit:,} tokens ({self.daily_pct}%)",
            f"📆 本月: {self.monthly_used:,} / {self.monthly_limit:,} tokens ({self.monthly_pct}%)",
            f"📈 累计: {self.total_used:,} tokens",
        ]
        if self.is_exhausted:
            lines.append("")
            lines.append("⚠️ 配额已用完，请升级或等待重置")
        return "\n".join(lines)


class UsageQuota:
    """
    Per-user usage quota management with tier system.

    Usage:
        quota = UsageQuota("quota.db")
        if quota.check("user123", tokens_needed=500):
            # Proceed with API call
            quota.consume("user123", tokens_used=450)
        else:
            # Quota exceeded
            status = quota.get_status("user123")
    """

    def __init__(self, db_path: str = "quota.db"):
        self.db_path = db_path
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
            conn.executescript(QUOTA_SCHEMA)

    def _ensure_user(self, conn, user_id: str) -> Dict:
        """Ensure user exists in quota table, create if not"""
        row = conn.execute(
            "SELECT * FROM user_quotas WHERE user_id = ?", (user_id,)
        ).fetchone()

        if row:
            return dict(row)

        now = time.time()
        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")
        tier = TIERS["free"]

        conn.execute(
            """INSERT INTO user_quotas
               (user_id, tier, daily_limit, monthly_limit, daily_used, monthly_used,
                last_daily_reset, last_monthly_reset, created_at, updated_at)
               VALUES (?, 'free', ?, ?, 0, 0, ?, ?, ?, ?)""",
            (user_id, tier["daily"], tier["monthly"], today, month, now, now),
        )
        return dict(
            conn.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,)).fetchone()
        )

    def _maybe_reset(self, conn, user_data: Dict) -> Dict:
        """Reset counters if period has changed"""
        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")
        user_id = user_data["user_id"]
        updated = False

        if user_data.get("last_daily_reset") != today:
            conn.execute(
                "UPDATE user_quotas SET daily_used = 0, last_daily_reset = ? WHERE user_id = ?",
                (today, user_id),
            )
            user_data["daily_used"] = 0
            user_data["last_daily_reset"] = today
            updated = True

        if user_data.get("last_monthly_reset") != month:
            conn.execute(
                "UPDATE user_quotas SET monthly_used = 0, last_monthly_reset = ? WHERE user_id = ?",
                (month, user_id),
            )
            user_data["monthly_used"] = 0
            user_data["last_monthly_reset"] = month
            updated = True

        if updated:
            conn.execute(
                "UPDATE user_quotas SET updated_at = ? WHERE user_id = ?",
                (time.time(), user_id),
            )

        return user_data

    def check(self, user_id: str, tokens_needed: int = 1) -> bool:
        """
        Check if user has enough quota.

        Args:
            user_id: User to check
            tokens_needed: Estimated tokens for the request

        Returns:
            True if user can proceed
        """
        with self._conn() as conn:
            user_data = self._ensure_user(conn, user_id)
            user_data = self._maybe_reset(conn, user_data)

            # Admins bypass quota
            if user_data.get("is_admin"):
                return True

            daily_ok = (user_data["daily_used"] + tokens_needed) <= user_data["daily_limit"]
            monthly_ok = (user_data["monthly_used"] + tokens_needed) <= user_data["monthly_limit"]
            return daily_ok and monthly_ok

    def consume(self, user_id: str, tokens_used: int, engine: str = "", action: str = "chat"):
        """
        Record token consumption.

        Args:
            user_id: User who consumed tokens
            tokens_used: Actual tokens used
            engine: Engine name
            action: Action type (chat, image, etc.)
        """
        now = time.time()
        with self._conn() as conn:
            user_data = self._ensure_user(conn, user_id)
            self._maybe_reset(conn, user_data)

            conn.execute(
                """UPDATE user_quotas
                   SET daily_used = daily_used + ?,
                       monthly_used = monthly_used + ?,
                       total_used = total_used + ?,
                       updated_at = ?
                   WHERE user_id = ?""",
                (tokens_used, tokens_used, tokens_used, now, user_id),
            )

            conn.execute(
                """INSERT INTO quota_history (user_id, tokens_used, engine, action, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, tokens_used, engine, action, now),
            )

    def get_status(self, user_id: str) -> QuotaStatus:
        """Get current quota status for user"""
        with self._conn() as conn:
            user_data = self._ensure_user(conn, user_id)
            user_data = self._maybe_reset(conn, user_data)

            daily_remaining = max(0, user_data["daily_limit"] - user_data["daily_used"])
            monthly_remaining = max(0, user_data["monthly_limit"] - user_data["monthly_used"])

            return QuotaStatus(
                user_id=user_id,
                tier=user_data["tier"],
                daily_limit=user_data["daily_limit"],
                monthly_limit=user_data["monthly_limit"],
                daily_used=user_data["daily_used"],
                monthly_used=user_data["monthly_used"],
                daily_remaining=daily_remaining,
                monthly_remaining=monthly_remaining,
                is_exhausted=(daily_remaining == 0 or monthly_remaining == 0),
                is_admin=bool(user_data.get("is_admin")),
                total_used=user_data.get("total_used", 0),
            )

    def set_tier(self, user_id: str, tier: str) -> bool:
        """Set user tier"""
        if tier not in TIERS:
            return False

        tier_info = TIERS[tier]
        with self._conn() as conn:
            self._ensure_user(conn, user_id)
            conn.execute(
                """UPDATE user_quotas
                   SET tier = ?, daily_limit = ?, monthly_limit = ?, updated_at = ?
                   WHERE user_id = ?""",
                (tier, tier_info["daily"], tier_info["monthly"], time.time(), user_id),
            )
        return True

    def set_admin(self, user_id: str, is_admin: bool = True):
        """Set or remove admin status"""
        with self._conn() as conn:
            self._ensure_user(conn, user_id)
            conn.execute(
                "UPDATE user_quotas SET is_admin = ?, updated_at = ? WHERE user_id = ?",
                (1 if is_admin else 0, time.time(), user_id),
            )

    def get_history(self, user_id: str, limit: int = 50) -> List[Dict]:
        """Get usage history for user"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM quota_history
                   WHERE user_id = ?
                   ORDER BY recorded_at DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_users(self, limit: int = 10) -> List[Dict]:
        """Get top users by total usage"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT user_id, tier, total_used, daily_used, monthly_used
                   FROM user_quotas
                   ORDER BY total_used DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_tiers(self) -> Dict:
        """Get all tier definitions"""
        return {k: dict(v) for k, v in TIERS.items()}
