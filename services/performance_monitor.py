"""
Performance Monitor - Request tracking, latency metrics, Prometheus-compatible /metrics

Features:
- Per-endpoint request tracking (latency, status, errors)
- Percentile calculation (P50, P95, P99) using reservoir sampling
- Throughput (RPS) calculation with sliding window
- Error rate and throttle rate tracking
- Circuit breaker state monitoring
- Prometheus text exposition format (/metrics endpoint)
- JSON + text report export
- Thread-safe metric collection
"""

import time
import threading
import random
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict, deque
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SLIDING_WINDOW_SEC = 60
RESERVOIR_SIZE = 1000


@dataclass
class LatencyStats:
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float('inf')
    max_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0


@dataclass
class EndpointMetrics:
    name: str
    total_requests: int = 0
    success_count: int = 0
    error_count: int = 0
    throttle_count: int = 0
    timeout_count: int = 0
    latency: LatencyStats = field(default_factory=LatencyStats)
    last_error: Optional[str] = None
    last_error_time: float = 0.0
    rps: float = 0.0


@dataclass
class SystemMetrics:
    uptime_sec: float = 0.0
    total_requests: int = 0
    total_errors: int = 0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    active_connections: int = 0
    circuit_breakers: Dict[str, str] = field(default_factory=dict)


class ReservoirSampler:
    """Vitter's reservoir sampling for percentile estimation."""

    def __init__(self, size: int = RESERVOIR_SIZE):
        self.size = size
        self.samples: List[float] = []
        self.count = 0
        self._sorted = False

    def add(self, value: float):
        self.count += 1
        self._sorted = False
        if len(self.samples) < self.size:
            self.samples.append(value)
        else:
            j = random.randint(0, self.count - 1)
            if j < self.size:
                self.samples[j] = value

    def percentile(self, p: float) -> float:
        """Get p-th percentile (0-100)."""
        if not self.samples:
            return 0.0
        if not self._sorted:
            self.samples.sort()
            self._sorted = True
        idx = int(len(self.samples) * p / 100)
        idx = min(idx, len(self.samples) - 1)
        return self.samples[idx]

    def reset(self):
        self.samples.clear()
        self.count = 0
        self._sorted = False


class SlidingWindowCounter:
    """Sliding window counter for RPS calculation."""

    def __init__(self, window_sec: int = SLIDING_WINDOW_SEC):
        self.window_sec = window_sec
        self.timestamps: deque = deque()
        self._lock = threading.Lock()

    def record(self, ts: Optional[float] = None):
        ts = ts or time.time()
        with self._lock:
            self.timestamps.append(ts)
            self._prune(ts)

    def count(self) -> int:
        now = time.time()
        with self._lock:
            self._prune(now)
            return len(self.timestamps)

    def rate(self) -> float:
        """Requests per second over the window."""
        now = time.time()
        with self._lock:
            self._prune(now)
            if not self.timestamps:
                return 0.0
            elapsed = now - self.timestamps[0]
            if elapsed < 1:
                return float(len(self.timestamps))
            return len(self.timestamps) / elapsed

    def _prune(self, now: float):
        cutoff = now - self.window_sec
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()


