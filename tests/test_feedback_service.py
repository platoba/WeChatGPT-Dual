"""Tests for FeedbackService."""

import json
import pytest

from services.feedback_service import (
    FeedbackService, FeedbackEntry,
)


@pytest.fixture
def svc(tmp_path):
    db = str(tmp_path / "feedback_test.db")
    return FeedbackService(db_path=db)


class TestThumbsFeedback:
    def test_submit_thumbs_up(self, svc):
        entry = svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        assert entry.rating_type == "thumbs"
        assert entry.rating_value == 1
        assert entry.user_id == "user1"
        assert entry.message_id == "msg1"
        assert entry.is_positive()
        assert not entry.is_negative()

    def test_submit_thumbs_down(self, svc):
        entry = svc.submit_thumbs("user1", "msg2", thumbs_up=False)
        assert entry.rating_value == 0
        assert entry.is_negative()
        assert not entry.is_positive()

    def test_thumbs_with_comment(self, svc):
        entry = svc.submit_thumbs(
            "user1", "msg1", thumbs_up=True,
            comment="Great response!",
            model_used="gpt-4",
        )
        assert entry.comment == "Great response!"
        assert entry.model_used == "gpt-4"

    def test_thumbs_with_metadata(self, svc):
        entry = svc.submit_thumbs(
            "user1", "msg1", thumbs_up=True,
            metadata={"source": "telegram"},
            tags=["helpful"],
        )
        assert entry.metadata == {"source": "telegram"}
        assert entry.tags == ["helpful"]

    def test_thumbs_with_original(self, svc):
        entry = svc.submit_thumbs(
            "user1", "msg1", thumbs_up=False,
            original_response="Bad answer",
            user_query="What is 2+2?",
        )
        assert entry.original_response == "Bad answer"
        assert entry.user_query == "What is 2+2?"


class TestStarsFeedback:
    def test_submit_5_stars(self, svc):
        entry = svc.submit_stars("user1", "msg1", stars=5)
        assert entry.rating_type == "stars"
        assert entry.rating_value == 5
        assert entry.is_positive()

    def test_submit_1_star(self, svc):
        entry = svc.submit_stars("user1", "msg1", stars=1)
        assert entry.rating_value == 1
        assert entry.is_negative()

    def test_submit_3_stars_neutral(self, svc):
        entry = svc.submit_stars("user1", "msg1", stars=3)
        assert not entry.is_positive()
        assert not entry.is_negative()

    def test_invalid_stars_too_high(self, svc):
        with pytest.raises(ValueError, match="Stars must be 1-5"):
            svc.submit_stars("user1", "msg1", stars=6)

    def test_invalid_stars_too_low(self, svc):
        with pytest.raises(ValueError, match="Stars must be 1-5"):
            svc.submit_stars("user1", "msg1", stars=0)

    def test_stars_with_comment(self, svc):
        entry = svc.submit_stars(
            "user1", "msg1", stars=4, comment="Pretty good",
            conversation_id="conv1",
        )
        assert entry.comment == "Pretty good"
        assert entry.conversation_id == "conv1"


class TestCorrectionFeedback:
    def test_submit_correction(self, svc):
        entry = svc.submit_correction(
            "user1", "msg1",
            correction="The correct answer is 4",
            original_response="The answer is 5",
            user_query="What is 2+2?",
        )
        assert entry.rating_type == "correction"
        assert entry.rating_value == 0
        assert entry.correction == "The correct answer is 4"
        assert entry.is_negative()

    def test_empty_correction_raises(self, svc):
        with pytest.raises(ValueError, match="Correction text cannot be empty"):
            svc.submit_correction("user1", "msg1", correction="")

    def test_whitespace_correction_raises(self, svc):
        with pytest.raises(ValueError, match="Correction text cannot be empty"):
            svc.submit_correction("user1", "msg1", correction="   ")

    def test_correction_with_model(self, svc):
        entry = svc.submit_correction(
            "user1", "msg1",
            correction="Better answer here",
            model_used="claude-3",
        )
        assert entry.model_used == "claude-3"


