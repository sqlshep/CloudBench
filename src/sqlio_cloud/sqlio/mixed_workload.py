"""SQLIO-equivalent mixed read/write workload test."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, ThroughputTracker, SQLIOResult


class MixedWorkloadTest:
    """Concurrent mixed read/write workload at configurable ratios.

    Simulates realistic OLTP patterns where reads and writes are interleaved.
    The read/write ratio is configurable (default 70/30).
    """

    def __init__(
        self,
        db: DatabaseConnection,
        table_rows: int = 1_000_000,
        block_size: int = 8192,
        read_pct: int = 70,
    ):
        self.db = db
        self.table_rows = table_rows
        self.block_size = block_size
        self.read_pct = read_pct

    def run(self, num_ops: int = 10_000, num_threads: int = 8) -> SQLIOResult:
        read_latency = LatencyHistogram()
        write_latency = LatencyHistogram()
        combined_latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        read_count = 0
        write_count = 0

        def _do_op():
            nonlocal read_count, write_count
            is_read = random.randint(1, 100) <= self.read_pct
            row_id = random.randint(1, self.table_rows)

            try:
                if is_read:
                    with self.db.engine.connect() as conn:
                        t0 = time.perf_counter()
                        conn.execute(
                            text("SELECT payload FROM sqlio_random_io WHERE id = :id"),
                            {"id": row_id},
                        ).fetchone()
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                    read_latency.record(elapsed_ms, time.perf_counter())
                    read_count += 1
                else:
                    new_payload = random.randbytes(self.block_size)
                    with self.db.engine.begin() as conn:
                        t0 = time.perf_counter()
                        conn.execute(
                            text("UPDATE sqlio_random_io SET payload = :p, checksum_val = :c WHERE id = :id"),
                            {"p": new_payload, "c": random.randint(0, 2**63 - 1), "id": row_id},
                        )
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                    write_latency.record(elapsed_ms, time.perf_counter())
                    write_count += 1

                combined_latency.record(elapsed_ms, time.perf_counter())
                throughput.record_op(self.block_size)
            except Exception:
                throughput.record_op(0, is_error=True)

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_do_op) for _ in range(num_ops)]
            for f in as_completed(futures):
                f.result()

        throughput.stop()
        return SQLIOResult(
            test_name="mixed_workload",
            operation="mixed",
            config={
                "table_rows": self.table_rows,
                "block_size": self.block_size,
                "num_ops": num_ops,
                "num_threads": num_threads,
                "read_pct": self.read_pct,
                "actual_reads": read_count,
                "actual_writes": write_count,
                "read_latency": read_latency.to_dict(),
                "write_latency": write_latency.to_dict(),
            },
            latency=combined_latency,
            throughput=throughput,
            pool_high_water=self.db.pool_stats.high_water_checked_out,
        )
