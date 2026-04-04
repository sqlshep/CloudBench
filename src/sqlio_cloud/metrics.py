"""Core metrics collection: latency histograms, throughput tracking, and result dataclasses."""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LatencyHistogram:
    """High-resolution latency tracking with percentiles, jitter, and distribution."""

    values_ms: list[float] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)

    def record(self, ms: float, ts: float | None = None):
        self.values_ms.append(ms)
        self.timestamps.append(ts if ts is not None else time.perf_counter())

    @property
    def count(self) -> int:
        return len(self.values_ms)

    @property
    def min_ms(self) -> float:
        return min(self.values_ms) if self.values_ms else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.values_ms) if self.values_ms else 0.0

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.values_ms) if self.values_ms else 0.0

    @property
    def stddev_ms(self) -> float:
        return statistics.stdev(self.values_ms) if len(self.values_ms) > 1 else 0.0

    @property
    def variance_ms(self) -> float:
        return statistics.variance(self.values_ms) if len(self.values_ms) > 1 else 0.0

    @property
    def jitter_ms(self) -> float:
        if len(self.values_ms) < 3:
            return 0.0
        diffs = [abs(self.values_ms[i] - self.values_ms[i - 1]) for i in range(1, len(self.values_ms))]
        return statistics.stdev(diffs)

    @property
    def coefficient_of_variation(self) -> float:
        m = self.mean_ms
        return (self.stddev_ms / m * 100) if m > 0 else 0.0

    def percentile(self, p: float) -> float:
        if not self.values_ms:
            return 0.0
        s = sorted(self.values_ms)
        idx = int(len(s) * p / 100.0)
        return s[min(idx, len(s) - 1)]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p75(self) -> float:
        return self.percentile(75)

    @property
    def p90(self) -> float:
        return self.percentile(90)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def p999(self) -> float:
        return self.percentile(99.9)

    def distribution_buckets(self, num_buckets: int = 20) -> list[dict]:
        if not self.values_ms:
            return []
        lo, hi = self.min_ms, self.max_ms
        width = (hi - lo) / num_buckets if hi > lo else 1.0
        buckets = [
            {"lo": round(lo + i * width, 3), "hi": round(lo + (i + 1) * width, 3), "count": 0}
            for i in range(num_buckets)
        ]
        for v in self.values_ms:
            idx = min(int((v - lo) / width), num_buckets - 1) if width > 0 else 0
            buckets[idx]["count"] += 1
        return buckets

    def time_series(self, window_sec: float = 1.0) -> list[dict]:
        """Rolling windowed ops/sec and latency over time."""
        if not self.timestamps or not self.values_ms:
            return []
        paired = sorted(zip(self.timestamps, self.values_ms))
        start = paired[0][0]
        windows: list[dict] = []
        window_vals: list[float] = []
        window_start = start

        for ts, val in paired:
            while ts - window_start >= window_sec:
                if window_vals:
                    sv = sorted(window_vals)
                    windows.append({
                        "time_offset_sec": round(window_start - start, 2),
                        "ops": len(window_vals),
                        "mean_ms": round(statistics.mean(window_vals), 3),
                        "p50_ms": round(sv[len(sv) // 2], 3),
                        "p99_ms": round(sv[min(int(len(sv) * 0.99), len(sv) - 1)], 3),
                    })
                window_vals = []
                window_start += window_sec
            window_vals.append(val)

        if window_vals:
            sv = sorted(window_vals)
            windows.append({
                "time_offset_sec": round(window_start - start, 2),
                "ops": len(window_vals),
                "mean_ms": round(statistics.mean(window_vals), 3),
                "p50_ms": round(sv[len(sv) // 2], 3),
                "p99_ms": round(sv[min(int(len(sv) * 0.99), len(sv) - 1)], 3),
            })
        return windows

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "stddev_ms": round(self.stddev_ms, 3),
            "jitter_ms": round(self.jitter_ms, 3),
            "cv_pct": round(self.coefficient_of_variation, 2),
            "p50_ms": round(self.p50, 3),
            "p75_ms": round(self.p75, 3),
            "p90_ms": round(self.p90, 3),
            "p95_ms": round(self.p95, 3),
            "p99_ms": round(self.p99, 3),
            "p999_ms": round(self.p999, 3),
        }


@dataclass
class ThroughputTracker:
    """Track operations and bytes over time with periodic snapshots."""

    _ops: int = 0
    _bytes: int = 0
    _errors: int = 0
    _start: float = 0.0
    _end: float = 0.0
    _snapshots: list[dict] = field(default_factory=list)
    _snapshot_interval_sec: float = 1.0
    _last_snapshot: float = 0.0
    _last_snapshot_ops: int = 0
    _last_snapshot_bytes: int = 0

    def start(self):
        self._start = time.perf_counter()
        self._last_snapshot = self._start
        self._last_snapshot_ops = 0
        self._last_snapshot_bytes = 0

    def stop(self):
        """Freeze elapsed time so ops_per_sec stays correct after the test ends."""
        if self._start and not self._end:
            self._end = time.perf_counter()
            self.snapshot()

    def record_op(self, byte_count: int = 0, is_error: bool = False):
        self._ops += 1
        self._bytes += byte_count
        if is_error:
            self._errors += 1
        now = time.perf_counter()
        if now - self._last_snapshot >= self._snapshot_interval_sec:
            self.snapshot()
            self._last_snapshot = now

    def record_batch(self, op_count: int, total_bytes: int = 0, error_count: int = 0):
        """Record multiple operations at once (e.g., rows from a full table scan)."""
        self._ops += op_count
        self._bytes += total_bytes
        self._errors += error_count
        now = time.perf_counter()
        if now - self._last_snapshot >= self._snapshot_interval_sec:
            self.snapshot()
            self._last_snapshot = now

    def snapshot(self):
        now = time.perf_counter()
        elapsed = now - self._start
        if elapsed <= 0:
            return
        interval_ops = self._ops - self._last_snapshot_ops
        interval_bytes = self._bytes - self._last_snapshot_bytes
        interval_sec = now - self._last_snapshot
        self._snapshots.append({
            "elapsed_sec": round(elapsed, 2),
            "total_ops": self._ops,
            "total_bytes": self._bytes,
            "ops_per_sec": round(interval_ops / interval_sec, 2) if interval_sec > 0 else 0,
            "avg_ops_per_sec": round(self._ops / elapsed, 2),
            "mbps": round((interval_bytes / (1024 * 1024)) / interval_sec, 3) if interval_sec > 0 else 0,
        })
        self._last_snapshot_ops = self._ops
        self._last_snapshot_bytes = self._bytes

    @property
    def elapsed_sec(self) -> float:
        if self._end:
            return self._end - self._start
        return time.perf_counter() - self._start if self._start else 0.0

    @property
    def ops_per_sec(self) -> float:
        e = self.elapsed_sec
        return self._ops / e if e > 0 else 0.0

    @property
    def mbps(self) -> float:
        e = self.elapsed_sec
        return (self._bytes / (1024 * 1024)) / e if e > 0 else 0.0

    @property
    def peak_ops_per_sec(self) -> float:
        if not self._snapshots:
            return self.ops_per_sec
        return max(s["ops_per_sec"] for s in self._snapshots)

    @property
    def error_rate_pct(self) -> float:
        return (self._errors / self._ops * 100) if self._ops > 0 else 0.0

    def to_dict(self) -> dict:
        if not self._end:
            self.stop()
        e = self.elapsed_sec
        return {
            "total_ops": self._ops,
            "total_bytes": self._bytes,
            "total_errors": self._errors,
            "error_rate_pct": round(self.error_rate_pct, 3),
            "duration_sec": round(e, 3),
            "avg_ops_per_sec": round(self._ops / e, 2) if e > 0 else 0,
            "avg_mbps": round((self._bytes / (1024 * 1024)) / e, 3) if e > 0 else 0,
            "peak_ops_per_sec": round(self.peak_ops_per_sec, 2),
            "time_series": self._snapshots,
        }


@dataclass
class ScalabilityPoint:
    threads: int
    iops: float
    p50_ms: float
    p99_ms: float
    throughput_mbps: float
    error_rate_pct: float


@dataclass
class ScalabilityAnalysis:
    """Thread-scaling analysis with efficiency and saturation detection."""

    metric_name: str
    points: list[ScalabilityPoint] = field(default_factory=list)

    def add(self, pt: ScalabilityPoint):
        self.points.append(pt)

    @property
    def baseline_iops(self) -> float:
        return self.points[0].iops if self.points else 0.0

    def scaling_efficiency(self, threads: int) -> float:
        if not self.points:
            return 0.0
        base = self.baseline_iops
        for pt in self.points:
            if pt.threads == threads:
                return (pt.iops / (base * threads)) * 100 if base > 0 else 0.0
        return 0.0

    @property
    def saturation_point(self) -> int:
        """Thread count where adding more threads yields <5% improvement."""
        if len(self.points) < 2:
            return self.points[0].threads if self.points else 1
        for i in range(1, len(self.points)):
            prev = self.points[i - 1].iops
            curr = self.points[i].iops
            if prev > 0 and (curr - prev) / prev < 0.05:
                return self.points[i - 1].threads
        return self.points[-1].threads

    @property
    def optimal_threads(self) -> int:
        """Thread count with best IOPS-per-thread ratio."""
        if not self.points:
            return 1
        best = max(self.points, key=lambda p: p.iops / p.threads if p.threads > 0 else 0)
        return best.threads

    @property
    def peak_iops(self) -> float:
        """Highest IOPS observed across all thread counts."""
        if not self.points:
            return 0.0
        best = max(self.points, key=lambda p: p.iops)
        return best.iops

    @property
    def peak_iops_threads(self) -> int:
        """Thread count at which peak IOPS was observed."""
        if not self.points:
            return 1
        best = max(self.points, key=lambda p: p.iops)
        return best.threads

    @property
    def amdahl_serial_fraction(self) -> float:
        """Estimated serial fraction via Amdahl's law from the highest-thread-count result."""
        if len(self.points) < 2:
            return 1.0
        base = self.baseline_iops
        last = self.points[-1]
        speedup = last.iops / base if base > 0 else 1.0
        n = last.threads
        if speedup <= 0 or n <= 1:
            return 1.0
        return max(0.0, min(1.0, (1.0 / speedup - 1.0 / n) / (1.0 - 1.0 / n)))

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name,
            "points": [
                {
                    "threads": p.threads,
                    "iops": round(p.iops, 2),
                    "p50_ms": round(p.p50_ms, 3),
                    "p99_ms": round(p.p99_ms, 3),
                    "throughput_mbps": round(p.throughput_mbps, 3),
                    "error_rate_pct": round(p.error_rate_pct, 3),
                    "efficiency_pct": round(self.scaling_efficiency(p.threads), 1),
                }
                for p in self.points
            ],
            "saturation_point_threads": self.saturation_point,
            "optimal_threads": self.optimal_threads,
            "peak_iops": round(self.peak_iops, 2),
            "peak_iops_threads": self.peak_iops_threads,
            "amdahl_serial_fraction": round(self.amdahl_serial_fraction, 4),
        }


# ---------------------------------------------------------------------------
# Composite result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SQLIOResult:
    test_name: str
    operation: str
    config: dict
    latency: LatencyHistogram
    throughput: ThroughputTracker
    scalability: Optional[ScalabilityAnalysis] = None
    warm_up_duration_sec: float = 0.0
    pool_high_water: int = 0

    def to_dict(self) -> dict:
        d = {
            "test_name": self.test_name,
            "operation": self.operation,
            "config": self.config,
            "latency": self.latency.to_dict(),
            "latency_distribution": self.latency.distribution_buckets(),
            "latency_time_series": self.latency.time_series(),
            "throughput": self.throughput.to_dict(),
            "warm_up_duration_sec": round(self.warm_up_duration_sec, 3),
            "pool_high_water": self.pool_high_water,
        }
        if self.scalability:
            d["scalability"] = self.scalability.to_dict()
        return d


@dataclass
class SQLIOSimResult:
    test_name: str
    config: dict = field(default_factory=dict)
    pages_written: int = 0
    pages_verified: int = 0
    corruptions_detected: int = 0
    integrity_pass: bool = True
    total_transactions: int = 0
    committed: int = 0
    deadlocks: int = 0
    lock_timeouts: int = 0
    serialization_failures: int = 0
    other_errors: int = 0
    commit_latency: LatencyHistogram = field(default_factory=LatencyHistogram)
    throughput: ThroughputTracker = field(default_factory=ThroughputTracker)
    balance_before: float = 0.0
    balance_after: float = 0.0

    @property
    def balance_drift(self) -> float:
        return abs(self.balance_after - self.balance_before)

    @property
    def tps(self) -> float:
        return self.throughput.ops_per_sec

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "config": self.config,
            "integrity": {
                "pages_written": self.pages_written,
                "pages_verified": self.pages_verified,
                "corruptions_detected": self.corruptions_detected,
                "integrity_pass": self.integrity_pass,
            },
            "concurrency": {
                "total_transactions": self.total_transactions,
                "committed": self.committed,
                "deadlocks": self.deadlocks,
                "lock_timeouts": self.lock_timeouts,
                "serialization_failures": self.serialization_failures,
                "other_errors": self.other_errors,
                "tps": round(self.tps, 2),
            },
            "commit_latency": self.commit_latency.to_dict(),
            "throughput": self.throughput.to_dict(),
            "data_integrity_audit": {
                "balance_before": self.balance_before,
                "balance_after": self.balance_after,
                "balance_drift": self.balance_drift,
                "conservation_pass": self.balance_drift < 0.01,
            },
        }


@dataclass
class DSBQueryResult:
    query_id: str
    duration_sec: float
    rows_returned: int
    status: str
    error_msg: Optional[str] = None
    cold_run_sec: float = 0.0
    warm_run_sec: float = 0.0
    all_durations: list[float] = field(default_factory=list)
    result_columns: list[str] = field(default_factory=list)
    result_rows_preview: list[list] = field(default_factory=list)
    results_truncated: bool = False

    @property
    def cache_speedup_ratio(self) -> float:
        return self.cold_run_sec / self.warm_run_sec if self.warm_run_sec > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "duration_sec": round(self.duration_sec, 3),
            "rows_returned": self.rows_returned,
            "status": self.status,
            "error_msg": self.error_msg,
            "cold_run_sec": round(self.cold_run_sec, 3),
            "warm_run_sec": round(self.warm_run_sec, 3),
            "cache_speedup_ratio": round(self.cache_speedup_ratio, 2),
            "all_durations": [round(d, 3) for d in self.all_durations],
            "result_columns": self.result_columns,
            "result_rows_preview": self.result_rows_preview,
            "results_truncated": self.results_truncated,
        }


