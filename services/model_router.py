"""
Smart Model Router - 智能模型路由

根据查询复杂度、用户偏好、成本预算自动选择最优引擎/模型。

Features:
- 查询复杂度分类 (simple/medium/complex/creative)
- 基于规则的路由策略
- 成本感知路由 (预算追踪)
- 用户偏好覆盖
- A/B测试支持 (流量分配)
- 路由历史+分析
"""

import re
import time
import random
import hashlib
import logging
import threading
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

logger = logging.getLogger(__name__)


class QueryComplexity(Enum):
    SIMPLE = "simple"       # 简单问答、翻译、闲聊
    MEDIUM = "medium"       # 总结、分析、解释
    COMPLEX = "complex"     # 编程、推理、多步骤
    CREATIVE = "creative"   # 创作、写作、头脑风暴


@dataclass
class ModelSpec:
    """模型规格"""
    name: str
    engine: str  # "openai" or "claude"
    model_id: str  # e.g. "gpt-4o-mini"
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    max_context: int = 8000
    strengths: List[str] = field(default_factory=list)  # ["code", "creative", "fast"]
    latency_tier: str = "medium"  # "fast", "medium", "slow"


@dataclass
class RoutingRule:
    """路由规则"""
    name: str
    condition: str  # "complexity==simple", "language==zh", "topic==code"
    target_model: str
    priority: int = 0
    weight: float = 1.0  # For A/B testing


@dataclass
class RoutingDecision:
    """路由决策结果"""
    model_name: str
    engine: str
    model_id: str
    reason: str
    complexity: QueryComplexity
    estimated_cost: float = 0.0
    latency_tier: str = "medium"


@dataclass
class RoutingStats:
    """路由统计"""
    total_routes: int = 0
    by_model: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_complexity: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    total_cost: float = 0.0
    avg_latency: float = 0.0


