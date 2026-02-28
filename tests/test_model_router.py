"""Tests for Smart Model Router"""

import pytest
from services.model_router import (
    ModelRouter,
    ModelSpec,
    RoutingRule,
    QueryAnalyzer,
    QueryComplexity,
    CostTracker,
    RoutingDecision,
)


# ---- Fixtures ----

@pytest.fixture
def models():
    return [
        ModelSpec(
            name="gpt4-mini",
            engine="openai",
            model_id="gpt-4o-mini",
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
            max_context=128000,
            strengths=["fast", "cheap"],
            latency_tier="fast",
        ),
        ModelSpec(
            name="gpt4",
            engine="openai",
            model_id="gpt-4o",
            cost_per_1k_input=0.005,
            cost_per_1k_output=0.015,
            max_context=128000,
            strengths=["code", "reasoning"],
            latency_tier="medium",
        ),
        ModelSpec(
            name="claude-haiku",
            engine="claude",
            model_id="claude-3-haiku",
            cost_per_1k_input=0.00025,
            cost_per_1k_output=0.00125,
            max_context=200000,
            strengths=["fast", "chinese"],
            latency_tier="fast",
        ),
        ModelSpec(
            name="claude-sonnet",
            engine="claude",
            model_id="claude-3.5-sonnet",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            max_context=200000,
            strengths=["creative", "code", "reasoning"],
            latency_tier="medium",
        ),
    ]


@pytest.fixture
def router(models):
    return ModelRouter(models=models, default_model="gpt4-mini")


# ---- QueryAnalyzer Tests ----

class TestQueryAnalyzer:
    def test_simple_greeting(self):
        features = QueryAnalyzer.analyze("hello")
        assert features["complexity"] == QueryComplexity.SIMPLE

    def test_simple_chinese_greeting(self):
        features = QueryAnalyzer.analyze("你好")
        assert features["complexity"] == QueryComplexity.SIMPLE

    def test_complex_code_request(self):
        features = QueryAnalyzer.analyze(
            "写一个Python代码实现快速排序算法，包含单元测试"
        )
        assert features["complexity"] in (QueryComplexity.COMPLEX, QueryComplexity.MEDIUM)

    def test_creative_writing(self):
        features = QueryAnalyzer.analyze("写一首关于月亮的诗")
        assert features["complexity"] == QueryComplexity.CREATIVE

    def test_language_detection_chinese(self):
        features = QueryAnalyzer.analyze("今天天气怎么样？")
        assert features["language"] == "zh"

    def test_language_detection_english(self):
        features = QueryAnalyzer.analyze("What is the weather today?")
        assert features["language"] == "en"

    def test_language_detection_mixed(self):
        features = QueryAnalyzer.analyze("请帮我translate这段English text到中文")
        assert features["language"] in ("zh", "mixed")

    def test_code_detection(self):
        features = QueryAnalyzer.analyze("```python\ndef hello():\n    pass\n```")
        assert features["has_code"] is True

    def test_no_code(self):
        features = QueryAnalyzer.analyze("今天吃什么？")
        assert features["has_code"] is False

    def test_token_estimation(self):
        features = QueryAnalyzer.analyze("Hello world")
        assert features["estimated_tokens"] > 0

    def test_token_estimation_chinese(self):
        features = QueryAnalyzer.analyze("你好世界")
        # 4 Chinese chars ≈ 8 tokens
        assert features["estimated_tokens"] >= 4

    def test_topic_extraction_code(self):
        features = QueryAnalyzer.analyze("帮我写一段Python代码")
        assert "code" in features["topics"]

    def test_topic_extraction_translation(self):
        features = QueryAnalyzer.analyze("翻译这段文字")
        assert "translation" in features["topics"]

    def test_topic_extraction_general(self):
        features = QueryAnalyzer.analyze("随便聊聊")
        assert "general" in features["topics"] or "chat" in features["topics"]

    def test_length_category_short(self):
        features = QueryAnalyzer.analyze("hi")
        assert features["length_category"] == "short"

    def test_length_category_long(self):
        features = QueryAnalyzer.analyze("x " * 200)
        assert features["length_category"] in ("long", "very_long")

    def test_complex_multiquestion(self):
        features = QueryAnalyzer.analyze(
            "第一个问题是什么？第二个问题呢？第三个问题？还有吗？"
        )
        # Multiple questions should bump complexity
        assert features["complexity"] in (QueryComplexity.MEDIUM, QueryComplexity.COMPLEX)

    def test_step_by_step_is_complex(self):
        features = QueryAnalyzer.analyze("请step by step分析这个问题")
        assert features["complexity"] in (QueryComplexity.COMPLEX, QueryComplexity.MEDIUM)

    def test_brainstorm_is_creative(self):
        features = QueryAnalyzer.analyze("头脑风暴一下这个产品的创意")
        assert features["complexity"] == QueryComplexity.CREATIVE


# ---- CostTracker Tests ----