@dataclass
class DSBResult:
    scale_factor: float
    queries: list[DSBQueryResult] = field(default_factory=list)
    total_runtime_sec: float = 0.0

    @property
    def geometric_mean_sec(self) -> float:
        ok_times = [q.duration_sec for q in self.queries if q.status == "ok" and q.duration_sec > 0]
        if not ok_times:
            return 0.0
        return math.exp(sum(math.log(t) for t in ok_times) / len(ok_times))

    @property
    def power_score(self) -> float:
        gm = self.geometric_mean_sec
        return (3600.0 * self.scale_factor) / gm if gm > 0 else 0.0

    @property
    def fastest_query(self) -> str:
        ok = [q for q in self.queries if q.status == "ok"]
        return min(ok, key=lambda q: q.duration_sec).query_id if ok else ""

    @property
    def slowest_query(self) -> str:
        ok = [q for q in self.queries if q.status == "ok"]
        return max(ok, key=lambda q: q.duration_sec).query_id if ok else ""

    def to_dict(self) -> dict:
        return {
            "scale_factor": self.scale_factor,
            "total_runtime_sec": round(self.total_runtime_sec, 3),
            "geometric_mean_sec": round(self.geometric_mean_sec, 3),
            "power_score": round(self.power_score, 2),
            "fastest_query": self.fastest_query,
            "slowest_query": self.slowest_query,
            "queries": [q.to_dict() for q in self.queries],
        }


