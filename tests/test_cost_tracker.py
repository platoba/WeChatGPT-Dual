"""Tests for cost_tracker module."""
import os
import pytest
import tempfile
from services.cost_tracker import CostTracker, MODEL_PRICING


@pytest.fixture
def tracker():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    t = CostTracker(db_path=db_path)
    yield t
    os.unlink(db_path)


class TestCostCalculation:
    def test_known_model_pricing(self, tracker):
        cost = tracker.calculate_cost('gpt-4', 1000, 500)
        expected = 1000/1000 * 0.03 + 500/1000 * 0.06
        assert abs(cost - expected) < 0.0001

    def test_gpt4o_mini_pricing(self, tracker):
        cost = tracker.calculate_cost('gpt-4o-mini', 10000, 5000)
        expected = 10000/1000 * 0.00015 + 5000/1000 * 0.0006
        assert abs(cost - expected) < 0.0001

    def test_unknown_model_uses_default(self, tracker):
        cost = tracker.calculate_cost('unknown-model', 1000, 1000)
        default = MODEL_PRICING['default']
        expected = 1000/1000 * default['input'] + 1000/1000 * default['output']
        assert abs(cost - expected) < 0.0001

    def test_zero_tokens(self, tracker):
        cost = tracker.calculate_cost('gpt-4', 0, 0)
        assert cost == 0

    def test_claude_pricing(self, tracker):
        cost = tracker.calculate_cost('claude-3-opus', 1000, 1000)
        expected = 1000/1000 * 0.015 + 1000/1000 * 0.075
        assert abs(cost - expected) < 0.0001

    def test_deepseek_pricing(self, tracker):
        cost = tracker.calculate_cost('deepseek-chat', 100000, 50000)
        assert cost > 0


class TestRecordUsage:
    def test_basic_record(self, tracker):
        record = tracker.record_usage("user1", "gpt-4", 500, 200)
        assert record.user_id == "user1"
        assert record.model == "gpt-4"
        assert record.input_tokens == 500
        assert record.output_tokens == 200
        assert record.cost_usd > 0

    def test_record_with_conversation(self, tracker):
        record = tracker.record_usage("user1", "gpt-4o", 1000, 500, conversation_id="conv_123")
        assert record.conversation_id == "conv_123"

    def test_multiple_records(self, tracker):
        tracker.record_usage("user1", "gpt-4", 100, 50)
        tracker.record_usage("user1", "gpt-4", 200, 100)
        tracker.record_usage("user2", "gpt-4o", 300, 150)
        report = tracker.generate_report("user1", days=1)
        assert report.request_count == 2


class TestBudget:
    def test_set_and_get_budget(self, tracker):
        tracker.set_budget("user1", daily_limit=1.0, monthly_limit=20.0)
        status = tracker.get_budget_status("user1")
        assert status.daily_limit == 1.0
        assert status.monthly_limit == 20.0
        assert status.daily_used == 0
        assert status.is_blocked == False

    def test_budget_tracking(self, tracker):
        tracker.set_budget("user1", daily_limit=0.10)
        tracker.record_usage("user1", "gpt-4", 1000, 500)
        status = tracker.get_budget_status("user1")
        assert status.daily_used > 0
        assert status.daily_pct > 0

    def test_can_spend_within_budget(self, tracker):
        tracker.set_budget("user1", daily_limit=10.0)
        can, msg = tracker.check_can_spend("user1", 0.01)
        assert can is True

    def test_cannot_spend_over_daily(self, tracker):
        tracker.set_budget("user1", daily_limit=0.001)
        tracker.record_usage("user1", "gpt-4", 10000, 5000)
        can, msg = tracker.check_can_spend("user1", 0.01)
        assert can is False
        assert "Daily" in msg

    def test_cannot_spend_over_monthly(self, tracker):
        tracker.set_budget("user1", monthly_limit=0.001)
        tracker.record_usage("user1", "gpt-4", 10000, 5000)
        can, msg = tracker.check_can_spend("user1", 0.01)
        assert can is False
        assert "Monthly" in msg

    def test_cannot_spend_over_total(self, tracker):
        tracker.set_budget("user1", total_limit=0.001)
        tracker.record_usage("user1", "gpt-4", 10000, 5000)
        can, msg = tracker.check_can_spend("user1")
        assert can is False

    def test_no_budget_always_can_spend(self, tracker):
        can, msg = tracker.check_can_spend("user1", 100)
        assert can is True

    def test_update_budget(self, tracker):
        tracker.set_budget("user1", daily_limit=1.0)
        tracker.set_budget("user1", daily_limit=5.0, monthly_limit=100.0)
        status = tracker.get_budget_status("user1")
        assert status.daily_limit == 5.0
        assert status.monthly_limit == 100.0


