"""
Content Safety - Input/output content filtering and moderation

Features:
- Keyword blacklist with categories (hate, violence, spam, nsfw, pii)
- Regex pattern matching (emails, phone numbers, credit cards, SSN)
- Scoring system: each violation adds to risk score
- Configurable thresholds per category
- Action pipeline: warn → filter → block
- Audit log of all moderation actions
- PII detection and redaction
- Multi-language support (EN + CN)
"""

import json
import re
import time
import sqlite3
import hashlib
import logging
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, field
from contextlib import contextmanager
from enum import Enum

logger = logging.getLogger(__name__)

SAFETY_SCHEMA = """
CREATE TABLE IF NOT EXISTS moderation_rules (
    rule_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    pattern TEXT NOT NULL,
    severity REAL DEFAULT 0.5,
    action TEXT DEFAULT 'warn',
    enabled INTEGER DEFAULT 1,
    description TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_category ON moderation_rules(category);

CREATE TABLE IF NOT EXISTS moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    direction TEXT NOT NULL,
    violations TEXT DEFAULT '[]',
    risk_score REAL DEFAULT 0,
    action_taken TEXT DEFAULT 'pass',
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_modlog_user ON moderation_log(user_id);
CREATE INDEX IF NOT EXISTS idx_modlog_ts ON moderation_log(timestamp);

CREATE TABLE IF NOT EXISTS user_strikes (
    user_id TEXT PRIMARY KEY,
    strike_count INTEGER DEFAULT 0,
    last_strike REAL DEFAULT 0,
    is_muted INTEGER DEFAULT 0,
    mute_until REAL DEFAULT 0,
    notes TEXT DEFAULT ''
);
"""


class Action(Enum):
    PASS = 'pass'
    WARN = 'warn'
    FILTER = 'filter'
    BLOCK = 'block'
    MUTE = 'mute'


class Category(Enum):
    HATE = 'hate'
    VIOLENCE = 'violence'
    SPAM = 'spam'
    NSFW = 'nsfw'
    PII = 'pii'
    INJECTION = 'injection'
    CUSTOM = 'custom'


@dataclass
class Violation:
    rule_id: str
    category: str
    severity: float
    matched_text: str
    action: str
    description: str = ''


@dataclass
class ModerationResult:
    is_safe: bool
    risk_score: float
    action: str
    violations: List[Violation]
    filtered_content: Optional[str] = None
    message: str = ''


# Built-in patterns
PII_PATTERNS = {
    'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    'phone_us': r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
    'phone_cn': r'(?<!\d)1[3-9]\d{9}(?!\d)',
    'credit_card': r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
    'ssn': r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b',
    'id_card_cn': r'(?<!\d)\d{17}[\dXx](?!\d)',
    'ip_address': r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
    'bank_account': r'\b\d{16,19}\b',
}

INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?previous\s+instructions',
    r'forget\s+(all\s+)?your\s+(instructions|rules|training)',
    r'you\s+are\s+now\s+(?:DAN|jailbroken|unrestricted)',
    r'pretend\s+you\s+(?:are|have)\s+no\s+(?:rules|restrictions|limits)',
    r'system\s*prompt\s*[:=]',
    r'<\|?(?:system|im_start|endoftext)\|?>',
    r'\[INST\].*\[/INST\]',
]

# Keyword lists (minimal built-in, expandable)
BUILTIN_KEYWORDS = {
    'spam': [
        'buy now', 'click here', 'free money', 'act now', 'limited time',
        'make money fast', 'earn \\$\\d+', 'work from home', 'no experience needed',
        '免费领取', '点击链接', '加微信', '日赚', '躺赚', '零投资',
    ],
}


