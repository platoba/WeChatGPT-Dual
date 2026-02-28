"""
tests/test_ab_testing.py - A/B 测试服务测试
"""

import os
import time
import math
import pytest
import threading
from unittest.mock import MagicMock

from services.ab_testing import (
    ABTestService, ABTestStore,
    Experiment, Variant, ExperimentStatus, VariantType,
    FeedbackType, TrafficAllocator, StatisticalTest,
)


# ──────────────── StatisticalTest ────────────────

class TestStatisticalTest:
    def test_z_test_equal(self):
        """相同比例应该不显著"""
        z, p, sig = StatisticalTest.z_test_proportions(50, 100, 50, 100)
        assert z == 0.0
        assert p == pytest.approx(1.0, abs=0.01)
        assert sig is False

    def test_z_test_significant(self):
        """明显差异应该显著"""
        z, p, sig = StatisticalTest.z_test_proportions(80, 100, 20, 100)
        assert abs(z) > 2
        assert p < 0.05
        assert sig is True

    def test_z_test_zero_samples(self):
        z, p, sig = StatisticalTest.z_test_proportions(0, 0, 0, 0)
        assert sig is False

    def test_z_test_one_zero(self):
        z, p, sig = StatisticalTest.z_test_proportions(10, 10, 0, 0)
        assert sig is False

    def test_z_test_all_success(self):
        """全部成功"""
        z, p, sig = StatisticalTest.z_test_proportions(100, 100, 100, 100)
        assert sig is False

    def test_z_test_moderate_difference(self):
        """中等差异"""
        z, p, sig = StatisticalTest.z_test_proportions(60, 100, 40, 100)
        assert abs(z) > 1.5

    def test_confidence_interval(self):
        low, high = StatisticalTest.confidence_interval(50, 100)
        assert low < 0.5
        assert high > 0.5
        assert low > 0
        assert high < 1

    def test_confidence_interval_zero(self):
        low, high = StatisticalTest.confidence_interval(0, 0)
        assert low == 0.0
        assert high == 0.0

    def test_confidence_interval_all_success(self):
        low, high = StatisticalTest.confidence_interval(100, 100)
        assert high > 0.95

    def test_confidence_interval_99(self):
        low95, high95 = StatisticalTest.confidence_interval(50, 100, 0.95)
        low99, high99 = StatisticalTest.confidence_interval(50, 100, 0.99)
        # 99% CI should be wider
        assert (high99 - low99) > (high95 - low95)

    def test_sample_size_needed(self):
        n = StatisticalTest.sample_size_needed(0.1, 0.05)
        assert n > 0
        assert isinstance(n, int)

    def test_sample_size_zero_mde(self):
        n = StatisticalTest.sample_size_needed(0.5, 0.0)
        assert n == 0

    def test_sample_size_high_power(self):
        n80 = StatisticalTest.sample_size_needed(0.1, 0.05, power=0.80)
        n90 = StatisticalTest.sample_size_needed(0.1, 0.05, power=0.90)
        assert n90 > n80

    def test_normal_cdf(self):
        # CDF(0) ≈ 0.5
        assert StatisticalTest._normal_cdf(0) == pytest.approx(0.5, abs=0.01)
        # CDF(-inf) ≈ 0
        assert StatisticalTest._normal_cdf(-10) < 0.001
        # CDF(inf) ≈ 1
        assert StatisticalTest._normal_cdf(10) > 0.999


# ──────────────── TrafficAllocator ────────────────

