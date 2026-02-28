"""
Notification & Alert Service - Configurable alerts for system events

Features:
- Alert rules engine (threshold-based triggers)
- Multiple severity levels (info, warning, critical)
- Cooldown periods (prevent alert storms)
- Alert channels (log, callback, webhook placeholder)
- Alert history with SQLite persistence
- Rule templates for common scenarios (error rate, latency, usage)
- Auto-resolve alerts when conditions clear
- Alert aggregation (group similar alerts)
"""

import json
import time
import sqlite3
import hashlib
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from contextlib import contextmanager
from enum import Enum

logger = logging.getLogger(__name__)

ALERT_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_rules (
    rule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    metric TEXT NOT NULL,
    condition TEXT NOT NULL,
    threshold REAL NOT NULL,
    severity TEXT DEFAULT 'warning',
    cooldown_sec INTEGER DEFAULT 300,
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alert_history (
    alert_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    metric_value REAL DEFAULT 0,
    threshold REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    fired_at REAL NOT NULL,
    resolved_at REAL DEFAULT 0,
    acknowledged INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (rule_id) REFERENCES alert_rules(rule_id)
);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alert_history(status);
CREATE INDEX IF NOT EXISTS idx_alert_rule ON alert_history(rule_id);
CREATE INDEX IF NOT EXISTS idx_alert_time ON alert_history(fired_at DESC);

CREATE TABLE IF NOT EXISTS alert_suppressions (
    rule_id TEXT PRIMARY KEY,
    suppressed_until REAL NOT NULL,
    reason TEXT DEFAULT ''
);
"""


class Severity(Enum):
    INFO = 'info'
    WARNING = 'warning'
    CRITICAL = 'critical'

    @staticmethod
    def from_str(s: str) -> 'Severity':
        return {'info': Severity.INFO, 'warning': Severity.WARNING,
                'critical': Severity.CRITICAL}.get(s, Severity.WARNING)


class Condition(Enum):
    GREATER_THAN = 'gt'
    LESS_THAN = 'lt'
    GREATER_EQUAL = 'gte'
    LESS_EQUAL = 'lte'
    EQUALS = 'eq'
    NOT_EQUALS = 'neq'

    def evaluate(self, value: float, threshold: float) -> bool:
        if self == Condition.GREATER_THAN:
            return value > threshold
        elif self == Condition.LESS_THAN:
            return value < threshold
        elif self == Condition.GREATER_EQUAL:
            return value >= threshold
        elif self == Condition.LESS_EQUAL:
            return value <= threshold
        elif self == Condition.EQUALS:
            return abs(value - threshold) < 1e-9
        elif self == Condition.NOT_EQUALS:
            return abs(value - threshold) >= 1e-9
        return False


@dataclass
class AlertRule:
    rule_id: str
    name: str
    metric: str
    condition: str
    threshold: float
    severity: str = 'warning'
    cooldown_sec: int = 300
    enabled: bool = True
    description: str = ''


@dataclass
class Alert:
    alert_id: str
    rule_id: str
    severity: str
    message: str
    metric_value: float
    threshold: float
    status: str = 'active'  # active, resolved, acknowledged
    fired_at: float = 0
    resolved_at: float = 0
    acknowledged: bool = False
    metadata: Dict = field(default_factory=dict)


class NotificationService:
    """Alert engine with rules, cooldowns, and history tracking."""

    def __init__(self, db_path: str = "alerts.db"):
        self.db_path = db_path
        self._callbacks: List[Callable[[Alert], None]] = []
        self._last_fired: Dict[str, float] = {}
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
            conn.executescript(ALERT_SCHEMA)

    def register_callback(self, callback: Callable[[Alert], None]):
        """Register a callback for alert notifications."""
        self._callbacks.append(callback)

    # ── Rule Management ─────────────────────────────────────────────

    def add_rule(self, name: str, metric: str, condition: str, threshold: float,
                 severity: str = 'warning', cooldown_sec: int = 300,
                 description: str = '') -> AlertRule:
        """Add a new alert rule."""
        rule_id = hashlib.md5(f"{name}:{metric}:{condition}:{threshold}".encode()).hexdigest()[:12]

        rule = AlertRule(
            rule_id=rule_id, name=name, metric=metric,
            condition=condition, threshold=threshold,
            severity=severity, cooldown_sec=cooldown_sec,
            description=description,
        )

        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO alert_rules
                (rule_id, name, metric, condition, threshold, severity, cooldown_sec, enabled, created_at, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (rule.rule_id, rule.name, rule.metric, rule.condition,
                  rule.threshold, rule.severity, rule.cooldown_sec,
                  time.time(), rule.description))

        logger.info(f"Alert rule added: {name} ({metric} {condition} {threshold})")
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove an alert rule."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM alert_rules WHERE rule_id=?", (rule_id,))
            return cursor.rowcount > 0

    def enable_rule(self, rule_id: str, enabled: bool = True):
        """Enable or disable a rule."""
        with self._get_conn() as conn:
            conn.execute("UPDATE alert_rules SET enabled=? WHERE rule_id=?",
                         (int(enabled), rule_id))

    def get_rules(self, enabled_only: bool = True) -> List[AlertRule]:
        """Get all alert rules."""
        with self._get_conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM alert_rules WHERE enabled=1"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM alert_rules").fetchall()

            return [AlertRule(
                rule_id=r['rule_id'], name=r['name'], metric=r['metric'],
                condition=r['condition'], threshold=r['threshold'],
                severity=r['severity'], cooldown_sec=r['cooldown_sec'],
                enabled=bool(r['enabled']), description=r['description'],
            ) for r in rows]

    # ── Pre-built Rule Templates ────────────────────────────────────

    def add_error_rate_rule(self, threshold: float = 0.1, severity: str = 'critical'):
        """Alert when error rate exceeds threshold (0.1 = 10%)."""
        return self.add_rule(
            name="High Error Rate",
            metric="error_rate",
            condition="gt",
            threshold=threshold,
            severity=severity,
            cooldown_sec=600,
            description=f"Error rate exceeds {threshold*100:.0f}%",
        )

    def add_latency_rule(self, threshold_ms: float = 5000, severity: str = 'warning'):
        """Alert when avg latency exceeds threshold."""
        return self.add_rule(
            name="High Latency",
            metric="avg_latency_ms",
            condition="gt",
            threshold=threshold_ms,
            severity=severity,
            cooldown_sec=300,
            description=f"Average latency exceeds {threshold_ms}ms",
        )

    def add_usage_quota_rule(self, threshold: float = 0.9, severity: str = 'warning'):
        """Alert when usage quota exceeds threshold (0.9 = 90%)."""
        return self.add_rule(
            name="Usage Quota Warning",
            metric="usage_quota_ratio",
            condition="gt",
            threshold=threshold,
            severity=severity,
            cooldown_sec=3600,
            description=f"Usage quota exceeds {threshold*100:.0f}%",
        )

    def add_engine_failure_rule(self, severity: str = 'critical'):
        """Alert when an AI engine fails."""
        return self.add_rule(
            name="Engine Failure",
            metric="engine_failures",
            condition="gt",
            threshold=0,
            severity=severity,
            cooldown_sec=300,
            description="AI engine encountered failures",
        )

    # ── Alert Evaluation ────────────────────────────────────────────

    def evaluate(self, metrics: Dict[str, float]) -> List[Alert]:
        """Evaluate all rules against current metrics. Returns fired alerts."""
        now = time.time()
        fired = []

        rules = self.get_rules(enabled_only=True)
        for rule in rules:
            if rule.metric not in metrics:
                continue

            value = metrics[rule.metric]
            try:
                condition = Condition(rule.condition)
            except ValueError:
                logger.warning(f"Unknown condition: {rule.condition}")
                continue

            if condition.evaluate(value, rule.threshold):
                # Check cooldown
                last = self._last_fired.get(rule.rule_id, 0)
                if now - last < rule.cooldown_sec:
                    continue

                # Check suppression
                if self._is_suppressed(rule.rule_id, now):
                    continue

                # Fire alert
                alert = self._fire_alert(rule, value, now)
                fired.append(alert)
                self._last_fired[rule.rule_id] = now
            else:
                # Auto-resolve if condition clears
                self._auto_resolve(rule.rule_id, now)

        return fired

    def _fire_alert(self, rule: AlertRule, value: float, now: float) -> Alert:
        """Create and store a new alert."""
        alert_id = hashlib.md5(
            f"{rule.rule_id}:{now}".encode()
        ).hexdigest()[:16]

        message = f"[{rule.severity.upper()}] {rule.name}: " \
                  f"{rule.metric}={value:.3f} (threshold: {rule.condition} {rule.threshold})"

        alert = Alert(
            alert_id=alert_id,
            rule_id=rule.rule_id,
            severity=rule.severity,
            message=message,
            metric_value=value,
            threshold=rule.threshold,
            status='active',
            fired_at=now,
        )

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO alert_history
                (alert_id, rule_id, severity, message, metric_value, threshold, status, fired_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            """, (alert.alert_id, alert.rule_id, alert.severity,
                  alert.message, alert.metric_value, alert.threshold, alert.fired_at))

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

        logger.warning(f"Alert fired: {alert.message}")
        return alert

    def _auto_resolve(self, rule_id: str, now: float):
        """Auto-resolve active alerts for a rule when condition clears."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE alert_history SET status='resolved', resolved_at=?
                WHERE rule_id=? AND status='active'
            """, (now, rule_id))

    def _is_suppressed(self, rule_id: str, now: float) -> bool:
        """Check if a rule is suppressed."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT suppressed_until FROM alert_suppressions WHERE rule_id=?",
                (rule_id,)
            ).fetchone()
            if row and row['suppressed_until'] > now:
                return True
            # Clean expired
            if row:
                conn.execute("DELETE FROM alert_suppressions WHERE rule_id=?", (rule_id,))
            return False

    # ── Alert Management ────────────────────────────────────────────

    def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE alert_history SET acknowledged=1, status='acknowledged'
                WHERE alert_id=? AND status='active'
            """, (alert_id,))
            return cursor.rowcount > 0

    def resolve(self, alert_id: str) -> bool:
        """Manually resolve an alert."""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE alert_history SET status='resolved', resolved_at=?
                WHERE alert_id=?
            """, (time.time(), alert_id))
            return cursor.rowcount > 0

    def suppress_rule(self, rule_id: str, duration_sec: int = 3600,
                      reason: str = '') -> bool:
        """Suppress a rule for a duration."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO alert_suppressions (rule_id, suppressed_until, reason)
                VALUES (?, ?, ?)
            """, (rule_id, time.time() + duration_sec, reason))
        return True

    def get_active_alerts(self) -> List[Alert]:
        """Get all currently active alerts."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM alert_history WHERE status='active'
                ORDER BY fired_at DESC
            """).fetchall()
            return [self._row_to_alert(r) for r in rows]

    def get_alert_history(self, limit: int = 50,
                          severity: Optional[str] = None) -> List[Alert]:
        """Get alert history."""
        with self._get_conn() as conn:
            if severity:
                rows = conn.execute("""
                    SELECT * FROM alert_history WHERE severity=?
                    ORDER BY fired_at DESC LIMIT ?
                """, (severity, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM alert_history
                    ORDER BY fired_at DESC LIMIT ?
                """, (limit,)).fetchall()
            return [self._row_to_alert(r) for r in rows]

    def get_alert_stats(self) -> Dict[str, Any]:
        """Get alert statistics."""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM alert_history WHERE status='active'"
            ).fetchone()[0]
            by_severity = {}
            for row in conn.execute("""
                SELECT severity, COUNT(*) as cnt FROM alert_history
                GROUP BY severity
            """).fetchall():
                by_severity[row['severity']] = row['cnt']

            rules_count = conn.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0]
            enabled_count = conn.execute(
                "SELECT COUNT(*) FROM alert_rules WHERE enabled=1"
            ).fetchone()[0]

            return {
                'total_alerts': total,
                'active_alerts': active,
                'by_severity': by_severity,
                'total_rules': rules_count,
                'enabled_rules': enabled_count,
            }

    def _row_to_alert(self, row) -> Alert:
        return Alert(
            alert_id=row['alert_id'],
            rule_id=row['rule_id'],
            severity=row['severity'],
            message=row['message'],
            metric_value=row['metric_value'],
            threshold=row['threshold'],
            status=row['status'],
            fired_at=row['fired_at'],
            resolved_at=row['resolved_at'],
            acknowledged=bool(row['acknowledged']),
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
        )

    # ── Report ──────────────────────────────────────────────────────

    def generate_text_report(self) -> str:
        """Generate human-readable alert report."""
        stats = self.get_alert_stats()
        active = self.get_active_alerts()
        recent = self.get_alert_history(limit=10)

        lines = [
            "═══ Alert & Notification Report ═══", "",
            f"Total Alerts: {stats['total_alerts']}",
            f"Active Alerts: {stats['active_alerts']}",
            f"Rules: {stats['enabled_rules']}/{stats['total_rules']} enabled",
        ]

        if stats['by_severity']:
            lines.append("\nBy Severity:")
            icons = {'info': 'ℹ️', 'warning': '⚠️', 'critical': '🚨'}
            for sev, count in sorted(stats['by_severity'].items()):
                icon = icons.get(sev, '❓')
                lines.append(f"  {icon} {sev}: {count}")

        if active:
            lines.append(f"\n🔴 Active Alerts ({len(active)}):")
            for a in active:
                age = time.time() - a.fired_at
                age_str = f"{age/3600:.1f}h" if age > 3600 else f"{age/60:.0f}m"
                lines.append(f"  • [{a.severity}] {a.message} (age: {age_str})")

        if recent:
            lines.append(f"\nRecent History:")
            for a in recent[:5]:
                status_icon = {'active': '🔴', 'resolved': '✅', 'acknowledged': '👁️'}.get(a.status, '❓')
                lines.append(f"  {status_icon} {a.message}")

        lines.append("\n═══════════════════════════════════")
        return '\n'.join(lines)
