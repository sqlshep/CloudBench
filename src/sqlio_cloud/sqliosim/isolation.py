"""SQLIOSim-equivalent isolation level testing.

Verifies that the database correctly enforces transaction isolation by running
known anomaly-detection patterns (dirty reads, non-repeatable reads, phantom
reads) at each isolation level.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection


@dataclass
class IsolationTestResult:
    isolation_level: str
    dirty_read_detected: bool = False
    non_repeatable_read_detected: bool = False
    phantom_read_detected: bool = False
    duration_sec: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def pass_all(self) -> bool:
        return not any([
            self.dirty_read_detected,
            self.non_repeatable_read_detected,
            self.phantom_read_detected,
        ])

    def to_dict(self) -> dict:
        return {
            "isolation_level": self.isolation_level,
            "dirty_read_detected": self.dirty_read_detected,
            "non_repeatable_read_detected": self.non_repeatable_read_detected,
            "phantom_read_detected": self.phantom_read_detected,
            "pass": self.pass_all,
            "duration_sec": round(self.duration_sec, 3),
            "errors": self.errors,
        }


class IsolationTest:
    """Tests transaction isolation behavior at various levels.

    For each isolation level, runs structured anomaly-detection tests and
    reports whether the database correctly prevents each anomaly type.
    """

    LEVELS_PG = ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
    LEVELS_MYSQL = ["READ UNCOMMITTED", "READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
    LEVELS_MSSQL = ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE", "SNAPSHOT"]

    def __init__(self, db: DatabaseConnection):
        self.db = db
        self.dialect = db.dialect_family

    def setup(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))
            conn.execute(text(self._create_ddl()))

    def teardown(self):
        with self.db.engine.begin() as conn:
            conn.execute(text(self._drop_ddl()))

    def run_all(self) -> list[IsolationTestResult]:
        levels = {
            "postgresql": self.LEVELS_PG,
            "mysql": self.LEVELS_MYSQL,
            "mssql": self.LEVELS_MSSQL,
        }.get(self.dialect, self.LEVELS_PG)

        results = []
        for level in levels:
            results.append(self._test_level(level))
        return results

    def _test_level(self, level: str) -> IsolationTestResult:
        result = IsolationTestResult(isolation_level=level)
        t0 = time.perf_counter()

        self._reset_data()

        try:
            result.dirty_read_detected = self._test_dirty_read(level)
        except Exception as e:
            result.errors.append(f"dirty_read test error: {e}")

        self._reset_data()

        try:
            result.non_repeatable_read_detected = self._test_non_repeatable_read(level)
        except Exception as e:
            result.errors.append(f"non_repeatable_read test error: {e}")

        self._reset_data()

        try:
            result.phantom_read_detected = self._test_phantom_read(level)
        except Exception as e:
            result.errors.append(f"phantom_read test error: {e}")

        result.duration_sec = time.perf_counter() - t0
        return result

    def _set_isolation(self, conn, level: str):
        if self.dialect == "mssql" and level == "SNAPSHOT":
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL SNAPSHOT"))
        else:
            conn.execute(text(f"SET TRANSACTION ISOLATION LEVEL {level}"))

    def _reset_data(self):
        with self.db.engine.begin() as conn:
            conn.execute(text("DELETE FROM sqliosim_isolation"))
            conn.execute(text("INSERT INTO sqliosim_isolation (id, val) VALUES (1, 100)"))
            conn.execute(text("INSERT INTO sqliosim_isolation (id, val) VALUES (2, 200)"))

    def _test_dirty_read(self, level: str) -> bool:
        """Returns True if a dirty read was detected (bad for most levels)."""
        saw_dirty = threading.Event()
        writer_started = threading.Event()

        def writer():
            conn = self.db.engine.connect()
            txn = conn.begin()
            conn.execute(text("UPDATE sqliosim_isolation SET val = 999 WHERE id = 1"))
            writer_started.set()
            time.sleep(0.5)
            txn.rollback()
            conn.close()

        def reader():
            time.sleep(0.1)
            writer_started.wait(timeout=5)
            conn = self.db.engine.connect()
            self._set_isolation(conn, level)
            txn = conn.begin()
            row = conn.execute(text("SELECT val FROM sqliosim_isolation WHERE id = 1")).fetchone()
            if row and row[0] == 999:
                saw_dirty.set()
            txn.commit()
            conn.close()

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        return saw_dirty.is_set()

    def _test_non_repeatable_read(self, level: str) -> bool:
        """Returns True if a non-repeatable read was detected."""
        detected = threading.Event()
        first_read_done = threading.Event()

        def reader():
            conn = self.db.engine.connect()
            self._set_isolation(conn, level)
            txn = conn.begin()
            row1 = conn.execute(text("SELECT val FROM sqliosim_isolation WHERE id = 1")).fetchone()
            first_read_done.set()
            time.sleep(0.5)
            row2 = conn.execute(text("SELECT val FROM sqliosim_isolation WHERE id = 1")).fetchone()
            if row1 and row2 and row1[0] != row2[0]:
                detected.set()
            txn.commit()
            conn.close()

        def writer():
            first_read_done.wait(timeout=5)
            time.sleep(0.1)
            with self.db.engine.begin() as conn:
                conn.execute(text("UPDATE sqliosim_isolation SET val = 777 WHERE id = 1"))

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        return detected.is_set()

    def _test_phantom_read(self, level: str) -> bool:
        """Returns True if a phantom read was detected."""
        detected = threading.Event()
        first_count_done = threading.Event()

        def reader():
            conn = self.db.engine.connect()
            self._set_isolation(conn, level)
            txn = conn.begin()
            c1 = conn.execute(text("SELECT COUNT(*) FROM sqliosim_isolation WHERE val > 50")).scalar()
            first_count_done.set()
            time.sleep(0.5)
            c2 = conn.execute(text("SELECT COUNT(*) FROM sqliosim_isolation WHERE val > 50")).scalar()
            if c1 != c2:
                detected.set()
            txn.commit()
            conn.close()

        def writer():
            first_count_done.wait(timeout=5)
            time.sleep(0.1)
            with self.db.engine.begin() as conn:
                conn.execute(text("INSERT INTO sqliosim_isolation (id, val) VALUES (99, 500)"))

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        return detected.is_set()

    def _create_ddl(self) -> str:
        if self.dialect == "mssql":
            return """
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sqliosim_isolation')
                CREATE TABLE sqliosim_isolation (id INT PRIMARY KEY, val INT NOT NULL)
            """
        return "CREATE TABLE IF NOT EXISTS sqliosim_isolation (id INT PRIMARY KEY, val INT NOT NULL)"

    def _drop_ddl(self) -> str:
        if self.dialect == "mssql":
            return "IF OBJECT_ID('sqliosim_isolation', 'U') IS NOT NULL DROP TABLE sqliosim_isolation"
        return "DROP TABLE IF EXISTS sqliosim_isolation"
