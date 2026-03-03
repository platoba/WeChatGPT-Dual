"""Tests for NotificationService (alert engine)."""
import os
import pytest
import tempfile
from services.notification_service import (
    NotificationService, Severity, Condition,
)


@pytest.fixture
def svc():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    ns = NotificationService(db_path=db_path)
    yield ns
    os.unlink(db_path)


class TestCondition:
    def test_greater_than(self):
        assert Condition.GREATER_THAN.evaluate(10, 5) is True
        assert Condition.GREATER_THAN.evaluate(5, 10) is False

    def test_less_than(self):
        assert Condition.LESS_THAN.evaluate(3, 5) is True
        assert Condition.LESS_THAN.evaluate(5, 3) is False

    def test_greater_equal(self):
        assert Condition.GREATER_EQUAL.evaluate(5, 5) is True
        assert Condition.GREATER_EQUAL.evaluate(4, 5) is False

    def test_less_equal(self):
        assert Condition.LESS_EQUAL.evaluate(5, 5) is True
        assert Condition.LESS_EQUAL.evaluate(6, 5) is False

    def test_equals(self):
        assert Condition.EQUALS.evaluate(5, 5) is True
        assert Condition.EQUALS.evaluate(5, 6) is False

    def test_not_equals(self):
        assert Condition.NOT_EQUALS.evaluate(5, 6) is True
        assert Condition.NOT_EQUALS.evaluate(5, 5) is False


class TestSeverity:
    def test_from_str(self):
        assert Severity.from_str('info') == Severity.INFO
        assert Severity.from_str('warning') == Severity.WARNING
        assert Severity.from_str('critical') == Severity.CRITICAL
        assert Severity.from_str('unknown') == Severity.WARNING


class TestRuleManagement:
    def test_add_rule(self, svc):
        rule = svc.add_rule("Test Rule", "error_rate", "gt", 0.1)
        assert rule.rule_id is not None
        assert rule.name == "Test Rule"
        assert rule.metric == "error_rate"

    def test_get_rules(self, svc):
        svc.add_rule("Rule 1", "metric_a", "gt", 10)
        svc.add_rule("Rule 2", "metric_b", "lt", 5)
        rules = svc.get_rules()
        assert len(rules) == 2

    def test_remove_rule(self, svc):
        rule = svc.add_rule("To Remove", "metric", "gt", 1)
        assert svc.remove_rule(rule.rule_id) is True
        assert len(svc.get_rules()) == 0

    def test_remove_nonexistent(self, svc):
        assert svc.remove_rule("fake_id") is False

    def test_enable_disable(self, svc):
        rule = svc.add_rule("Toggle", "metric", "gt", 1)
        svc.enable_rule(rule.rule_id, False)
        assert len(svc.get_rules(enabled_only=True)) == 0
        assert len(svc.get_rules(enabled_only=False)) == 1

    def test_rule_templates(self, svc):
        svc.add_error_rate_rule()
        svc.add_latency_rule()
        svc.add_usage_quota_rule()
        svc.add_engine_failure_rule()
        rules = svc.get_rules()
        assert len(rules) == 4


class TestAlertEvaluation:
    def test_fire_alert(self, svc):
        svc.add_rule("High Errors", "error_rate", "gt", 0.1, cooldown_sec=0)
        alerts = svc.evaluate({"error_rate": 0.5})
        assert len(alerts) == 1
        assert alerts[0].severity == 'warning'
        assert alerts[0].status == 'active'

    def test_no_fire_below_threshold(self, svc):
        svc.add_rule("High Errors", "error_rate", "gt", 0.1, cooldown_sec=0)
        alerts = svc.evaluate({"error_rate": 0.05})
        assert len(alerts) == 0

    def test_cooldown(self, svc):
        svc.add_rule("Test", "metric", "gt", 5, cooldown_sec=3600)
        alerts1 = svc.evaluate({"metric": 10})
        assert len(alerts1) == 1
        # Second evaluation within cooldown should not fire
        alerts2 = svc.evaluate({"metric": 10})
        assert len(alerts2) == 0

    def test_multiple_rules(self, svc):
        svc.add_rule("Error Rate", "error_rate", "gt", 0.1, cooldown_sec=0)
        svc.add_rule("Latency", "avg_latency", "gt", 1000, cooldown_sec=0)
        alerts = svc.evaluate({"error_rate": 0.5, "avg_latency": 2000})
        assert len(alerts) == 2

    def test_missing_metric_ignored(self, svc):
        svc.add_rule("Missing", "nonexistent", "gt", 1, cooldown_sec=0)
        alerts = svc.evaluate({"other_metric": 10})
        assert len(alerts) == 0

    def test_auto_resolve(self, svc):
        svc.add_rule("Resolve Test", "error_rate", "gt", 0.1, cooldown_sec=0)
        svc.evaluate({"error_rate": 0.5})
        active = svc.get_active_alerts()
        assert len(active) == 1
        # Metric goes below threshold → auto-resolve
        svc.evaluate({"error_rate": 0.01})
        active = svc.get_active_alerts()
        assert len(active) == 0

    def test_callback_fired(self, svc):
        received = []
        svc.register_callback(lambda alert: received.append(alert))
        svc.add_rule("CB Test", "metric", "gt", 0, cooldown_sec=0)
        svc.evaluate({"metric": 1})
        assert len(received) == 1

    def test_callback_error_handled(self, svc):
        def bad_callback(alert):
            raise RuntimeError("boom")
        svc.register_callback(bad_callback)
        svc.add_rule("CB Err", "metric", "gt", 0, cooldown_sec=0)
        # Should not raise
        alerts = svc.evaluate({"metric": 1})
        assert len(alerts) == 1

    def test_disabled_rule_not_evaluated(self, svc):
        rule = svc.add_rule("Disabled", "metric", "gt", 0, cooldown_sec=0)
        svc.enable_rule(rule.rule_id, False)
        alerts = svc.evaluate({"metric": 100})
        assert len(alerts) == 0

    def test_severity_levels(self, svc):
        svc.add_rule("Info", "m1", "gt", 0, severity='info', cooldown_sec=0)
        svc.add_rule("Warn", "m2", "gt", 0, severity='warning', cooldown_sec=0)
        svc.add_rule("Crit", "m3", "gt", 0, severity='critical', cooldown_sec=0)
        alerts = svc.evaluate({"m1": 1, "m2": 1, "m3": 1})
        severities = {a.severity for a in alerts}
        assert severities == {'info', 'warning', 'critical'}