class TestTrafficAllocator:
    @pytest.fixture
    def variants(self):
        return [
            Variant(id="a", name="A", weight=1.0),
            Variant(id="b", name="B", weight=1.0),
        ]

    def test_uniform_random(self, variants):
        result = TrafficAllocator.uniform_random(variants)
        assert result in variants

    def test_uniform_empty(self):
        with pytest.raises(ValueError):
            TrafficAllocator.uniform_random([])

    def test_weighted_random(self, variants):
        result = TrafficAllocator.weighted_random(variants)
        assert result in variants

    def test_weighted_heavy(self):
        """高权重变体应该被更多选中"""
        variants = [
            Variant(id="a", name="A", weight=100.0),
            Variant(id="b", name="B", weight=0.001),
        ]
        counts = {"A": 0, "B": 0}
        for _ in range(1000):
            v = TrafficAllocator.weighted_random(variants)
            counts[v.name] += 1
        assert counts["A"] > 900

    def test_weighted_empty(self):
        with pytest.raises(ValueError):
            TrafficAllocator.weighted_random([])

    def test_user_sticky(self, variants):
        # 同一用户应该总是得到同一变体
        v1 = TrafficAllocator.user_sticky("user123", variants)
        v2 = TrafficAllocator.user_sticky("user123", variants)
        assert v1.id == v2.id

    def test_user_sticky_different_users(self, variants):
        """不同用户可能得到不同变体"""
        seen = set()
        for i in range(100):
            v = TrafficAllocator.user_sticky(f"user{i}", variants)
            seen.add(v.id)
        # With 100 users and 2 variants, should see both
        assert len(seen) == 2

    def test_user_sticky_empty(self):
        with pytest.raises(ValueError):
            TrafficAllocator.user_sticky("user1", [])


# ──────────────── Variant ────────────────

class TestVariant:
    def test_feedback_rate_zero(self):
        v = Variant()
        assert v.feedback_rate == 0.0

    def test_feedback_rate(self):
        v = Variant(positive_feedback=7, negative_feedback=3)
        assert v.feedback_rate == pytest.approx(0.7)

    def test_average_rating_zero(self):
        v = Variant()
        assert v.average_rating == 0.0

    def test_average_rating(self):
        v = Variant(total_rating=20.0, rating_count=5)
        assert v.average_rating == pytest.approx(4.0)


# ──────────────── ABTestStore ────────────────

class TestABTestStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ABTestStore(str(tmp_path / "test_ab.db"))

    def test_save_and_get(self, store):
        exp = Experiment(
            name="Test Exp",
            description="Testing",
            variants=[
                Variant(name="A", variant_type=VariantType.PROMPT, value="prompt A"),
                Variant(name="B", variant_type=VariantType.PROMPT, value="prompt B"),
            ],
        )
        exp_id = store.save_experiment(exp)
        assert exp_id

        loaded = store.get_experiment(exp_id)
        assert loaded is not None
        assert loaded.name == "Test Exp"
        assert len(loaded.variants) == 2

    def test_get_nonexistent(self, store):
        assert store.get_experiment("nonexistent") is None

    def test_list_experiments(self, store):
        for i in range(3):
            exp = Experiment(
                name=f"Exp {i}",
                variants=[
                    Variant(name="A", value="a"),
                    Variant(name="B", value="b"),
                ],
            )
            store.save_experiment(exp)

        all_exps = store.list_experiments()
        assert len(all_exps) == 3

    def test_list_by_status(self, store):
        exp = Experiment(
            name="Running",
            status=ExperimentStatus.RUNNING,
            variants=[Variant(name="A"), Variant(name="B")],
        )
        store.save_experiment(exp)

        running = store.list_experiments("running")
        assert len(running) == 1
        assert running[0].status == ExperimentStatus.RUNNING

    def test_record_assignment(self, store):
        exp = Experiment(
            name="Test",
            variants=[
                Variant(name="A", value="a"),
                Variant(name="B", value="b"),
            ],
        )
        exp_id = store.save_experiment(exp)
        loaded = store.get_experiment(exp_id)

        store.record_assignment(exp_id, loaded.variants[0].id, "user1")

        assigned = store.get_user_assignment(exp_id, "user1")
        assert assigned == loaded.variants[0].id

    def test_assignment_sticky(self, store):
        exp = Experiment(
            name="Test",
            variants=[Variant(name="A"), Variant(name="B")],
        )
        exp_id = store.save_experiment(exp)
        loaded = store.get_experiment(exp_id)

        store.record_assignment(exp_id, loaded.variants[0].id, "user1")
        store.record_assignment(exp_id, loaded.variants[1].id, "user1")  # should be ignored

        assigned = store.get_user_assignment(exp_id, "user1")
        assert assigned == loaded.variants[0].id

    def test_no_assignment(self, store):
        assert store.get_user_assignment("exp1", "user1") is None

    def test_record_thumbs_feedback(self, store):
        exp = Experiment(
            name="Test",
            variants=[Variant(name="A"), Variant(name="B")],
        )
        exp_id = store.save_experiment(exp)
        loaded = store.get_experiment(exp_id)
        vid = loaded.variants[0].id

        store.record_feedback(exp_id, vid, "u1", FeedbackType.THUMBS, "up")
        store.record_feedback(exp_id, vid, "u2", FeedbackType.THUMBS, "down")

        reloaded = store.get_experiment(exp_id)
        v = [v for v in reloaded.variants if v.id == vid][0]
        assert v.positive_feedback == 1
        assert v.negative_feedback == 1

    def test_record_rating_feedback(self, store):
        exp = Experiment(
            name="Test",
            variants=[Variant(name="A"), Variant(name="B")],
        )
        exp_id = store.save_experiment(exp)
        loaded = store.get_experiment(exp_id)
        vid = loaded.variants[0].id

        store.record_feedback(exp_id, vid, "u1", FeedbackType.RATING, "5")
        store.record_feedback(exp_id, vid, "u2", FeedbackType.RATING, "4")
        store.record_feedback(exp_id, vid, "u3", FeedbackType.RATING, "1")

        reloaded = store.get_experiment(exp_id)
        v = [v for v in reloaded.variants if v.id == vid][0]
        assert v.rating_count == 3
        assert v.total_rating == 10.0
        assert v.positive_feedback == 2  # 5 and 4
        assert v.negative_feedback == 1  # 1

    def test_update_status(self, store):
        exp = Experiment(
            name="Test",
            variants=[Variant(name="A"), Variant(name="B")],
        )
        exp_id = store.save_experiment(exp)

        store.update_experiment_status(
            exp_id, ExperimentStatus.RUNNING
        )
        loaded = store.get_experiment(exp_id)
        assert loaded.status == ExperimentStatus.RUNNING
        assert loaded.started_at > 0

        store.update_experiment_status(
            exp_id, ExperimentStatus.COMPLETED,
            winner="A", significance=0.97,
        )
        loaded = store.get_experiment(exp_id)
        assert loaded.status == ExperimentStatus.COMPLETED
        assert loaded.winner == "A"
        assert loaded.completed_at > 0


