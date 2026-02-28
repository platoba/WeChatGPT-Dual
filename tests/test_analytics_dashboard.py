"""Tests for services/analytics_dashboard.py"""

import time
import pytest
from datetime import datetime

from services.analytics_dashboard import (
    AnalyticsDashboard, MetricsCollector,
    HourlyStats, DailyReport, UserProfile,
)


# ─── MetricsCollector ────────────────────────────────────

class TestMetricsCollector:
    @pytest.fixture
    def mc(self):
        return MetricsCollector(max_events=1000)

    def test_record_event(self, mc):
        mc.record(user_id="u1", channel="telegram", tokens=100)
        events = mc.get_events()
        assert len(events) == 1
        assert events[0]["user_id"] == "u1"
        assert events[0]["tokens"] == 100

    def test_first_seen_tracking(self, mc):
        mc.record(user_id="u1")
        mc.record(user_id="u1")
        assert "u1" in mc._user_first_seen

    def test_daily_users(self, mc):
        mc.record(user_id="u1")
        mc.record(user_id="u2")
        today = datetime.now().strftime("%Y-%m-%d")
        assert "u1" in mc._daily_users[today]
        assert "u2" in mc._daily_users[today]

    def test_max_events(self):
        mc = MetricsCollector(max_events=10)
        for i in range(20):
            mc.record(user_id=f"u{i}")
        assert len(mc._events) == 10

    def test_get_events_by_user(self, mc):
        mc.record(user_id="u1")
        mc.record(user_id="u2")
        mc.record(user_id="u1")
        events = mc.get_events(user_id="u1")
        assert len(events) == 2

    def test_get_events_since(self, mc):
        mc.record(user_id="u1")
        events = mc.get_events(since=time.time() - 1)
        assert len(events) >= 1

    def test_get_events_limit(self, mc):
        for i in range(10):
            mc.record(user_id="u1")
        events = mc.get_events(limit=3)
        assert len(events) == 3


# ─── AnalyticsDashboard ──────────────────────────────────

class TestAnalyticsDashboard:
    @pytest.fixture
    def dash(self):
        d = AnalyticsDashboard()
        # Seed some data
        for i in range(20):
            d.record_interaction(
                user_id=f"u{i % 5}",
                channel="telegram" if i % 2 == 0 else "wechat",
                engine="openai" if i % 3 == 0 else "claude",
                tokens=100 + i * 10,
                latency=0.5 + i * 0.1,
                message_length=50 + i,
            )
        return d

    def test_daily_report(self, dash):
        today = datetime.now().strftime("%Y-%m-%d")
        report = dash.daily_report(today)
        assert report.date == today
        assert report.total_messages == 20
        assert report.unique_users == 5

    def test_daily_report_no_data(self, dash):
        report = dash.daily_report("2020-01-01")
        assert report.total_messages == 0

    def test_daily_report_tokens(self, dash):
        today = datetime.now().strftime("%Y-%m-%d")
        report = dash.daily_report(today)
        assert report.total_tokens > 0

    def test_daily_report_top_users(self, dash):
        today = datetime.now().strftime("%Y-%m-%d")
        report = dash.daily_report(today)
        assert len(report.top_users) > 0
        assert "user_id" in report.top_users[0]
        assert "messages" in report.top_users[0]

    def test_daily_report_channels(self, dash):
        today = datetime.now().strftime("%Y-%m-%d")
        report = dash.daily_report(today)
        assert "telegram" in report.channel_breakdown
        assert "wechat" in report.channel_breakdown

    def test_daily_report_engines(self, dash):
        today = datetime.now().strftime("%Y-%m-%d")
        report = dash.daily_report(today)
        assert len(report.engine_breakdown) > 0

    def test_user_profile(self, dash):
        profile = dash.user_profile("u0")
        assert profile.user_id == "u0"
        assert profile.total_messages >= 4
        assert profile.total_tokens > 0
        assert profile.preferred_channel in ("telegram", "wechat")

    def test_user_profile_engagement(self, dash):
        profile = dash.user_profile("u1")
        assert 0 <= profile.engagement_score <= 100

    def test_user_profile_not_found(self, dash):
        profile = dash.user_profile("nonexistent")
        assert profile.total_messages == 0

    def test_user_profile_active_hours(self, dash):
        profile = dash.user_profile("u0")
        assert isinstance(profile.active_hours, list)

    def test_user_profile_sessions(self, dash):
        profile = dash.user_profile("u0")
        assert profile.session_count >= 1

    def test_heatmap(self, dash):
        heatmap = dash.heatmap(days=7)
        assert len(heatmap) == 7
        for day, hours in heatmap.items():
            assert len(hours) == 24
            assert all(isinstance(v, int) for v in hours)

    def test_heatmap_has_data(self, dash):
        heatmap = dash.heatmap(days=1)
        total = sum(sum(hours) for hours in heatmap.values())
        assert total > 0

    def test_retention(self, dash):
        result = dash.retention(window_days=7)
        assert "base_users" in result
        assert "retention" in result

    def test_overview(self, dash):
        ov = dash.overview()
        assert ov["total_events"] == 20
        assert ov["total_users"] == 5
        assert "today" in ov
        assert "last_24h" in ov
        assert ov["today"]["messages"] == 20

    def test_overview_empty(self):
        dash = AnalyticsDashboard()
        ov = dash.overview()
        assert ov["status"] == "no_data"

    def test_overview_channels(self, dash):
        ov = dash.overview()
        assert "telegram" in ov["channels"]

    # ─── Export ────────────────────────────────────────

    def test_export_text(self, dash):
        text = dash.export_report(fmt="text")
        assert "Daily Report" in text
        assert "Messages" in text

    def test_export_json(self, dash):
        result = dash.export_report(fmt="json")
        data = __import__("json").loads(result)
        assert "total_messages" in data
        assert data["total_messages"] == 20

    def test_export_csv(self, dash):
        result = dash.export_report(fmt="csv")
        assert "hour" in result
        assert "messages" in result

    def test_export_text_top_users(self, dash):
        text = dash.export_report(fmt="text")
        assert "Top Users" in text

    def test_export_text_peak_hours(self, dash):
        text = dash.export_report(fmt="text")
        assert "Peak Hours" in text

    def test_get_stats(self, dash):
        stats = dash.get_stats()
        assert stats["total_events"] == 20
        assert stats["total_users"] == 5
        assert stats["days_tracked"] >= 1


# ─── HourlyStats ─────────────────────────────────────────

class TestHourlyStats:
    def test_defaults(self):
        h = HourlyStats(hour=14)
        assert h.messages == 0
        assert h.tokens == 0


# ─── DailyReport ─────────────────────────────────────────

class TestDailyReport:
    def test_defaults(self):
        r = DailyReport(date="2026-01-01")
        assert r.total_messages == 0
        assert r.unique_users == 0
        assert r.hourly == []


# ─── UserProfile ─────────────────────────────────────────

class TestUserProfile:
    def test_defaults(self):
        p = UserProfile(user_id="test")
        assert p.total_messages == 0
        assert p.engagement_score == 0.0
        assert p.active_hours == []
