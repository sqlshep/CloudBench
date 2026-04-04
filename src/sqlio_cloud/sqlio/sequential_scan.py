"""SQLIO-equivalent sequential scan test: measures full-table read throughput."""

from __future__ import annotations

import time
from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, ThroughputTracker, SQLIOResult
from sqlio_cloud.sqlio.dialect import data_length_func


class SequentialScanTest:
    """Measures sequential read throughput via full-table scans.

    This is the cloud equivalent of SQLIO's sequential read test: a full table
    scan forces the database to read pages in order, exercising sequential I/O
    on the storage layer.
    """

    def __init__(self, db: DatabaseConnection):
        self.db = db
        self.dialect = db.dialect_family

    def run(self, table: str = "sqlio_random_io", iterations: int = 3) -> SQLIOResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        length_expr = data_length_func(self.dialect, "payload")
        total_rows = 0
        total_bytes = 0

        with self.db.engine.connect() as conn:
            row = conn.execute(text(f"SELECT COUNT(*), COALESCE(SUM({length_expr}), 0) FROM {table}")).fetchone()
            total_rows = int(row[0] or 0)
            total_bytes = int(row[1] or 0)

        for i in range(iterations):
            with self.db.engine.connect() as conn:
                t0 = time.perf_counter()
                result = conn.execute(text(f"SELECT id, payload, checksum_val FROM {table}"))
                _ = result.fetchall()
                elapsed_ms = (time.perf_counter() - t0) * 1000

            latency.record(elapsed_ms, time.perf_counter())
            throughput.record_batch(total_rows, total_bytes)

        throughput.stop()
        return SQLIOResult(
            test_name="sequential_scan",
            operation="seq_read",
            config={
                "table": table,
                "iterations": iterations,
                "total_rows": total_rows,
                "total_data_mb": round(total_bytes / (1024 * 1024), 2),
            },
            latency=latency,
            throughput=throughput,
            pool_high_water=self.db.pool_stats.high_water_checked_out,
        )