class QueryAnalyzer:
    """查询分析器 - 判断复杂度和特征"""

    # Complexity indicators
    COMPLEX_PATTERNS = [
        r"(写|编写|实现|开发|写一个|create|implement|build|develop).{0,20}(代码|程序|脚本|函数|算法|code|script|function|class|algorithm)",
        r"(分析|推理|论证|比较|evaluate|analyze|compare|reason)",
        r"(step[\s-]?by[\s-]?step|一步一步|详细分析|逐步)",
        r"(数学|计算|证明|公式|math|calculate|prove)",
        r"(debug|调试|修复|fix|错误|bug|error)",
    ]

    SIMPLE_PATTERNS = [
        r"^(hi|hello|你好|嗨|hey|ok|好的|谢谢|thanks)\s*[!.?]*$",
        r"^(翻译|translate)[：:]\s*",
        r"^.{0,20}(是什么|什么意思|什么是|what is|what\'s)\s*[?？]*$",
        r"^(天气|时间|日期|weather|time|date)",
    ]

    CREATIVE_PATTERNS = [
        r"(写|创作|编|作).{0,10}(诗|歌|故事|文章|小说|剧本|文案|poem|story|essay)",
        r"(头脑风暴|brainstorm|创意|idea|想象|imagine)",
        r"(设计|design|起名|命名|name|slogan|标语)",
    ]

    CODE_INDICATORS = [
        "```", "def ", "class ", "function ", "import ",
        "var ", "let ", "const ", "SELECT ", "INSERT ",
    ]

    @classmethod
    def analyze(cls, query: str) -> Dict[str, Any]:
        """
        分析查询特征

        Returns:
            {
                "complexity": QueryComplexity,
                "language": "zh" | "en" | "mixed",
                "has_code": bool,
                "estimated_tokens": int,
                "topics": List[str]
            }
        """
        query_lower = query.lower().strip()
        features = {
            "complexity": cls._classify_complexity(query),
            "language": cls._detect_language(query),
            "has_code": cls._has_code(query),
            "estimated_tokens": cls._estimate_tokens(query),
            "topics": cls._extract_topics(query),
            "length_category": cls._categorize_length(query),
        }
        return features

    @classmethod
    def _classify_complexity(cls, query: str) -> QueryComplexity:
        """分类查询复杂度"""
        query_lower = query.lower()

        # Check creative first
        for pattern in cls.CREATIVE_PATTERNS:
            if re.search(pattern, query_lower):
                return QueryComplexity.CREATIVE

        # Check simple
        for pattern in cls.SIMPLE_PATTERNS:
            if re.search(pattern, query_lower):
                return QueryComplexity.SIMPLE

        # Check complex
        complex_score = 0
        for pattern in cls.COMPLEX_PATTERNS:
            if re.search(pattern, query_lower):
                complex_score += 1

        if cls._has_code(query):
            complex_score += 2

        if len(query) > 500:
            complex_score += 1
        if query.count("?") > 2 or query.count("？") > 2:
            complex_score += 1

        if complex_score >= 2:
            return QueryComplexity.COMPLEX
        elif complex_score >= 1 or len(query) > 200:
            return QueryComplexity.MEDIUM
        else:
            return QueryComplexity.SIMPLE

    @classmethod
    def _detect_language(cls, query: str) -> str:
        """检测主要语言"""
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', query))
        en_chars = len(re.findall(r'[a-zA-Z]', query))
        total = cn_chars + en_chars
        if total == 0:
            return "other"
        cn_ratio = cn_chars / total
        if cn_ratio > 0.6:
            return "zh"
        elif cn_ratio < 0.2:
            return "en"
        else:
            return "mixed"

    @classmethod
    def _has_code(cls, query: str) -> bool:
        """检测是否包含代码"""
        return any(indicator in query for indicator in cls.CODE_INDICATORS)

    @classmethod
    def _estimate_tokens(cls, query: str) -> int:
        """估算token数"""
        # Rough: 1 Chinese char ≈ 2 tokens, 1 English word ≈ 1.3 tokens
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', query))
        en_words = len(re.findall(r'[a-zA-Z]+', query))
        other = len(query) - cn_chars - sum(len(w) for w in re.findall(r'[a-zA-Z]+', query))
        return int(cn_chars * 2 + en_words * 1.3 + other * 0.5)

    @classmethod
    def _extract_topics(cls, query: str) -> List[str]:
        """提取话题标签"""
        topics = []
        query_lower = query.lower()

        topic_map = {
            "code": [r"代码|code|编程|programming|python|java|javascript"],
            "math": [r"数学|math|计算|calculate|公式|formula"],
            "writing": [r"写作|writing|文章|essay|作文"],
            "translation": [r"翻译|translate|translation"],
            "qa": [r"是什么|什么是|what is|explain|解释"],
            "chat": [r"^(hi|hello|你好|聊聊|chat)"],
        }

        for topic, patterns in topic_map.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    topics.append(topic)
                    break

        return topics or ["general"]

    @classmethod
    def _categorize_length(cls, query: str) -> str:
        """分类查询长度"""
        length = len(query)
        if length < 20:
            return "short"
        elif length < 200:
            return "medium"
        elif length < 1000:
            return "long"
        else:
            return "very_long"


class CostTracker:
    """成本追踪器"""

    def __init__(self, daily_budget: float = 10.0):
        self.daily_budget = daily_budget
        self._daily_spend: Dict[str, float] = {}  # date -> spend
        self._lock = threading.Lock()

    def record_cost(self, cost: float):
        """记录花费"""
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            self._daily_spend[date] = self._daily_spend.get(date, 0.0) + cost

    def get_remaining_budget(self) -> float:
        """获取今日剩余预算"""
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            spent = self._daily_spend.get(date, 0.0)
        return max(0, self.daily_budget - spent)

    def is_over_budget(self) -> bool:
        return self.get_remaining_budget() <= 0

    def get_total_spend(self) -> float:
        with self._lock:
            return sum(self._daily_spend.values())

    def get_daily_history(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._daily_spend)