class ContentSafety:
    """Content safety filter with scoring, actions, and audit trail."""

    def __init__(self, db_path: str = "safety.db", default_threshold: float = 0.6):
        self.db_path = db_path
        self.default_threshold = default_threshold
        self._init_db()
        self._load_builtin_rules()

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
            conn.executescript(SAFETY_SCHEMA)

    def _load_builtin_rules(self):
        """Load built-in rules if not already in DB."""
        with self._get_conn() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM moderation_rules").fetchone()[0]
            if existing > 0:
                return

            now = time.time()
            rules = []

            # PII rules
            for name, pattern in PII_PATTERNS.items():
                rules.append((
                    f"pii_{name}", 'pii', 'regex', pattern, 0.7, 'filter',
                    f"PII detection: {name}", now
                ))

            # Injection rules
            for i, pattern in enumerate(INJECTION_PATTERNS):
                rules.append((
                    f"injection_{i}", 'injection', 'regex', pattern, 0.9, 'block',
                    f"Prompt injection pattern {i}", now
                ))

            # Spam keywords
            for i, kw in enumerate(BUILTIN_KEYWORDS.get('spam', [])):
                rules.append((
                    f"spam_{i}", 'spam', 'keyword', kw, 0.4, 'warn',
                    f"Spam keyword: {kw}", now
                ))

            for rule in rules:
                conn.execute(
                    """INSERT OR IGNORE INTO moderation_rules
                       (rule_id, category, rule_type, pattern, severity, action, description, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    rule
                )

    def check(self, content: str, user_id: str = '',
              direction: str = 'input') -> ModerationResult:
        """Check content for safety violations."""
        violations = []
        content_lower = content.lower()

        with self._get_conn() as conn:
            rules = conn.execute(
                "SELECT * FROM moderation_rules WHERE enabled = 1"
            ).fetchall()

            for rule in rules:
                matched = self._match_rule(rule, content, content_lower)
                if matched:
                    violations.append(Violation(
                        rule_id=rule['rule_id'],
                        category=rule['category'],
                        severity=rule['severity'],
                        matched_text=matched,
                        action=rule['action'],
                        description=rule['description'],
                    ))

        # Calculate risk score
        if violations:
            risk_score = min(1.0, sum(v.severity for v in violations))
        else:
            risk_score = 0.0

        # Determine action
        action = self._determine_action(violations, risk_score)

        # Generate filtered content if needed
        filtered = None
        if action in ('filter', 'warn') and violations:
            filtered = self._redact_content(content, violations)

        result = ModerationResult(
            is_safe=(action == 'pass'),
            risk_score=round(risk_score, 3),
            action=action,
            violations=violations,
            filtered_content=filtered,
            message=self._generate_message(action, violations),
        )

        # Log moderation
        if user_id:
            self._log_moderation(user_id, content, direction, violations, risk_score, action)

        return result

    def add_rule(self, rule_id: str, category: str, rule_type: str,
                 pattern: str, severity: float = 0.5, action: str = 'warn',
                 description: str = '') -> bool:
        """Add a custom moderation rule."""
        try:
            if rule_type == 'regex':
                re.compile(pattern)
        except re.error:
            return False

        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO moderation_rules
                   (rule_id, category, rule_type, pattern, severity, action, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rule_id, category, rule_type, pattern, severity, action, description, time.time())
            )
        return True

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a moderation rule."""
        with self._get_conn() as conn:
            result = conn.execute("DELETE FROM moderation_rules WHERE rule_id = ?", (rule_id,))
            return result.rowcount > 0

    def toggle_rule(self, rule_id: str, enabled: bool) -> bool:
        """Enable/disable a rule."""
        with self._get_conn() as conn:
            result = conn.execute(
                "UPDATE moderation_rules SET enabled = ? WHERE rule_id = ?",
                (1 if enabled else 0, rule_id)
            )
            return result.rowcount > 0

    def list_rules(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all moderation rules."""
        with self._get_conn() as conn:
            sql = "SELECT * FROM moderation_rules"
            params: list = []
            if category:
                sql += " WHERE category = ?"
                params.append(category)
            sql += " ORDER BY category, severity DESC"
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def get_user_strikes(self, user_id: str) -> Dict[str, Any]:
        """Get strike info for a user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_strikes WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return dict(row)
            return {'user_id': user_id, 'strike_count': 0, 'is_muted': False}

    def add_strike(self, user_id: str, reason: str = '') -> int:
        """Add a strike to a user. Returns new strike count."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO user_strikes (user_id, strike_count, last_strike, notes)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   strike_count = strike_count + 1,
                   last_strike = ?,
                   notes = notes || '\n' || ?""",
                (user_id, now, reason, now, reason)
            )
            row = conn.execute(
                "SELECT strike_count FROM user_strikes WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row[0]

    def mute_user(self, user_id: str, duration_seconds: int) -> None:
        """Mute a user for a duration."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO user_strikes (user_id, is_muted, mute_until)
                   VALUES (?, 1, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                   is_muted = 1, mute_until = ?""",
                (user_id, now + duration_seconds, now + duration_seconds)
            )

    def is_muted(self, user_id: str) -> bool:
        """Check if a user is currently muted."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT is_muted, mute_until FROM user_strikes WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            if not row or not row['is_muted']:
                return False
            if row['mute_until'] > 0 and row['mute_until'] < time.time():
                conn.execute(
                    "UPDATE user_strikes SET is_muted = 0 WHERE user_id = ?", (user_id,)
                )
                return False
            return True

    def get_moderation_stats(self, days: int = 7) -> Dict[str, Any]:
        """Get moderation statistics."""
        start = time.time() - days * 86400
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM moderation_log WHERE timestamp >= ?", (start,)
            ).fetchone()[0]

            by_action = {}
            rows = conn.execute(
                """SELECT action_taken, COUNT(*) as cnt
                   FROM moderation_log WHERE timestamp >= ?
                   GROUP BY action_taken""",
                (start,)
            ).fetchall()
            for row in rows:
                by_action[row['action_taken']] = row['cnt']

            avg_risk = conn.execute(
                "SELECT AVG(risk_score) FROM moderation_log WHERE timestamp >= ?",
                (start,)
            ).fetchone()[0]

            return {
                'period_days': days,
                'total_checks': total,
                'by_action': by_action,
                'avg_risk_score': round(avg_risk or 0, 4),
                'block_rate': round(by_action.get('block', 0) / total * 100, 2) if total > 0 else 0,
            }

    def redact_pii(self, content: str) -> str:
        """Redact all PII from content."""
        result = content
        for name, pattern in PII_PATTERNS.items():
            result = re.sub(pattern, f'[{name.upper()}_REDACTED]', result)
        return result

    def _match_rule(self, rule, content: str, content_lower: str) -> Optional[str]:
        """Check if a rule matches the content."""
        pattern = rule['pattern']
        rule_type = rule['rule_type']

        try:
            if rule_type == 'keyword':
                # Case-insensitive keyword/pattern search
                match = re.search(pattern, content_lower)
                if match:
                    return match.group()
            elif rule_type == 'regex':
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    return match.group()
            elif rule_type == 'exact':
                if pattern.lower() in content_lower:
                    return pattern
        except re.error:
            logger.warning(f"Invalid regex in rule {rule['rule_id']}: {pattern}")

        return None

    def _determine_action(self, violations: List[Violation], risk_score: float) -> str:
        """Determine the strictest action needed."""
        if not violations:
            return 'pass'

        action_priority = {'pass': 0, 'warn': 1, 'filter': 2, 'block': 3, 'mute': 4}
        max_action = 'warn'

        for v in violations:
            if action_priority.get(v.action, 0) > action_priority.get(max_action, 0):
                max_action = v.action

        if risk_score >= 0.9:
            max_action = 'block'
        elif risk_score >= self.default_threshold and max_action == 'warn':
            max_action = 'filter'

        return max_action

    def _redact_content(self, content: str, violations: List[Violation]) -> str:
        """Redact matched content."""
        result = content
        for v in violations:
            if v.matched_text and v.category == 'pii':
                result = result.replace(v.matched_text, f'[{v.category.upper()}_REDACTED]')
            elif v.matched_text:
                result = result.replace(v.matched_text, '[***]')
        return result

    def _generate_message(self, action: str, violations: List[Violation]) -> str:
        """Generate user-facing message."""
        if action == 'pass':
            return ''
        categories = set(v.category for v in violations)
        cat_str = ', '.join(categories)

        if action == 'block':
            return f"⛔ Content blocked due to policy violation ({cat_str})"
        elif action == 'filter':
            return f"⚠️ Some content was filtered ({cat_str})"
        elif action == 'warn':
            return f"💡 Content flagged for review ({cat_str})"
        return ''

    def _log_moderation(self, user_id: str, content: str, direction: str,
                        violations: List[Violation], risk_score: float, action: str):
        """Log moderation action."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        violations_json = json.dumps([
            {'rule_id': v.rule_id, 'category': v.category, 'severity': v.severity}
            for v in violations
        ])

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO moderation_log
                   (user_id, content_hash, direction, violations, risk_score, action_taken, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, content_hash, direction, violations_json, risk_score, action, time.time())
            )
