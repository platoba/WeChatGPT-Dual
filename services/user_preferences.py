"""
用户偏好服务 - 个性化设置持久化

Features:
- Per-user language / engine / persona / temperature preferences
- Daily & monthly token budget limits
- Auto-translate toggle
- SQLite persistence with migration support
"""

import json
import time
import sqlite3
import logging
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

PREFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    language TEXT DEFAULT 'auto',
    engine TEXT DEFAULT '',
    persona TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    temperature REAL DEFAULT -1.0,
    max_tokens INTEGER DEFAULT -1,
    auto_translate INTEGER DEFAULT 0,
    daily_token_budget INTEGER DEFAULT -1,
    monthly_token_budget INTEGER DEFAULT -1,
    timezone TEXT DEFAULT 'UTC',
    response_style TEXT DEFAULT 'balanced',
    extra TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


@dataclass
class UserPrefs:
    """User preferences data class"""

    user_id: str
    language: str = "auto"
    engine: str = ""
    persona: str = ""
    system_prompt: str = ""
    temperature: float = -1.0
    max_tokens: int = -1
    auto_translate: bool = False
    daily_token_budget: int = -1
    monthly_token_budget: int = -1
    timezone: str = "UTC"
    response_style: str = "balanced"
    extra: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    VALID_STYLES = ("concise", "balanced", "detailed", "creative")
    VALID_LANGUAGES = (
        "auto", "en", "zh", "ja", "ko", "es", "fr", "de",
        "pt", "ru", "ar", "hi", "th", "vi", "id",
    )

    def effective_temperature(self, default: float = 0.7) -> float:
        """Return user temperature or default"""
        return self.temperature if self.temperature >= 0 else default

    def effective_max_tokens(self, default: int = 2000) -> int:
        """Return user max_tokens or default"""
        return self.max_tokens if self.max_tokens > 0 else default

    def has_budget(self) -> bool:
        """Check if any budget is configured"""
        return self.daily_token_budget > 0 or self.monthly_token_budget > 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["auto_translate"] = bool(d["auto_translate"])
        return d


class PreferenceService:
    """
    User preference manager with SQLite backend.

    Usage:
        svc = PreferenceService("bot.db")
        svc.set(user_id, language="zh", engine="claude")
        prefs = svc.get(user_id)
    """

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
            conn.executescript(PREFERENCES_SCHEMA)

    def get(self, user_id: str) -> UserPrefs:
        """Get user preferences, returns defaults if not set"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        if not row:
            return UserPrefs(user_id=user_id, created_at=0, updated_at=0)

        return UserPrefs(
            user_id=row["user_id"],
            language=row["language"],
            engine=row["engine"],
            persona=row["persona"],
            system_prompt=row["system_prompt"],
            temperature=row["temperature"],
            max_tokens=row["max_tokens"],
            auto_translate=bool(row["auto_translate"]),
            daily_token_budget=row["daily_token_budget"],
            monthly_token_budget=row["monthly_token_budget"],
            timezone=row["timezone"],
            response_style=row["response_style"],
            extra=json.loads(row["extra"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set(self, user_id: str, **kwargs) -> UserPrefs:
        """
        Set or update preferences for a user.

        Accepts any UserPrefs field as keyword argument.
        Only provided fields are updated; others keep current values.
        """
        self._validate_kwargs(kwargs)
        now = time.time()
        current = self.get(user_id)

        # Merge new values
        for key, value in kwargs.items():
            if key == "extra":
                current.extra.update(value)
            elif hasattr(current, key):
                setattr(current, key, value)

        current.updated_at = now

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_preferences
                   (user_id, language, engine, persona, system_prompt,
                    temperature, max_tokens, auto_translate,
                    daily_token_budget, monthly_token_budget,
                    timezone, response_style, extra, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                    language=excluded.language, engine=excluded.engine,
                    persona=excluded.persona, system_prompt=excluded.system_prompt,
                    temperature=excluded.temperature, max_tokens=excluded.max_tokens,
                    auto_translate=excluded.auto_translate,
                    daily_token_budget=excluded.daily_token_budget,
                    monthly_token_budget=excluded.monthly_token_budget,
                    timezone=excluded.timezone, response_style=excluded.response_style,
                    extra=excluded.extra, updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    current.language,
                    current.engine,
                    current.persona,
                    current.system_prompt,
                    current.temperature,
                    current.max_tokens,
                    int(current.auto_translate),
                    current.daily_token_budget,
                    current.monthly_token_budget,
                    current.timezone,
                    current.response_style,
                    json.dumps(current.extra, ensure_ascii=False),
                    current.created_at or now,
                    current.updated_at,
                ),
            )

        logger.info(f"Updated preferences for user {user_id}: {list(kwargs.keys())}")
        return self.get(user_id)

    def reset(self, user_id: str) -> bool:
        """Reset user preferences to defaults"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_preferences WHERE user_id = ?",
                (user_id,),
            )
            deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"Reset preferences for user {user_id}")
        return deleted

    def list_users(self, limit: int = 100) -> List[UserPrefs]:
        """List all users with preferences"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id FROM user_preferences ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get(row["user_id"]) for row in rows]

    def get_users_by_engine(self, engine: str) -> List[str]:
        """Find all users preferring a specific engine"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id FROM user_preferences WHERE engine = ?",
                (engine,),
            ).fetchall()
        return [row["user_id"] for row in rows]

    def get_users_by_language(self, language: str) -> List[str]:
        """Find all users with a specific language preference"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id FROM user_preferences WHERE language = ?",
                (language,),
            ).fetchall()
        return [row["user_id"] for row in rows]

    def bulk_set(self, updates: Dict[str, Dict[str, Any]]) -> int:
        """
        Bulk update preferences for multiple users.

        Args:
            updates: {user_id: {field: value, ...}}

        Returns:
            Number of users updated
        """
        count = 0
        for user_id, kwargs in updates.items():
            self.set(user_id, **kwargs)
            count += 1
        return count

    def export_all(self) -> List[Dict]:
        """Export all preferences as dicts"""
        users = self.list_users(limit=10000)
        return [u.to_dict() for u in users]

    @staticmethod
    def _validate_kwargs(kwargs: Dict):
        """Validate preference values"""
        if "language" in kwargs:
            if kwargs["language"] not in UserPrefs.VALID_LANGUAGES:
                raise ValueError(
                    f"Invalid language: {kwargs['language']}. "
                    f"Valid: {UserPrefs.VALID_LANGUAGES}"
                )

        if "response_style" in kwargs:
            if kwargs["response_style"] not in UserPrefs.VALID_STYLES:
                raise ValueError(
                    f"Invalid style: {kwargs['response_style']}. "
                    f"Valid: {UserPrefs.VALID_STYLES}"
                )

        if "temperature" in kwargs:
            t = kwargs["temperature"]
            if not isinstance(t, (int, float)) or (t >= 0 and (t < 0 or t > 2)):
                if t >= 0 and t > 2:
                    raise ValueError("Temperature must be between 0 and 2")

        if "daily_token_budget" in kwargs:
            b = kwargs["daily_token_budget"]
            if not isinstance(b, int):
                raise ValueError("daily_token_budget must be an integer")

        if "monthly_token_budget" in kwargs:
            b = kwargs["monthly_token_budget"]
            if not isinstance(b, int):
                raise ValueError("monthly_token_budget must be an integer")
