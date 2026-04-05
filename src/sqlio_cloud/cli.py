"""CLI entry point: interactive wizard + direct commands for advanced users."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from sqlio_cloud.config import load_config, apply_preset, PRESET_PROFILES
from sqlio_cloud.connection import (
    ConnectionConfig, DatabaseConnection,
    DB_TYPE_DIALECTS, DB_TYPE_PORTS,
)
from sqlio_cloud.errors import friendly_error, validate_port
from sqlio_cloud.metrics import FullBenchmarkResult
from sqlio_cloud.reporter import ConsoleReporter, JSONReporter, HTMLReporter

console = Console()


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def _wizard():
    """Guided interactive setup — the default experience."""
    try:
        import questionary
    except ImportError:
        console.print("[red]questionary is required for the wizard. Install with: pip install questionary[/]")
        sys.exit(1)

    console.print()
    console.print(Panel.fit(
        "[bold cyan]Data Bench — Database Performance Suite[/]\n"
        "Cloud database benchmarking made simple.\n\n"
        "This wizard will walk you through everything.",
        border_style="cyan",
    ))

    db_type = questionary.select(
        "What database are you testing?",
        choices=list(DB_TYPE_DIALECTS.keys()),
    ).ask()
    if db_type is None:
        return

    console.print("\n[bold]Connection Details[/]")
    console.print("[dim]Tip: set DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME as env vars to skip prompts[/]\n")

    import os
    host = os.environ.get("DB_HOST") or questionary.text(
        "Hostname:",
        validate=lambda v: True if v.strip() else "Hostname cannot be empty",
    ).ask()
    if host is None:
        return

    port = os.environ.get("DB_PORT") or questionary.text(
        "Port:",
        default=DB_TYPE_PORTS.get(db_type, "5432"),
        validate=validate_port,
    ).ask()
    if port is None:
        return

    database = os.environ.get("DB_NAME") or questionary.text("Database name:", default="benchmarks").ask()
    if database is None:
        return

    username = os.environ.get("DB_USER") or questionary.text("Username:").ask()
    if username is None:
        return

    password = os.environ.get("DB_PASSWORD") or questionary.password("Password:").ask()
    if password is None:
        return

    config = ConnectionConfig(
        dialect=DB_TYPE_DIALECTS[db_type],
        host=host.strip(),
        port=int(port),
        database=database.strip(),
        username=username.strip(),
        password=password,
    )

    console.print("\n[bold yellow]Testing connection...[/]")
    db = DatabaseConnection(config)
    vr = db.validate()

    if not vr.success:
        console.print(f"\n[bold red]Connection failed:[/] {vr.error}")
        advice = friendly_error(Exception(vr.error))
        console.print(f"\n[dim]{advice}[/]")
        retry = questionary.confirm("Retry with different credentials?", default=True).ask()
        if retry:
            db.dispose()
            return _wizard()
        db.dispose()
        return

    console.print(f"[bold green]Connected![/] Server: {vr.server_version[:80]}")
    console.print(f"  Round-trip latency: {vr.ping_ms:.1f} ms")
    if vr.max_connections > 0:
        console.print(f"  Max connections:    {vr.max_connections}")

    preset_choices = [
        questionary.Choice(PRESET_PROFILES[k]["label"], value=k)
        for k in ("smoke", "standard", "full")
    ]
    preset_choices.append(questionary.Choice("Custom — pick individual tests", value="custom"))

    suite = questionary.select("\nWhat would you like to run?", choices=preset_choices).ask()
    if suite is None:
        db.dispose()
        return

    if suite == "custom":
        all_tests = [
            questionary.Choice("Random Read I/O", value="random_read", checked=True),
            questionary.Choice("Random Write I/O", value="random_write", checked=True),
            questionary.Choice("Sequential Scan", value="seq_scan", checked=True),
            questionary.Choice("Mixed Read/Write", value="mixed", checked=True),
            questionary.Choice("Bulk Insert Throughput", value="bulk_insert"),
            questionary.Choice("Data Integrity Stress", value="integrity", checked=True),
            questionary.Choice("Concurrent Transaction Stress", value="concurrency", checked=True),
            questionary.Choice("Isolation Level Testing", value="isolation"),
            questionary.Choice("Analytical Queries", value="dsb", checked=True),
            questionary.Choice("Connection Pool Stress", value="pool_stress"),
            questionary.Choice("Network Latency Profiling", value="net_latency"),
        ]
        tests = questionary.checkbox("Select tests to run:", choices=all_tests).ask()
        if not tests:
            db.dispose()
            return
        cfg = load_config(Path(__file__).parent.parent.parent / "config" / "default.yaml")
        cfg["_tests"] = tests
        cfg["_preset"] = "custom"
    else:
        cfg = load_config(Path(__file__).parent.parent.parent / "config" / "default.yaml")
        cfg = apply_preset(cfg, suite)
        tests = cfg["_tests"]

    _confirm_and_run(db, cfg, tests, suite, vr)


def _estimate_label(suite: str) -> str:
    return {
        "smoke": "~5-15 minutes",
        "standard": "~20-60 minutes",
        "full": "~60 minutes",
        "custom": "varies",
    }.get(suite, "varies")


def _confirm_and_run(db: DatabaseConnection, cfg: dict, tests: list[str], suite: str, vr):
    try:
        import questionary
    except ImportError:
        pass

    console.print(Panel(
        f"[bold]Test Plan Summary[/]\n\n"
        f"  Tests:        {len(tests)} selected\n"
        f"  Est. runtime: {_estimate_label(suite)}\n"
        f"  Cleanup:      All test tables dropped after run\n",
        title="Ready to Run",
        border_style="green",
    ))

    try:
        import questionary
        go = questionary.confirm("Start the benchmark?", default=True).ask()
        if not go:
            db.dispose()
            return
    except ImportError:
        pass

    result = _execute_suite(db, cfg, tests)
    result.preset = suite
    result.database_info = {
        "host": db.config.host,
        "dialect_family": db.dialect_family,
        "server_version": vr.server_version,
        "ping_ms": vr.ping_ms,
        **vr.server_metadata,
    }

    reporter = ConsoleReporter(console)
    reporter.print_full(result)

    output_dir = cfg.get("reporting", {}).get("output_dir", "results")
    if cfg.get("reporting", {}).get("json_report", True):
        jp = JSONReporter().save(result, output_dir)
        console.print(f"[dim]JSON report: {jp}[/]")
    if cfg.get("reporting", {}).get("html_report", True):
        hp = HTMLReporter().save(result, output_dir)
        console.print(f"[dim]HTML report: {hp}[/]")

    db.dispose()
    console.print("\n[bold green]Benchmark complete![/]")


# ---------------------------------------------------------------------------
# Suite executor
# ---------------------------------------------------------------------------

def _execute_suite(db: DatabaseConnection, cfg: dict, tests: list[str]) -> FullBenchmarkResult:
    result = FullBenchmarkResult()
    sqlio_cfg = cfg.get("sqlio", {})
    sim_cfg = cfg.get("sqliosim", {})
    dsb_cfg = cfg.get("dsb", {})
    net_cfg = cfg.get("network", {})

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )

    with progress:
        # --- I/O tests ---
        needs_io_table = any(t in tests for t in ("random_read", "random_write", "seq_scan", "mixed"))

        if needs_io_table:
            task = progress.add_task("Setting up I/O test table...", total=100)
            from sqlio_cloud.sqlio.random_io import RandomIOTest
            rio = RandomIOTest(
                db,
                table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                block_size=sqlio_cfg.get("block_size", 8192),
            )
            try:
                rio.setup(progress_callback=lambda pct: progress.update(task, completed=pct))
            except Exception as e:
                console.print(f"\n[bold red]Setup failed:[/] {e}")
                console.print(f"[dim]{friendly_error(e)}[/]")
                return result
            progress.update(task, completed=100)

        if "random_read" in tests:
            task = progress.add_task("I/O: Random Reads...", total=100)
            try:
                thread_counts = sqlio_cfg.get("thread_counts", [1, 4, 8, 16])
                ops = sqlio_cfg.get("ops_per_run", 10_000)
                sweep_ops = max(50, ops // 5)
                from sqlio_cloud.sqlio.random_io import RandomIOTest
                rio2 = RandomIOTest(db, table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                                    block_size=sqlio_cfg.get("block_size", 8192))
                sa = rio2.run_scaling_sweep("read", thread_counts, ops_per_run=sweep_ops,
                    progress_callback=lambda msg, pct: progress.update(task, completed=int(pct * 0.5)))
                progress.update(task, completed=50)
                rr = rio2.run_random_reads(
                    num_ops=ops, num_threads=sa.optimal_threads,
                    progress_callback=lambda done, total: progress.update(
                        task, completed=50 + int(done / total * 50),
                        description=f"I/O: Random Reads ({done}/{total})",
                    ),
                )
                rr.scalability = sa
                result.sqlio_results.append(rr)
            except Exception as e:
                console.print(f"\n[yellow]Random read failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        if "random_write" in tests:
            task = progress.add_task("I/O: Random Writes...", total=100)
            try:
                thread_counts = sqlio_cfg.get("thread_counts", [1, 4, 8, 16])
                ops = sqlio_cfg.get("ops_per_run", 10_000)
                sweep_ops = max(50, ops // 5)
                from sqlio_cloud.sqlio.random_io import RandomIOTest
                rio3 = RandomIOTest(db, table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                                    block_size=sqlio_cfg.get("block_size", 8192))
                sa = rio3.run_scaling_sweep("write", thread_counts, ops_per_run=sweep_ops,
                    progress_callback=lambda msg, pct: progress.update(task, completed=int(pct * 0.5)))
                progress.update(task, completed=50)
                rw = rio3.run_random_writes(
                    num_ops=ops, num_threads=sa.optimal_threads,
                    progress_callback=lambda done, total: progress.update(
                        task, completed=50 + int(done / total * 50),
                        description=f"I/O: Random Writes ({done}/{total})",
                    ),
                )
                rw.scalability = sa
                result.sqlio_results.append(rw)
            except Exception as e:
                console.print(f"\n[yellow]Random write failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        if "seq_scan" in tests:
            task = progress.add_task("I/O: Sequential Scan...", total=100)
            try:
                from sqlio_cloud.sqlio.sequential_scan import SequentialScanTest
                ss = SequentialScanTest(db)
                result.sqlio_results.append(ss.run(iterations=3))
            except Exception as e:
                console.print(f"\n[yellow]Sequential scan failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        if "mixed" in tests:
            task = progress.add_task("I/O: Mixed Workload...", total=100)
            try:
                from sqlio_cloud.sqlio.mixed_workload import MixedWorkloadTest
                mw = MixedWorkloadTest(
                    db,
                    table_rows=sqlio_cfg.get("table_rows", 1_000_000),
                    block_size=sqlio_cfg.get("block_size", 8192),
                )
                result.sqlio_results.append(mw.run(
                    num_ops=sqlio_cfg.get("ops_per_run", 10_000),
                    num_threads=8,
                ))
            except Exception as e:
                console.print(f"\n[yellow]Mixed workload failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        if "bulk_insert" in tests:
            task = progress.add_task("I/O: Bulk Insert...", total=100)
            try:
                from sqlio_cloud.sqlio.bulk_write import BulkWriteTest
                bw = BulkWriteTest(db, block_size=sqlio_cfg.get("block_size", 8192))
                bw.setup()
                result.sqlio_results.append(bw.run(
                    total_rows=sqlio_cfg.get("table_rows", 1_000_000) // 10,
                    batch_size=1000,
                ))
                bw.teardown()
            except Exception as e:
                console.print(f"\n[yellow]Bulk insert failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        # --- Stress tests ---
        if "integrity" in tests:
            task = progress.add_task("Stress: Integrity Check...", total=100)
            try:
                from sqlio_cloud.sqliosim.integrity import IntegrityStressTest
                ist = IntegrityStressTest(db, page_size=sim_cfg.get("page_size", 8192))
                ist.setup()
                ir = ist.run(
                    num_cycles=sim_cfg.get("write_cycles", 5000),
                    write_threads=sim_cfg.get("threads", 8),
                    verify_sample_pct=sim_cfg.get("verify_sample_pct", 0.2),
                )
                result.sqliosim_results.append(ir)
                ist.teardown()
            except Exception as e:
                console.print(f"\n[yellow]Integrity test failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        if "concurrency" in tests:
            task = progress.add_task("Stress: Concurrent Transactions...", total=100)
            try:
                from sqlio_cloud.sqliosim.concurrent_stress import ConcurrentStressTest
                cs = ConcurrentStressTest(db, account_count=sim_cfg.get("account_count", 10_000))
                cs.setup()
                cr = cs.run(
                    num_txns=sim_cfg.get("write_cycles", 5000) * 2,
                    num_threads=sim_cfg.get("threads", 8) * 2,
                )
                result.sqliosim_results.append(cr)
                cs.teardown()
            except Exception as e:
                console.print(f"\n[yellow]Concurrency test failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        if "isolation" in tests:
            task = progress.add_task("Stress: Isolation Tests...", total=100)
            try:
                from sqlio_cloud.sqliosim.isolation import IsolationTest
                iso = IsolationTest(db)
                iso.setup()
                iso_results = iso.run_all()
                result.isolation_results = [ir.to_dict() for ir in iso_results]
                for ir in iso_results:
                    console.print(
                        f"  {ir.isolation_level}: "
                        f"dirty={'[red]YES[/]' if ir.dirty_read_detected else '[green]no[/]'} | "
                        f"non-repeatable={'[red]YES[/]' if ir.non_repeatable_read_detected else '[green]no[/]'} | "
                        f"phantom={'[red]YES[/]' if ir.phantom_read_detected else '[green]no[/]'}"
                    )
                iso.teardown()
            except Exception as e:
                console.print(f"\n[yellow]Isolation test failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        # --- Pool Stress ---
        if "pool_stress" in tests:
            task = progress.add_task("Pool Stress...", total=100)
            try:
                from sqlio_cloud.sqlio.pool_stress import PoolStressTest
                pst = PoolStressTest(db)
                result.sqlio_results.append(pst.run(
                    progress_callback=lambda done, total: progress.update(
                        task, completed=int(done / total * 100),
                        description=f"Pool Stress ({done}/{total})",
                    ),
                ))
            except Exception as e:
                console.print(f"\n[yellow]Pool stress failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        # --- Analytical ---
        if "dsb" in tests:
            task = progress.add_task("Analytical: Generating data...", total=100)
            try:
                from sqlio_cloud.dsb.data_gen import DSBDataGenerator
                from sqlio_cloud.dsb.runner import DSBRunner
                sf = dsb_cfg.get("scale_factor", 1.0)
                gen = DSBDataGenerator(db, scale_factor=sf)
                gen.drop_schema()
                gen.create_schema()
                gen.generate_all(
                    progress_callback=lambda name, pct: progress.update(task, completed=pct)
                )
                progress.update(task, completed=100)

                task2 = progress.add_task("Analytical: Running queries...", total=100)
                runner = DSBRunner(db, scale_factor=sf)
                selected = dsb_cfg.get("selected_queries", "all")
                dsb_result = runner.run_all(
                    selected_queries=selected,
                    timeout_sec=dsb_cfg.get("query_timeout_sec", 300),
                    iterations=dsb_cfg.get("iterations", 1),
                    progress_callback=lambda qid, pct: progress.update(task2, completed=pct),
                )
                result.dsb_result = dsb_result
                progress.update(task2, completed=100)
                gen.drop_schema()
            except Exception as e:
                console.print(f"\n[yellow]Analytical queries failed:[/] {friendly_error(e)}")

        # --- Network ---
        if "net_latency" in tests:
            task = progress.add_task("Network: Profiling...", total=100)
            try:
                from sqlio_cloud.network.profiler import NetworkProfiler
                np = NetworkProfiler(db)
                result.network_result = np.run(
                    ping_count=net_cfg.get("ping_count", 100),
                    connection_count=net_cfg.get("connection_count", 50),
                    bandwidth_rows=net_cfg.get("bandwidth_rows", 100_000),
                    progress_callback=lambda step, total, phase: progress.update(
                        task, completed=int(step / total * 100),
                        description=f"Network: {phase} ({step}/{total})",
                    ),
                )
            except Exception as e:
                console.print(f"\n[yellow]Network profiling failed:[/] {friendly_error(e)}")
            progress.update(task, completed=100)

        # --- Cleanup ---
        if needs_io_table:
            task = progress.add_task("Cleaning up...", total=100)
            try:
                rio.teardown()
            except Exception:
                pass
            progress.update(task, completed=100)

    return result


# ---------------------------------------------------------------------------
# Click CLI for advanced / scriptable usage
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option("--config", "-c", type=click.Path(exists=True), default=None,
              help="Path to YAML config file")
@click.option("--profile", "-p", type=click.Path(exists=True), default=None,
              help="Cloud provider profile YAML to merge")
@click.option("--preset", type=click.Choice(["smoke", "standard", "full"]), default=None,
              help="Use a preset profile")
@click.option("--output", "-o", type=click.Path(), default="results",
              help="Output directory for reports")
@click.pass_context
def cli(ctx, config, profile, preset, output):
    """Data Bench: Cloud database performance test suite.

    Run without arguments for the interactive wizard, or use subcommands
    for scriptable benchmarking.
    """
    ctx.ensure_object(dict)
    ctx.obj["output"] = output

    if config:
        ctx.obj["config"] = load_config(config, profile)
    else:
        default_cfg = Path(__file__).parent.parent.parent / "config" / "default.yaml"
        if default_cfg.exists():
            ctx.obj["config"] = load_config(default_cfg, profile)
        else:
            ctx.obj["config"] = {}

    if preset:
        ctx.obj["config"] = apply_preset(ctx.obj["config"], preset)

    if ctx.invoked_subcommand is None:
        _wizard()


@cli.command()
@click.option("--host", required=True, help="Database hostname")
@click.option("--port", type=int, default=5432, help="Database port")
@click.option("--database", default="benchmarks", help="Database name")
@click.option("--username", required=True, help="Database username")
@click.option("--password", required=True, help="Database password")
@click.option("--dialect", default="postgresql+psycopg", help="SQLAlchemy dialect")
@click.option("--preset", type=click.Choice(["smoke", "standard", "full"]), default="standard")
@click.pass_context
def run(ctx, host, port, database, username, password, dialect, preset):
    """Run benchmarks non-interactively (for CI/CD or scripting)."""
    config = ConnectionConfig(
        dialect=dialect, host=host, port=port,
        database=database, username=username, password=password,
    )
    db = DatabaseConnection(config)
    vr = db.validate()

    if not vr.success:
        console.print(f"[bold red]Connection failed:[/] {vr.error}")
        console.print(f"[dim]{friendly_error(Exception(vr.error))}[/]")
        db.dispose()
        sys.exit(1)

    console.print(f"[bold green]Connected![/] {vr.server_version[:80]}")

    cfg = ctx.obj.get("config", {})
    cfg = apply_preset(cfg, preset)
    tests = cfg["_tests"]

    result = _execute_suite(db, cfg, tests)
    result.preset = preset
    result.database_info = {
        "host": host,
        "dialect_family": db.dialect_family,
        "server_version": vr.server_version,
        "ping_ms": vr.ping_ms,
        **vr.server_metadata,
    }

    reporter = ConsoleReporter(console)
    reporter.print_full(result)

    output_dir = ctx.obj.get("output", "results")
    jp = JSONReporter().save(result, output_dir)
    hp = HTMLReporter().save(result, output_dir)
    console.print(f"\n[dim]JSON: {jp}[/]")
    console.print(f"[dim]HTML: {hp}[/]")
    db.dispose()


@cli.command()
@click.option("--host", required=True)
@click.option("--port", type=int, default=5432)
@click.option("--database", default="benchmarks")
@click.option("--username", required=True)
@click.option("--password", required=True)
@click.option("--dialect", default="postgresql+psycopg")
def validate(host, port, database, username, password, dialect):
    """Test database connectivity without running benchmarks."""
    config = ConnectionConfig(
        dialect=dialect, host=host, port=port,
        database=database, username=username, password=password,
    )
    db = DatabaseConnection(config)
    vr = db.validate()

    if vr.success:
        console.print(f"[bold green]Connection successful![/]")
        console.print(f"  Server:      {vr.server_version[:80]}")
        console.print(f"  Ping:        {vr.ping_ms:.1f} ms")
        console.print(f"  Max conns:   {vr.max_connections}")
        console.print(f"  DNS resolve: {db.dns_resolve_ms():.1f} ms")
        console.print(f"  Conn setup:  {db.measure_connection_setup():.1f} ms")
    else:
        console.print(f"[bold red]Connection failed:[/] {vr.error}")
        console.print(f"\n[dim]{friendly_error(Exception(vr.error))}[/]")

    db.dispose()


@cli.command()
@click.argument("report_path", type=click.Path(exists=True))
def show(report_path):
    """Display a previously generated JSON report."""
    data = json.loads(Path(report_path).read_text())
    console.print_json(json.dumps(data, indent=2, default=str))


@cli.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", "-p", type=int, default=8080, help="Port to listen on")
def web(host, port):
    """Launch the web UI for browser-based benchmarking."""
    console.print(Panel.fit(
        f"[bold cyan]Data Bench Web Interface[/]\n\n"
        f"  Open [bold]http://localhost:{port}[/] in your browser\n"
        f"  Press Ctrl+C to stop",
        border_style="cyan",
    ))
    from sqlio_cloud.web.app import start_server
    start_server(host=host, port=port)


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
