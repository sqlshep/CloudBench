"""SQLIO-equivalent random I/O test: random point reads and single-row writes."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import (
    LatencyHistogram, ThroughputTracker, ScalabilityAnalysis,
    ScalabilityPoint, SQLIOResult,
)
from sqlio_cloud.sqlio.dialect import (
    create_io_table, populate_batch, drop_io_table,
)


class RandomIOTest:
    """Measures random point-lookup and single-row update performance.

    This is the cloud equivalent of SQLIO's random read/write test: instead of
    hitting raw disk blocks, we hit random rows by primary key — exercising the
    same access pattern through the cloud database's I/O subsystem.
    """

    def __init__(self, db: DatabaseConnection, table_rows: int = 1_000_000, block_size: int = 8192):
        self.db = db
        self.table_rows = table_rows
        self.block_size = block_size
        self.dialect = db.dialect_family

    def setup(self, progress_callback=None):
        """Drop, recreate, and populate the test table from scratch."""
        with self.db.engine.begin() as conn:
            if self.dialect == "postgresql":
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            conn.execute(text(drop_io_table(self.dialect)))
            conn.execute(text(create_io_table(self.dialect)))

        batch = 1000 if self.dialect == "mssql" else 10_000
        total_batches = (self.table_rows + batch - 1) // batch

        for i, offset in enumerate(range(1, self.table_rows + 1, batch)):
            end = min(offset + batch - 1, self.table_rows)
            with self.db.engine.begin() as conn:
                conn.execute(
                    text(populate_batch(self.dialect, self.block_size)),
                    {"offset_start": offset, "offset_end": end},
                )
            if progress_callback:
                progress_callback(int((i + 1) / total_batches * 100))

    def teardown(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(drop_io_table(self.dialect)))

    def run_random_reads(self, num_ops: int = 10_000, num_threads: int = 8,
                         progress_callback=None) -> SQLIOResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        def _read_one():
            row_id = random.randint(1, self.table_rows)
            try:
                with self.db.engine.connect() as conn:
                    t0 = time.perf_counter()
                    conn.execute(
                        text("SELECT payload FROM sqlio_random_io WHERE id = :id"),
                        {"id": row_id},
                    ).fetchone()
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                latency.record(elapsed_ms, time.perf_counter())
                throughput.record_op(self.block_size)
                return elapsed_ms
            except Exception:
                throughput.record_op(0, is_error=True)
                return None

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_read_one) for _ in range(num_ops)]
            for i, f in enumerate(as_completed(futures), 1):
                f.result()
                if progress_callback and i % max(1, num_ops // 100) == 0:
                    progress_callback(i, num_ops)

        throughput.stop()
        return SQLIOResult(
            test_name="random_read",
            operation="read",
            config={"table_rows": self.table_rows, "block_size": self.block_size,
                    "num_ops": num_ops, "num_threads": num_threads},
            latency=latency,
            throughput=throughput,
            pool_high_water=self.db.pool_stats.high_water_checked_out,
        )

    def run_random_writes(self, num_ops: int = 10_000, num_threads: int = 8,
                          progress_callback=None) -> SQLIOResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        def _write_one():
            row_id = random.randint(1, self.table_rows)
            new_payload = random.randbytes(self.block_size)
            try:
                with self.db.engine.begin() as conn:
                    t0 = time.perf_counter()
                    conn.execute(
                        text("UPDATE sqlio_random_io SET payload = :p, checksum_val = :c WHERE id = :id"),
                        {"p": new_payload, "c": random.randint(0, 2**63 - 1), "id": row_id},
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                latency.record(elapsed_ms, time.perf_counter())
                throughput.record_op(self.block_size)
                return elapsed_ms
            except Exception:
                throughput.record_op(0, is_error=True)
                return None

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_write_one) for _ in range(num_ops)]
            for i, f in enumerate(as_completed(futures), 1):
                f.result()
                if progress_callback and i % max(1, num_ops // 100) == 0:
                    progress_callback(i, num_ops)

        throughput.stop()
        return SQLIOResult(
            test_name="random_write",
            operation="write",
            config={"table_rows": self.table_rows, "block_size": self.block_size,
                    "num_ops": num_ops, "num_threads": num_threads},
            latency=latency,
            throughput=throughput,
            pool_high_water=self.db.pool_stats.high_water_checked_out,
        )

    def run_scaling_sweep(
        self,
        operation: str,
        thread_counts: list[int],
        ops_per_run: int = 10_000,
        progress_callback=None,
    ) -> ScalabilityAnalysis:
        """Run the same test at multiple thread counts and produce a scaling analysis."""
        analysis = ScalabilityAnalysis(metric_name=f"random_{operation}")
        for i, tc in enumerate(thread_counts):
            if progress_callback:
                progress_callback(f"Scaling {operation} @ {tc} threads", int(i / len(thread_counts) * 90))

            if operation == "read":
                result = self.run_random_reads(num_ops=ops_per_run, num_threads=tc)
            else:
                result = self.run_random_writes(num_ops=ops_per_run, num_threads=tc)

            analysis.add(ScalabilityPoint(
                threads=tc,
                iops=result.throughput.ops_per_sec,
                p50_ms=result.latency.p50,
                p99_ms=result.latency.p99,
                throughput_mbps=result.throughput.mbps,
                error_rate_pct=result.throughput.error_rate_pct,
            ))
        return analysis
