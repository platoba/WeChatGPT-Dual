"""Tests for ConversationInsights engine."""
import os
import pytest
import tempfile
from services.conversation_insights import (
    ConversationInsights,
)


@pytest.fixture
def insights():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    ci = ConversationInsights(db_path=db_path)
    yield ci
    os.unlink(db_path)


class TestSentimentAnalysis:
    def test_positive_text(self, insights):
        result = insights.analyze_sentiment("This is great! I love it!")
        assert result.label == 'positive'
        assert result.score > 0

    def test_negative_text(self, insights):
        result = insights.analyze_sentiment("This is terrible, I hate it")
        assert result.label == 'negative'
        assert result.score < 0

    def test_neutral_text(self, insights):
        result = insights.analyze_sentiment("The meeting is at 3pm")
        assert result.label == 'neutral'

    def test_empty_text(self, insights):
        result = insights.analyze_sentiment("")
        assert result.label == 'neutral'
        assert result.score == 0.0

    def test_chinese_positive(self, insights):
        result = insights.analyze_sentiment("这个功能太棒了！非常好用")
        assert result.label == 'positive'
        assert result.score > 0

    def test_chinese_negative(self, insights):
        result = insights.analyze_sentiment("这个太差了，非常失望")
        assert result.label == 'negative'
        assert result.score < 0

    def test_negation(self, insights):
        result = insights.analyze_sentiment("This is not good at all")
        # "not good" should flip to negative
        assert result.score < 0.5  # at least less positive

    def test_intensifier(self, insights):
        base = insights.analyze_sentiment("good")
        intensified = insights.analyze_sentiment("very good")
        assert intensified.score >= base.score

    def test_confidence_range(self, insights):
        result = insights.analyze_sentiment("Great amazing wonderful")
        assert 0 <= result.confidence <= 1.0

    def test_positive_terms_returned(self, insights):
        result = insights.analyze_sentiment("I love this amazing product")
        assert len(result.positive_terms) > 0

    def test_negative_terms_returned(self, insights):
        result = insights.analyze_sentiment("This is terrible and broken")
        assert len(result.negative_terms) > 0

    def test_mixed_sentiment(self, insights):
        result = insights.analyze_sentiment("The food was good but the service was terrible")
        assert isinstance(result.score, float)

    def test_emoji_boost(self, insights):
        no_emoji = insights.analyze_sentiment("nice work")
        with_emoji = insights.analyze_sentiment("nice work 🎉🎊")
        assert with_emoji.score >= no_emoji.score

    def test_exclamation_boost(self, insights):
        base = insights.analyze_sentiment("great")
        excited = insights.analyze_sentiment("great!!!")
        assert excited.score >= base.score

    def test_score_range(self, insights):
        for text in ["amazing", "terrible", "okay", "very very good", "absolutely awful"]:
            result = insights.analyze_sentiment(text)
            assert -1.0 <= result.score <= 1.0

    def test_double_negation(self, insights):
        result = insights.analyze_sentiment("not bad")
        # "not bad" = weakly positive
        assert result.score >= 0


class TestTopicExtraction:
    def test_basic_extraction(self, insights):
        topics = insights.extract_topics("Python programming and machine learning are important")
        assert len(topics) > 0
        assert any('python' in t for t in topics)

    def test_chinese_topics(self, insights):
        topics = insights.extract_topics("跨境电商和人工智能是未来趋势")
        assert len(topics) > 0

    def test_empty_text(self, insights):
        topics = insights.extract_topics("")
        assert topics == []

    def test_max_topics(self, insights):
        text = "apple banana cherry date elderberry fig grape honeydew"
        topics = insights.extract_topics(text, max_topics=3)
        assert len(topics) <= 3

    def test_stop_words_filtered(self, insights):
        topics = insights.extract_topics("the quick brown fox jumps over the lazy dog")
        # Common stop words should be filtered
        assert 'the' not in topics


class TestLanguageDetection:
    def test_english(self, insights):
        assert insights.detect_language("Hello world") == 'en'

    def test_chinese(self, insights):
        assert insights.detect_language("你好世界") == 'zh'

    def test_mixed(self, insights):
        lang = insights.detect_language("Hello你好World世界Test")
        assert lang in ('mixed', 'zh')

    def test_empty(self, insights):
        assert insights.detect_language("") == 'unknown'

    def test_numbers_only(self, insights):
        assert insights.detect_language("12345") == 'unknown'