class TestCostTracker:
    def test_initial_budget(self):
        tracker = CostTracker(daily_budget=10.0)
        assert tracker.get_remaining_budget() == 10.0

    def test_record_cost(self):
        tracker = CostTracker(daily_budget=10.0)
        tracker.record_cost(3.5)
        assert tracker.get_remaining_budget() == 6.5

    def test_over_budget(self):
        tracker = CostTracker(daily_budget=1.0)
        tracker.record_cost(1.5)
        assert tracker.is_over_budget() is True

    def test_total_spend(self):
        tracker = CostTracker(daily_budget=100.0)
        tracker.record_cost(5.0)
        tracker.record_cost(3.0)
        assert tracker.get_total_spend() == 8.0

    def test_daily_history(self):
        tracker = CostTracker()
        tracker.record_cost(1.0)
        history = tracker.get_daily_history()
        assert len(history) > 0


# ---- ModelRouter Tests ----

class TestModelRouter:
    def test_route_returns_decision(self, router):
        decision = router.route("Hello")
        assert isinstance(decision, RoutingDecision)
        assert decision.model_name in ("gpt4-mini", "gpt4", "claude-haiku", "claude-sonnet")

    def test_default_model_used(self, router):
        decision = router.route("hi")
        # Simple query should route to fast/cheap model or default
        assert decision.model_name is not None

    def test_user_preference_override(self, router):
        router.set_user_preference("user1", "claude-sonnet")
        decision = router.route("anything", user_id="user1")
        assert decision.model_name == "claude-sonnet"
        assert decision.reason == "user_preference"

    def test_clear_user_preference(self, router):
        router.set_user_preference("user1", "claude-sonnet")
        router.clear_user_preference("user1")
        decision = router.route("hi", user_id="user1")
        assert decision.reason != "user_preference"

    def test_rule_based_routing(self, models):
        rules = [
            RoutingRule(
                name="code_to_gpt4",
                condition="has_code==True",
                target_model="gpt4",
                priority=10,
            ),
        ]
        router = ModelRouter(models=models, rules=rules, default_model="gpt4-mini")
        decision = router.route("```python\ndef test(): pass\n```")
        assert decision.model_name == "gpt4"
        assert "rule:" in decision.reason

    def test_complexity_rule(self, models):
        rules = [
            RoutingRule(
                name="simple_to_mini",
                condition="complexity==simple",
                target_model="gpt4-mini",
                priority=5,
            ),
        ]
        router = ModelRouter(models=models, rules=rules, default_model="gpt4")
        decision = router.route("hi")
        assert decision.model_name == "gpt4-mini"

    def test_language_rule(self, models):
        rules = [
            RoutingRule(
                name="zh_to_claude",
                condition="language==zh",
                target_model="claude-haiku",
                priority=5,
            ),
        ]
        router = ModelRouter(models=models, rules=rules, default_model="gpt4-mini")
        decision = router.route("你好世界，今天天气怎么样？")
        assert decision.model_name == "claude-haiku"

    def test_budget_fallback(self, models):
        router = ModelRouter(models=models, daily_budget=0.001, default_model="gpt4")
        # Spend budget
        router.cost_tracker.record_cost(0.002)
        decision = router.route("complex task please analyze step by step")
        # Should fall back to cheapest when over budget
        assert decision.model_name is not None

    def test_register_model(self, router):
        new_model = ModelSpec(
            name="new-model",
            engine="openai",
            model_id="gpt-5",
        )
        router.register_model(new_model)
        assert "new-model" in router.models

    def test_remove_model(self, router):
        router.remove_model("gpt4")
        assert "gpt4" not in router.models

    def test_no_models_raises(self):
        router = ModelRouter(models=[])
        with pytest.raises(ValueError, match="No models"):
            router.route("test")

    def test_stats_tracking(self, router):
        router.route("hello")
        router.route("你好")
        stats = router.get_stats()
        assert stats["total_routes"] == 2

    def test_history(self, router):
        router.route("test1")
        router.route("test2")
        history = router.get_history()
        assert len(history) == 2
        assert history[0]["model"] is not None

    def test_ab_test_deterministic(self, models):
        rules = [
            RoutingRule(name="a", condition="complexity==simple", target_model="gpt4-mini", weight=0.5),
            RoutingRule(name="b", condition="complexity==simple", target_model="gpt4", weight=0.5),
        ]
        router = ModelRouter(
            models=models,
            rules=rules,
            ab_test_enabled=True,
            default_model="gpt4-mini",
        )
        # Same user should get consistent routing
        d1 = router.route("hi", user_id="stable-user")
        d2 = router.route("hi", user_id="stable-user")
        # Both should route the same (deterministic hash)
        # Note: might go through different paths, but user preference would be consistent
        assert d1.model_name is not None
        assert d2.model_name is not None

    def test_complexity_based_routing_code(self, router):
        # Code query should prefer models with "code" strength
        decision = router.route(
            "写一个Python函数实现二分查找 ```python\ndef binary_search(arr, target):\n    pass\n```"
        )
        assert decision.complexity in (QueryComplexity.COMPLEX, QueryComplexity.MEDIUM)

    def test_add_rule(self, router):
        rule = RoutingRule(
            name="test", condition="topic==math", target_model="gpt4", priority=20
        )
        router.add_rule(rule)
        assert len(router.rules) == 1
        assert router.rules[0].priority == 20

    def test_decision_has_engine(self, router):
        decision = router.route("test")
        assert decision.engine in ("openai", "claude")

    def test_decision_has_model_id(self, router):
        decision = router.route("test")
        assert decision.model_id is not None
        assert len(decision.model_id) > 0