class TestReport:
    def test_empty_report(self, tracker):
        report = tracker.generate_report("user1", days=30)
        assert report.total_cost == 0
        assert report.request_count == 0

    def test_report_with_data(self, tracker):
        tracker.record_usage("user1", "gpt-4", 1000, 500)
        tracker.record_usage("user1", "gpt-4o", 2000, 1000)
        tracker.record_usage("user1", "gpt-4", 500, 200)
        report = tracker.generate_report("user1", days=1)
        assert report.request_count == 3
        assert report.total_cost > 0
        assert report.total_input_tokens == 3500
        assert report.total_output_tokens == 1700
        assert 'gpt-4' in report.by_model
        assert 'gpt-4o' in report.by_model
        assert report.by_model['gpt-4']['requests'] == 2

    def test_report_daily_breakdown(self, tracker):
        tracker.record_usage("user1", "gpt-4", 1000, 500)
        report = tracker.generate_report("user1", days=1)
        assert len(report.daily_breakdown) == 1

    def test_report_avg_cost(self, tracker):
        tracker.record_usage("user1", "gpt-4", 1000, 500)
        tracker.record_usage("user1", "gpt-4", 1000, 500)
        report = tracker.generate_report("user1", days=1)
        assert report.avg_cost_per_request > 0
        assert abs(report.avg_cost_per_request - report.total_cost / 2) < 0.0001


class TestTopUsers:
    def test_top_users(self, tracker):
        tracker.record_usage("user1", "gpt-4", 10000, 5000)
        tracker.record_usage("user2", "gpt-4", 1000, 500)
        tracker.record_usage("user3", "gpt-4", 50000, 25000)
        top = tracker.get_top_users(days=1, limit=10)
        assert len(top) == 3
        # user3 should be first (highest cost)
        assert top[0]['user_id'] == "user3"

    def test_top_users_empty(self, tracker):
        top = tracker.get_top_users(days=1)
        assert len(top) == 0


class TestExport:
    def test_csv_export(self, tracker):
        tracker.record_usage("user1", "gpt-4", 1000, 500)
        tracker.record_usage("user1", "gpt-4o", 2000, 1000)
        csv = tracker.export_csv("user1", days=1)
        lines = csv.strip().split('\n')
        assert len(lines) == 3  # header + 2 records
        assert 'gpt-4' in lines[1]

    def test_csv_empty(self, tracker):
        csv = tracker.export_csv("user1", days=1)
        lines = csv.strip().split('\n')
        assert len(lines) == 1  # header only


class TestForecast:
    def test_forecast_with_data(self, tracker):
        for i in range(10):
            tracker.record_usage("user1", "gpt-4", 1000, 500)
        forecast = tracker.forecast_cost("user1", days_ahead=30)
        assert forecast['projected_cost'] > 0
        assert forecast['daily_avg'] > 0

    def test_forecast_no_data(self, tracker):
        forecast = tracker.forecast_cost("user1", days_ahead=30)
        assert forecast['projected_cost'] == 0
        assert forecast['confidence'] == 0


class TestAlerts:
    def test_daily_budget_alert(self, tracker):
        tracker.set_budget("user1", daily_limit=0.01, alert_threshold=0.5)
        # Record enough to trigger alert
        tracker.record_usage("user1", "gpt-4", 10000, 5000)
        # Check alerts were created
        with tracker._get_conn() as conn:
            alerts = conn.execute(
                "SELECT * FROM cost_alerts WHERE user_id = ?", ("user1",)
            ).fetchall()
            assert len(alerts) > 0
