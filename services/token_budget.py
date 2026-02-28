"""
Token预算管理服务 - Per-user token budget with daily/monthly limits

Features:
- Per-user daily and monthly token budgets
- Real-time usage tracking with SQLite persistence
- Budget alerts (80% / 100% thresholds)
- Auto-reset at day/month boundaries
- Admin override for unlimited users
- Usage reports and trend analysis
"""

import time
import sqlite3
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger(__name__)

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_budget (
    user_id TEXT NOT NULL,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    tokens_limit INTEGER DEFAULT -1,
    requests_count INTEGER DEFAULT 0,
    first_use REAL NOT NULL,
    last_use REAL NOT NULL,
    PRIMARY KEY (user_id, period, period_key)
);

CREATE TABLE IF NOT EXISTS budget_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    threshold REAL NOT NULL,
    tokens_used INTEGER NOT NULL,
    tokens_limit INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_overrides (
    user_id TEXT PRIMARY KEY,
    unlimited INTEGER DEFAULT 0,
    daily_override INTEGER DEFAULT -1,
    monthly_override INTEGER DEFAULT -1,
    reason TEXT DEFAULT '',
    set_by TEXT DEFAULT 'system',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_budget_user ON token_budget(user_id);
CREATE INDEX IF NOT EXISTS idx_budget_period ON token_budget(period_key);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON budget_alerts(user_id);
"""


@dataclass
class BudgetStatus:
    """Current budget status for a user"""

    user_id: str
    daily_used: int
    daily_limit: int
    daily_remaining: int
    daily_pct: float
    monthly_used: int
    monthly_limit: int
    monthly_remaining: int
    monthly_pct: float
    is_unlimited: bool
    is_over_budget: bool
    alerts: List[str]

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "daily": {
                "used": self.daily_used,
                "limit": self.daily_limit,
                "remaining": self.daily_remaining,
                "pct": round(self.daily_pct, 1),
            },
            "monthly": {
                "used": self.monthly_used,
                "limit": self.monthly_limit,
                "remaining": self.monthly_remaining,
                "pct": round(self.monthly_pct, 1),
            },
            "is_unlimited": self.is_unlimited,
            "is_over_budget": self.is_over_budget,
            "alerts": self.alerts,
        }


@dataclass
class UsageReport:
    """Usage report for a period"""

    user_id: str
    period: str
    entries: List[Dict]
    total_tokens: int
    total_requests: int
    avg_tokens_per_request: float

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "period": self.period,
            "entries": self.entries,
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "avg_tokens_per_request": round(self.avg_tokens_per_request, 1),
        }


class TokenBudgetManager:
    """
    Per-user token budget management.

    Tracks daily and monthly token usage with configurable limits.
    Supports admin overrides and budget alerts.

    Usage:
        mgr = TokenBudgetManager("bot.db")
        allowed, msg = mgr.check_budget("user123", daily_limit=10000, monthly_limit=300000)
        if allowed:
            # ... process request ...
            mgr.record_usage("user123", tokens=150)
    """

    ALERT_THRESHOLDS = (0.8, 1.0)  # 80% warning, 100% exceeded

    def __init__(self, db_path: str = "wechatgpt.db"):
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
            conn.executescript(BUDGET_SCHEMA)

    @staticmethod
    def _daily_key(ts: Optional[float] = None) -> str:
        """Get daily period key like '2026-02-28'"""
        dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _monthly_key(ts: Optional[float] = None) -> str:
        """Get monthly period key like '2026-02'"""
        dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return dt.strftime("%Y-%m")

    def check_budget(
        self,
        user_id: str,
        daily_limit: int = -1,
        monthly_limit: int = -1,
    ) -> Tuple[bool, str]:
        """
        Check if user is within budget.

        Args:
            user_id: User identifier
            daily_limit: Default daily limit (-1 = unlimited)
            monthly_limit: Default monthly limit (-1 = unlimited)

        Returns:
            (allowed: bool, message: str)
        """
        # Check admin overrides first
        override = self._get_override(user_id)
        if override and override["unlimited"]:
            return True, "unlimited"

        # Apply overrides to limits
        if override:
            if override["daily_override"] > 0:
                daily_limit = override["daily_override"]
            if override["monthly_override"] > 0:
                monthly_limit = override["monthly_override"]

        now = time.time()
        daily_key = self._daily_key(now)
        monthly_key = self._monthly_key(now)

        daily_used = self._get_usage(user_id, "daily", daily_key)
        monthly_used = self._get_usage(user_id, "monthly", monthly_key)

        # Check daily limit
        if daily_limit > 0 and daily_used >= daily_limit:
            return False, f"Daily token budget exceeded ({daily_used}/{daily_limit})"

        # Check monthly limit
        if monthly_limit > 0 and monthly_used >= monthly_limit:
            return False, f"Monthly token budget exceeded ({monthly_used}/{monthly_limit})"

        return True, "ok"

    def record_usage(self, user_id: str, tokens: int):
        """Record token usage for a user"""
        if tokens <= 0:
            return

        now = time.time()
        daily_key = self._daily_key(now)
        monthly_key = self._monthly_key(now)

        with self._conn() as conn:
            for period, key in [("daily", daily_key), ("monthly", monthly_key)]:
                row = conn.execute(
                    "SELECT * FROM token_budget WHERE user_id = ? AND period = ? AND period_key = ?",
                    (user_id, period, key),
                ).fetchone()

                if row:
                    conn.execute(
                        """UPDATE token_budget
                           SET tokens_used = tokens_used + ?,
                               requests_count = requests_count + 1,
                               last_use = ?
                           WHERE user_id = ? AND period = ? AND period_key = ?""",
                        (tokens, now, user_id, period, key),
                    )
                else:
                    conn.execute(
                        """INSERT INTO token_budget
                           (user_id, period, period_key, tokens_used, requests_count, first_use, last_use)
                           VALUES (?, ?, ?, ?, 1, ?, ?)""",
                        (user_id, period, key, tokens, now, now),
                    )

        logger.debug(f"Recorded {tokens} tokens for user {user_id}")

    def get_status(
        self,
        user_id: str,
        daily_limit: int = -1,
        monthly_limit: int = -1,
    ) -> BudgetStatus:
        """Get comprehensive budget status for a user"""
        override = self._get_override(user_id)
        is_unlimited = bool(override and override["unlimited"])

        if override:
            if override["daily_override"] > 0:
                daily_limit = override["daily_override"]
            if override["monthly_override"] > 0:
                monthly_limit = override["monthly_override"]

        now = time.time()
        daily_used = self._get_usage(user_id, "daily", self._daily_key(now))
        monthly_used = self._get_usage(user_id, "monthly", self._monthly_key(now))

        daily_remaining = max(0, daily_limit - daily_used) if daily_limit > 0 else -1
        monthly_remaining = max(0, monthly_limit - monthly_used) if monthly_limit > 0 else -1

        daily_pct = (daily_used / daily_limit * 100) if daily_limit > 0 else 0
        monthly_pct = (monthly_used / monthly_limit * 100) if monthly_limit > 0 else 0

        is_over = (
            (daily_limit > 0 and daily_used >= daily_limit)
            or (monthly_limit > 0 and monthly_used >= monthly_limit)
        )

        alerts = []
        if daily_limit > 0:
            if daily_pct >= 100:
                alerts.append("⛔ Daily budget exceeded")
            elif daily_pct >= 80:
                alerts.append(f"⚠️ Daily budget at {daily_pct:.0f}%")
        if monthly_limit > 0:
            if monthly_pct >= 100:
                alerts.append("⛔ Monthly budget exceeded")
            elif monthly_pct >= 80:
                alerts.append(f"⚠️ Monthly budget at {monthly_pct:.0f}%")

        return BudgetStatus(
            user_id=user_id,
            daily_used=daily_used,
            daily_limit=daily_limit,
            daily_remaining=daily_remaining,
            daily_pct=daily_pct,
            monthly_used=monthly_used,
            monthly_limit=monthly_limit,
            monthly_remaining=monthly_remaining,
            monthly_pct=monthly_pct,
            is_unlimited=is_unlimited,
            is_over_budget=is_over,
            alerts=alerts,
        )

    def set_override(
        self,
        user_id: str,
        unlimited: bool = False,
        daily_override: int = -1,
        monthly_override: int = -1,
        reason: str = "",
        set_by: str = "admin",
    ):
        """Set admin override for a user"""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO budget_overrides
                   (user_id, unlimited, daily_override, monthly_override, reason, set_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                    unlimited=excluded.unlimited,
                    daily_override=excluded.daily_override,
                    monthly_override=excluded.monthly_override,
                    reason=excluded.reason,
                    set_by=excluded.set_by,
                    created_at=excluded.created_at""",
                (user_id, int(unlimited), daily_override, monthly_override, reason, set_by, time.time()),
            )
        logger.info(f"Budget override set for {user_id}: unlimited={unlimited}")

    def remove_override(self, user_id: str) -> bool:
        """Remove admin override"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM budget_overrides WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount > 0

    def get_usage_report(
        self,
        user_id: str,
        period: str = "daily",
        limit: int = 30,
    ) -> UsageReport:
        """
        Get usage report for a user.

        Args:
            user_id: User identifier
            period: 'daily' or 'monthly'
            limit: Number of periods to include
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT period_key, tokens_used, requests_count, first_use, last_use
                   FROM token_budget
                   WHERE user_id = ? AND period = ?
                   ORDER BY period_key DESC
                   LIMIT ?""",
                (user_id, period, limit),
            ).fetchall()

        entries = [dict(r) for r in rows]
        total_tokens = sum(e["tokens_used"] for e in entries)
        total_requests = sum(e["requests_count"] for e in entries)
        avg = total_tokens / total_requests if total_requests > 0 else 0

        return UsageReport(
            user_id=user_id,
            period=period,
            entries=entries,
            total_tokens=total_tokens,
            total_requests=total_requests,
            avg_tokens_per_request=avg,
        )

    def get_top_users(self, period: str = "daily", limit: int = 10) -> List[Dict]:
        """Get top token consumers for the current period"""
        now = time.time()
        key = self._daily_key(now) if period == "daily" else self._monthly_key(now)

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT user_id, tokens_used, requests_count
                   FROM token_budget
                   WHERE period = ? AND period_key = ?
                   ORDER BY tokens_used DESC
                   LIMIT ?""",
                (period, key, limit),
            ).fetchall()

        return [dict(r) for r in rows]

    def _get_usage(self, user_id: str, period: str, period_key: str) -> int:
        """Get token usage for a specific period"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT tokens_used FROM token_budget WHERE user_id = ? AND period = ? AND period_key = ?",
                (user_id, period, period_key),
            ).fetchone()
            return row["tokens_used"] if row else 0

    def _get_override(self, user_id: str) -> Optional[Dict]:
        """Get admin override for a user"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM budget_overrides WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None
