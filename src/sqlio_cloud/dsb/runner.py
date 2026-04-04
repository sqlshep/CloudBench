"""Analytical query runner with cold/warm iteration support and dialect adaptation."""

from __future__ import annotations

import time
from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.metrics import DSBQueryResult, DSBResult
from sqlio_cloud.dsb.queries import DSB_QUERIES

from decimal import Decimal
from datetime import date as _date, datetime as _datetime


def _safe_val(v):
    """Convert DB values to JSON-safe primitives."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_date, _datetime)):
        return str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _adapt_sql(sql: str, dialect: str) -> str:
    """Light dialect translation for cross-database compatibility."""
    if dialect == "postgresql":
        import re
        sql = re.sub(r"DATE\s+:(\w+)", r"CAST(:\1 AS DATE)", sql)
        return sql

    if dialect == "mysql":
        sql = sql.replace("EXTRACT(YEAR FROM ", "YEAR(")
        sql = sql.replace("::NUMERIC", "")
        sql = sql.replace("DATE '", "'")
        sql = sql.replace("INTERVAL '90 DAY'", "INTERVAL 90 DAY")
        sql = sql.replace("INTERVAL '3 MONTH'", "INTERVAL 3 MONTH")
        sql = sql.replace("INTERVAL '1 YEAR'", "INTERVAL 1 YEAR")
        sql = sql.replace("INTERVAL '1 MONTH'", "INTERVAL 1 MONTH")

    elif dialect == "mssql":
        sql = sql.replace("EXTRACT(YEAR FROM ", "YEAR(")
        sql = sql.replace("::NUMERIC", "")

        import re
        sql = re.sub(
            r"DATE\s+'([^']+)'\s*-\s*INTERVAL\s+'(\d+)\s+DAY'",
            r"DATEADD(DAY, -\2, '\1')",
            sql,
        )
        sql = re.sub(
            r"DATE\s+'([^']+)'\s*\+\s*INTERVAL\s+'(\d+)\s+MONTH'",
            r"DATEADD(MONTH, \2, '\1')",
            sql,
        )
        sql = re.sub(
            r"DATE\s+:(\w+)\s*\+\s*INTERVAL\s+'3\s+MONTH'",
            r"DATEADD(MONTH, 3, :\1)",
            sql,
        )
        sql = re.sub(
            r"DATE\s+:(\w+)\s*\+\s*INTERVAL\s+'1\s+YEAR'",
            r"DATEADD(YEAR, 1, :\1)",
            sql,
        )
        sql = re.sub(
            r"DATE\s+:(\w+)\s*\+\s*INTERVAL\s+'1\s+MONTH'",
            r"DATEADD(MONTH, 1, :\1)",
            sql,
        )
        sql = sql.replace("DATE '", "'")
        sql = sql.replace("DATE :", ":")
    return sql


class DSBRunner:
    """Runs decision-support benchmark queries with multi-iteration cold/warm support."""

    def __init__(self, db: DatabaseConnection, scale_factor: float = 1.0):
        self.db = db
        self.sf = scale_factor
        self.dialect = db.dialect_family

    def run_query(
        self,
        query_id: str,
        params: dict | None = None,
        timeout_sec: int = 300,
        iterations: int = 1,
    ) -> DSBQueryResult:
        sql_template, default_params = DSB_QUERIES[query_id]
        merged = {**default_params, **(params or {})}
        sql = _adapt_sql(sql_template, self.dialect)

        durations: list[float] = []
        preview_columns: list[str] = []
        preview_rows: list[list] = []
        preview_limit = 10
        results_truncated = False

        for _ in range(iterations):
            try:
                with self.db.engine.connect() as conn:
                    self._set_timeout(conn, timeout_sec)
                    t0 = time.perf_counter()
                    result = conn.execute(text(sql), merged)
                    rows = result.fetchall()
                    duration = time.perf_counter() - t0
                    durations.append(duration)

                    if not preview_columns and result.keys():
                        preview_columns = [str(c) for c in result.keys()]
                    if not preview_rows:
                        preview_rows = [
                            [_safe_val(v) for v in r] for r in rows[:preview_limit]
                        ]
                        results_truncated = len(rows) > preview_limit
            except Exception as e:
                return DSBQueryResult(
                    query_id=query_id,
                    duration_sec=0,
                    rows_returned=0,
                    status="error",
                    error_msg=str(e),
                    all_durations=durations,
                    result_columns=preview_columns,
                    result_rows_preview=preview_rows,
                    results_truncated=results_truncated,
                )

        cold = durations[0] if durations else 0
        warm = durations[-1] if len(durations) > 1 else cold
        best = min(durations) if durations else 0

        return DSBQueryResult(
            query_id=query_id,
            duration_sec=best,
            rows_returned=len(rows),
            status="ok",
            cold_run_sec=cold,
            warm_run_sec=warm,
            all_durations=durations,
            result_columns=preview_columns,
            result_rows_preview=preview_rows,
            results_truncated=results_truncated,
        )

    def run_all(
        self,
        selected_queries: list[str] | str = "all",
        params_map: dict | None = None,
        timeout_sec: int = 300,
        iterations: int = 1,
        progress_callback=None,
    ) -> DSBResult:
        if selected_queries == "all":
            query_ids = sorted(DSB_QUERIES.keys(), key=lambda k: int(k[1:]))
        else:
            query_ids = selected_queries

        t0 = time.perf_counter()
        results: list[DSBQueryResult] = []

        for i, qid in enumerate(query_ids):
            params = (params_map or {}).get(qid, {})
            qr = self.run_query(qid, params, timeout_sec=timeout_sec, iterations=iterations)
            results.append(qr)
            if progress_callback:
                progress_callback(qid, int((i + 1) / len(query_ids) * 100))

        total_time = time.perf_counter() - t0

        return DSBResult(
            scale_factor=self.sf,
            queries=results,
            total_runtime_sec=total_time,
        )

    def _set_timeout(self, conn, timeout_sec: int):
        try:
            if self.dialect == "postgresql":
                conn.execute(text(f"SET statement_timeout = '{timeout_sec * 1000}'"))
            elif self.dialect == "mysql":
                conn.execute(text(f"SET max_execution_time = {timeout_sec * 1000}"))
        except Exception:
            pass
