"""Network latency profiler: measures round-trip time, connection setup, and bandwidth."""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

from sqlalchemy import text, pool as sa_pool, create_engine

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, NetworkResult

FIRST_BYTE_COUNT = 20
CONN_TIMEOUT_SEC = 15


class NetworkProfiler:
    """Dedicated network-layer profiling between client and cloud database.

    Measures:
    - Ping (SELECT 1) round-trip latency
    - New connection establishment time (with per-connection timeout)
    - First-byte latency on result sets
    - Upload bandwidth (bulk INSERT)
    - Download bandwidth (large SELECT)
    """

    def __init__(self, db: DatabaseConnection):
        self.db = db
        self.dialect = db.dialect_family

    def run(
        self,
        ping_count: int = 100,
        connection_count: int = 50,
        bandwidth_rows: int = 100_000,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> NetworkResult:
        """Run all network profiling tests.

        progress_callback(step, total_steps, phase_label) is called after
        each countable operation so callers can show granular progress.
        """
        result = NetworkResult()
        total_steps = 1 + ping_count + connection_count + FIRST_BYTE_COUNT + 2
        step = 0

        def _tick(label: str):
            nonlocal step
            step += 1
            if progress_callback:
                progress_callback(step, total_steps, label)

        # --- DNS ---
        result.dns_resolution_ms = self.db.dns_resolve_ms()
        _tick("DNS resolution")

        # --- Ping ---
        result.ping_latency = self._measure_ping(ping_count, _tick)

        # --- Connection setup ---
        result.connection_setup = self._measure_connection_setup(connection_count, _tick)

        # --- First byte ---
        result.first_byte_latency = self._measure_first_byte(FIRST_BYTE_COUNT, _tick)

        # --- Bandwidth ---
        upload, download = self._measure_bandwidth(bandwidth_rows, _tick)
        result.bandwidth_upload_mbps = upload
        result.bandwidth_download_mbps = download

        return result

    def _measure_ping(self, count: int, tick) -> LatencyHistogram:
        hist = LatencyHistogram()
        with self.db.engine.connect() as conn:
            for _ in range(count):
                t0 = time.perf_counter()
                conn.execute(text("SELECT 1"))
                elapsed_ms = (time.perf_counter() - t0) * 1000
                hist.record(elapsed_ms, time.perf_counter())
                tick("Ping")
        return hist

    def _measure_connection_setup(self, count: int, tick) -> LatencyHistogram:
        hist = LatencyHistogram()
        connect_args: dict = {}
        dialect = self.db.config.dialect

        if "psycopg" in dialect or "postgresql" in dialect:
            connect_args["connect_timeout"] = CONN_TIMEOUT_SEC
        elif "pymssql" in dialect or "mssql" in dialect:
            connect_args["login_timeout"] = CONN_TIMEOUT_SEC
            connect_args["timeout"] = CONN_TIMEOUT_SEC
        elif "pymysql" in dialect or "mysql" in dialect:
            connect_args["connect_timeout"] = CONN_TIMEOUT_SEC
            connect_args["read_timeout"] = CONN_TIMEOUT_SEC

        raw_engine = create_engine(
            self.db.config.url,
            poolclass=sa_pool.NullPool,
            connect_args=connect_args,
        )
        try:
            for _ in range(count):
                t0 = time.perf_counter()
                try:
                    conn = raw_engine.connect()
                    conn.execute(text("SELECT 1"))
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    hist.record(elapsed_ms, time.perf_counter())
                    conn.close()
                except Exception:
                    pass
                tick("Connection setup")
        finally:
            raw_engine.dispose()
        return hist

    def _measure_first_byte(self, count: int, tick) -> LatencyHistogram:
        """Time from execute() to first row returned on a moderately sized result."""
        hist = LatencyHistogram()

        if self.dialect == "mssql":
            query = "SELECT TOP 1000 id, payload FROM sqlio_random_io"
        elif self.dialect == "mysql":
            query = "SELECT id, payload FROM sqlio_random_io LIMIT 1000"
        else:
            query = "SELECT id, payload FROM sqlio_random_io LIMIT 1000"

        for _ in range(count):
            try:
                with self.db.engine.connect() as conn:
                    t0 = time.perf_counter()
                    result = conn.execute(text(query))
                    _ = result.fetchone()
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    result.close()
                    hist.record(elapsed_ms, time.perf_counter())
            except Exception:
                pass
            tick("First byte")
        return hist

    def _measure_bandwidth(self, row_count: int, tick) -> tuple[float, float]:
        """Returns (upload_mbps, download_mbps)."""
        chunk = 1024
        actual_rows = min(row_count, 5_000)
        self._ensure_bandwidth_table()

        data = [
            {"id": i, "payload": os.urandom(chunk)}
            for i in range(1, actual_rows + 1)
        ]
        total_bytes = len(data) * chunk

        t0 = time.perf_counter()
        try:
            max_per_stmt = 500
            with self.db.engine.begin() as conn:
                for i in range(0, len(data), max_per_stmt):
                    batch = data[i:i + max_per_stmt]
                    placeholders = []
                    params: dict = {}
                    for ri, row in enumerate(batch):
                        params[f"id{ri}"] = row["id"]
                        params[f"p{ri}"] = row["payload"]
                        placeholders.append(f"(:id{ri}, :p{ri})")
                    sql = f"INSERT INTO sqlio_net_bw (id, payload) VALUES {', '.join(placeholders)}"
                    conn.execute(text(sql), params)
            upload_sec = time.perf_counter() - t0
            upload_mbps = (total_bytes / (1024 * 1024)) / upload_sec if upload_sec > 0 else 0
        except Exception:
            upload_mbps = 0
        tick("Bandwidth upload")

        t0 = time.perf_counter()
        try:
            with self.db.engine.connect() as conn:
                rows = conn.execute(text("SELECT id, payload FROM sqlio_net_bw")).fetchall()
            download_sec = time.perf_counter() - t0
            download_bytes = len(rows) * chunk
            download_mbps = (download_bytes / (1024 * 1024)) / download_sec if download_sec > 0 else 0
        except Exception:
            download_mbps = 0
        tick("Bandwidth download")

        self._cleanup_bandwidth_table()
        return upload_mbps, download_mbps

    def _ensure_bandwidth_table(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_bw()))
            conn.execute(text(self._create_bw()))

    def _cleanup_bandwidth_table(self):
        try:
            with self.db.engine.begin() as conn:
                conn.execute(text(self._drop_bw()))
        except Exception:
            pass

    def _create_bw(self) -> str:
        if self.dialect == "mssql":
            return """
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sqlio_net_bw')
                CREATE TABLE sqlio_net_bw (id BIGINT PRIMARY KEY, payload VARBINARY(MAX))
            """
        if self.dialect == "mysql":
            return "CREATE TABLE IF NOT EXISTS sqlio_net_bw (id BIGINT PRIMARY KEY, payload LONGBLOB)"
        return "CREATE TABLE IF NOT EXISTS sqlio_net_bw (id BIGINT PRIMARY KEY, payload BYTEA)"

    def _drop_bw(self) -> str:
        if self.dialect == "mssql":
            return "IF OBJECT_ID('sqlio_net_bw', 'U') IS NOT NULL DROP TABLE sqlio_net_bw"
        return "DROP TABLE IF EXISTS sqlio_net_bw"
