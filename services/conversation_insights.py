"""
Conversation Insights Engine - Sentiment, topics, engagement scoring

Features:
- Sentiment analysis (rule-based, no external deps)
- Topic extraction (keyword frequency + co-occurrence)
- Conversation quality scoring (response time, depth, engagement)
- User engagement metrics (activity patterns, message frequency)
- Conversation flow analysis (question/answer ratio, topic shifts)
- Per-user and global insights dashboard
- Trend detection (sentiment shifts, topic popularity changes)
"""

import json
import re
import time
import sqlite3
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import Counter
from contextlib import contextmanager

logger = logging.getLogger(__name__)

INSIGHTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_sentiments (
    message_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    sentiment_score REAL NOT NULL,
    sentiment_label TEXT NOT NULL,
    confidence REAL NOT NULL,
    topics TEXT DEFAULT '[]',
    message_type TEXT DEFAULT 'user',
    word_count INTEGER DEFAULT 0,
    has_question INTEGER DEFAULT 0,
    has_emoji INTEGER DEFAULT 0,
    language TEXT DEFAULT 'en',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sentiment_user ON message_sentiments(user_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_label ON message_sentiments(sentiment_label);
CREATE INDEX IF NOT EXISTS idx_sentiment_time ON message_sentiments(created_at);

CREATE TABLE IF NOT EXISTS topic_tracking (
    topic TEXT NOT NULL,
    user_id TEXT NOT NULL,
    count INTEGER DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    avg_sentiment REAL DEFAULT 0.0,
    PRIMARY KEY (topic, user_id)
);
CREATE INDEX IF NOT EXISTS idx_topic_count ON topic_tracking(count DESC);

CREATE TABLE IF NOT EXISTS engagement_metrics (
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    avg_sentiment REAL DEFAULT 0.0,
    avg_word_count REAL DEFAULT 0.0,
    question_count INTEGER DEFAULT 0,
    topic_diversity INTEGER DEFAULT 0,
    session_count INTEGER DEFAULT 1,
    total_duration_sec REAL DEFAULT 0.0,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS conversation_quality (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    avg_response_time REAL DEFAULT 0.0,
    topic_depth REAL DEFAULT 0.0,
    sentiment_variance REAL DEFAULT 0.0,
    quality_score REAL DEFAULT 0.0,
    resolution_status TEXT DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_quality_user ON conversation_quality(user_id);
CREATE INDEX IF NOT EXISTS idx_quality_score ON conversation_quality(quality_score DESC);
"""

# Sentiment lexicons (EN + CN)
POSITIVE_WORDS_EN = {
    'good', 'great', 'excellent', 'amazing', 'wonderful', 'fantastic', 'awesome',
    'love', 'like', 'happy', 'glad', 'pleased', 'enjoy', 'perfect', 'beautiful',
    'brilliant', 'outstanding', 'superb', 'nice', 'best', 'better', 'helpful',
    'thank', 'thanks', 'appreciate', 'cool', 'impressive', 'incredible', 'yes',
    'agree', 'right', 'correct', 'exactly', 'definitely', 'absolutely', 'sure',
    'fine', 'okay', 'well', 'wow', 'fun', 'interesting', 'exciting', 'useful',
    'recommend', 'favorite', 'success', 'succeed', 'win', 'won', 'achieve',
    'progress', 'improve', 'solved', 'fixed', 'works', 'working', 'done',
}

NEGATIVE_WORDS_EN = {
    'bad', 'terrible', 'horrible', 'awful', 'poor', 'worst', 'worse', 'hate',
    'dislike', 'angry', 'annoyed', 'frustrated', 'disappointed', 'sad', 'unhappy',
    'wrong', 'error', 'bug', 'broken', 'fail', 'failed', 'failure', 'crash',
    'issue', 'problem', 'trouble', 'difficult', 'hard', 'confusing', 'confused',
    'slow', 'ugly', 'useless', 'waste', 'never', 'no', 'not', 'cannot', "can't",
    "don't", "won't", "shouldn't", "couldn't", 'impossible', 'stuck', 'lost',
    'missing', 'broken', 'sucks', 'stupid', 'boring', 'annoying', 'pain',
    'unfortunately', 'sadly', 'sorry', 'regret', 'complaint', 'complain',
}

INTENSIFIERS = {
    'very': 1.5, 'really': 1.5, 'so': 1.3, 'extremely': 2.0, 'absolutely': 1.8,
    'totally': 1.5, 'completely': 1.5, 'quite': 1.2, 'pretty': 1.2, 'rather': 1.1,
    'incredibly': 1.8, 'amazingly': 1.8, 'super': 1.5, 'truly': 1.3,
    '非常': 1.5, '极其': 2.0, '特别': 1.5, '真的': 1.3, '太': 1.5,
    '十分': 1.5, '相当': 1.2, '超': 1.5, '超级': 1.8,
}

NEGATORS = {'not', "n't", 'no', 'never', 'neither', 'nor', 'hardly', 'barely',
            '不', '没', '没有', '别', '勿', '未', '非', '莫'}

POSITIVE_WORDS_CN = {
    '好', '棒', '优秀', '厉害', '强', '赞', '喜欢', '爱', '开心', '高兴',
    '满意', '感谢', '谢谢', '完美', '漂亮', '精彩', '出色', '了不起', '方便',
    '有用', '推荐', '成功', '解决', '进步', '改善', '舒服', '酷', '牛',
    '给力', '可以', '不错', '行', '妙', '绝', '正确', '对', '是的', '好的',
    '明白', '清楚', '简单', '快', '顺利', '有效', '靠谱', '稳定',
}

NEGATIVE_WORDS_CN = {
    '差', '烂', '垃圾', '讨厌', '恨', '生气', '愤怒', '失望', '伤心', '难过',
    '难受', '痛苦', '无聊', '慢', '卡', '崩溃', '错误', '问题', '故障', '异常',
    '丑', '废', '渣', '坑', '骗', '假', '烦', '累', '难', '糟糕', '恶心',
    '不行', '不好', '不对', '不能', '不会', '不可以', '麻烦', '复杂', '混乱',
}

QUESTION_PATTERNS = [
    r'\?', r'？', r'\bwhat\b', r'\bhow\b', r'\bwhy\b', r'\bwhen\b', r'\bwhere\b',
    r'\bwhich\b', r'\bwho\b', r'\bcan\b.*\?', r'\bis\b.*\?', r'\bdo\b.*\?',
    r'什么', r'怎么', r'为什么', r'哪里', r'哪个', r'谁', r'几', r'多少',
    r'是否', r'能否', r'可以吗', r'吗$', r'呢$', r'嘛$',
]

EMOJI_PATTERN = re.compile(
    r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
    r'\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF'
    r'\U00002702-\U000027B0\U000024C2-\U0001F251'
    r'\U0001f900-\U0001f9FF\U0001fa00-\U0001fa6f'
    r'\U0001fa70-\U0001faff]+'
)


@dataclass
class SentimentResult:
    score: float  # -1.0 to 1.0
    label: str  # 'positive', 'negative', 'neutral'
    confidence: float  # 0.0 to 1.0
    positive_terms: List[str] = field(default_factory=list)
    negative_terms: List[str] = field(default_factory=list)


@dataclass
class TopicInfo:
    topic: str
    count: int
    avg_sentiment: float
    first_seen: float
    last_seen: float
    trending: bool = False


@dataclass
class EngagementReport:
    user_id: str
    period: str
    total_messages: int
    avg_messages_per_day: float
    avg_sentiment: float
    sentiment_trend: str  # 'improving', 'declining', 'stable'
    top_topics: List[TopicInfo]
    peak_hours: List[int]
    avg_word_count: float
    question_ratio: float
    quality_score: float


@dataclass
class ConversationSummary:
    conversation_id: str
    user_id: str
    duration_sec: float
    message_count: int
    avg_response_time: float
    sentiment_arc: List[float]  # sentiment over time
    topics: List[str]
    quality_score: float
    resolution: str


class ConversationInsights:
    """Conversation analytics engine with sentiment, topics, and engagement."""

    def __init__(self, db_path: str = "insights.db"):
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
            conn.executescript(INSIGHTS_SCHEMA)

    # ── Sentiment Analysis ──────────────────────────────────────────

    def analyze_sentiment(self, text: str) -> SentimentResult:
        """Analyze sentiment of a text message."""
        if not text or not text.strip():
            return SentimentResult(score=0.0, label='neutral', confidence=0.5)

        text_lower = text.lower()
        # Tokenize: split CJK into individual chars + bigrams for lexicon matching
        raw_words = re.findall(r'[\w\u4e00-\u9fff]+|[^\s\w]', text_lower)
        words = []
        for w in raw_words:
            if re.match(r'^[\u4e00-\u9fff]+$', w):
                # Pure Chinese: emit individual chars + consecutive bigrams
                for ch in w:
                    words.append(ch)
                for i in range(len(w) - 1):
                    words.append(w[i:i+2])
            else:
                words.append(w)

        pos_score = 0.0
        neg_score = 0.0
        pos_terms = []
        neg_terms = []
        multiplier = 1.0
        negate = False

        for i, word in enumerate(words):
            # Check intensifiers
            if word in INTENSIFIERS:
                multiplier = INTENSIFIERS[word]
                continue

            # Check negators
            if word in NEGATORS:
                negate = True
                continue

            # Check positive
            if word in POSITIVE_WORDS_EN or word in POSITIVE_WORDS_CN:
                if negate:
                    neg_score += 0.7 * multiplier
                    neg_terms.append(f'not-{word}')
                else:
                    pos_score += 1.0 * multiplier
                    pos_terms.append(word)
                negate = False
                multiplier = 1.0
                continue

            # Check negative
            if word in NEGATIVE_WORDS_EN or word in NEGATIVE_WORDS_CN:
                if negate:
                    pos_score += 0.5 * multiplier  # negated negative = weakly positive
                    pos_terms.append(f'not-{word}')
                else:
                    neg_score += 1.0 * multiplier
                    neg_terms.append(word)
                negate = False
                multiplier = 1.0
                continue

            # Reset modifiers after non-sentiment word
            if word.isalpha() or re.match(r'[\u4e00-\u9fff]', word):
                negate = False
                multiplier = 1.0

        # Emoji bonus
        emojis = EMOJI_PATTERN.findall(text)
        if emojis:
            pos_score += len(emojis) * 0.3  # emojis generally add positivity

        # Exclamation marks
        excl_count = text.count('!') + text.count('！')
        if excl_count > 0:
            if pos_score > neg_score:
                pos_score *= (1 + 0.1 * min(excl_count, 3))
            elif neg_score > pos_score:
                neg_score *= (1 + 0.1 * min(excl_count, 3))

        # Compute final score
        total = pos_score + neg_score
        if total == 0:
            return SentimentResult(score=0.0, label='neutral', confidence=0.5)

        raw_score = (pos_score - neg_score) / max(total, 1)
        # Normalize to [-1, 1]
        score = max(-1.0, min(1.0, raw_score))
        confidence = min(1.0, total / (len(words) * 0.3 + 1))

        if score > 0.15:
            label = 'positive'
        elif score < -0.15:
            label = 'negative'
        else:
            label = 'neutral'

        return SentimentResult(
            score=round(score, 3),
            label=label,
            confidence=round(confidence, 3),
            positive_terms=pos_terms,
            negative_terms=neg_terms,
        )

    # ── Topic Extraction ────────────────────────────────────────────

    def extract_topics(self, text: str, max_topics: int = 5) -> List[str]:
        """Extract key topics from a message using keyword frequency."""
        if not text or not text.strip():
            return []

        text_lower = text.lower()

        # Extract meaningful words
        words = re.findall(r'[a-z]{3,}|[\u4e00-\u9fff]{2,}', text_lower)

        stop_words = {
            'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
            'had', 'her', 'was', 'one', 'our', 'out', 'has', 'have', 'been',
            'would', 'could', 'should', 'will', 'just', 'that', 'this', 'with',
            'from', 'they', 'what', 'which', 'when', 'where', 'how', 'who',
            'about', 'each', 'make', 'like', 'into', 'than', 'then', 'them',
            'some', 'very', 'also', 'more', 'much', 'most', 'only', 'over',
            'such', 'take', 'does', 'these', 'other', 'there', 'their', 'here',
        }

        filtered = [w for w in words if w not in stop_words]
        if not filtered:
            return []

        # Count frequency
        counts = Counter(filtered)

        # Boost multi-char Chinese words and longer English words
        scored = {}
        for word, count in counts.items():
            if re.match(r'[\u4e00-\u9fff]', word):
                scored[word] = count * (1 + len(word) * 0.3)
            else:
                scored[word] = count * (1 + len(word) * 0.1)

        # Return top topics by score
        sorted_topics = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        return [t[0] for t in sorted_topics[:max_topics]]

    # ── Language Detection ──────────────────────────────────────────

    def detect_language(self, text: str) -> str:
        """Simple language detection (en/zh/mixed)."""
        if not text:
            return 'unknown'
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_chars = len(re.findall(r'[a-zA-Z]', text))
        total = cn_chars + en_chars
        if total == 0:
            return 'unknown'
        cn_ratio = cn_chars / total
        if cn_ratio > 0.5:
            return 'zh'
        elif cn_ratio > 0.2:
            return 'mixed'
        return 'en'

    def has_question(self, text: str) -> bool:
        """Detect if a message contains a question."""
        for pattern in QUESTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    # ── Storage & Tracking ──────────────────────────────────────────

    def record_message(self, message_id: str, user_id: str, text: str,
                       message_type: str = 'user') -> SentimentResult:
        """Analyze and record a message's sentiment + topics."""
        sentiment = self.analyze_sentiment(text)
        topics = self.extract_topics(text)
        lang = self.detect_language(text)
        is_question = self.has_question(text)
        has_emoji = bool(EMOJI_PATTERN.search(text or ''))
        word_count = len(re.findall(r'[\w\u4e00-\u9fff]+', text or ''))
        now = time.time()

        with self._get_conn() as conn:
            # Store sentiment
            conn.execute("""
                INSERT OR REPLACE INTO message_sentiments
                (message_id, user_id, sentiment_score, sentiment_label, confidence,
                 topics, message_type, word_count, has_question, has_emoji, language, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (message_id, user_id, sentiment.score, sentiment.label,
                  sentiment.confidence, json.dumps(topics), message_type,
                  word_count, int(is_question), int(has_emoji), lang, now))

            # Update topic tracking
            for topic in topics:
                existing = conn.execute(
                    "SELECT count, avg_sentiment FROM topic_tracking WHERE topic=? AND user_id=?",
                    (topic, user_id)
                ).fetchone()

                if existing:
                    new_count = existing['count'] + 1
                    new_avg = (existing['avg_sentiment'] * existing['count'] + sentiment.score) / new_count
                    conn.execute("""
                        UPDATE topic_tracking SET count=?, last_seen=?, avg_sentiment=?
                        WHERE topic=? AND user_id=?
                    """, (new_count, now, new_avg, topic, user_id))
                else:
                    conn.execute("""
                        INSERT INTO topic_tracking (topic, user_id, count, first_seen, last_seen, avg_sentiment)
                        VALUES (?, ?, 1, ?, ?, ?)
                    """, (topic, user_id, now, now, sentiment.score))

            # Update daily engagement
            date_str = time.strftime('%Y-%m-%d', time.localtime(now))
            existing_eng = conn.execute(
                "SELECT * FROM engagement_metrics WHERE user_id=? AND date=?",
                (user_id, date_str)
            ).fetchone()

            if existing_eng:
                new_count = existing_eng['message_count'] + 1
                new_avg_sent = (existing_eng['avg_sentiment'] * existing_eng['message_count'] + sentiment.score) / new_count
                new_avg_wc = (existing_eng['avg_word_count'] * existing_eng['message_count'] + word_count) / new_count
                new_q_count = existing_eng['question_count'] + (1 if is_question else 0)
                conn.execute("""
                    UPDATE engagement_metrics
                    SET message_count=?, avg_sentiment=?, avg_word_count=?, question_count=?
                    WHERE user_id=? AND date=?
                """, (new_count, new_avg_sent, new_avg_wc, new_q_count, user_id, date_str))
            else:
                conn.execute("""
                    INSERT INTO engagement_metrics
                    (user_id, date, message_count, avg_sentiment, avg_word_count, question_count)
                    VALUES (?, ?, 1, ?, ?, ?)
                """, (user_id, date_str, sentiment.score, word_count, 1 if is_question else 0))

        return sentiment

    # ── Query & Reports ─────────────────────────────────────────────

    def get_user_sentiment_history(self, user_id: str, days: int = 7,
                                    limit: int = 100) -> List[Dict]:
        """Get recent sentiment history for a user."""
        cutoff = time.time() - days * 86400
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT sentiment_score, sentiment_label, confidence, topics,
                       word_count, has_question, language, created_at
                FROM message_sentiments
                WHERE user_id=? AND created_at > ?
                ORDER BY created_at DESC LIMIT ?
            """, (user_id, cutoff, limit)).fetchall()

            return [dict(row) for row in rows]

    def get_top_topics(self, user_id: Optional[str] = None, limit: int = 10) -> List[TopicInfo]:
        """Get most discussed topics."""
        with self._get_conn() as conn:
            if user_id:
                rows = conn.execute("""
                    SELECT topic, count, avg_sentiment, first_seen, last_seen
                    FROM topic_tracking WHERE user_id=?
                    ORDER BY count DESC LIMIT ?
                """, (user_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT topic, SUM(count) as count, AVG(avg_sentiment) as avg_sentiment,
                           MIN(first_seen) as first_seen, MAX(last_seen) as last_seen
                    FROM topic_tracking
                    GROUP BY topic ORDER BY count DESC LIMIT ?
                """, (limit,)).fetchall()

            now = time.time()
            results = []
            for row in rows:
                # Trending = high activity in recent 24h
                trending = (now - row['last_seen']) < 86400 and row['count'] >= 3
                results.append(TopicInfo(
                    topic=row['topic'],
                    count=row['count'],
                    avg_sentiment=round(row['avg_sentiment'], 3),
                    first_seen=row['first_seen'],
                    last_seen=row['last_seen'],
                    trending=trending,
                ))
            return results

    def get_engagement_report(self, user_id: str, days: int = 7) -> EngagementReport:
        """Generate engagement report for a user."""
        cutoff = time.time() - days * 86400
        cutoff_date = time.strftime('%Y-%m-%d', time.localtime(cutoff))

        with self._get_conn() as conn:
            # Daily metrics
            rows = conn.execute("""
                SELECT * FROM engagement_metrics
                WHERE user_id=? AND date >= ?
                ORDER BY date
            """, (user_id, cutoff_date)).fetchall()

            if not rows:
                return EngagementReport(
                    user_id=user_id, period=f'{days}d',
                    total_messages=0, avg_messages_per_day=0.0,
                    avg_sentiment=0.0, sentiment_trend='stable',
                    top_topics=[], peak_hours=[], avg_word_count=0.0,
                    question_ratio=0.0, quality_score=0.0,
                )

            total_msgs = sum(r['message_count'] for r in rows)
            avg_per_day = total_msgs / max(len(rows), 1)
            avg_sentiment = sum(r['avg_sentiment'] * r['message_count'] for r in rows) / max(total_msgs, 1)
            avg_wc = sum(r['avg_word_count'] * r['message_count'] for r in rows) / max(total_msgs, 1)
            total_questions = sum(r['question_count'] for r in rows)
            q_ratio = total_questions / max(total_msgs, 1)

            # Sentiment trend
            if len(rows) >= 3:
                first_half = rows[:len(rows)//2]
                second_half = rows[len(rows)//2:]
                first_avg = sum(r['avg_sentiment'] for r in first_half) / len(first_half)
                second_avg = sum(r['avg_sentiment'] for r in second_half) / len(second_half)
                if second_avg - first_avg > 0.1:
                    trend = 'improving'
                elif first_avg - second_avg > 0.1:
                    trend = 'declining'
                else:
                    trend = 'stable'
            else:
                trend = 'stable'

            # Peak hours
            hour_counts = Counter()
            msg_rows = conn.execute("""
                SELECT created_at FROM message_sentiments
                WHERE user_id=? AND created_at > ?
            """, (user_id, cutoff)).fetchall()
            for mr in msg_rows:
                hour = int(time.strftime('%H', time.localtime(mr['created_at'])))
                hour_counts[hour] += 1
            peak_hours = [h for h, _ in hour_counts.most_common(3)]

            # Top topics
            top_topics = self.get_top_topics(user_id, limit=5)

            # Quality score (0-100)
            quality = min(100, (
                min(avg_per_day, 10) * 3 +  # activity
                (avg_sentiment + 1) * 15 +   # positivity
                min(avg_wc, 50) * 0.4 +      # message depth
                len(top_topics) * 4 +         # topic diversity
                (1 - q_ratio) * 10            # not just asking questions
            ))

            return EngagementReport(
                user_id=user_id,
                period=f'{days}d',
                total_messages=total_msgs,
                avg_messages_per_day=round(avg_per_day, 1),
                avg_sentiment=round(avg_sentiment, 3),
                sentiment_trend=trend,
                top_topics=top_topics,
                peak_hours=peak_hours,
                avg_word_count=round(avg_wc, 1),
                question_ratio=round(q_ratio, 3),
                quality_score=round(quality, 1),
            )

    def get_sentiment_distribution(self, user_id: Optional[str] = None,
                                    days: int = 30) -> Dict[str, int]:
        """Get sentiment label distribution."""
        cutoff = time.time() - days * 86400
        with self._get_conn() as conn:
            if user_id:
                rows = conn.execute("""
                    SELECT sentiment_label, COUNT(*) as cnt
                    FROM message_sentiments
                    WHERE user_id=? AND created_at > ?
                    GROUP BY sentiment_label
                """, (user_id, cutoff)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT sentiment_label, COUNT(*) as cnt
                    FROM message_sentiments
                    WHERE created_at > ?
                    GROUP BY sentiment_label
                """, (cutoff,)).fetchall()

            return {row['sentiment_label']: row['cnt'] for row in rows}

    def get_global_stats(self) -> Dict[str, Any]:
        """Get global insights statistics."""
        with self._get_conn() as conn:
            total_msgs = conn.execute("SELECT COUNT(*) FROM message_sentiments").fetchone()[0]
            total_users = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM message_sentiments"
            ).fetchone()[0]
            avg_sentiment = conn.execute(
                "SELECT AVG(sentiment_score) FROM message_sentiments"
            ).fetchone()[0] or 0.0
            total_topics = conn.execute(
                "SELECT COUNT(DISTINCT topic) FROM topic_tracking"
            ).fetchone()[0]

            return {
                'total_messages_analyzed': total_msgs,
                'total_users': total_users,
                'avg_sentiment': round(avg_sentiment, 3),
                'total_topics': total_topics,
            }

    def generate_text_report(self, user_id: Optional[str] = None, days: int = 7) -> str:
        """Generate a human-readable insights report."""
        lines = ["═══ Conversation Insights Report ═══", ""]

        if user_id:
            report = self.get_engagement_report(user_id, days)
            lines.append(f"User: {user_id}")
            lines.append(f"Period: Last {days} days")
            lines.append(f"Total Messages: {report.total_messages}")
            lines.append(f"Avg Messages/Day: {report.avg_messages_per_day}")
            lines.append(f"Avg Sentiment: {report.avg_sentiment:.3f} ({report.sentiment_trend})")
            lines.append(f"Avg Word Count: {report.avg_word_count}")
            lines.append(f"Question Ratio: {report.question_ratio:.1%}")
            lines.append(f"Quality Score: {report.quality_score}/100")

            if report.top_topics:
                lines.append("\nTop Topics:")
                for t in report.top_topics:
                    trend = " 🔥" if t.trending else ""
                    lines.append(f"  • {t.topic} (×{t.count}, sentiment: {t.avg_sentiment:.2f}){trend}")

            if report.peak_hours:
                lines.append(f"\nPeak Hours: {', '.join(f'{h}:00' for h in report.peak_hours)}")
        else:
            stats = self.get_global_stats()
            lines.append("Global Statistics")
            lines.append(f"Messages Analyzed: {stats['total_messages_analyzed']}")
            lines.append(f"Users Tracked: {stats['total_users']}")
            lines.append(f"Avg Sentiment: {stats['avg_sentiment']:.3f}")
            lines.append(f"Topics Discovered: {stats['total_topics']}")

            dist = self.get_sentiment_distribution(days=days)
            if dist:
                lines.append(f"\nSentiment Distribution ({days}d):")
                total = sum(dist.values())
                for label, count in sorted(dist.items()):
                    pct = count / total * 100 if total else 0
                    bar = '█' * int(pct / 5)
                    lines.append(f"  {label:>8}: {bar} {pct:.1f}% ({count})")

            topics = self.get_top_topics(limit=10)
            if topics:
                lines.append("\nTrending Topics:")
                for t in topics:
                    trend = " 🔥" if t.trending else ""
                    lines.append(f"  • {t.topic} (×{t.count}){trend}")

        lines.append("\n═══════════════════════════════════")
        return '\n'.join(lines)