class TestGetFeedback:
    def test_get_by_id(self, svc):
        entry = svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        fetched = svc.get_feedback(entry.feedback_id)
        assert fetched is not None
        assert fetched.feedback_id == entry.feedback_id
        assert fetched.user_id == "user1"

    def test_get_nonexistent(self, svc):
        assert svc.get_feedback("nonexistent") is None

    def test_get_for_message(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_stars("user2", "msg1", stars=4)
        svc.submit_thumbs("user3", "msg2", thumbs_up=False)
        results = svc.get_feedback_for_message("msg1")
        assert len(results) == 2

    def test_get_user_feedback(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_thumbs("user1", "msg2", thumbs_up=False)
        svc.submit_thumbs("user2", "msg3", thumbs_up=True)
        results = svc.get_user_feedback("user1")
        assert len(results) == 2

    def test_get_user_feedback_by_type(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_stars("user1", "msg2", stars=5)
        results = svc.get_user_feedback("user1", rating_type="stars")
        assert len(results) == 1
        assert results[0].rating_type == "stars"

    def test_get_user_feedback_pagination(self, svc):
        for i in range(10):
            svc.submit_thumbs("user1", f"msg{i}", thumbs_up=True)
        results = svc.get_user_feedback("user1", limit=3, offset=0)
        assert len(results) == 3
        results2 = svc.get_user_feedback("user1", limit=3, offset=3)
        assert len(results2) == 3


class TestQualityMetrics:
    def test_empty_metrics(self, svc):
        metrics = svc.get_quality_metrics()
        assert metrics.total_feedback == 0
        assert metrics.satisfaction_rate == 0

    def test_thumbs_metrics(self, svc):
        for i in range(8):
            svc.submit_thumbs("user1", f"msg{i}", thumbs_up=True)
        for i in range(2):
            svc.submit_thumbs("user1", f"msg_neg{i}", thumbs_up=False)
        metrics = svc.get_quality_metrics()
        assert metrics.thumbs_up == 8
        assert metrics.thumbs_down == 2
        assert metrics.satisfaction_rate == 80.0

    def test_star_metrics(self, svc):
        svc.submit_stars("user1", "msg1", stars=5)
        svc.submit_stars("user1", "msg2", stars=4)
        svc.submit_stars("user1", "msg3", stars=2)
        metrics = svc.get_quality_metrics()
        assert metrics.star_count == 3
        assert metrics.avg_star_rating > 0

    def test_model_filter(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True, model_used="gpt-4")
        svc.submit_thumbs("user1", "msg2", thumbs_up=False, model_used="claude")
        metrics = svc.get_quality_metrics(model="gpt-4")
        assert metrics.thumbs_up == 1
        assert metrics.thumbs_down == 0

    def test_correction_count(self, svc):
        svc.submit_correction("user1", "msg1", correction="fix1")
        svc.submit_correction("user1", "msg2", correction="fix2")
        metrics = svc.get_quality_metrics()
        assert metrics.corrections_count == 2


class TestModelComparison:
    def test_compare_models(self, svc):
        for i in range(5):
            svc.submit_thumbs("user1", f"gpt{i}", thumbs_up=True, model_used="gpt-4")
        for i in range(3):
            svc.submit_thumbs("user1", f"claude{i}", thumbs_up=True, model_used="claude")
        svc.submit_thumbs("user1", "claude_bad", thumbs_up=False, model_used="claude")
        comp = svc.get_model_comparison()
        assert "gpt-4" in comp
        assert "claude" in comp
        assert comp["gpt-4"]["satisfaction_rate"] == 100.0
        assert comp["claude"]["positive"] == 3
        assert comp["claude"]["negative"] == 1

    def test_empty_comparison(self, svc):
        comp = svc.get_model_comparison()
        assert comp == {}


class TestCorrections:
    def test_get_corrections(self, svc):
        svc.submit_correction(
            "user1", "msg1",
            correction="4", original_response="5",
            user_query="2+2?", model_used="gpt-4",
        )
        corrs = svc.get_corrections()
        assert len(corrs) == 1
        assert corrs[0]["query"] == "2+2?"
        assert corrs[0]["correction"] == "4"

    def test_get_corrections_by_model(self, svc):
        svc.submit_correction("user1", "msg1", correction="fix1", model_used="gpt-4")
        svc.submit_correction("user1", "msg2", correction="fix2", model_used="claude")
        corrs = svc.get_corrections(model="gpt-4")
        assert len(corrs) == 1
        assert corrs[0]["model"] == "gpt-4"


class TestSuggestions:
    def test_good_quality_suggestions(self, svc):
        for i in range(10):
            svc.submit_thumbs("user1", f"msg{i}", thumbs_up=True)
        suggestions = svc.suggest_improvements()
        assert any(s["area"] == "status" for s in suggestions)

    def test_low_satisfaction_suggestion(self, svc):
        for i in range(2):
            svc.submit_thumbs("user1", f"good{i}", thumbs_up=True)
        for i in range(8):
            svc.submit_thumbs("user1", f"bad{i}", thumbs_up=False)
        suggestions = svc.suggest_improvements()
        assert any(s["area"] == "overall_quality" for s in suggestions)

    def test_many_corrections_suggestion(self, svc):
        for i in range(15):
            svc.submit_correction("user1", f"msg{i}", correction=f"fix{i}")
        suggestions = svc.suggest_improvements()
        assert any(s["area"] == "accuracy" for s in suggestions)


class TestExport:
    def test_export_json(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_stars("user1", "msg2", stars=4)
        data = json.loads(svc.export_json())
        assert len(data) == 2

    def test_export_csv(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        csv_data = svc.export_csv()
        assert "feedback_id" in csv_data
        assert "user1" in csv_data

    def test_export_with_model_filter(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True, model_used="gpt-4")
        svc.submit_thumbs("user1", "msg2", thumbs_up=True, model_used="claude")
        data = json.loads(svc.export_json(model="gpt-4"))
        assert len(data) == 1


class TestDelete:
    def test_delete_feedback(self, svc):
        entry = svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        assert svc.delete_feedback(entry.feedback_id)
        assert svc.get_feedback(entry.feedback_id) is None

    def test_delete_nonexistent(self, svc):
        assert not svc.delete_feedback("nonexistent")


class TestCount:
    def test_count_all(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_stars("user1", "msg2", stars=5)
        assert svc.count_feedback() == 2

    def test_count_by_user(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_thumbs("user2", "msg2", thumbs_up=True)
        assert svc.count_feedback(user_id="user1") == 1

    def test_count_by_type(self, svc):
        svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        svc.submit_stars("user1", "msg2", stars=5)
        assert svc.count_feedback(rating_type="thumbs") == 1
        assert svc.count_feedback(rating_type="stars") == 1


class TestFeedbackEntry:
    def test_to_dict(self, svc):
        entry = svc.submit_thumbs("user1", "msg1", thumbs_up=True)
        d = entry.to_dict()
        assert "feedback_id" in d
        assert d["user_id"] == "user1"
        assert d["rating_type"] == "thumbs"

    def test_positive_negative_methods(self):
        entry = FeedbackEntry(
            feedback_id="t1", user_id="u1", message_id="m1",
            conversation_id="", model_used="", rating_type="stars",
            rating_value=5, comment="", correction="",
            original_response="", user_query="",
            tags=[], metadata={}, created_at=0,
        )
        assert entry.is_positive()
        entry.rating_value = 2
        assert entry.is_negative()
