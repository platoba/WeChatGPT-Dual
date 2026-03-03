"""Tests for PerformanceMonitor."""
import time
import pytest
import threading
from services.performance_monitor import (
    PerformanceMonitor, ReservoirSampler, SlidingWindowCounter,
)


@pytest.fixture
def monitor():
    m = PerformanceMonitor()
    yield m
    m.reset()


class TestReservoirSampler:
    def test_add_and_percentile(self):
        rs = ReservoirSampler(size=100)
        for i in range(100):
            rs.add(float(i))
        assert rs.percentile(50) == pytest.approx(50, abs=5)
        assert rs.percentile(99) >= 90

    def test_empty(self):
        rs = ReservoirSampler()
        assert rs.percentile(50) == 0.0

    def test_single_value(self):
        rs = ReservoirSampler()
        rs.add(42.0)
        assert rs.percentile(50) == 42.0
        assert rs.percentile(99) == 42.0

    def test_overflow(self):
        rs = ReservoirSampler(size=10)
        for i in range(1000):
            rs.add(float(i))
        assert rs.count == 1000
        assert len(rs.samples) == 10

    def test_reset(self):
        rs = ReservoirSampler()
        rs.add(1.0)
        rs.add(2.0)
        rs.reset()
        assert rs.count == 0
        assert len(rs.samples) == 0


class TestSlidingWindowCounter:
    def test_record_and_count(self):
        sw = SlidingWindowCounter(window_sec=60)
        sw.record()
        sw.record()
        sw.record()
        assert sw.count() == 3

    def test_rate(self):
        sw = SlidingWindowCounter(window_sec=60)
        for _ in range(10):
            sw.record()
        rate = sw.rate()
        assert rate > 0

    def test_empty(self):
        sw = SlidingWindowCounter()
        assert sw.count() == 0
        assert sw.rate() == 0.0

    def test_expiration(self):
        sw = SlidingWindowCounter(window_sec=1)
        sw.record(time.time() - 2)  # expired
        assert sw.count() == 0


class TestPerformanceMonitor:
    def test_record_request(self, monitor):
        monitor.record_request("api/chat", 100.0)
        metrics = monitor.get_endpoint_metrics("api/chat")
        assert metrics.total_requests == 1
        assert metrics.success_count == 1

    def test_record_error(self, monitor):
        monitor.record_request("api/chat", 200.0, success=False, error="timeout")
        metrics = monitor.get_endpoint_metrics("api/chat")
        assert metrics.error_count == 1
        assert metrics.last_error == "timeout"

    def test_record_throttle(self, monitor):
        monitor.record_request("api/chat", 50.0, throttled=True)
        metrics = monitor.get_endpoint_metrics("api/chat")
        assert metrics.throttle_count == 1

    def test_record_timeout(self, monitor):
        monitor.record_request("api/chat", 5000.0, timed_out=True)
        metrics = monitor.get_endpoint_metrics("api/chat")
        assert metrics.timeout_count == 1

    def test_latency_percentiles(self, monitor):
        for i in range(100):
            monitor.record_request("api/chat", float(i))
        metrics = monitor.get_endpoint_metrics("api/chat")
        assert metrics.latency.p50_ms > 0
        assert metrics.latency.p95_ms > metrics.latency.p50_ms
        assert metrics.latency.p99_ms >= metrics.latency.p95_ms

    def test_min_max_latency(self, monitor):
        monitor.record_request("api/chat", 10.0)
        monitor.record_request("api/chat", 100.0)
        monitor.record_request("api/chat", 50.0)
        metrics = monitor.get_endpoint_metrics("api/chat")
        assert metrics.latency.min_ms == 10.0
        assert metrics.latency.max_ms == 100.0

    def test_multiple_endpoints(self, monitor):
        monitor.record_request("api/chat", 100.0)
        monitor.record_request("api/health", 5.0)
        monitor.record_request("api/webhook", 200.0)
        endpoints = monitor.get_all_endpoints()
        assert len(endpoints) == 3

    def test_context_manager_success(self, monitor):
        with monitor.track_request("api/test"):
            time.sleep(0.01)
        metrics = monitor.get_endpoint_metrics("api/test")
        assert metrics.total_requests == 1
        assert metrics.success_count == 1
        assert metrics.latency.p50_ms >= 5  # at least ~10ms

    def test_context_manager_error(self, monitor):
        with pytest.raises(ValueError):
            with monitor.track_request("api/test"):
                raise ValueError("test error")
        metrics = monitor.get_endpoint_metrics("api/test")
        assert metrics.error_count == 1

    def test_active_connections(self, monitor):
        system = monitor.get_system_metrics()
        assert system.active_connections == 0

    def test_circuit_breaker_state(self, monitor):
        monitor.set_circuit_breaker("openai", "closed")
        monitor.set_circuit_breaker("claude", "open")
        system = monitor.get_system_metrics()
        assert system.circuit_breakers["openai"] == "closed"
        assert system.circuit_breakers["claude"] == "open"

    def test_custom_gauge(self, monitor):
        monitor.set_gauge("queue_depth", 42.0)
        data = monitor.to_json()
        assert data['custom_gauges']['queue_depth'] == 42.0

    def test_custom_counter(self, monitor):
        monitor.increment_counter("messages_processed", 5)
        monitor.increment_counter("messages_processed", 3)
        data = monitor.to_json()
        assert data['custom_counters']['messages_processed'] == 8

    def test_system_metrics(self, monitor):
        monitor.record_request("api/chat", 100.0)
        monitor.record_request("api/chat", 200.0, success=False)
        system = monitor.get_system_metrics()
        assert system.total_requests == 2
        assert system.total_errors == 1
        assert system.error_rate == 0.5

    def test_nonexistent_endpoint(self, monitor):
        metrics = monitor.get_endpoint_metrics("nonexistent")
        assert metrics.total_requests == 0

    def test_rps_calculation(self, monitor):
        for _ in range(20):
            monitor.record_request("api/fast", 1.0)
        metrics = monitor.get_endpoint_metrics("api/fast")
        assert metrics.rps > 0


