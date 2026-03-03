"""
自动审核 / 内容过滤器 - 垃圾消息检测 + 关键词黑名单 + 频率限制
"""

import re
import time
import hashlib
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class FilterAction(Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    MUTE = "mute"


class ViolationType(Enum):
    SPAM = "spam"
    BLACKLISTED_WORD = "blacklisted_word"
    FLOOD = "flood"
    DUPLICATE = "duplicate"
    URL_SPAM = "url_spam"
    LONG_MESSAGE = "long_message"
    EMPTY_MESSAGE = "empty_message"


@dataclass
class ModerationResult:
    """审核结果"""
    allowed: bool
    action: FilterAction
    violations: List[str] = field(default_factory=list)
    violation_types: List[ViolationType] = field(default_factory=list)
    score: float = 0.0  # 0-1, 越高越可疑
    details: Dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return "; ".join(self.violations) if self.violations else ""


@dataclass
class UserModerationState:
    """用户审核状态"""
    user_id: str
    violation_count: int = 0
    last_violation: float = 0.0
    muted_until: float = 0.0
    recent_messages: List[float] = field(default_factory=list)
    recent_hashes: List[str] = field(default_factory=list)
    warning_count: int = 0


class ContentFilter:
    """内容过滤规则引擎"""

    # 默认黑名单模式 (正则)
    DEFAULT_PATTERNS = [
        r"https?://bit\.ly/\S+",         # 短链接
        r"https?://t\.me/joinchat/\S+",   # TG邀请链接
        r"免费.*?领取",                     # 中文垃圾广告
        r"加[微vV]信?\s*[:：]?\s*\w{5,}",  # 加微信
        r"(?:earn|make)\s+\$?\d+.*(?:daily|day|hour)",  # 赚钱广告
        r"\b(?:casino|poker|gambling|bet365)\b",         # 赌博
    ]

    def __init__(
        self,
        blacklisted_words: Optional[Set[str]] = None,
        blacklisted_patterns: Optional[List[str]] = None,
        max_message_length: int = 5000,
        min_message_length: int = 1,
        max_urls_per_message: int = 3,
        use_default_patterns: bool = True,
    ):
        self.blacklisted_words = blacklisted_words or set()
        self.max_message_length = max_message_length
        self.min_message_length = min_message_length
        self.max_urls_per_message = max_urls_per_message

        patterns = list(blacklisted_patterns or [])
        if use_default_patterns:
            patterns.extend(self.DEFAULT_PATTERNS)

        self._compiled_patterns = []
        for p in patterns:
            try:
                self._compiled_patterns.append(re.compile(p, re.IGNORECASE))
            except re.error:
                logger.warning("Invalid regex pattern: %s", p)

        self._url_pattern = re.compile(
            r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.IGNORECASE
        )

    def check_content(self, text: str) -> Tuple[float, List[str], List[ViolationType]]:
        """
        检查消息内容

        Returns:
            (score, violations, types)
            score: 0.0-1.0 (0=clean, 1=spam)
        """
        if not text:
            return 0.0, [], []

        score = 0.0
        violations = []
        types = []

        # 长度检查
        if len(text) > self.max_message_length:
            score += 0.3
            violations.append(
                f"Message too long ({len(text)}/{self.max_message_length})"
            )
            types.append(ViolationType.LONG_MESSAGE)

        text_stripped = text.strip()
        if len(text_stripped) < self.min_message_length:
            return 0.0, ["Empty message"], [ViolationType.EMPTY_MESSAGE]

        # 黑名单词检查
        text_lower = text.lower()
        for word in self.blacklisted_words:
            if word.lower() in text_lower:
                score += 0.5
                violations.append(f"Blacklisted word: {word}")
                types.append(ViolationType.BLACKLISTED_WORD)

        # 正则模式检查
        for pattern in self._compiled_patterns:
            match = pattern.search(text)
            if match:
                score += 0.4
                violations.append(f"Pattern match: {match.group()[:50]}")
                types.append(ViolationType.SPAM)

        # URL数量检查
        urls = self._url_pattern.findall(text)
        if len(urls) > self.max_urls_per_message:
            score += 0.3
            violations.append(
                f"Too many URLs ({len(urls)}/{self.max_urls_per_message})"
            )
            types.append(ViolationType.URL_SPAM)

        # 全大写检查 (英文)
        alpha_chars = [c for c in text if c.isalpha()]
        if len(alpha_chars) > 20:
            upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            if upper_ratio > 0.8:
                score += 0.2
                violations.append("Excessive caps")
                types.append(ViolationType.SPAM)

        # 重复字符检查
        if self._has_char_spam(text):
            score += 0.3
            violations.append("Repetitive characters")
            types.append(ViolationType.SPAM)

        return min(score, 1.0), violations, types

    def _has_char_spam(self, text: str, threshold: int = 10) -> bool:
        """检测重复字符垃圾"""
        if len(text) < threshold:
            return False
        for i in range(len(text) - threshold + 1):
            if len(set(text[i:i + threshold])) <= 2:
                return True
        return False

    def add_word(self, word: str):
        self.blacklisted_words.add(word)

    def remove_word(self, word: str):
        self.blacklisted_words.discard(word)

    def add_pattern(self, pattern: str):
        try:
            self._compiled_patterns.append(re.compile(pattern, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}")


class FloodDetector:
    """洪水消息检测 (短时间内大量消息)"""

    def __init__(
        self,
        window_seconds: float = 10.0,
        max_messages: int = 5,
        duplicate_window: float = 60.0,
        max_duplicates: int = 2,
    ):
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.duplicate_window = duplicate_window
        self.max_duplicates = max_duplicates

    def check(
        self, state: UserModerationState, text: str
    ) -> Tuple[float, List[str], List[ViolationType]]:
        """检查洪水/重复消息"""
        now = time.time()
        score = 0.0
        violations = []
        types = []

        # 时间窗口内消息数
        state.recent_messages = [
            t for t in state.recent_messages
            if now - t < self.window_seconds
        ]
        state.recent_messages.append(now)

        if len(state.recent_messages) > self.max_messages:
            score += 0.6
            violations.append(
                f"Flood: {len(state.recent_messages)} msgs in {self.window_seconds}s"
            )
            types.append(ViolationType.FLOOD)

        # 重复消息检测
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        state.recent_hashes = state.recent_hashes[-20:]  # keep last 20

        recent_same = state.recent_hashes.count(text_hash)
        if recent_same >= self.max_duplicates:
            score += 0.5
            violations.append(f"Duplicate message (seen {recent_same + 1} times)")
            types.append(ViolationType.DUPLICATE)

        state.recent_hashes.append(text_hash)

        return min(score, 1.0), violations, types


class AutoModerator:
    """
    自动审核引擎

    Features:
    - 内容过滤 (黑名单词 + 正则模式 + URL检测)
    - 洪水检测 (频率 + 重复)
    - 用户状态追踪 (违规计数 + 自动静音)
    - 分级处置 (允许/警告/阻止/静音)
    - 白名单绕过
    """

    def __init__(
        self,
        content_filter: Optional[ContentFilter] = None,
        flood_detector: Optional[FloodDetector] = None,
        warn_threshold: float = 0.3,
        block_threshold: float = 0.6,
        mute_after_violations: int = 3,
        mute_duration: float = 300.0,
        whitelist: Optional[Set[str]] = None,
    ):
        self.content_filter = content_filter or ContentFilter()
        self.flood_detector = flood_detector or FloodDetector()
        self.warn_threshold = warn_threshold
        self.block_threshold = block_threshold
        self.mute_after_violations = mute_after_violations
        self.mute_duration = mute_duration
        self.whitelist = whitelist or set()

        self._user_states: Dict[str, UserModerationState] = {}
        self._total_checked = 0
        self._total_blocked = 0
        self._total_warned = 0

    def _get_state(self, user_id: str) -> UserModerationState:
        if user_id not in self._user_states:
            self._user_states[user_id] = UserModerationState(user_id=user_id)
        return self._user_states[user_id]

    def moderate(self, user_id: str, text: str) -> ModerationResult:
        """
        审核用户消息

        Returns:
            ModerationResult with action and details
        """
        self._total_checked += 1

        # 白名单
        if user_id in self.whitelist:
            return ModerationResult(allowed=True, action=FilterAction.ALLOW)

        state = self._get_state(user_id)

        # 静音检查
        if state.muted_until > time.time():
            remaining = state.muted_until - time.time()
            return ModerationResult(
                allowed=False,
                action=FilterAction.MUTE,
                violations=[f"Muted for {remaining:.0f}s"],
                score=1.0,
                details={"muted_until": state.muted_until},
            )

        all_violations = []
        all_types = []
        total_score = 0.0

        # 内容过滤
        c_score, c_viols, c_types = self.content_filter.check_content(text)
        total_score += c_score
        all_violations.extend(c_viols)
        all_types.extend(c_types)

        # 洪水检测
        f_score, f_viols, f_types = self.flood_detector.check(state, text)
        total_score += f_score
        all_violations.extend(f_viols)
        all_types.extend(f_types)

        total_score = min(total_score, 1.0)

        # 决策
        if total_score >= self.block_threshold:
            action = FilterAction.BLOCK
            state.violation_count += 1
            state.last_violation = time.time()
            self._total_blocked += 1

            # 累计违规→静音
            if state.violation_count >= self.mute_after_violations:
                action = FilterAction.MUTE
                state.muted_until = time.time() + self.mute_duration
                all_violations.append(
                    f"Auto-muted for {self.mute_duration}s "
                    f"({state.violation_count} violations)"
                )

            return ModerationResult(
                allowed=False,
                action=action,
                violations=all_violations,
                violation_types=all_types,
                score=total_score,
            )

        elif total_score >= self.warn_threshold:
            state.warning_count += 1
            self._total_warned += 1
            return ModerationResult(
                allowed=True,
                action=FilterAction.WARN,
                violations=all_violations,
                violation_types=all_types,
                score=total_score,
            )

        return ModerationResult(
            allowed=True,
            action=FilterAction.ALLOW,
            score=total_score,
        )

    def add_whitelist(self, user_id: str):
        self.whitelist.add(user_id)

    def remove_whitelist(self, user_id: str):
        self.whitelist.discard(user_id)

    def unmute(self, user_id: str) -> bool:
        state = self._user_states.get(user_id)
        if state:
            state.muted_until = 0
            return True
        return False

    def reset_user(self, user_id: str):
        if user_id in self._user_states:
            del self._user_states[user_id]

    def get_user_status(self, user_id: str) -> Dict:
        state = self._user_states.get(user_id)
        if not state:
            return {"user_id": user_id, "status": "clean"}
        return {
            "user_id": user_id,
            "violations": state.violation_count,
            "warnings": state.warning_count,
            "muted": state.muted_until > time.time(),
            "muted_until": state.muted_until if state.muted_until > time.time() else 0,
        }

    def get_stats(self) -> Dict:
        return {
            "total_checked": self._total_checked,
            "total_blocked": self._total_blocked,
            "total_warned": self._total_warned,
            "block_rate": (
                round(self._total_blocked / self._total_checked * 100, 1)
                if self._total_checked > 0 else 0
            ),
            "tracked_users": len(self._user_states),
            "muted_users": sum(
                1 for s in self._user_states.values()
                if s.muted_until > time.time()
            ),
            "whitelist_size": len(self.whitelist),
        }

    def cleanup(self, max_age: float = 3600):
        now = time.time()
        expired = [
            uid for uid, state in self._user_states.items()
            if (now - max(state.last_violation, 0) > max_age
                and state.muted_until < now)
        ]
        for uid in expired:
            del self._user_states[uid]
        return len(expired)
