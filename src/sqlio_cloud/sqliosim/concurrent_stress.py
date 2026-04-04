"""SQLIOSim-equivalent concurrent transaction stress test.

Runs a bank-transfer workload under heavy concurrency to stress-test locking,
deadlock detection, and transaction isolation. Verifies that the sum of all
balances is conserved (no phantom money created or destroyed).
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import LatencyHistogram, ThroughputTracker, SQLIOSimResult


class ConcurrentStressTest:
    def __init__(self, db: DatabaseConnection, account_count: int = 10_000):
        self.db = db
        self.account_count = account_count
        self.dialect = db.dialect_family
        self.initial_balance = 1000.00

    def setup(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))
            conn.execute(text(self._create_ddl()))
            conn.execute(text(self._populate()))

    def teardown(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))

    def run(self, num_txns: int = 10_000, num_threads: int = 16) -> SQLIOSimResult:
        latency = LatencyHistogram()
        throughput = ThroughputTracker()
        throughput.start()

        counters = {"ok": 0, "deadlock": 0, "lock_timeout": 0, "serial": 0, "other": 0}

        with self.db.engine.connect() as conn:
            balance_before = conn.execute(
                text("SELECT SUM(balance) FROM sqliosim_accounts")
            ).scalar() or 0

        def _transfer():
            a, b = random.sample(range(1, self.account_count + 1), 2)
            lo, hi = min(a, b), max(a, b)
            amount = round(random.uniform(0.01, 50.00), 2)

            try:
                with self.db.engine.begin() as conn:
                    t0 = time.perf_counter()

                    if self.dialect == "mssql":
                        conn.execute(
                            text("SELECT balance FROM sqliosim_accounts WITH (UPDLOCK) WHERE id IN (:lo, :hi)"),
                            {"lo": lo, "hi": hi},
                        )
                    else:
                        conn.execute(
                            text("SELECT balance FROM sqliosim_accounts WHERE id IN (:lo, :hi) FOR UPDATE"),
                            {"lo": lo, "hi": hi},
                        )

                    conn.execute(
                        text("UPDATE sqliosim_accounts SET balance = balance - :amt, version = version + 1 WHERE id = :id"),
                        {"amt": amount, "id": lo},
                    )
                    conn.execute(
                        text("UPDATE sqliosim_accounts SET balance = balance + :amt, version = version + 1 WHERE id = :id"),
                        {"amt": amount, "id": hi},
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                latency.record(elapsed_ms, time.perf_counter())
                throughput.record_op()
                return "ok"
            except Exception as e:
                throughput.record_op(0, is_error=True)
                err = str(e).lower()
                if "deadlock" in err:
                    return "deadlock"
                if "lock" in err and "timeout" in err:
                    return "lock_timeout"
                if "serialization" in err or "could not serialize" in err:
                    return "serial"
                return "other"

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_transfer) for _ in range(num_txns)]
            for f in as_completed(futures):
                counters[f.result()] += 1

        throughput.stop()
        with self.db.engine.connect() as conn:
            balance_after = conn.execute(
                text("SELECT SUM(balance) FROM sqliosim_accounts")
            ).scalar() or 0

        return SQLIOSimResult(
            test_name="concurrent_stress",
            config={
                "num_transactions": num_txns,
                "num_threads": num_threads,
                "account_count": self.account_count,
            },
            total_transactions=num_txns,
            committed=counters["ok"],
            deadlocks=counters["deadlock"],
            lock_timeouts=counters["lock_timeout"],
            serialization_failures=counters["serial"],
            other_errors=counters["other"],
            commit_latency=latency,
            throughput=throughput,
            balance_before=float(balance_before),
            balance_after=float(balance_after),
        )

    def _create_ddl(self) -> str:
        if self.dialect == "mssql":
            return """
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sqliosim_accounts')
                CREATE TABLE sqliosim_accounts (
                    id BIGINT PRIMARY KEY,
                    balance DECIMAL(18,2) NOT NULL DEFAULT 1000.00,
                    version INT NOT NULL DEFAULT 1
                )
            """
        if self.dialect == "mysql":
            return """
                CREATE TABLE IF NOT EXISTS sqliosim_accounts (
                    id BIGINT PRIMARY KEY,
                    balance DECIMAL(18,2) NOT NULL DEFAULT 1000.00,
                    version INT NOT NULL DEFAULT 1
                ) ENGINE=InnoDB
            """
        return """
            CREATE TABLE IF NOT EXISTS sqliosim_accounts (
                id BIGINT PRIMARY KEY,
                balance NUMERIC(18,2) NOT NULL DEFAULT 1000.00,
                version INT NOT NULL DEFAULT 1
            )
        """

    def _populate(self) -> str:
        if self.dialect == "mssql":
            return f"""
                SET NOCOUNT ON;
                DECLARE @i INT = 1;
                WHILE @i <= {self.account_count}
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM sqliosim_accounts WHERE id = @i)
                        INSERT INTO sqliosim_accounts (id, balance) VALUES (@i, {self.initial_balance});
                    SET @i = @i + 1;
                END
            """
        if self.dialect == "mysql":
            return f"""
                INSERT IGNORE INTO sqliosim_accounts (id, balance)
                SELECT n.num, {self.initial_balance}
                FROM (
                    SELECT @row := @row + 1 AS num
                    FROM information_schema.columns a, information_schema.columns b,
                         (SELECT @row := 0) r
                    LIMIT {self.account_count}
                ) n
            """
        return f"""
            INSERT INTO sqliosim_accounts (id, balance)
            SELECT n, {self.initial_balance}
            FROM generate_series(1, {self.account_count}) AS gs(n)
            ON CONFLICT (id) DO NOTHING
        """

    def _drop_ddl(self) -> str:
        if self.dialect == "mssql":
            return "IF OBJECT_ID('sqliosim_accounts', 'U') IS NOT NULL DROP TABLE sqliosim_accounts"
        return "DROP TABLE IF EXISTS sqliosim_accounts"