# ──────────────── ABTestService ────────────────

class TestABTestService:
    @pytest.fixture
    def service(self, tmp_path):
        return ABTestService(db_path=str(tmp_path / "test_ab.db"))

    def test_create_experiment(self, service):
        exp = service.create_experiment(
            "Prompt Test",
            variants=[
                {"name": "Control", "type": "prompt", "value": "You are helpful"},
                {"name": "Friendly", "type": "prompt", "value": "You are friendly and warm"},
            ],
        )
        assert exp.id
        assert exp.name == "Prompt Test"
        assert len(exp.variants) == 2
        assert exp.status == ExperimentStatus.DRAFT

    def test_create_auto_start(self, service):
        exp = service.create_experiment(
            "Auto Start",
            variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        assert exp.status == ExperimentStatus.RUNNING

    def test_create_too_few_variants(self, service):
        with pytest.raises(ValueError, match="at least 2"):
            service.create_experiment(
                "Bad", variants=[{"name": "A", "value": "a"}]
            )

    def test_start_experiment(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ]
        )
        assert service.start_experiment(exp.id) is True

        loaded = service.store.get_experiment(exp.id)
        assert loaded.status == ExperimentStatus.RUNNING

    def test_start_nonexistent(self, service):
        assert service.start_experiment("fake") is False

    def test_start_already_running(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        assert service.start_experiment(exp.id) is False

    def test_pause_resume(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        assert service.pause_experiment(exp.id) is True
        loaded = service.store.get_experiment(exp.id)
        assert loaded.status == ExperimentStatus.PAUSED

        assert service.resume_experiment(exp.id) is True
        loaded = service.store.get_experiment(exp.id)
        assert loaded.status == ExperimentStatus.RUNNING

    def test_abort(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        assert service.abort_experiment(exp.id) is True
        loaded = service.store.get_experiment(exp.id)
        assert loaded.status == ExperimentStatus.ABORTED

    def test_assign_variant(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        v = service.assign_variant(exp.id, "user1")
        assert v is not None
        assert v.name in ("A", "B")

    def test_assign_sticky(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        v1 = service.assign_variant(exp.id, "user1", sticky=True)
        v2 = service.assign_variant(exp.id, "user1", sticky=True)
        assert v1.id == v2.id

    def test_assign_not_running(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
        )
        assert service.assign_variant(exp.id, "user1") is None

    def test_record_feedback(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        v = service.assign_variant(exp.id, "user1")

        result = service.record_feedback(
            exp.id, "user1", FeedbackType.THUMBS, "up"
        )
        assert result is True

    def test_record_feedback_no_assignment(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        result = service.record_feedback(
            exp.id, "unassigned_user", FeedbackType.THUMBS, "up"
        )
        assert result is False

    def test_analyze_basic(self, service):
        exp = service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )

        # Simulate feedback
        for i in range(50):
            v = service.assign_variant(exp.id, f"user_a_{i}", sticky=False)
        for i in range(50):
            service.record_feedback(
                exp.id, f"user_a_{i}", FeedbackType.THUMBS,
                "up" if i < 40 else "down",
                variant_id=exp.variants[0].id,
            )
        for i in range(50):
            service.record_feedback(
                exp.id, f"user_b_{i}", FeedbackType.THUMBS,
                "up" if i < 20 else "down",
                variant_id=exp.variants[1].id,
            )

        analysis = service.analyze(exp.id)
        assert "variants" in analysis
        assert "comparisons" in analysis
        assert "recommendation" in analysis

    def test_analyze_nonexistent(self, service):
        result = service.analyze("fake")
        assert "error" in result

    def test_generate_report(self, service):
        exp = service.create_experiment(
            "Report Test", variants=[
                {"name": "Control", "value": "a"},
                {"name": "Treatment", "value": "b"},
            ],
            auto_start=True,
        )
        report = service.generate_report(exp.id)
        assert "Report Test" in report
        assert "Control" in report
        assert "Treatment" in report

    def test_generate_report_nonexistent(self, service):
        report = service.generate_report("fake")
        assert "Error" in report

    def test_get_active_experiments(self, service):
        service.create_experiment(
            "Active", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        service.create_experiment(
            "Draft", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
        )
        active = service.get_active_experiments()
        assert len(active) == 1

    def test_get_stats(self, service):
        service.create_experiment(
            "Test", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            auto_start=True,
        )
        stats = service.get_stats()
        assert stats["total_experiments"] == 1
        assert stats["by_status"]["running"] == 1

    def test_multi_variant(self, service):
        """三变体实验"""
        exp = service.create_experiment(
            "Multi", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
                {"name": "C", "value": "c"},
            ],
            auto_start=True,
        )
        assert len(exp.variants) == 3

    def test_model_variant(self, service):
        """模型变体实验"""
        exp = service.create_experiment(
            "Model Test", variants=[
                {"name": "GPT-4", "type": "model", "value": "gpt-4o"},
                {"name": "Claude", "type": "model", "value": "claude-3-haiku"},
            ],
            auto_start=True,
        )
        assert exp.variants[0].variant_type == VariantType.MODEL

    def test_auto_stop_on_significance(self, service):
        """达到显著性时自动停止"""
        exp = service.create_experiment(
            "Auto Stop", variants=[
                {"name": "A", "value": "a"},
                {"name": "B", "value": "b"},
            ],
            min_samples=10,
            auto_start=True,
        )

        # 模拟大量反馈使A明显优于B
        for i in range(30):
            service.assign_variant(exp.id, f"user_{i}")

        vid_a = exp.variants[0].id
        vid_b = exp.variants[1].id

        # A: 90% positive, B: 10% positive
        for i in range(30):
            service.store.record_feedback(
                exp.id, vid_a, f"u_a_{i}", FeedbackType.THUMBS, "up"
            )
        for i in range(5):
            service.store.record_feedback(
                exp.id, vid_a, f"u_an_{i}", FeedbackType.THUMBS, "down"
            )
        for i in range(5):
            service.store.record_feedback(
                exp.id, vid_b, f"u_b_{i}", FeedbackType.THUMBS, "up"
            )
        for i in range(30):
            service.store.record_feedback(
                exp.id, vid_b, f"u_bn_{i}", FeedbackType.THUMBS, "down"
            )

        # Force check
        reloaded = service.store.get_experiment(exp.id)
        service._check_auto_stop(reloaded)

        final = service.store.get_experiment(exp.id)
        assert final.status == ExperimentStatus.COMPLETED
        assert final.winner == "A"