class PerformanceMonitor:
    """Centralized performance monitoring with Prometheus export."""

    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._endpoints: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'total': 0,
            'success': 0,
            'errors': 0,
            'throttles': 0,
            'timeouts': 0,
            'reservoir': ReservoirSampler(),
            'window': SlidingWindowCounter(),
            'last_error': None,
            'last_error_time': 0.0,
            'total_latency': 0.0,
            'min_latency': float('inf'),
            'max_latency': 0.0,
        })
        self._global_window = SlidingWindowCounter()
        self._active_connections = 0
        self._circuit_breakers: Dict[str, str] = {}
        self._custom_gauges: Dict[str, float] = {}
        self._custom_counters: Dict[str, int] = defaultdict(int)

    @contextmanager
    def track_request(self, endpoint: str):
        """Context manager to track a request's latency and outcome."""
        start = time.time()
        error_occurred = False
        error_msg = None
        try:
            with self._lock:
                self._active_connections += 1
            yield
        except Exception as e:
            error_occurred = True
            error_msg = str(e)
            raise
        finally:
            elapsed_ms = (time.time() - start) * 1000
            with self._lock:
                self._active_connections = max(0, self._active_connections - 1)
                ep = self._endpoints[endpoint]
                ep['total'] += 1
                ep['total_latency'] += elapsed_ms
                ep['min_latency'] = min(ep['min_latency'], elapsed_ms)
                ep['max_latency'] = max(ep['max_latency'], elapsed_ms)
                ep['reservoir'].add(elapsed_ms)
                ep['window'].record()
                self._global_window.record()

                if error_occurred:
                    ep['errors'] += 1
                    ep['last_error'] = error_msg
                    ep['last_error_time'] = time.time()
                else:
                    ep['success'] += 1

    def record_request(self, endpoint: str, latency_ms: float,
                       success: bool = True, error: Optional[str] = None,
                       throttled: bool = False, timed_out: bool = False):
        """Manually record a request metric."""
        now = time.time()
        with self._lock:
            ep = self._endpoints[endpoint]
            ep['total'] += 1
            ep['total_latency'] += latency_ms
            ep['min_latency'] = min(ep['min_latency'], latency_ms)
            ep['max_latency'] = max(ep['max_latency'], latency_ms)
            ep['reservoir'].add(latency_ms)
            ep['window'].record(now)
            self._global_window.record(now)

            if success:
                ep['success'] += 1
            else:
                ep['errors'] += 1
                if error:
                    ep['last_error'] = error
                    ep['last_error_time'] = now
            if throttled:
                ep['throttles'] += 1
            if timed_out:
                ep['timeouts'] += 1

    def record_throttle(self, endpoint: str):
        """Record a throttled request."""
        with self._lock:
            self._endpoints[endpoint]['throttles'] += 1

    def record_timeout(self, endpoint: str):
        """Record a timed-out request."""
        with self._lock:
            self._endpoints[endpoint]['timeouts'] += 1

    def set_circuit_breaker(self, name: str, state: str):
        """Update circuit breaker state (closed/open/half_open)."""
        with self._lock:
            self._circuit_breakers[name] = state

    def set_gauge(self, name: str, value: float):
        """Set a custom gauge metric."""
        with self._lock:
            self._custom_gauges[name] = value

    def increment_counter(self, name: str, amount: int = 1):
        """Increment a custom counter."""
        with self._lock:
            self._custom_counters[name] += amount

    # ── Query Methods ───────────────────────────────────────────────

    def get_endpoint_metrics(self, endpoint: str) -> EndpointMetrics:
        """Get metrics for a specific endpoint."""
        with self._lock:
            if endpoint not in self._endpoints:
                return EndpointMetrics(name=endpoint)
            ep = self._endpoints[endpoint]
            reservoir = ep['reservoir']
            return EndpointMetrics(
                name=endpoint,
                total_requests=ep['total'],
                success_count=ep['success'],
                error_count=ep['errors'],
                throttle_count=ep['throttles'],
                timeout_count=ep['timeouts'],
                latency=LatencyStats(
                    count=ep['total'],
                    total_ms=ep['total_latency'],
                    min_ms=ep['min_latency'] if ep['total'] > 0 else 0,
                    max_ms=ep['max_latency'],
                    p50_ms=round(reservoir.percentile(50), 2),
                    p95_ms=round(reservoir.percentile(95), 2),
                    p99_ms=round(reservoir.percentile(99), 2),
                ),
                last_error=ep['last_error'],
                last_error_time=ep['last_error_time'],
                rps=round(ep['window'].rate(), 2),
            )

    def get_all_endpoints(self) -> List[EndpointMetrics]:
        """Get metrics for all endpoints."""
        with self._lock:
            endpoints = list(self._endpoints.keys())
        return [self.get_endpoint_metrics(ep) for ep in endpoints]

    def get_system_metrics(self) -> SystemMetrics:
        """Get system-wide metrics."""
        now = time.time()
        with self._lock:
            total_req = sum(ep['total'] for ep in self._endpoints.values())
            total_err = sum(ep['errors'] for ep in self._endpoints.values())
            total_lat = sum(ep['total_latency'] for ep in self._endpoints.values())

            return SystemMetrics(
                uptime_sec=round(now - self._start_time, 1),
                total_requests=total_req,
                total_errors=total_err,
                error_rate=round(total_err / max(total_req, 1), 4),
                avg_latency_ms=round(total_lat / max(total_req, 1), 2),
                active_connections=self._active_connections,
                circuit_breakers=dict(self._circuit_breakers),
            )

    # ── Export Formats ──────────────────────────────────────────────

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text exposition format."""
        lines = []
        now = time.time()

        # System metrics
        lines.append('# HELP app_uptime_seconds Application uptime in seconds')
        lines.append('# TYPE app_uptime_seconds gauge')
        lines.append(f'app_uptime_seconds {now - self._start_time:.1f}')

        lines.append('# HELP app_active_connections Current active connections')
        lines.append('# TYPE app_active_connections gauge')
        with self._lock:
            lines.append(f'app_active_connections {self._active_connections}')

        # Per-endpoint metrics
        lines.append('# HELP http_requests_total Total HTTP requests')
        lines.append('# TYPE http_requests_total counter')
        lines.append('# HELP http_request_duration_ms HTTP request duration in milliseconds')
        lines.append('# TYPE http_request_duration_ms summary')
        lines.append('# HELP http_errors_total Total HTTP errors')
        lines.append('# TYPE http_errors_total counter')

        for em in self.get_all_endpoints():
            ep_label = em.name.replace('"', '\\"')
            lines.append(f'http_requests_total{{endpoint="{ep_label}"}} {em.total_requests}')
            lines.append(f'http_errors_total{{endpoint="{ep_label}"}} {em.error_count}')
            lines.append(f'http_request_duration_ms{{endpoint="{ep_label}",quantile="0.5"}} {em.latency.p50_ms}')
            lines.append(f'http_request_duration_ms{{endpoint="{ep_label}",quantile="0.95"}} {em.latency.p95_ms}')
            lines.append(f'http_request_duration_ms{{endpoint="{ep_label}",quantile="0.99"}} {em.latency.p99_ms}')
            lines.append(f'http_requests_throttled_total{{endpoint="{ep_label}"}} {em.throttle_count}')

        # Circuit breakers
        lines.append('# HELP circuit_breaker_state Circuit breaker state (0=closed, 1=open, 2=half_open)')
        lines.append('# TYPE circuit_breaker_state gauge')
        with self._lock:
            for name, state in self._circuit_breakers.items():
                val = {'closed': 0, 'open': 1, 'half_open': 2}.get(state, -1)
                lines.append(f'circuit_breaker_state{{name="{name}"}} {val}')

        # Custom gauges
        for name, value in self._custom_gauges.items():
            safe_name = name.replace('.', '_').replace('-', '_')
            lines.append(f'# TYPE {safe_name} gauge')
            lines.append(f'{safe_name} {value}')

        # Custom counters
        for name, value in self._custom_counters.items():
            safe_name = name.replace('.', '_').replace('-', '_')
            lines.append(f'# TYPE {safe_name} counter')
            lines.append(f'{safe_name} {value}')

        return '\n'.join(lines) + '\n'

    def to_json(self) -> Dict[str, Any]:
        """Export all metrics as JSON."""
        system = self.get_system_metrics()
        endpoints = self.get_all_endpoints()

        return {
            'system': {
                'uptime_sec': system.uptime_sec,
                'total_requests': system.total_requests,
                'total_errors': system.total_errors,
                'error_rate': system.error_rate,
                'avg_latency_ms': system.avg_latency_ms,
                'active_connections': system.active_connections,
                'circuit_breakers': system.circuit_breakers,
            },
            'endpoints': {
                em.name: {
                    'total': em.total_requests,
                    'success': em.success_count,
                    'errors': em.error_count,
                    'throttles': em.throttle_count,
                    'timeouts': em.timeout_count,
                    'rps': em.rps,
                    'latency': {
                        'p50': em.latency.p50_ms,
                        'p95': em.latency.p95_ms,
                        'p99': em.latency.p99_ms,
                        'min': em.latency.min_ms if em.latency.min_ms != float('inf') else 0,
                        'max': em.latency.max_ms,
                        'avg': round(em.latency.total_ms / max(em.latency.count, 1), 2),
                    },
                    'last_error': em.last_error,
                } for em in endpoints
            },
            'custom_gauges': dict(self._custom_gauges),
            'custom_counters': dict(self._custom_counters),
        }

    def generate_text_report(self) -> str:
        """Generate human-readable performance report."""
        system = self.get_system_metrics()
        endpoints = self.get_all_endpoints()

        lines = [
            "═══ Performance Monitor Report ═══", "",
            f"Uptime: {system.uptime_sec:.0f}s",
            f"Total Requests: {system.total_requests}",
            f"Total Errors: {system.total_errors} ({system.error_rate:.2%})",
            f"Avg Latency: {system.avg_latency_ms:.1f}ms",
            f"Active Connections: {system.active_connections}",
            f"Global RPS: {self._global_window.rate():.1f}",
        ]

        if system.circuit_breakers:
            lines.append("\nCircuit Breakers:")
            for name, state in system.circuit_breakers.items():
                icon = {'closed': '✅', 'open': '🔴', 'half_open': '🟡'}.get(state, '❓')
                lines.append(f"  {icon} {name}: {state}")

        if endpoints:
            lines.append("\nEndpoints:")
            for em in sorted(endpoints, key=lambda e: e.total_requests, reverse=True):
                err_pct = em.error_count / max(em.total_requests, 1) * 100
                lines.append(f"\n  [{em.name}]")
                lines.append(f"    Requests: {em.total_requests} ({em.rps} rps)")
                lines.append(f"    Success: {em.success_count} | Errors: {em.error_count} ({err_pct:.1f}%)")
                lines.append(f"    Latency P50/P95/P99: {em.latency.p50_ms}/{em.latency.p95_ms}/{em.latency.p99_ms}ms")
                if em.throttle_count:
                    lines.append(f"    Throttled: {em.throttle_count}")
                if em.last_error:
                    lines.append(f"    Last Error: {em.last_error}")

        lines.append("\n═══════════════════════════════════")
        return '\n'.join(lines)

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._endpoints.clear()
            self._global_window = SlidingWindowCounter()
            self._active_connections = 0
            self._circuit_breakers.clear()
            self._custom_gauges.clear()
            self._custom_counters.clear()
            self._start_time = time.time()
