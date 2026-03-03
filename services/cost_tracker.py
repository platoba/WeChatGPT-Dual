"""
Cost Tracker - Per-user token cost tracking, budgets, and reports

Features:
- Track token usage per user/model/conversation
- Cost calculation based on model pricing
- Daily/weekly/monthly budgets with alerts
- Usage reports and summaries
- Top users ranking
- Cost projection/forecasting
- CSV/JSON export
"""

import time
import sqlite3
import logging
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

COST_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    conversation_id TEXT DEFAULT '',
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON token_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_model ON token_usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON token_usage(timestamp);

CREATE TABLE IF NOT EXISTS user_budgets (
    user_id TEXT PRIMARY KEY,
    daily_limit_usd REAL DEFAULT 0,
    monthly_limit_usd REAL DEFAULT 0,
    total_limit_usd REAL DEFAULT 0,
    alert_threshold REAL DEFAULT 0.8,
    is_blocked INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    budget_used REAL DEFAULT 0,
    budget_limit REAL DEFAULT 0,
    acknowledged INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON cost_alerts(user_id);
"""

# Pricing per 1K tokens (USD)
MODEL_PRICING = {
    'gpt-4': {'input': 0.03, 'output': 0.06},
    'gpt-4-turbo': {'input': 0.01, 'output': 0.03},
    'gpt-4o': {'input': 0.005, 'output': 0.015},
    'gpt-4o-mini': {'input': 0.00015, 'output': 0.0006},
    'gpt-3.5-turbo': {'input': 0.0005, 'output': 0.0015},
    'claude-3-opus': {'input': 0.015, 'output': 0.075},
    'claude-3-sonnet': {'input': 0.003, 'output': 0.015},
    'claude-3-haiku': {'input': 0.00025, 'output': 0.00125},
    'claude-3.5-sonnet': {'input': 0.003, 'output': 0.015},
    'deepseek-chat': {'input': 0.00014, 'output': 0.00028},
    'deepseek-reasoner': {'input': 0.00055, 'output': 0.0022},
    'default': {'input': 0.001, 'output': 0.002},
}


@dataclass
class UsageRecord:
    user_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    conversation_id: str = ''
    timestamp: float = 0


@dataclass
class BudgetStatus:
    user_id: str
    daily_used: float = 0
    daily_limit: float = 0
    daily_pct: float = 0
    monthly_used: float = 0
    monthly_limit: float = 0
    monthly_pct: float = 0
    total_used: float = 0
    total_limit: float = 0
    total_pct: float = 0
    is_blocked: bool = False
    alerts: List[str] = field(default_factory=list)


@dataclass
class CostReport:
    user_id: str
    period: str
    start_time: float
    end_time: float
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    by_model: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    daily_breakdown: List[Dict] = field(default_factory=list)
    avg_cost_per_request: float = 0
    request_count: int = 0


class CostTracker:
    """Track token costs, enforce budgets, generate reports."""

    def __init__(self, db_path: str = "costs.db"):
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
            conn.executescript(COST_SCHEMA)

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD for given token usage."""
        pricing = MODEL_PRICING.get(model, MODEL_PRICING['default'])
        cost = (input_tokens / 1000 * pricing['input'] +
                output_tokens / 1000 * pricing['output'])
        return round(cost, 8)

    def record_usage(self, user_id: str, model: str, input_tokens: int,
                     output_tokens: int, conversation_id: str = '') -> UsageRecord:
        """Record a token usage event."""
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        now = time.time()

        record = UsageRecord(
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            conversation_id=conversation_id,
            timestamp=now,
        )

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO token_usage
                   (user_id, model, input_tokens, output_tokens, cost_usd, conversation_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, model, input_tokens, output_tokens, cost, conversation_id, now)
            )

        # Check budget
        self._check_budget_alerts(user_id)
        return record

    def set_budget(self, user_id: str, daily_limit: float = 0,
                   monthly_limit: float = 0, total_limit: float = 0,
                   alert_threshold: float = 0.8) -> None:
        """Set spending budget for a user."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO user_budgets
                   (user_id, daily_limit_usd, monthly_limit_usd, total_limit_usd,
                    alert_threshold, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   daily_limit_usd = ?, monthly_limit_usd = ?, total_limit_usd = ?,
                   alert_threshold = ?, updated_at = ?""",
                (user_id, daily_limit, monthly_limit, total_limit, alert_threshold,
                 now, now, daily_limit, monthly_limit, total_limit, alert_threshold, now)
            )

    def get_budget_status(self, user_id: str) -> BudgetStatus:
        """Get current budget status for a user."""
        now = time.time()
        today_start = self._day_start(now)
        month_start = self._month_start(now)

        with self._get_conn() as conn:
            budget = conn.execute(
                "SELECT * FROM user_budgets WHERE user_id = ?", (user_id,)
            ).fetchone()

            daily_used = self._sum_cost(conn, user_id, today_start, now)
            monthly_used = self._sum_cost(conn, user_id, month_start, now)
            total_used = self._sum_cost(conn, user_id, 0, now)

            if budget:
                dl = budget['daily_limit_usd']
                ml = budget['monthly_limit_usd']
                tl = budget['total_limit_usd']
                return BudgetStatus(
                    user_id=user_id,
                    daily_used=daily_used,
                    daily_limit=dl,
                    daily_pct=(daily_used / dl * 100) if dl > 0 else 0,
                    monthly_used=monthly_used,
                    monthly_limit=ml,
                    monthly_pct=(monthly_used / ml * 100) if ml > 0 else 0,
                    total_used=total_used,
                    total_limit=tl,
                    total_pct=(total_used / tl * 100) if tl > 0 else 0,
                    is_blocked=bool(budget['is_blocked']),
                )
            else:
                return BudgetStatus(
                    user_id=user_id,
                    daily_used=daily_used,
                    monthly_used=monthly_used,
                    total_used=total_used,
                )

    def check_can_spend(self, user_id: str, estimated_cost: float = 0) -> Tuple[bool, str]:
        """Check if user can spend (budget enforcement)."""
        status = self.get_budget_status(user_id)

        if status.is_blocked:
            return False, "Account is blocked due to budget limits"

        if status.daily_limit > 0 and status.daily_used + estimated_cost > status.daily_limit:
            return False, f"Daily budget exceeded (${status.daily_used:.4f}/${status.daily_limit:.2f})"

        if status.monthly_limit > 0 and status.monthly_used + estimated_cost > status.monthly_limit:
            return False, f"Monthly budget exceeded (${status.monthly_used:.4f}/${status.monthly_limit:.2f})"

        if status.total_limit > 0 and status.total_used + estimated_cost > status.total_limit:
            return False, f"Total budget exceeded (${status.total_used:.4f}/${status.total_limit:.2f})"

        return True, "OK"

    def generate_report(self, user_id: str, days: int = 30) -> CostReport:
        """Generate a cost report for the last N days."""
        now = time.time()
        start = now - days * 86400

        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM token_usage
                   WHERE user_id = ? AND timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp""",
                (user_id, start, now)
            ).fetchall()

            total_cost = 0
            total_input = 0
            total_output = 0
            by_model: Dict[str, Dict[str, Any]] = {}
            by_day: Dict[str, Dict[str, float]] = {}

            for row in rows:
                total_cost += row['cost_usd']
                total_input += row['input_tokens']
                total_output += row['output_tokens']

                model = row['model']
                if model not in by_model:
                    by_model[model] = {'cost': 0, 'input_tokens': 0, 'output_tokens': 0, 'requests': 0}
                by_model[model]['cost'] += row['cost_usd']
                by_model[model]['input_tokens'] += row['input_tokens']
                by_model[model]['output_tokens'] += row['output_tokens']
                by_model[model]['requests'] += 1

                day_key = datetime.fromtimestamp(row['timestamp']).strftime('%Y-%m-%d')
                if day_key not in by_day:
                    by_day[day_key] = {'cost': 0, 'requests': 0}
                by_day[day_key]['cost'] += row['cost_usd']
                by_day[day_key]['requests'] += 1

            request_count = len(rows)
            daily_breakdown = [
                {'date': d, 'cost': round(v['cost'], 6), 'requests': v['requests']}
                for d, v in sorted(by_day.items())
            ]

            return CostReport(
                user_id=user_id,
                period=f"last_{days}_days",
                start_time=start,
                end_time=now,
                total_cost=round(total_cost, 6),
                total_input_tokens=total_input,
                total_output_tokens=total_output,
                by_model={k: {kk: round(vv, 6) if isinstance(vv, float) else vv
                              for kk, vv in v.items()} for k, v in by_model.items()},
                daily_breakdown=daily_breakdown,
                avg_cost_per_request=round(total_cost / request_count, 8) if request_count > 0 else 0,
                request_count=request_count,
            )

    def get_top_users(self, days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top users by cost."""
        now = time.time()
        start = now - days * 86400

        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT user_id, SUM(cost_usd) as total_cost,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   COUNT(*) as request_count
                   FROM token_usage
                   WHERE timestamp >= ? AND timestamp <= ?
                   GROUP BY user_id
                   ORDER BY total_cost DESC
                   LIMIT ?""",
                (start, now, limit)
            ).fetchall()

            return [
                {
                    'user_id': row['user_id'],
                    'total_cost': round(row['total_cost'], 6),
                    'total_input_tokens': row['total_input'],
                    'total_output_tokens': row['total_output'],
                    'request_count': row['request_count'],
                }
                for row in rows
            ]

    def export_csv(self, user_id: str, days: int = 30) -> str:
        """Export usage data as CSV string."""
        now = time.time()
        start = now - days * 86400

        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM token_usage
                   WHERE user_id = ? AND timestamp >= ?
                   ORDER BY timestamp""",
                (user_id, start)
            ).fetchall()

        lines = ['timestamp,model,input_tokens,output_tokens,cost_usd,conversation_id']
        for row in rows:
            ts = datetime.fromtimestamp(row['timestamp']).isoformat()
            lines.append(f"{ts},{row['model']},{row['input_tokens']},"
                        f"{row['output_tokens']},{row['cost_usd']:.8f},{row['conversation_id']}")

        return '\n'.join(lines)

    def forecast_cost(self, user_id: str, days_ahead: int = 30) -> Dict[str, float]:
        """Forecast future costs based on recent usage patterns."""
        report = self.generate_report(user_id, days=7)
        if report.request_count == 0:
            return {'projected_cost': 0, 'daily_avg': 0, 'confidence': 0}

        daily_avg = report.total_cost / 7
        projected = daily_avg * days_ahead

        return {
            'projected_cost': round(projected, 4),
            'daily_avg': round(daily_avg, 6),
            'avg_per_request': report.avg_cost_per_request,
            'requests_per_day': round(report.request_count / 7, 1),
            'confidence': min(0.9, report.request_count / 100),
        }

    def _check_budget_alerts(self, user_id: str):
        """Check and create budget alerts."""
        with self._get_conn() as conn:
            budget = conn.execute(
                "SELECT * FROM user_budgets WHERE user_id = ?", (user_id,)
            ).fetchone()

            if not budget:
                return

            now = time.time()
            threshold = budget['alert_threshold']

            # Check daily
            if budget['daily_limit_usd'] > 0:
                daily_used = self._sum_cost(conn, user_id, self._day_start(now), now)
                pct = daily_used / budget['daily_limit_usd']
                if pct >= 1.0:
                    self._create_alert(conn, user_id, 'daily_exceeded',
                                       f"Daily budget exceeded: ${daily_used:.4f}/${budget['daily_limit_usd']:.2f}",
                                       daily_used, budget['daily_limit_usd'])
                elif pct >= threshold:
                    self._create_alert(conn, user_id, 'daily_warning',
                                       f"Daily budget {pct*100:.0f}%: ${daily_used:.4f}/${budget['daily_limit_usd']:.2f}",
                                       daily_used, budget['daily_limit_usd'])

    def _create_alert(self, conn, user_id: str, alert_type: str,
                      message: str, used: float, limit: float):
        conn.execute(
            """INSERT INTO cost_alerts (user_id, alert_type, message, budget_used, budget_limit, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, alert_type, message, used, limit, time.time())
        )

    def _sum_cost(self, conn, user_id: str, start: float, end: float) -> float:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM token_usage WHERE user_id = ? AND timestamp >= ? AND timestamp <= ?",
            (user_id, start, end)
        ).fetchone()
        return row['total']

    def _day_start(self, ts: float) -> float:
        dt = datetime.fromtimestamp(ts)
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start.timestamp()

    def _month_start(self, ts: float) -> float:
        dt = datetime.fromtimestamp(ts)
        month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return month_start.timestamp()
