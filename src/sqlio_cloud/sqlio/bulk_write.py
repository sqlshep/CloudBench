"""SQLIO-equivalent bulk write test: measures INSERT throughput at various batch sizes."""

from __future__ import annotations

import random
import time
from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, ThroughputTracker, SQLIOResult


class BulkWriteTest:
    """Measures bulk INSERT throughput — the cloud analogue of SQLIO's sequential write.

    Tests how fast the cloud database can ingest data by inserting batches of
    rows with varying batch sizes and measuring rows/sec and MB/s.
    """

    def __init__(self, db: DatabaseConnection, block_size: int = 8192):
        self.db = db
        self.block_size = block_size
        self.dialect = db.dialect_family

    def setup(self):
        ddl = self._create_ddl()
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))
            conn.execute(text(ddl))

    def teardown(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))

    def run(self, total_rows: int = 100_000, batch_size: int = 1000) -> SQLIOResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        row_id = 0
        num_batches = (total_rows + batch_size - 1) // batch_size

        for _ in range(num_batches):
            batch_data = []
            for _ in range(batch_size):
                row_id += 1
                if row_id > total_rows:
                    break
                batch_data.append({
                    "id": row_id,
                    "payload": random.randbytes(self.block_size),
                    "checksum_val": row_id * 7 + 13,
                })

            if not batch_data:
                break

            t0 = time.perf_counter()
            try:
                with self.db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO sqlio_bulk_write (id, payload, checksum_val) "
                            "VALUES (:id, :payload, :checksum_val)"
                        ),
                        batch_data,
                    )
                elapsed_ms = (time.perf_counter() - t0) * 1000
                latency.record(elapsed_ms, time.perf_counter())
                throughput.record_batch(len(batch_data), len(batch_data) * self.block_size)
            except Exception:
                throughput.record_batch(len(batch_data), 0, error_count=len(batch_data))

        throughput.stop()
        return SQLIOResult(
            test_name="bulk_write",
            operation="bulk_insert",
            config={
                "total_rows": total_rows,
                "batch_size": batch_size,
                "block_size": self.block_size,
            },
            latency=latency,
            throughput=throughput,
            pool_high_water=self.db.pool_stats.high_water_checked_out,
        )

    def _create_ddl(self) -> str:
        if self.dialect == "mssql":
            return """
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sqlio_bulk_write')
                CREATE TABLE sqlio_bulk_write (
                    id BIGINT PRIMARY KEY, payload VARBINARY(MAX), checksum_val BIGINT
                )
            """
        if self.dialect == "mysql":
            return """
                CREATE TABLE IF NOT EXISTS sqlio_bulk_write (
                    id BIGINT PRIMARY KEY, payload LONGBLOB, checksum_val BIGINT
                )
            """
        return """
            CREATE TABLE IF NOT EXISTS sqlio_bulk_write (
                id BIGINT PRIMARY KEY, payload BYTEA, checksum_val BIGINT
            )
        """

    def _drop_ddl(self) -> str:
        if self.dialect == "mssql":
            return "IF OBJECT_ID('sqlio_bulk_write', 'U') IS NOT NULL DROP TABLE sqlio_bulk_write"
        return "DROP TABLE IF EXISTS sqlio_bulk_write"