class TestPrometheusExport:
    def test_format(self, monitor):
        monitor.record_request("api/chat", 100.0)
        monitor.set_circuit_breaker("openai", "closed")
        prom = monitor.to_prometheus()
        assert 'http_requests_total' in prom
        assert 'api/chat' in prom
        assert 'circuit_breaker_state' in prom
        assert 'app_uptime_seconds' in prom

    def test_empty_export(self, monitor):
        prom = monitor.to_prometheus()
        assert 'app_uptime_seconds' in prom

    def test_custom_metrics_in_prometheus(self, monitor):
        monitor.set_gauge("test_gauge", 3.14)
        monitor.increment_counter("test_counter", 7)
        prom = monitor.to_prometheus()
        assert 'test_gauge' in prom
        assert 'test_counter' in prom


class TestJSONExport:
    def test_structure(self, monitor):
        monitor.record_request("api/chat", 100.0)
        data = monitor.to_json()
        assert 'system' in data
        assert 'endpoints' in data
        assert 'api/chat' in data['endpoints']

    def test_endpoint_detail(self, monitor):
        monitor.record_request("api/chat", 50.0)
        data = monitor.to_json()
        ep = data['endpoints']['api/chat']
        assert ep['total'] == 1
        assert 'latency' in ep
        assert 'p50' in ep['latency']


class TestTextReport:
    def test_report_content(self, monitor):
        monitor.record_request("api/chat", 100.0)
        monitor.record_request("api/chat", 200.0, success=False, error="bad request")
        monitor.set_circuit_breaker("openai", "open")
        report = monitor.generate_text_report()
        assert "Performance Monitor" in report
        assert "api/chat" in report
        assert "openai" in report

    def test_empty_report(self, monitor):
        report = monitor.generate_text_report()
        assert "Performance Monitor" in report


class TestReset:
    def test_reset_clears_all(self, monitor):
        monitor.record_request("api/chat", 100.0)
        monitor.set_gauge("test", 1.0)
        monitor.set_circuit_breaker("cb", "open")
        monitor.reset()
        assert monitor.get_all_endpoints() == []
        assert monitor.get_system_metrics().total_requests == 0
        data = monitor.to_json()
        assert data['custom_gauges'] == {}


class TestThreadSafety:
    def test_concurrent_recording(self, monitor):
        errors = []

        def record_many(endpoint):
            try:
                for i in range(100):
                    monitor.record_request(endpoint, float(i))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many, args=(f"ep{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        total = sum(monitor.get_endpoint_metrics(f"ep{i}").total_requests for i in range(5))
        assert total == 500