@dataclass
class NetworkResult:
    ping_latency: LatencyHistogram = field(default_factory=LatencyHistogram)
    connection_setup: LatencyHistogram = field(default_factory=LatencyHistogram)
    dns_resolution_ms: float = 0.0
    first_byte_latency: LatencyHistogram = field(default_factory=LatencyHistogram)
    bandwidth_upload_mbps: float = 0.0
    bandwidth_download_mbps: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ping_latency": self.ping_latency.to_dict(),
            "connection_setup": self.connection_setup.to_dict(),
            "dns_resolution_ms": round(self.dns_resolution_ms, 3),
            "first_byte_latency": self.first_byte_latency.to_dict(),
            "bandwidth_upload_mbps": round(self.bandwidth_upload_mbps, 3),
            "bandwidth_download_mbps": round(self.bandwidth_download_mbps, 3),
        }


@dataclass
class FullBenchmarkResult:
    """Top-level result aggregating all sub-benchmarks."""

    timestamp: float = field(default_factory=time.time)
    preset: str = ""
    database_info: dict = field(default_factory=dict)
    sqlio_results: list[SQLIOResult] = field(default_factory=list)
    sqliosim_results: list[SQLIOSimResult] = field(default_factory=list)
    dsb_result: Optional[DSBResult] = None
    network_result: Optional[NetworkResult] = None
    isolation_results: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def record_error(self, test_name: str, error: str):
        self.errors.append({"test": test_name, "error": error})

    def to_dict(self) -> dict:
        d: dict = {
            "timestamp": self.timestamp,
            "preset": self.preset,
            "database_info": self.database_info,
            "sqlio": [r.to_dict() for r in self.sqlio_results],
            "sqliosim": [r.to_dict() for r in self.sqliosim_results],
        }
        if self.dsb_result:
            d["dsb"] = self.dsb_result.to_dict()
        if self.network_result:
            d["network"] = self.network_result.to_dict()
        if self.isolation_results:
            d["isolation"] = self.isolation_results
        if self.errors:
            d["errors"] = self.errors
        return d