class TestQuestionDetection:
    def test_question_mark(self, insights):
        assert insights.has_question("What is this?") is True

    def test_chinese_question(self, insights):
        assert insights.has_question("这是什么？") is True

    def test_question_word(self, insights):
        assert insights.has_question("How does this work") is True

    def test_chinese_ma(self, insights):
        assert insights.has_question("你好吗") is True

    def test_statement(self, insights):
        assert insights.has_question("The sky is blue.") is False


class TestRecordMessage:
    def test_record_and_retrieve(self, insights):
        insights.record_message("msg1", "user1", "This is a great day!")
        history = insights.get_user_sentiment_history("user1")
        assert len(history) == 1
        assert history[0]['sentiment_label'] == 'positive'

    def test_multiple_messages(self, insights):
        insights.record_message("msg1", "user1", "Great!")
        insights.record_message("msg2", "user1", "Terrible.")
        insights.record_message("msg3", "user1", "Okay then")
        history = insights.get_user_sentiment_history("user1")
        assert len(history) == 3

    def test_topic_tracking(self, insights):
        insights.record_message("msg1", "user1", "Python programming is fun")
        insights.record_message("msg2", "user1", "Python development tools")
        topics = insights.get_top_topics("user1")
        assert len(topics) > 0
        assert any(t.topic == 'python' for t in topics)

    def test_engagement_metrics(self, insights):
        insights.record_message("msg1", "user1", "Hello world")
        insights.record_message("msg2", "user1", "How are you?")
        report = insights.get_engagement_report("user1")
        assert report.total_messages == 2

    def test_different_users_isolated(self, insights):
        insights.record_message("msg1", "user1", "Great!")
        insights.record_message("msg2", "user2", "Terrible!")
        h1 = insights.get_user_sentiment_history("user1")
        h2 = insights.get_user_sentiment_history("user2")
        assert len(h1) == 1
        assert len(h2) == 1
        assert h1[0]['sentiment_label'] != h2[0]['sentiment_label']


class TestEngagementReport:
    def test_empty_report(self, insights):
        report = insights.get_engagement_report("nonexistent")
        assert report.total_messages == 0
        assert report.quality_score == 0.0

    def test_sentiment_trend_stable(self, insights):
        for i in range(5):
            insights.record_message(f"msg{i}", "user1", "okay fine")
        report = insights.get_engagement_report("user1")
        assert report.sentiment_trend == 'stable'

    def test_question_ratio(self, insights):
        insights.record_message("msg1", "user1", "What is this?")
        insights.record_message("msg2", "user1", "How does it work?")
        insights.record_message("msg3", "user1", "The sky is blue")
        report = insights.get_engagement_report("user1")
        assert report.question_ratio > 0

    def test_quality_score_range(self, insights):
        for i in range(10):
            insights.record_message(f"msg{i}", "user1", f"This is message number {i} about coding")
        report = insights.get_engagement_report("user1")
        assert 0 <= report.quality_score <= 100


class TestSentimentDistribution:
    def test_distribution(self, insights):
        insights.record_message("msg1", "user1", "Great!")
        insights.record_message("msg2", "user1", "Terrible!")
        insights.record_message("msg3", "user1", "Okay")
        dist = insights.get_sentiment_distribution("user1")
        assert isinstance(dist, dict)
        total = sum(dist.values())
        assert total == 3

    def test_global_distribution(self, insights):
        insights.record_message("msg1", "user1", "Great!")
        insights.record_message("msg2", "user2", "Terrible!")
        dist = insights.get_sentiment_distribution()
        total = sum(dist.values())
        assert total == 2


class TestGlobalStats:
    def test_stats(self, insights):
        insights.record_message("msg1", "user1", "Hello")
        insights.record_message("msg2", "user2", "World")
        stats = insights.get_global_stats()
        assert stats['total_messages_analyzed'] == 2
        assert stats['total_users'] == 2


class TestTextReport:
    def test_user_report(self, insights):
        for i in range(5):
            insights.record_message(f"msg{i}", "user1", f"Message about coding and Python {i}")
        report = insights.generate_text_report("user1")
        assert "user1" in report
        assert "Insights Report" in report

    def test_global_report(self, insights):
        insights.record_message("msg1", "user1", "Hello great day")
        report = insights.generate_text_report()
        assert "Global Statistics" in report