class ModelRouter:
    """智能模型路由器"""

    def __init__(
        self,
        models: List[ModelSpec] = None,
        rules: List[RoutingRule] = None,
        daily_budget: float = 10.0,
        ab_test_enabled: bool = False,
        default_model: str = None,
    ):
        self.models: Dict[str, ModelSpec] = {}
        if models:
            for m in models:
                self.models[m.name] = m

        self.rules = sorted(rules or [], key=lambda r: -r.priority)
        self.cost_tracker = CostTracker(daily_budget=daily_budget)
        self.ab_test_enabled = ab_test_enabled
        self.default_model = default_model
        self._user_preferences: Dict[str, str] = {}  # user_id -> model_name
        self._history: List[Dict] = []
        self._history_lock = threading.Lock()
        self._max_history = 5000

        # Stats
        self._stats = RoutingStats()
        self._stats_lock = threading.Lock()

    def register_model(self, model: ModelSpec):
        """注册模型"""
        self.models[model.name] = model

    def remove_model(self, name: str):
        """移除模型"""
        self.models.pop(name, None)

    def add_rule(self, rule: RoutingRule):
        """添加路由规则"""
        self.rules.append(rule)
        self.rules.sort(key=lambda r: -r.priority)

    def set_user_preference(self, user_id: str, model_name: str):
        """设置用户模型偏好"""
        if model_name in self.models:
            self._user_preferences[user_id] = model_name

    def clear_user_preference(self, user_id: str):
        """清除用户偏好"""
        self._user_preferences.pop(user_id, None)

    def route(
        self,
        query: str,
        user_id: str = "",
        context: Dict = None,
    ) -> RoutingDecision:
        """
        路由查询到最优模型

        Priority:
        1. User preference override
        2. A/B test (if enabled)
        3. Rule-based routing
        4. Complexity-based routing
        5. Budget-aware fallback
        6. Default model
        """
        if not self.models:
            raise ValueError("No models registered")

        # Analyze query
        features = QueryAnalyzer.analyze(query)
        complexity = features["complexity"]

        # 1. User preference
        if user_id and user_id in self._user_preferences:
            pref = self._user_preferences[user_id]
            if pref in self.models:
                decision = self._make_decision(
                    pref, complexity, "user_preference"
                )
                self._record_route(decision, user_id, features)
                return decision

        # 2. A/B test
        if self.ab_test_enabled:
            ab_result = self._ab_test_route(user_id, features)
            if ab_result:
                decision = self._make_decision(
                    ab_result, complexity, "ab_test"
                )
                self._record_route(decision, user_id, features)
                return decision

        # 3. Rule-based
        for rule in self.rules:
            if self._match_rule(rule, features):
                if rule.target_model in self.models:
                    decision = self._make_decision(
                        rule.target_model, complexity, f"rule:{rule.name}"
                    )
                    self._record_route(decision, user_id, features)
                    return decision

        # 4. Complexity-based
        best = self._complexity_route(complexity, features)
        if best:
            decision = self._make_decision(
                best, complexity, "complexity_based"
            )
            self._record_route(decision, user_id, features)
            return decision

        # 5. Budget-aware fallback
        if self.cost_tracker.is_over_budget():
            cheapest = self._find_cheapest_model()
            if cheapest:
                decision = self._make_decision(
                    cheapest, complexity, "budget_fallback"
                )
                self._record_route(decision, user_id, features)
                return decision

        # 6. Default
        model_name = self.default_model or list(self.models.keys())[0]
        decision = self._make_decision(model_name, complexity, "default")
        self._record_route(decision, user_id, features)
        return decision

    def _make_decision(
        self, model_name: str, complexity: QueryComplexity, reason: str
    ) -> RoutingDecision:
        """构建路由决策"""
        model = self.models[model_name]
        return RoutingDecision(
            model_name=model.name,
            engine=model.engine,
            model_id=model.model_id,
            reason=reason,
            complexity=complexity,
            estimated_cost=model.cost_per_1k_input * 2,  # rough estimate
            latency_tier=model.latency_tier,
        )

    def _match_rule(self, rule: RoutingRule, features: Dict) -> bool:
        """匹配路由规则"""
        condition = rule.condition
        parts = condition.split("==")
        if len(parts) != 2:
            return False

        key, value = parts[0].strip(), parts[1].strip()

        if key == "complexity":
            return features.get("complexity", QueryComplexity.SIMPLE).value == value
        elif key == "language":
            return features.get("language", "") == value
        elif key == "has_code":
            return str(features.get("has_code", False)).lower() == value.lower()
        elif key == "topic":
            return value in features.get("topics", [])
        elif key == "length":
            return features.get("length_category", "") == value
        else:
            return False

    def _complexity_route(
        self, complexity: QueryComplexity, features: Dict
    ) -> Optional[str]:
        """基于复杂度选择模型"""
        # Find models with matching strengths
        candidates = []
        for name, model in self.models.items():
            score = 0
            if complexity == QueryComplexity.SIMPLE:
                if "fast" in model.strengths:
                    score += 3
                if model.latency_tier == "fast":
                    score += 2
                # Prefer cheaper for simple
                score -= model.cost_per_1k_input * 10
            elif complexity == QueryComplexity.COMPLEX:
                if "code" in model.strengths or "reasoning" in model.strengths:
                    score += 3
                if model.max_context > 16000:
                    score += 1
            elif complexity == QueryComplexity.CREATIVE:
                if "creative" in model.strengths:
                    score += 3
            else:  # MEDIUM
                score += 1  # neutral

            if features.get("has_code") and "code" in model.strengths:
                score += 2
            if features.get("language") == "zh" and "chinese" in model.strengths:
                score += 1

            candidates.append((name, score))

        if not candidates:
            return None

        candidates.sort(key=lambda x: -x[1])
        return candidates[0][0]

    def _ab_test_route(self, user_id: str, features: Dict) -> Optional[str]:
        """A/B测试路由"""
        if not self.rules:
            return None

        # Deterministic bucket based on user_id
        if user_id:
            bucket = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100
        else:
            bucket = random.randint(0, 99)

        # Weighted selection from rules
        cumulative = 0
        for rule in self.rules:
            cumulative += int(rule.weight * 100)
            if bucket < cumulative and rule.target_model in self.models:
                return rule.target_model

        return None

    def _find_cheapest_model(self) -> Optional[str]:
        """找最便宜的模型"""
        if not self.models:
            return None
        return min(
            self.models.keys(),
            key=lambda n: self.models[n].cost_per_1k_input,
        )

    def _record_route(self, decision: RoutingDecision, user_id: str, features: Dict):
        """记录路由历史"""
        with self._stats_lock:
            self._stats.total_routes += 1
            self._stats.by_model[decision.model_name] += 1
            self._stats.by_complexity[decision.complexity.value] += 1

        record = {
            "timestamp": time.time(),
            "user_id": user_id,
            "model": decision.model_name,
            "engine": decision.engine,
            "complexity": decision.complexity.value,
            "reason": decision.reason,
            "features": {
                k: v.value if isinstance(v, Enum) else v
                for k, v in features.items()
            },
        }
        with self._history_lock:
            self._history.append(record)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

    def get_stats(self) -> Dict:
        """获取路由统计"""
        with self._stats_lock:
            return {
                "total_routes": self._stats.total_routes,
                "by_model": dict(self._stats.by_model),
                "by_complexity": dict(self._stats.by_complexity),
                "total_cost": self.cost_tracker.get_total_spend(),
                "remaining_budget": self.cost_tracker.get_remaining_budget(),
            }

    def get_history(self, limit: int = 50) -> List[Dict]:
        """获取路由历史"""
        with self._history_lock:
            return self._history[-limit:]
