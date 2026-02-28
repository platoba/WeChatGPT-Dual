"""
用户反馈服务 - 收集用户对AI回复的评价，驱动质量提升

Features:
- Thumbs up/down rating on AI responses
- 1-5 star ratings with optional text feedback
- Correction submissions (user provides better answer)
- Quality metrics aggregation (satisfaction rate, avg rating)
- Per-model quality comparison
- Per-topic quality tracking
- Feedback-based prompt tuning suggestions
- Export feedback data (JSON/CSV)
- SQLite persistence with efficient indexing
"""

import json
import time
import sqlite3
import csv
import io
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)

FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    conversation_id TEXT DEFAULT '',
    model_used TEXT DEFAULT '',
    rating_type TEXT NOT NULL,
    rating_value INTEGER NOT NULL,
    comment TEXT DEFAULT '',
    correction TEXT DEFAULT '',
    original_response TEXT DEFAULT '',
    user_query TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_model ON feedback(model_used);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating_type, rating_value);
CREATE INDEX IF NOT EXISTS idx_feedback_time ON feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_conversation ON feedback(conversation_id);

CREATE TABLE IF NOT EXISTS feedback_summary (
    summary_id TEXT PRIMARY KEY,
    period TEXT NOT NULL,
    model TEXT DEFAULT '',
    total_feedback INTEGER DEFAULT 0,
    positive_count INTEGER DEFAULT 0,
    negative_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0.0,
    corrections_count INTEGER DEFAULT 0,
    top_issues TEXT DEFAULT '[]',
    computed_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_summary_period ON feedback_summary(period);
"""


class RatingType(str, Enum):
    THUMBS = "thumbs"       # 0=down, 1=up
    STARS = "stars"          # 1-5
    CORRECTION = "correction"  # always 0 (negative implicit)


@dataclass
class FeedbackEntry:
    feedback_id: str
    user_id: str
    message_id: str
    conversation_id: str
    model_used: str
    rating_type: str
    rating_value: int
    comment: str
    correction: str
    original_response: str
    user_query: str
    tags: List[str]
    metadata: Dict[str, Any]
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def is_positive(self) -> bool:
        if self.rating_type == RatingType.THUMBS:
            return self.rating_value == 1
        elif self.rating_type == RatingType.STARS:
            return self.rating_value >= 4
        return False

    def is_negative(self) -> bool:
        if self.rating_type == RatingType.THUMBS:
            return self.rating_value == 0
        elif self.rating_type == RatingType.STARS:
            return self.rating_value <= 2
        return True  # corrections are implicitly negative


@dataclass
class QualityMetrics:
    total_feedback: int = 0
    thumbs_up: int = 0
    thumbs_down: int = 0
    avg_star_rating: float = 0.0
    star_count: int = 0
    corrections_count: int = 0
    satisfaction_rate: float = 0.0  # percentage of positive feedback
    model_scores: Dict[str, float] = field(default_factory=dict)
    common_issues: List[str] = field(default_factory=list)
    trend: str = "stable"  # improving/declining/stable

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FeedbackService:
    """User feedback collection and quality metrics service."""

    def __init__(self, db_path: str = "feedback.db"):
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
            conn.executescript(FEEDBACK_SCHEMA)

    def _generate_id(self) -> str:
        import hashlib
        return hashlib.sha256(f"{time.time()}-{id(self)}".encode()).hexdigest()[:16]

    def submit_thumbs(
        self,
        user_id: str,
        message_id: str,
        thumbs_up: bool,
        comment: str = "",
        conversation_id: str = "",
        model_used: str = "",
        original_response: str = "",
        user_query: str = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackEntry:
        """Submit thumbs up/down feedback."""
        entry = FeedbackEntry(
            feedback_id=self._generate_id(),
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            model_used=model_used,
            rating_type=RatingType.THUMBS,
            rating_value=1 if thumbs_up else 0,
            comment=comment,
            correction="",
            original_response=original_response,
            user_query=user_query,
            tags=tags or [],
            metadata=metadata or {},
            created_at=time.time(),
        )
        self._save_entry(entry)
        logger.info(f"Thumbs {'up' if thumbs_up else 'down'} from {user_id} on {message_id}")
        return entry

    def submit_stars(
        self,
        user_id: str,
        message_id: str,
        stars: int,
        comment: str = "",
        conversation_id: str = "",
        model_used: str = "",
        original_response: str = "",
        user_query: str = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackEntry:
        """Submit 1-5 star rating."""
        if not 1 <= stars <= 5:
            raise ValueError(f"Stars must be 1-5, got {stars}")
        entry = FeedbackEntry(
            feedback_id=self._generate_id(),
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            model_used=model_used,
            rating_type=RatingType.STARS,
            rating_value=stars,
            comment=comment,
            correction="",
            original_response=original_response,
            user_query=user_query,
            tags=tags or [],
            metadata=metadata or {},
            created_at=time.time(),
        )
        self._save_entry(entry)
        logger.info(f"{stars}-star rating from {user_id} on {message_id}")
        return entry

    def submit_correction(
        self,
        user_id: str,
        message_id: str,
        correction: str,
        original_response: str = "",
        user_query: str = "",
        comment: str = "",
        conversation_id: str = "",
        model_used: str = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackEntry:
        """Submit a correction (better answer from user)."""
        if not correction.strip():
            raise ValueError("Correction text cannot be empty")
        entry = FeedbackEntry(
            feedback_id=self._generate_id(),
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            model_used=model_used,
            rating_type=RatingType.CORRECTION,
            rating_value=0,
            comment=comment,
            correction=correction,
            original_response=original_response,
            user_query=user_query,
            tags=tags or [],
            metadata=metadata or {},
            created_at=time.time(),
        )
        self._save_entry(entry)
        logger.info(f"Correction from {user_id} on {message_id}")
        return entry

    def _save_entry(self, entry: FeedbackEntry):
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO feedback
                   (feedback_id, user_id, message_id, conversation_id, model_used,
                    rating_type, rating_value, comment, correction, original_response,
                    user_query, tags, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.feedback_id, entry.user_id, entry.message_id,
                    entry.conversation_id, entry.model_used,
                    entry.rating_type, entry.rating_value,
                    entry.comment, entry.correction, entry.original_response,
                    entry.user_query, json.dumps(entry.tags),
                    json.dumps(entry.metadata), entry.created_at,
                ),
            )

    def get_feedback(self, feedback_id: str) -> Optional[FeedbackEntry]:
        """Get a single feedback entry."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM feedback WHERE feedback_id = ?", (feedback_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_entry(row)

    def get_feedback_for_message(self, message_id: str) -> List[FeedbackEntry]:
        """Get all feedback for a message."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE message_id = ? ORDER BY created_at DESC",
                (message_id,),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_user_feedback(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        rating_type: Optional[str] = None,
    ) -> List[FeedbackEntry]:
        """Get feedback from a specific user."""
        query = "SELECT * FROM feedback WHERE user_id = ?"
        params: list = [user_id]
        if rating_type:
            query += " AND rating_type = ?"
            params.append(rating_type)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_quality_metrics(
        self,
        model: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> QualityMetrics:
        """Compute quality metrics with optional filters."""
        metrics = QualityMetrics()

        where_clauses = []
        params: list = []
        if model:
            where_clauses.append("model_used = ?")
            params.append(model)
        if since:
            where_clauses.append("created_at >= ?")
            params.append(since)
        if until:
            where_clauses.append("created_at <= ?")
            params.append(until)

        where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        with self._get_conn() as conn:
            # Thumbs
            row = conn.execute(
                f"SELECT COUNT(*) as cnt, SUM(rating_value) as ups FROM feedback{where} AND rating_type = 'thumbs'"
                if where else
                "SELECT COUNT(*) as cnt, SUM(rating_value) as ups FROM feedback WHERE rating_type = 'thumbs'",
                params,
            ).fetchone()
            metrics.thumbs_up = int(row["ups"] or 0)
            metrics.thumbs_down = int(row["cnt"] or 0) - metrics.thumbs_up

            # Stars
            star_where = where + " AND rating_type = 'stars'" if where else " WHERE rating_type = 'stars'"
            row = conn.execute(
                f"SELECT COUNT(*) as cnt, AVG(rating_value) as avg_r FROM feedback{star_where}",
                params,
            ).fetchone()
            metrics.star_count = int(row["cnt"] or 0)
            metrics.avg_star_rating = round(float(row["avg_r"] or 0), 2)

            # Corrections
            corr_where = where + " AND rating_type = 'correction'" if where else " WHERE rating_type = 'correction'"
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM feedback{corr_where}",
                params,
            ).fetchone()
            metrics.corrections_count = int(row["cnt"] or 0)

            # Total
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM feedback{where}",
                params,
            ).fetchone()
            metrics.total_feedback = int(row["cnt"] or 0)

            # Satisfaction rate
            positive = metrics.thumbs_up
            negative = metrics.thumbs_down + metrics.corrections_count
            if metrics.star_count > 0:
                star_rows = conn.execute(
                    f"SELECT rating_value FROM feedback{star_where}",
                    params,
                ).fetchall()
                for sr in star_rows:
                    if sr["rating_value"] >= 4:
                        positive += 1
                    elif sr["rating_value"] <= 2:
                        negative += 1
            total_rated = positive + negative
            metrics.satisfaction_rate = round(
                (positive / total_rated * 100) if total_rated > 0 else 0, 1
            )

            # Per-model scores
            model_rows = conn.execute(
                f"""SELECT model_used, COUNT(*) as cnt,
                    SUM(CASE WHEN rating_type='thumbs' AND rating_value=1 THEN 1
                             WHEN rating_type='stars' AND rating_value>=4 THEN 1
                             ELSE 0 END) as pos
                    FROM feedback{where}
                    GROUP BY model_used
                    HAVING model_used != ''""",
                params,
            ).fetchall()
            for mr in model_rows:
                cnt = int(mr["cnt"])
                pos = int(mr["pos"] or 0)
                metrics.model_scores[mr["model_used"]] = round(
                    pos / cnt * 100 if cnt > 0 else 0, 1
                )

            # Common issues from negative feedback comments
            neg_where = where + " AND rating_value <= 1 AND comment != ''" if where else " WHERE rating_value <= 1 AND comment != ''"
            neg_rows = conn.execute(
                f"SELECT comment FROM feedback{neg_where} ORDER BY created_at DESC LIMIT 100",
                params,
            ).fetchall()
            issue_counts: Dict[str, int] = defaultdict(int)
            issue_keywords = [
                "wrong", "incorrect", "slow", "irrelevant", "repetitive",
                "too long", "too short", "confusing", "outdated", "hallucination",
                "错误", "不准确", "太慢", "无关", "重复", "太长", "太短", "混乱",
            ]
            for nr in neg_rows:
                comment_lower = nr["comment"].lower()
                for kw in issue_keywords:
                    if kw in comment_lower:
                        issue_counts[kw] += 1
            metrics.common_issues = sorted(
                issue_counts.keys(), key=lambda k: issue_counts[k], reverse=True
            )[:5]

        return metrics

    def get_model_comparison(
        self, since: Optional[float] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Compare quality metrics across models."""
        with self._get_conn() as conn:
            where = " WHERE created_at >= ?" if since else ""
            params = [since] if since else []
            rows = conn.execute(
                f"""SELECT model_used,
                    COUNT(*) as total,
                    SUM(CASE WHEN rating_type='thumbs' AND rating_value=1 THEN 1
                             WHEN rating_type='stars' AND rating_value>=4 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN rating_type='thumbs' AND rating_value=0 THEN 1
                             WHEN rating_type='stars' AND rating_value<=2 THEN 1
                             WHEN rating_type='correction' THEN 1 ELSE 0 END) as negative,
                    AVG(CASE WHEN rating_type='stars' THEN rating_value END) as avg_stars,
                    SUM(CASE WHEN rating_type='correction' THEN 1 ELSE 0 END) as corrections
                    FROM feedback{where}
                    GROUP BY model_used
                    HAVING model_used != ''
                    ORDER BY positive DESC""",
                params,
            ).fetchall()
        result = {}
        for r in rows:
            total = int(r["total"])
            pos = int(r["positive"] or 0)
            result[r["model_used"]] = {
                "total": total,
                "positive": pos,
                "negative": int(r["negative"] or 0),
                "satisfaction_rate": round(pos / total * 100, 1) if total > 0 else 0,
                "avg_stars": round(float(r["avg_stars"] or 0), 2),
                "corrections": int(r["corrections"] or 0),
            }
        return result

    def get_corrections(
        self,
        model: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, str]]:
        """Get correction entries for fine-tuning / prompt improvement."""
        query = "SELECT user_query, original_response, correction, model_used, comment FROM feedback WHERE rating_type = 'correction'"
        params: list = []
        if model:
            query += " AND model_used = ?"
            params.append(model)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "query": r["user_query"],
                "original": r["original_response"],
                "correction": r["correction"],
                "model": r["model_used"],
                "comment": r["comment"],
            }
            for r in rows
        ]

    def suggest_improvements(self) -> List[Dict[str, str]]:
        """Analyze feedback and suggest improvements."""
        metrics = self.get_quality_metrics()
        suggestions = []

        if metrics.satisfaction_rate < 70:
            suggestions.append({
                "priority": "high",
                "area": "overall_quality",
                "suggestion": f"Satisfaction rate is {metrics.satisfaction_rate}%. Review negative feedback and corrections.",
            })

        if metrics.corrections_count > 10:
            suggestions.append({
                "priority": "high",
                "area": "accuracy",
                "suggestion": f"{metrics.corrections_count} corrections submitted. Consider fine-tuning or updating knowledge base.",
            })

        if metrics.avg_star_rating > 0 and metrics.avg_star_rating < 3.5:
            suggestions.append({
                "priority": "medium",
                "area": "star_rating",
                "suggestion": f"Average star rating is {metrics.avg_star_rating}/5. Review low-rated responses.",
            })

        if metrics.common_issues:
            suggestions.append({
                "priority": "medium",
                "area": "common_issues",
                "suggestion": f"Top issues: {', '.join(metrics.common_issues[:3])}. Focus prompt engineering on these.",
            })

        model_comp = self.get_model_comparison()
        if len(model_comp) >= 2:
            sorted_models = sorted(
                model_comp.items(),
                key=lambda x: x[1]["satisfaction_rate"],
                reverse=True,
            )
            best = sorted_models[0]
            worst = sorted_models[-1]
            if best[1]["satisfaction_rate"] - worst[1]["satisfaction_rate"] > 20:
                suggestions.append({
                    "priority": "medium",
                    "area": "model_selection",
                    "suggestion": f"{best[0]} ({best[1]['satisfaction_rate']}%) significantly outperforms {worst[0]} ({worst[1]['satisfaction_rate']}%). Consider adjusting model routing.",
                })

        if not suggestions:
            suggestions.append({
                "priority": "low",
                "area": "status",
                "suggestion": "Quality metrics look good. Keep monitoring.",
            })

        return suggestions

    def export_json(
        self,
        since: Optional[float] = None,
        model: Optional[str] = None,
    ) -> str:
        """Export feedback as JSON."""
        entries = self._query_entries(since=since, model=model)
        return json.dumps(
            [e.to_dict() for e in entries], indent=2, ensure_ascii=False
        )

    def export_csv(
        self,
        since: Optional[float] = None,
        model: Optional[str] = None,
    ) -> str:
        """Export feedback as CSV."""
        entries = self._query_entries(since=since, model=model)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "feedback_id", "user_id", "message_id", "model_used",
            "rating_type", "rating_value", "comment", "correction", "created_at",
        ])
        for e in entries:
            writer.writerow([
                e.feedback_id, e.user_id, e.message_id, e.model_used,
                e.rating_type, e.rating_value, e.comment, e.correction,
                e.created_at,
            ])
        return output.getvalue()

    def _query_entries(
        self,
        since: Optional[float] = None,
        model: Optional[str] = None,
        limit: int = 1000,
    ) -> List[FeedbackEntry]:
        where_parts = []
        params: list = []
        if since:
            where_parts.append("created_at >= ?")
            params.append(since)
        if model:
            where_parts.append("model_used = ?")
            params.append(model)
        where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback{where} ORDER BY created_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def delete_feedback(self, feedback_id: str) -> bool:
        """Delete a feedback entry."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM feedback WHERE feedback_id = ?", (feedback_id,)
            )
            return cursor.rowcount > 0

    def count_feedback(
        self,
        user_id: Optional[str] = None,
        rating_type: Optional[str] = None,
    ) -> int:
        """Count feedback entries."""
        where_parts = []
        params: list = []
        if user_id:
            where_parts.append("user_id = ?")
            params.append(user_id)
        if rating_type:
            where_parts.append("rating_type = ?")
            params.append(rating_type)
        where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
        with self._get_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM feedback{where}", params
            ).fetchone()
        return int(row["cnt"])

    def _row_to_entry(self, row: sqlite3.Row) -> FeedbackEntry:
        return FeedbackEntry(
            feedback_id=row["feedback_id"],
            user_id=row["user_id"],
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            model_used=row["model_used"],
            rating_type=row["rating_type"],
            rating_value=row["rating_value"],
            comment=row["comment"],
            correction=row["correction"],
            original_response=row["original_response"],
            user_query=row["user_query"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
        )