class TestAlertManagement:
    def test_acknowledge(self, svc):
        svc.add_rule("Ack", "metric", "gt", 0, cooldown_sec=0)
        svc.evaluate({"metric": 1})
        active = svc.get_active_alerts()
        assert len(active) == 1
        assert svc.acknowledge(active[0].alert_id) is True
        assert len(svc.get_active_alerts()) == 0

    def test_manual_resolve(self, svc):
        svc.add_rule("Resolve", "metric", "gt", 0, cooldown_sec=0)
        svc.evaluate({"metric": 1})
        active = svc.get_active_alerts()
        assert svc.resolve(active[0].alert_id) is True
        assert len(svc.get_active_alerts()) == 0

    def test_suppress_rule(self, svc):
        rule = svc.add_rule("Suppress", "metric", "gt", 0, cooldown_sec=0)
        svc.suppress_rule(rule.rule_id, duration_sec=3600, reason="maintenance")
        alerts = svc.evaluate({"metric": 100})
        assert len(alerts) == 0

    def test_alert_history(self, svc):
        svc.add_rule("History", "metric", "gt", 0, cooldown_sec=0)
        svc.evaluate({"metric": 1})
        history = svc.get_alert_history()
        assert len(history) == 1

    def test_history_severity_filter(self, svc):
        svc.add_rule("Info", "m1", "gt", 0, severity='info', cooldown_sec=0)
        svc.add_rule("Crit", "m2", "gt", 0, severity='critical', cooldown_sec=0)
        svc.evaluate({"m1": 1, "m2": 1})
        info_only = svc.get_alert_history(severity='info')
        assert all(a.severity == 'info' for a in info_only)

    def test_history_limit(self, svc):
        svc.add_rule("Limit", "metric", "gt", 0, cooldown_sec=0)
        for i in range(20):
            svc._last_fired.clear()  # bypass cooldown for test
            svc.evaluate({"metric": float(i + 1)})
        history = svc.get_alert_history(limit=5)
        assert len(history) == 5


class TestAlertStats:
    def test_stats(self, svc):
        svc.add_rule("Stat1", "m1", "gt", 0, severity='warning', cooldown_sec=0)
        svc.add_rule("Stat2", "m2", "gt", 0, severity='critical', cooldown_sec=0)
        svc.evaluate({"m1": 1, "m2": 1})
        stats = svc.get_alert_stats()
        assert stats['total_alerts'] == 2
        assert stats['active_alerts'] == 2
        assert stats['total_rules'] == 2
        assert stats['enabled_rules'] == 2
        assert 'warning' in stats['by_severity']
        assert 'critical' in stats['by_severity']

    def test_empty_stats(self, svc):
        stats = svc.get_alert_stats()
        assert stats['total_alerts'] == 0


class TestTextReport:
    def test_report_with_data(self, svc):
        svc.add_rule("Report", "metric", "gt", 0, cooldown_sec=0)
        svc.evaluate({"metric": 1})
        report = svc.generate_text_report()
        assert "Alert" in report
        assert "Active" in report

    def test_empty_report(self, svc):
        report = svc.generate_text_report()
        assert "Alert" in report
        assert "Total Alerts: 0" in report


class TestConditionTypes:
    """Test all condition type edge cases."""

    def test_gt_boundary(self, svc):
        svc.add_rule("GT", "m", "gt", 10, cooldown_sec=0)
        assert len(svc.evaluate({"m": 10})) == 0  # equal, not greater
        assert len(svc.evaluate({"m": 10.001})) == 1

    def test_lt_boundary(self, svc):
        svc.add_rule("LT", "m", "lt", 10, cooldown_sec=0)
        assert len(svc.evaluate({"m": 10})) == 0
        svc._last_fired.clear()
        assert len(svc.evaluate({"m": 9.999})) == 1

    def test_gte_boundary(self, svc):
        svc.add_rule("GTE", "m", "gte", 10, cooldown_sec=0)
        assert len(svc.evaluate({"m": 10})) == 1

    def test_lte_boundary(self, svc):
        svc.add_rule("LTE", "m", "lte", 10, cooldown_sec=0)
        assert len(svc.evaluate({"m": 10})) == 1

    def test_eq_exact(self, svc):
        svc.add_rule("EQ", "m", "eq", 42, cooldown_sec=0)
        assert len(svc.evaluate({"m": 42})) == 1
        svc._last_fired.clear()
        assert len(svc.evaluate({"m": 43})) == 0

    def test_neq(self, svc):
        svc.add_rule("NEQ", "m", "neq", 42, cooldown_sec=0)
        assert len(svc.evaluate({"m": 43})) == 1
        svc._last_fired.clear()
        assert len(svc.evaluate({"m": 42})) == 0
