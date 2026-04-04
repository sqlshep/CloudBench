"""Connection pool stress test — find pool saturation and exhaustion thresholds."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, ThroughputTracker, SQLIOResult


class PoolStressTest:
    """Hammers the connection pool at increasing concurrency to find its limits.

    Each burst fires N concurrent SELECT 1 queries, measuring how connection
    checkout latency degrades as the pool approaches saturation.
    """

    BURST_SIZES = [1, 5, 10, 20, 40, 60, 80, 100, 150, 200]

    def __init__(self, db: DatabaseConnection):
        self.db = db

    def run(self, ops_per_burst: int = 200, progress_callback=None) -> SQLIOResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()
        errors = 0
        total_bursts = len(self.BURST_SIZES)
        burst_stats: list[dict] = []

        for idx, concurrency in enumerate(self.BURST_SIZES):
            burst_latency = LatencyHistogram()

            def _ping():
                try:
                    with self.db.engine.connect() as conn:
                        t0 = time.perf_counter()
                        conn.execute(text("SELECT 1"))
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                    latency.record(elapsed_ms, time.perf_counter())
                    burst_latency.record(elapsed_ms, time.perf_counter())
                    throughput.record_op(0)
                    return elapsed_ms
                except Exception:
                    throughput.record_op(0, is_error=True)
                    return None

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(_ping) for _ in range(ops_per_burst)]
                for f in as_completed(futures):
                    if f.result() is None:
                        errors += 1

            burst_stats.append({
                "concurrency": concurrency,
                "p50_ms": round(burst_latency.p50, 3),
                "p99_ms": round(burst_latency.p99, 3),
                "errors": errors,
            })

            if progress_callback:
                progress_callback(idx + 1, total_bursts)

        throughput.stop()
        return SQLIOResult(
            test_name="pool_stress",
            operation="pool",
            config={
                "burst_sizes": self.BURST_SIZES,
                "ops_per_burst": ops_per_burst,
                "burst_stats": burst_stats,
            },
            latency=latency,
            throughput=throughput,
            pool_high_water=self.db.pool_stats.high_water_checked_out,
        )
