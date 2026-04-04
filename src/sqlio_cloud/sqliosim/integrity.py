"""SQLIOSim-equivalent data integrity stress test.

Writes pages with SHA-256 checksums under concurrent load, then verifies that
data can be read back without corruption — the cloud equivalent of SQLIOSim's
I/O path integrity verification.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, ThroughputTracker, SQLIOSimResult


class IntegrityStressTest:
    def __init__(self, db: DatabaseConnection, page_size: int = 8192):
        self.db = db
        self.page_size = page_size
        self.dialect = db.dialect_family

    def setup(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))
            conn.execute(text(self._create_ddl()))

    def teardown(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))

    def run(
        self,
        num_cycles: int = 5000,
        write_threads: int = 8,
        verify_sample_pct: float = 0.2,
    ) -> SQLIOSimResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        written_ids: list[int] = []
        errors = 0

        def _write_page():
            data = os.urandom(self.page_size)
            digest = hashlib.sha256(data).hexdigest()
            params = {"data": data, "hash": digest, "epoch": int(time.time() * 1_000_000)}
            try:
                with self.db.engine.begin() as conn:
                    t0 = time.perf_counter()
                    if self.dialect == "mssql":
                        row = conn.execute(
                            text(
                                "INSERT INTO sqliosim_integrity (page_data, sha256_hash, write_epoch) "
                                "OUTPUT INSERTED.id "
                                "VALUES (:data, :hash, :epoch)"
                            ), params,
                        )
                        row_id = row.scalar()
                    elif self.dialect == "mysql":
                        conn.execute(
                            text(
                                "INSERT INTO sqliosim_integrity (page_data, sha256_hash, write_epoch) "
                                "VALUES (:data, :hash, :epoch)"
                            ), params,
                        )
                        row_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                    else:
                        row = conn.execute(
                            text(
                                "INSERT INTO sqliosim_integrity (page_data, sha256_hash, write_epoch) "
                                "VALUES (:data, :hash, :epoch) RETURNING id"
                            ), params,
                        )
                        row_id = row.scalar()
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                latency.record(elapsed_ms, time.perf_counter())
                throughput.record_op(self.page_size)
                return row_id
            except Exception:
                throughput.record_op(0, is_error=True)
                return None

        with ThreadPoolExecutor(max_workers=write_threads) as pool:
            futures = [pool.submit(_write_page) for _ in range(num_cycles)]
            for f in as_completed(futures):
                rid = f.result()
                if rid is not None:
                    written_ids.append(rid)
                else:
                    errors += 1

        throughput.stop()
        sample_size = max(1, int(len(written_ids) * verify_sample_pct))
        sample_ids = random.sample(written_ids, min(sample_size, len(written_ids)))
        corruptions = 0

        with self.db.engine.connect() as conn:
            for row_id in sample_ids:
                row = conn.execute(
                    text("SELECT page_data, sha256_hash FROM sqliosim_integrity WHERE id = :id"),
                    {"id": row_id},
                ).fetchone()
                if row is None:
                    corruptions += 1
                    continue
                page_data = row[0]
                if isinstance(page_data, memoryview):
                    page_data = bytes(page_data)
                actual = hashlib.sha256(page_data).hexdigest()
                if actual != row[1]:
                    corruptions += 1

        result = SQLIOSimResult(
            test_name="integrity_stress",
            config={
                "num_cycles": num_cycles,
                "write_threads": write_threads,
                "page_size": self.page_size,
                "verify_sample_pct": verify_sample_pct,
            },
            pages_written=len(written_ids),
            pages_verified=len(sample_ids),
            corruptions_detected=corruptions,
            integrity_pass=(corruptions == 0),
            total_transactions=num_cycles,
            committed=len(written_ids),
            other_errors=errors,
            commit_latency=latency,
            throughput=throughput,
        )
        return result

    def _create_ddl(self) -> str:
        if self.dialect == "mssql":
            return """
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sqliosim_integrity')
                CREATE TABLE sqliosim_integrity (
                    id BIGINT IDENTITY(1,1) PRIMARY KEY,
                    page_data VARBINARY(MAX) NOT NULL,
                    sha256_hash VARCHAR(64) NOT NULL,
                    write_epoch BIGINT NOT NULL
                )
            """
        if self.dialect == "mysql":
            return """
                CREATE TABLE IF NOT EXISTS sqliosim_integrity (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    page_data LONGBLOB NOT NULL,
                    sha256_hash VARCHAR(64) NOT NULL,
                    write_epoch BIGINT NOT NULL
                )
            """
        return """
            CREATE TABLE IF NOT EXISTS sqliosim_integrity (
                id BIGSERIAL PRIMARY KEY,
                page_data BYTEA NOT NULL,
                sha256_hash VARCHAR(64) NOT NULL,
                write_epoch BIGINT NOT NULL
            )
        """

    def _drop_ddl(self) -> str:
        if self.dialect == "mssql":
            return "IF OBJECT_ID('sqliosim_integrity', 'U') IS NOT NULL DROP TABLE sqliosim_integrity"
        return "DROP TABLE IF EXISTS sqliosim_integrity"
