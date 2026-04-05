"""FastAPI web interface for CloudBench with WebSocket live progress."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sqlio_cloud.connection import (
    ConnectionConfig, DatabaseConnection, ValidationResult,
    DB_TYPE_DIALECTS, DB_TYPE_PORTS,
)
from sqlio_cloud.config import load_config, apply_preset, PRESET_PROFILES
from sqlio_cloud.errors import friendly_error
from sqlio_cloud.metrics import FullBenchmarkResult

from sqlio_cloud.reporter import JSONReporter, HTMLReporter


def _validate_mssql_direct(config: ConnectionConfig) -> ValidationResult:
    """Validate SQL Server / SQL MI using raw pymssql (bypasses SQLAlchemy
    dialect init which can hang on SQL MI public endpoints)."""
    import pymssql
    try:
        conn = pymssql.connect(
            server=config.host,
            port=config.port,
            user=config.username,
            password=config.password,
            database=config.database,
            tds_version="7.3",
            login_timeout=15,
            autocommit=True,
        )
        cursor = conn.cursor()
        t0 = time.perf_counter()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        ping = (time.perf_counter() - t0) * 1000
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0] or ""
        cursor.close()
        conn.close()
        return ValidationResult(
            success=True,
            server_version=version,
            ping_ms=ping,
            max_connections=0,
            dialect_family="mssql",
        )
    except Exception as e:
        err_str = str(e)
        if "18456" in err_str or "40615" in err_str or "does not exist" in err_str.lower():
            created, create_err = DatabaseConnection(config)._auto_create_mssql(
                config.database
            )
            if create_err is None:
                return _validate_mssql_direct(config)
            return ValidationResult(
                success=False,
                error=f"Database '{config.database}' does not exist and auto-create failed: {create_err}",
            )
        return ValidationResult(success=False, error=err_str)

_AUTH_USER = "sqladmin"
_AUTH_HASH = "9c82affa7f297103c1c747ff8c5e506ac3863070912a272ad92c2f62d90328cc"
_AUTH_SALT = "cloudbench_v1"
_active_sessions: set[str] = set()

def _resolve_base_dir() -> Path:
    """Locate the sqlio_cloud package root, handling PyInstaller bundles."""
    import sys
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / "sqlio_cloud" / "web"
    return Path(__file__).parent

WEB_DIR = _resolve_base_dir()
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(title="CloudBench", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def _check_password(password: str) -> bool:
    h = hashlib.sha256((_AUTH_SALT + password).encode()).hexdigest()
    return secrets.compare_digest(h, _AUTH_HASH)


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get("cb_session")
    return token is not None and token in _active_sessions


# In-memory store for active benchmark runs
_runs: dict[str, dict] = {}
_cancel_events: dict[str, threading.Event] = {}


class ConnectRequest(BaseModel):
    db_type: str
    host: str
    port: int
    database: str
    username: str
    password: str


class BenchmarkRequest(BaseModel):
    db_type: str
    host: str
    port: int
    database: str
    username: str
    password: str
    preset: str = "standard"
    tests: Optional[list[str]] = None


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def landing():
    html_path = STATIC_DIR / "landing.html"
    return HTMLResponse(html_path.read_text())


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    html_path = STATIC_DIR / "login.html"
    return HTMLResponse(html_path.read_text())


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def api_login(req: LoginRequest, response: Response):
    if req.username == _AUTH_USER and _check_password(req.password):
        token = secrets.token_urlsafe(32)
        _active_sessions.add(token)
        response.set_cookie(
            key="cb_session", value=token,
            httponly=True, samesite="lax", max_age=86400,
        )
        return {"success": True}
    return {"success": False, "error": "Invalid username or password"}


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("cb_session")
    if token:
        _active_sessions.discard(token)
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("cb_session")
    return resp


@app.get("/app", response_class=HTMLResponse)
async def index(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/db-types")
async def db_types():
    return {
        "types": [
            {"name": k, "dialect": v, "default_port": int(DB_TYPE_PORTS.get(k, 5432))}
            for k, v in DB_TYPE_DIALECTS.items()
        ]
    }


@app.get("/api/host-ip")
async def host_ip():
    import urllib.request
    try:
        ip = urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode()
    except Exception:
        ip = None
    return {"ip": ip}


@app.get("/api/presets")
async def presets():
    return {
        name: {
            "label": p["label"],
            "tests": p["tests"],
        }
        for name, p in PRESET_PROFILES.items()
    }


@app.post("/api/validate")
async def validate_connection(req: ConnectRequest, request: Request):
    if not _is_authenticated(request):
        return {"success": False, "error": "Not authenticated"}
    try:
        host = req.host.strip()
        if ":" in host:
            return {
                "success": False,
                "error": f"Hostname '{host}' contains colons — this looks like a Cloud SQL connection name, not a network address.",
                "advice": "Use the Public IP address (e.g. 34.26.137.105) or DNS hostname instead of the connection name. You can find the public IP on the Cloud SQL instance's Overview or Networking page in the GCP console.",
            }
        dialect = DB_TYPE_DIALECTS.get(req.db_type, "postgresql+psycopg")
        config = ConnectionConfig(
            dialect=dialect,
            host=host,
            port=req.port,
            database=req.database,
            username=req.username,
            password=req.password,
        )

        if "pymssql" in dialect:
            vr = await asyncio.to_thread(
                _validate_mssql_direct, config
            )
        else:
            db = DatabaseConnection(config)
            def _blocking_validate():
                try:
                    return db.validate()
                finally:
                    db.dispose()
            vr = await asyncio.to_thread(_blocking_validate)

        if vr.success:
            resp = {
                "success": True,
                "server_version": vr.server_version[:120],
                "ping_ms": round(vr.ping_ms, 1),
                "max_connections": vr.max_connections,
            }
            if vr.database_created:
                resp["database_created"] = True
                resp["database_name"] = req.database
            return resp
        return {
            "success": False,
            "error": vr.error,
            "advice": friendly_error(Exception(vr.error)),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "advice": friendly_error(e),
        }


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = _runs.get(run_id)
    if not run:
        return {"error": "Run not found"}
    return {
        "status": run["status"],
        "progress": run["progress"],
        "current_test": run.get("current_test", ""),
        "results": run.get("results"),
    }


@app.get("/api/runs/{run_id}/report")
async def get_report(run_id: str):
    run = _runs.get(run_id)
    if not run or "results" not in run:
        return {"error": "Results not available"}
    return run["results"]


# ------------------------------------------------------------------
# WebSocket for live benchmark progress
# ------------------------------------------------------------------

@app.websocket("/ws/benchmark")
async def benchmark_ws(ws: WebSocket):
    token = ws.cookies.get("cb_session")
    if not token or token not in _active_sessions:
        await ws.close(code=4001, reason="Not authenticated")
        return
    await ws.accept()
    run_id = None
    try:
        raw = await ws.receive_text()
        req = json.loads(raw)

        run_id = str(uuid.uuid4())[:8]
        cancel = threading.Event()
        _runs[run_id] = {"status": "starting", "progress": [], "current_test": ""}
        _cancel_events[run_id] = cancel
        await ws.send_json({"type": "run_started", "run_id": run_id})

        config = ConnectionConfig(
            dialect=DB_TYPE_DIALECTS.get(req["db_type"], "postgresql+psycopg"),
            host=req["host"],
            port=req["port"],
            database=req["database"],
            username=req["username"],
            password=req["password"],
        )

        preset = req.get("preset", "standard")
        custom_tests = req.get("tests")
        block_size = req.get("block_size", 8192)

        loop = asyncio.get_event_loop()
        result_holder: dict = {}

        def _run_benchmark():
            db = None
            try:
                db = DatabaseConnection(config)
                vr = db.validate()
                if not vr.success:
                    result_holder["error"] = vr.error
                    return

                default_cfg_path = Path(__file__).parent.parent.parent.parent / "config" / "default.yaml"
                if default_cfg_path.exists():
                    cfg = load_config(default_cfg_path)
                else:
                    cfg = {}
                cfg = apply_preset(cfg, preset)
                cfg.setdefault("sqlio", {})["block_size"] = block_size
                cfg.setdefault("sqliosim", {})["page_size"] = block_size
                tests = custom_tests or cfg["_tests"]

                result = _execute_suite_with_ws(db, cfg, tests, run_id, loop, ws, cancel)

                if cancel.is_set():
                    result_holder["error"] = "Benchmark cancelled"
                    return

                result.preset = preset
                result.database_info = {
                    "host": config.host,
                    "dialect_family": db.dialect_family,
                    "server_version": vr.server_version[:120],
                    "ping_ms": vr.ping_ms,
                }
                result_holder["result"] = result.to_dict()

                output_dir = cfg.get("reporting", {}).get("output_dir", "results")
                JSONReporter().save(result, output_dir)
                HTMLReporter().save(result, output_dir)
            except Exception as e:
                if not cancel.is_set():
                    result_holder["error"] = str(e)
            finally:
                if db:
                    try:
                        db.dispose()
                    except Exception:
                        pass
                _cancel_events.pop(run_id, None)

        thread = threading.Thread(target=_run_benchmark, daemon=True)
        thread.start()

        while thread.is_alive():
            await asyncio.sleep(0.5)
            run = _runs.get(run_id, {})
            try:
                await ws.send_json({
                    "type": "progress",
                    "status": run.get("status", "running"),
                    "current_test": run.get("current_test", ""),
                    "progress": run.get("progress", []),
                })
            except Exception:
                break

        if cancel.is_set():
            thread.join(timeout=2)
            _runs[run_id]["status"] = "cancelled"
            return

        thread.join(timeout=5)

        if "error" in result_holder:
            await ws.send_json({
                "type": "error",
                "error": result_holder["error"],
                "advice": friendly_error(Exception(result_holder["error"])),
            })
            _runs[run_id]["status"] = "failed"
        else:
            _runs[run_id]["status"] = "complete"
            _runs[run_id]["results"] = result_holder.get("result")
            await ws.send_json({
                "type": "complete",
                "results": result_holder.get("result"),
            })

    except WebSocketDisconnect:
        if run_id and run_id in _cancel_events:
            _cancel_events[run_id].set()
            run = _runs.get(run_id)
            if run:
                run["status"] = "cancelled"
                run["current_test"] = "Cancelled — client disconnected"
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "error": str(e)})
        except Exception:
            pass


def _send_ws_update(run_id: str, test_name: str, pct: int, loop, ws):
    """Thread-safe progress update."""
    run = _runs.get(run_id)
    if run:
        run["current_test"] = test_name
        run["status"] = "running"
        existing = [p for p in run["progress"] if p["test"] == test_name]
        if existing:
            existing[0]["pct"] = pct
        else:
            run["progress"].append({"test": test_name, "pct": pct})


def _execute_suite_with_ws(db, cfg, tests, run_id, loop, ws, cancel: threading.Event) -> FullBenchmarkResult:
    """Same logic as CLI _execute_suite but with WebSocket progress updates and cancellation."""
    import logging
    log = logging.getLogger("cloudbench")
    from sqlio_cloud.metrics import FullBenchmarkResult
    from sqlio_cloud.errors import friendly_error
    result = FullBenchmarkResult()
    sqlio_cfg = cfg.get("sqlio", {})
    sim_cfg = cfg.get("sqliosim", {})
    dsb_cfg = cfg.get("dsb", {})
    net_cfg = cfg.get("network", {})

    def _cancelled():
        return cancel.is_set()

    def _fail(test_label: str, exc: Exception):
        msg = friendly_error(exc)
        result.record_error(test_label, msg)
        log.warning("%s failed: %s", test_label, msg)
        run = _runs.get(run_id)
        if run:
            run["current_test"] = f"{test_label} — FAILED: {msg[:120]}"

    needs_io_table = any(t in tests for t in ("random_read", "random_write", "seq_scan", "mixed"))

    if needs_io_table and not _cancelled():
        _send_ws_update(run_id, "Setting up test table", 0, loop, ws)
        from sqlio_cloud.sqlio.random_io import RandomIOTest
        rio = RandomIOTest(db, table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                           block_size=sqlio_cfg.get("block_size", 8192))
        rio.setup(progress_callback=lambda pct: _send_ws_update(run_id, "Setting up test table", pct, loop, ws))
        _send_ws_update(run_id, "Setting up test table", 100, loop, ws)

    if "random_read" in tests and not _cancelled():
        _send_ws_update(run_id, "I/O: Random Reads", 0, loop, ws)
        try:
            from sqlio_cloud.sqlio.random_io import RandomIOTest
            tc = sqlio_cfg.get("thread_counts", [1, 4, 8, 16])
            ops = sqlio_cfg.get("ops_per_run", 10_000)
            sweep_ops = max(50, ops // 5)
            rio2 = RandomIOTest(db, table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                                block_size=sqlio_cfg.get("block_size", 8192))
            def _read_progress(msg, pct):
                _send_ws_update(run_id, "I/O: Random Reads", pct, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Random Reads — {msg}"
            sa = rio2.run_scaling_sweep("read", tc, ops_per_run=sweep_ops, progress_callback=_read_progress)
            _send_ws_update(run_id, "I/O: Random Reads", 50, loop, ws)

            def _read_final(done, total):
                pct = 50 + int(done / total * 50)
                _send_ws_update(run_id, "I/O: Random Reads", pct, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Random Reads — final run ({done}/{total})"

            run = _runs.get(run_id)
            if run:
                run["current_test"] = f"Random Reads — final run (0/{ops})"
            rr = rio2.run_random_reads(num_ops=ops, num_threads=sa.optimal_threads,
                                       progress_callback=_read_final)
            rr.scalability = sa
            result.sqlio_results.append(rr)
        except Exception as e:
            _fail("I/O: Random Reads", e)
        _send_ws_update(run_id, "I/O: Random Reads", 100, loop, ws)

    if "random_write" in tests and not _cancelled():
        _send_ws_update(run_id, "I/O: Random Writes", 0, loop, ws)
        try:
            from sqlio_cloud.sqlio.random_io import RandomIOTest
            tc = sqlio_cfg.get("thread_counts", [1, 4, 8, 16])
            ops = sqlio_cfg.get("ops_per_run", 10_000)
            sweep_ops = max(50, ops // 5)
            rio3 = RandomIOTest(db, table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                                block_size=sqlio_cfg.get("block_size", 8192))
            def _write_progress(msg, pct):
                _send_ws_update(run_id, "I/O: Random Writes", pct, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Random Writes — {msg}"
            sa = rio3.run_scaling_sweep("write", tc, ops_per_run=sweep_ops, progress_callback=_write_progress)
            _send_ws_update(run_id, "I/O: Random Writes", 50, loop, ws)

            def _write_final(done, total):
                pct = 50 + int(done / total * 50)
                _send_ws_update(run_id, "I/O: Random Writes", pct, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Random Writes — final run ({done}/{total})"

            run = _runs.get(run_id)
            if run:
                run["current_test"] = f"Random Writes — final run (0/{ops})"
            rw = rio3.run_random_writes(num_ops=ops, num_threads=sa.optimal_threads,
                                        progress_callback=_write_final)
            rw.scalability = sa
            result.sqlio_results.append(rw)
        except Exception as e:
            _fail("I/O: Random Writes", e)
        _send_ws_update(run_id, "I/O: Random Writes", 100, loop, ws)

    if "seq_scan" in tests and not _cancelled():
        _send_ws_update(run_id, "I/O: Sequential Scan", 0, loop, ws)
        try:
            from sqlio_cloud.sqlio.sequential_scan import SequentialScanTest
            result.sqlio_results.append(SequentialScanTest(db).run(iterations=3))
        except Exception as e:
            _fail("I/O: Sequential Scan", e)
        _send_ws_update(run_id, "I/O: Sequential Scan", 100, loop, ws)

    if "mixed" in tests and not _cancelled():
        _send_ws_update(run_id, "I/O: Mixed Workload", 0, loop, ws)
        try:
            from sqlio_cloud.sqlio.mixed_workload import MixedWorkloadTest
            mw = MixedWorkloadTest(db, table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                                   block_size=sqlio_cfg.get("block_size", 8192))
            result.sqlio_results.append(mw.run(num_ops=sqlio_cfg.get("ops_per_run", 10_000), num_threads=8))
        except Exception as e:
            _fail("I/O: Mixed Workload", e)
        _send_ws_update(run_id, "I/O: Mixed Workload", 100, loop, ws)

    if "bulk_insert" in tests and not _cancelled():
        _send_ws_update(run_id, "I/O: Bulk Insert", 0, loop, ws)
        try:
            from sqlio_cloud.sqlio.bulk_write import BulkWriteTest
            bw = BulkWriteTest(db, block_size=sqlio_cfg.get("block_size", 8192))
            bw.setup()
            result.sqlio_results.append(bw.run(total_rows=sqlio_cfg.get("table_rows", 1_000_000) // 10, batch_size=1000))
            bw.teardown()
        except Exception as e:
            _fail("I/O: Bulk Insert", e)
        _send_ws_update(run_id, "I/O: Bulk Insert", 100, loop, ws)

    if "integrity" in tests and not _cancelled():
        _send_ws_update(run_id, "Stress: Integrity", 0, loop, ws)
        try:
            from sqlio_cloud.sqliosim.integrity import IntegrityStressTest
            ist = IntegrityStressTest(db, page_size=sim_cfg.get("page_size", 8192))
            ist.setup()
            result.sqliosim_results.append(ist.run(
                num_cycles=sim_cfg.get("write_cycles", 5000),
                write_threads=sim_cfg.get("threads", 8),
                verify_sample_pct=sim_cfg.get("verify_sample_pct", 0.2),
            ))
            ist.teardown()
        except Exception as e:
            _fail("Stress: Integrity", e)
        _send_ws_update(run_id, "Stress: Integrity", 100, loop, ws)

    if "concurrency" in tests and not _cancelled():
        _send_ws_update(run_id, "Stress: Concurrency", 0, loop, ws)
        try:
            from sqlio_cloud.sqliosim.concurrent_stress import ConcurrentStressTest
            cs = ConcurrentStressTest(db, account_count=sim_cfg.get("account_count", 10_000))
            cs.setup()
            result.sqliosim_results.append(cs.run(
                num_txns=sim_cfg.get("write_cycles", 5000) * 2,
                num_threads=sim_cfg.get("threads", 8) * 2,
            ))
            cs.teardown()
        except Exception as e:
            _fail("Stress: Concurrency", e)
        _send_ws_update(run_id, "Stress: Concurrency", 100, loop, ws)

    if "isolation" in tests and not _cancelled():
        _send_ws_update(run_id, "Stress: Isolation", 0, loop, ws)
        try:
            from sqlio_cloud.sqliosim.isolation import IsolationTest
            iso = IsolationTest(db)
            iso.setup()
            iso_results = iso.run_all()
            result.isolation_results = [ir.to_dict() for ir in iso_results]
            iso.teardown()
        except Exception as e:
            _fail("Stress: Isolation", e)
        _send_ws_update(run_id, "Stress: Isolation", 100, loop, ws)

    if "dsb" in tests and not _cancelled():
        _send_ws_update(run_id, "Analytical Queries", 0, loop, ws)
        try:
            from sqlio_cloud.dsb.data_gen import DSBDataGenerator
            from sqlio_cloud.dsb.runner import DSBRunner
            sf = dsb_cfg.get("scale_factor", 1.0)
            gen = DSBDataGenerator(db, scale_factor=sf)
            gen.drop_schema()
            gen.create_schema()

            def _gen_progress(table_name, pct):
                scaled = int(pct * 0.3)
                _send_ws_update(run_id, "Analytical Queries", scaled, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Generating data — {table_name}"

            gen.generate_all(progress_callback=_gen_progress)
            _send_ws_update(run_id, "Analytical Queries", 30, loop, ws)

            runner = DSBRunner(db, scale_factor=sf)

            def _query_progress(qid, pct):
                scaled = 30 + int(pct * 0.7)
                _send_ws_update(run_id, "Analytical Queries", scaled, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Running query {qid}"

            result.dsb_result = runner.run_all(
                selected_queries=dsb_cfg.get("selected_queries", "all"),
                timeout_sec=dsb_cfg.get("query_timeout_sec", 300),
                iterations=dsb_cfg.get("iterations", 1),
                progress_callback=_query_progress,
            )
            gen.drop_schema()
        except Exception as e:
            _fail("Analytical Queries", e)
        _send_ws_update(run_id, "Analytical Queries", 100, loop, ws)

    if "pool_stress" in tests and not _cancelled():
        _send_ws_update(run_id, "Pool Stress", 0, loop, ws)
        try:
            from sqlio_cloud.sqlio.pool_stress import PoolStressTest
            pst = PoolStressTest(db)

            def _pool_progress(done, total):
                pct = int(done / total * 100)
                _send_ws_update(run_id, "Pool Stress", pct, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Pool Stress — burst {done}/{total}"

            result.sqlio_results.append(pst.run(progress_callback=_pool_progress))
        except Exception as e:
            _fail("Pool Stress", e)
        _send_ws_update(run_id, "Pool Stress", 100, loop, ws)

    if "net_latency" in tests and not _cancelled():
        _send_ws_update(run_id, "Network: Profiling", 0, loop, ws)
        try:
            from sqlio_cloud.network.profiler import NetworkProfiler

            def _net_progress(step, total, phase):
                pct = int(step / total * 100)
                _send_ws_update(run_id, "Network: Profiling", pct, loop, ws)
                run = _runs.get(run_id)
                if run:
                    run["current_test"] = f"Network — {phase} ({step}/{total})"

            result.network_result = NetworkProfiler(db).run(
                ping_count=net_cfg.get("ping_count", 100),
                connection_count=net_cfg.get("connection_count", 50),
                bandwidth_rows=net_cfg.get("bandwidth_rows", 100_000),
                progress_callback=_net_progress,
            )
        except Exception as e:
            _fail("Network: Profiling", e)
        _send_ws_update(run_id, "Network: Profiling", 100, loop, ws)

    if needs_io_table:
        try:
            rio.teardown()
        except Exception:
            pass

    return result


def start_server(host: str = "0.0.0.0", port: int = 8080):
    import uvicorn
    uvicorn.run(app, host=host, port=port)
