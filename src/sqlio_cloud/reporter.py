"""Rich console reporter and JSON/HTML report generators."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sqlio_cloud.metrics import (
    FullBenchmarkResult, SQLIOResult, SQLIOSimResult,
    DSBResult, NetworkResult,
)


def _rate_iops(val: float) -> str:
    if val > 10_000:
        return "[bold green]EXCELLENT[/]"
    if val > 5_000:
        return "[green]GOOD[/]"
    if val > 1_000:
        return "[yellow]FAIR[/]"
    return "[red]POOR[/]"


def _rate_latency(val: float) -> str:
    if val < 5:
        return "[bold green]EXCELLENT[/]"
    if val < 20:
        return "[green]GOOD[/]"
    if val < 100:
        return "[yellow]FAIR[/]"
    return "[red]HIGH[/]"


def _rate_tps(val: float) -> str:
    if val > 5_000:
        return "[bold green]EXCELLENT[/]"
    if val > 1_000:
        return "[green]GOOD[/]"
    if val > 200:
        return "[yellow]FAIR[/]"
    return "[red]LOW[/]"


def _rate_error(val: float) -> str:
    if val < 0.1:
        return "[bold green]GOOD[/]"
    if val < 1.0:
        return "[yellow]WARN[/]"
    return "[red]HIGH[/]"


def _bool_badge(val: bool) -> str:
    return "[bold green]PASS[/]" if val else "[bold red]FAIL[/]"


def _config_summary(cfg: dict) -> str:
    if not cfg:
        return ""
    labels: list[str] = []
    for key, label in [
        ("table_rows", "rows"), ("total_rows", "rows"),
        ("num_ops", "ops"), ("num_transactions", "txns"),
        ("num_cycles", "cycles"), ("account_count", "accounts"),
        ("num_threads", "threads"), ("write_threads", "write threads"),
        ("batch_size", "batch"), ("iterations", "iterations"),
        ("ops_per_burst", "ops/burst"),
    ]:
        if key in cfg:
            labels.append(f"{cfg[key]:,} {label}")
    if "block_size" in cfg:
        labels.append(f"{cfg['block_size'] // 1024} KB blocks")
    if "page_size" in cfg:
        labels.append(f"{cfg['page_size'] // 1024} KB pages")
    if "total_data_mb" in cfg:
        labels.append(f"{cfg['total_data_mb']} MB data")
    if "read_pct" in cfg:
        labels.append(f"{cfg['read_pct']}% reads")
    if "verify_sample_pct" in cfg:
        labels.append(f"{int(cfg['verify_sample_pct'] * 100)}% verify")
    return " · ".join(labels)


class ConsoleReporter:
    """Rich terminal output for benchmark results."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def print_header(self, preset: str, db_info: dict):
        self.console.print()
        lines = [
            f"[bold cyan]Data Bench — Database Performance Suite[/]",
            f"  Database: {db_info.get('dialect_family', '?')} @ {db_info.get('host', '?')}",
        ]
        edition = db_info.get("edition")
        sku = db_info.get("service_objective")
        if edition:
            tier_line = f"  Tier:     {edition}"
            if sku:
                tier_line += f" ({sku})"
            lines.append(tier_line)
        pool = db_info.get("elastic_pool")
        if pool:
            lines.append(f"  Pool:     {pool}")
        vcores = db_info.get("vcores")
        mem = db_info.get("memory_gb")
        if vcores or mem:
            parts = []
            if vcores:
                parts.append(f"{vcores} vCores")
            if mem:
                parts.append(f"{mem} GB RAM")
            lines.append(f"  Hardware: {' / '.join(parts)}")
        max_sz = db_info.get("max_size_gb")
        if max_sz:
            lines.append(f"  Max Size: {max_sz} GB")
        lines.append(f"  Version:  {db_info.get('server_version', '?')[:60]}")
        lines.append(f"  Profile:  {preset or 'custom'}")
        self.console.print(Panel.fit("\n".join(lines), border_style="cyan"))

    def print_sqlio(self, result: SQLIOResult):
        t = result.throughput.to_dict()
        lat = result.latency

        cfg_parts = _config_summary(result.config)
        subtitle = f"[dim]{cfg_parts}[/]" if cfg_parts else None
        table = Table(
            title=f"I/O: {result.test_name}",
            caption=subtitle,
            border_style="cyan",
            show_lines=True,
        )
        table.add_column("Metric", style="bold", min_width=22)
        table.add_column("Value", justify="right", min_width=14)
        table.add_column("Rating", justify="center", min_width=12)

        iops = t["avg_ops_per_sec"]
        table.add_row("IOPS", f"{iops:,.0f}", _rate_iops(iops))
        table.add_row("Throughput", f"{t['avg_mbps']:.1f} MB/s", "")
        table.add_row("Total Ops", f"{t['total_ops']:,}", "")
        table.add_row("Duration", f"{t['duration_sec']:.1f} s", "")
        table.add_row("", "", "")
        table.add_row("[bold]Latency[/]", "", "")
        table.add_row("  Min", f"{lat.min_ms:.2f} ms", "")
        table.add_row("  p50 (median)", f"{lat.p50:.2f} ms", _rate_latency(lat.p50))
        table.add_row("  p75", f"{lat.p75:.2f} ms", "")
        table.add_row("  p90", f"{lat.p90:.2f} ms", "")
        table.add_row("  p95", f"{lat.p95:.2f} ms", _rate_latency(lat.p95))
        table.add_row("  p99", f"{lat.p99:.2f} ms", _rate_latency(lat.p99))
        table.add_row("  p99.9", f"{lat.p999:.2f} ms", "")
        table.add_row("  Max", f"{lat.max_ms:.2f} ms", "")
        table.add_row("  Std Dev", f"{lat.stddev_ms:.2f} ms", "")
        table.add_row("  Jitter", f"{lat.jitter_ms:.2f} ms", "")
        table.add_row("  CV", f"{lat.coefficient_of_variation:.1f}%", "")
        table.add_row("", "", "")
        table.add_row("[bold]Stability[/]", "", "")
        table.add_row("  Peak IOPS", f"{t['peak_ops_per_sec']:,.0f}", "")
        table.add_row("  Error Rate", f"{t['error_rate_pct']:.2f}%", _rate_error(t["error_rate_pct"]))
        table.add_row("  Pool High Water", f"{result.pool_high_water}", "")

        self.console.print(table)
        self.console.print()

    def print_sqlio_scaling(self, result: SQLIOResult):
        if not result.scalability:
            return
        sa = result.scalability
        table = Table(
            title=f"Thread Scaling: {sa.metric_name}",
            border_style="magenta",
            show_lines=True,
        )
        table.add_column("Threads", justify="right")
        table.add_column("IOPS", justify="right")
        table.add_column("p50 ms", justify="right")
        table.add_column("p99 ms", justify="right")
        table.add_column("MB/s", justify="right")
        table.add_column("Efficiency", justify="right")
        table.add_column("Error %", justify="right")

        for pt in sa.points:
            eff = sa.scaling_efficiency(pt.threads)
            table.add_row(
                str(pt.threads),
                f"{pt.iops:,.0f}",
                f"{pt.p50_ms:.2f}",
                f"{pt.p99_ms:.2f}",
                f"{pt.throughput_mbps:.1f}",
                f"{eff:.0f}%",
                f"{pt.error_rate_pct:.2f}",
            )

        self.console.print(table)
        self.console.print(f"  Peak IOPS:        [bold]{sa.peak_iops:,.0f}[/] (at {sa.peak_iops_threads} threads)")
        self.console.print(f"  Saturation point: [bold]{sa.saturation_point}[/] threads")
        self.console.print(f"  Optimal threads:  [bold]{sa.optimal_threads}[/]")
        self.console.print(f"  Amdahl serial:    [bold]{sa.amdahl_serial_fraction:.4f}[/]")
        self.console.print()

    def print_sqliosim(self, result: SQLIOSimResult):
        d = result.to_dict()
        integrity = d["integrity"]
        conc = d["concurrency"]
        audit = d["data_integrity_audit"]

        cfg_parts = _config_summary(result.config)
        subtitle = f"[dim]{cfg_parts}[/]" if cfg_parts else None
        table = Table(title=f"Stress: {result.test_name}", caption=subtitle, border_style="yellow", show_lines=True)
        table.add_column("Metric", style="bold", min_width=24)
        table.add_column("Value", justify="right", min_width=14)
        table.add_column("Rating", justify="center", min_width=12)

        if integrity["pages_written"] > 0:
            table.add_row("[bold]Data Integrity[/]", "", "")
            table.add_row("  Pages Written", f"{integrity['pages_written']:,}", "")
            table.add_row("  Pages Verified", f"{integrity['pages_verified']:,}", "")
            table.add_row("  Corruptions", str(integrity["corruptions_detected"]),
                          _bool_badge(integrity["integrity_pass"]))

        if conc["total_transactions"] > 0:
            table.add_row("", "", "")
            table.add_row("[bold]Concurrency[/]", "", "")
            table.add_row("  Total Transactions", f"{conc['total_transactions']:,}", "")
            table.add_row("  Committed", f"{conc['committed']:,}", "")
            table.add_row("  Deadlocks", str(conc["deadlocks"]), "")
            table.add_row("  Lock Timeouts", str(conc["lock_timeouts"]), "")
            table.add_row("  Serialization Fails", str(conc["serialization_failures"]), "")
            table.add_row("  TPS", f"{conc['tps']:,.0f}", _rate_tps(conc["tps"]))

        if audit.get("balance_before", 0) > 0:
            table.add_row("", "", "")
            table.add_row("[bold]Conservation Audit[/]", "", "")
            table.add_row("  Balance Before", f"${audit['balance_before']:,.2f}", "")
            table.add_row("  Balance After", f"${audit['balance_after']:,.2f}", "")
            table.add_row("  Drift", f"${audit['balance_drift']:.2f}",
                          _bool_badge(audit["conservation_pass"]))

        cl = d["commit_latency"]
        if cl["count"] > 0:
            table.add_row("", "", "")
            table.add_row("[bold]Commit Latency[/]", "", "")
            table.add_row("  p50", f"{cl['p50_ms']:.2f} ms", _rate_latency(cl["p50_ms"]))
            table.add_row("  p99", f"{cl['p99_ms']:.2f} ms", _rate_latency(cl["p99_ms"]))
            table.add_row("  Jitter", f"{cl['jitter_ms']:.2f} ms", "")

        self.console.print(table)
        self.console.print()

    def print_dsb(self, result: DSBResult):
        table = Table(title="Analytical Benchmark", border_style="green", show_lines=True)
        table.add_column("Query", style="bold", min_width=8)
        table.add_column("Duration", justify="right", min_width=10)
        table.add_column("Rows", justify="right")
        table.add_column("Cold", justify="right")
        table.add_column("Warm", justify="right")
        table.add_column("Speedup", justify="right")
        table.add_column("Status", justify="center")

        for q in result.queries:
            status = "[green]OK[/]" if q.status == "ok" else f"[red]{q.status.upper()}[/]"
            table.add_row(
                q.query_id,
                f"{q.duration_sec:.3f} s",
                f"{q.rows_returned:,}",
                f"{q.cold_run_sec:.3f} s",
                f"{q.warm_run_sec:.3f} s",
                f"{q.cache_speedup_ratio:.1f}x" if q.cache_speedup_ratio > 0 else "-",
                status,
            )

        self.console.print(table)
        self.console.print(f"  Geometric Mean:  [bold]{result.geometric_mean_sec:.3f} s[/]")
        self.console.print(f"  Power Score:     [bold]{result.power_score:,.0f}[/]")
        self.console.print(f"  Fastest:         [bold]{result.fastest_query}[/]")
        self.console.print(f"  Slowest:         [bold]{result.slowest_query}[/]")
        self.console.print(f"  Total Runtime:   [bold]{result.total_runtime_sec:.1f} s[/]")
        self.console.print()

    def print_network(self, result: NetworkResult):
        d = result.to_dict()
        table = Table(title="Network Profile", border_style="blue", show_lines=True)
        table.add_column("Metric", style="bold", min_width=22)
        table.add_column("Value", justify="right", min_width=14)
        table.add_column("Rating", justify="center", min_width=12)

        ping = d["ping_latency"]
        table.add_row("[bold]Ping (SELECT 1)[/]", "", "")
        table.add_row("  p50", f"{ping['p50_ms']:.2f} ms", _rate_latency(ping["p50_ms"]))
        table.add_row("  p99", f"{ping['p99_ms']:.2f} ms", _rate_latency(ping["p99_ms"]))
        table.add_row("  Jitter", f"{ping['jitter_ms']:.2f} ms", "")

        setup = d["connection_setup"]
        table.add_row("", "", "")
        table.add_row("[bold]Connection Setup[/]", "", "")
        table.add_row("  p50", f"{setup['p50_ms']:.1f} ms", "")
        table.add_row("  p99", f"{setup['p99_ms']:.1f} ms", "")

        table.add_row("", "", "")
        table.add_row("DNS Resolution", f"{d['dns_resolution_ms']:.1f} ms", "")

        fb = d["first_byte_latency"]
        table.add_row("[bold]First Byte[/]", "", "")
        table.add_row("  p50", f"{fb['p50_ms']:.2f} ms", "")

        table.add_row("", "", "")
        table.add_row("[bold]Bandwidth[/]", "", "")
        table.add_row("  Upload", f"{d['bandwidth_upload_mbps']:.1f} MB/s", "")
        table.add_row("  Download", f"{d['bandwidth_download_mbps']:.1f} MB/s", "")

        self.console.print(table)
        self.console.print()

    def print_full(self, result: FullBenchmarkResult):
        self.print_header(result.preset, result.database_info)
        for r in result.sqlio_results:
            self.print_sqlio(r)
            self.print_sqlio_scaling(r)
        for r in result.sqliosim_results:
            self.print_sqliosim(r)
        if result.dsb_result:
            self.print_dsb(result.dsb_result)
        if result.network_result:
            self.print_network(result.network_result)


class JSONReporter:
    def save(self, result: FullBenchmarkResult, output_dir: str | Path) -> Path:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"sqlio_cloud_{int(time.time())}.json"
        path.write_text(json.dumps(result.to_dict(), indent=2, default=str))
        return path


class HTMLReporter:
    """Generates a self-contained HTML report with embedded Chart.js visualizations."""

    def save(self, result: FullBenchmarkResult, output_dir: str | Path) -> Path:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"sqlio_cloud_{int(time.time())}.html"
        html = self._render(result)
        path.write_text(html)
        return path

    def _render(self, result: FullBenchmarkResult) -> str:
        data = result.to_dict()
        data_json = json.dumps(data, indent=2, default=str)

        sqlio_charts = self._sqlio_chart_js(result)
        dsb_chart = self._dsb_chart_js(result)
        network_chart = self._network_chart_js(result)
        sqliosim_section = self._sqliosim_html(result)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Data Bench Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 2rem; }}
  h1 {{ color: #58a6ff; margin-bottom: 0.5rem; }}
  h2 {{ color: #79c0ff; margin: 2rem 0 1rem; border-bottom: 1px solid #21262d; padding-bottom: 0.5rem; }}
  h3 {{ color: #d2a8ff; margin: 1rem 0 0.5rem; }}
  .header {{ background: #161b22; padding: 1.5rem; border-radius: 8px; margin-bottom: 2rem;
             border: 1px solid #30363d; }}
  .header p {{ color: #8b949e; margin: 0.25rem 0; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(450px, 1fr)); gap: 1.5rem; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.5rem; }}
  .card canvas {{ max-height: 320px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ padding: 0.5rem 0.75rem; border: 1px solid #21262d; text-align: left; }}
  th {{ background: #21262d; color: #58a6ff; }}
  tr:nth-child(even) {{ background: #0d1117; }}
  .pass {{ color: #3fb950; font-weight: bold; }}
  .fail {{ color: #f85149; font-weight: bold; }}
  .collapsible {{ cursor: pointer; background: #21262d; padding: 0.75rem; border-radius: 4px;
                  margin-top: 2rem; }}
  .collapsible:hover {{ background: #30363d; }}
  .raw-json {{ display: none; background: #0d1117; padding: 1rem; border-radius: 4px;
               overflow-x: auto; max-height: 600px; font-family: monospace; font-size: 0.85rem;
               white-space: pre; }}
</style>
</head>
<body>

<div class="header">
  <h1>Data Bench Report</h1>
  <p>Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(result.timestamp))}</p>
  <p>Database: {result.database_info.get('dialect_family', '?')} @ {result.database_info.get('host', '?')}</p>
  {"<p>Tier: " + result.database_info["edition"] + (" (" + result.database_info["service_objective"] + ")" if result.database_info.get("service_objective") else "") + "</p>" if result.database_info.get("edition") else ""}
  {"<p>Elastic Pool: " + str(result.database_info["elastic_pool"]) + "</p>" if result.database_info.get("elastic_pool") else ""}
  {"<p>Resources: " + str(result.database_info.get("vcores", "")) + " vCores / " + str(result.database_info.get("memory_gb", "")) + " GB RAM</p>" if result.database_info.get("vcores") else ""}
  {"<p>Max Size: " + str(result.database_info["max_size_gb"]) + " GB</p>" if result.database_info.get("max_size_gb") else ""}
  <p>Profile: {result.preset or 'custom'}</p>
</div>

<h2>I/O Performance</h2>
<div class="grid">
{sqlio_charts}
</div>

<h2>Integrity &amp; Stress Tests</h2>
{sqliosim_section}

<h2>Analytical Queries</h2>
<div class="grid">
{dsb_chart}
</div>

<h2>Network Profile</h2>
<div class="grid">
{network_chart}
</div>

<div class="collapsible" onclick="let el=this.nextElementSibling; el.style.display=el.style.display==='block'?'none':'block';">
  Raw JSON Data (click to expand)
</div>
<div class="raw-json">{data_json}</div>

</body>
</html>"""

    def _sqlio_chart_js(self, result: FullBenchmarkResult) -> str:
        html = ""
        for i, r in enumerate(result.sqlio_results):
            lat = r.latency
            percentiles = [lat.p50, lat.p75, lat.p90, lat.p95, lat.p99, lat.p999]
            t = r.throughput.to_dict()
            ts_data = t.get("time_series", [])

            cfg_line = _config_summary(r.config)
            cfg_html = f'<p style="margin:0.25rem 0 0.75rem;color:#8b949e;font-size:0.85rem">{cfg_line}</p>' if cfg_line else ""
            html += f"""
<div class="card">
  <h3>{r.test_name} — Latency Percentiles</h3>
  {cfg_html}
  <canvas id="sqlio_lat_{i}"></canvas>
  <script>
    new Chart(document.getElementById('sqlio_lat_{i}'), {{
      type: 'bar',
      data: {{
        labels: ['p50','p75','p90','p95','p99','p99.9'],
        datasets: [{{ label: 'Latency (ms)', data: {percentiles},
                     backgroundColor: ['#3fb950','#56d364','#e3b341','#d29922','#f0883e','#f85149'] }}]
      }},
      options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
    }});
  </script>
  <p style="margin-top:0.75rem;color:#8b949e">
    IOPS: <b>{t['avg_ops_per_sec']:,.0f}</b> | Throughput: <b>{t['avg_mbps']:.1f} MB/s</b> |
    Errors: <b>{t['error_rate_pct']:.2f}%</b>
  </p>
</div>"""

            if ts_data:
                labels = [s["elapsed_sec"] for s in ts_data]
                ops = [s["ops_per_sec"] for s in ts_data]
                html += f"""
<div class="card">
  <h3>{r.test_name} — IOPS Over Time</h3>
  <canvas id="sqlio_ts_{i}"></canvas>
  <script>
    new Chart(document.getElementById('sqlio_ts_{i}'), {{
      type: 'line',
      data: {{
        labels: {labels},
        datasets: [{{ label: 'IOPS', data: {ops}, borderColor: '#58a6ff', fill: false, tension: 0.2 }}]
      }},
      options: {{ responsive: true, scales: {{ x: {{ title: {{ display: true, text: 'Seconds' }} }} }} }}
    }});
  </script>
</div>"""

            if r.scalability:
                sa = r.scalability
                threads = [p.threads for p in sa.points]
                iops_pts = [round(p.iops, 1) for p in sa.points]
                eff = [round(sa.scaling_efficiency(p.threads), 1) for p in sa.points]
                html += f"""
<div class="card">
  <h3>{r.test_name} — Thread Scaling</h3>
  <canvas id="sqlio_sc_{i}"></canvas>
  <script>
    new Chart(document.getElementById('sqlio_sc_{i}'), {{
      type: 'line',
      data: {{
        labels: {threads},
        datasets: [
          {{ label: 'IOPS', data: {iops_pts}, borderColor: '#58a6ff', yAxisID: 'y' }},
          {{ label: 'Efficiency %', data: {eff}, borderColor: '#d2a8ff', yAxisID: 'y1' }}
        ]
      }},
      options: {{ responsive: true,
        scales: {{
          y: {{ position: 'left', title: {{ display: true, text: 'IOPS' }} }},
          y1: {{ position: 'right', title: {{ display: true, text: 'Efficiency %' }}, min: 0, max: 120,
                 grid: {{ drawOnChartArea: false }} }}
        }}
      }}
    }});
  </script>
  <p style="margin-top:0.75rem;color:#8b949e">
    Peak IOPS: <b>{sa.peak_iops:,.0f}</b> (at {sa.peak_iops_threads} threads) |
    Saturation: <b>{sa.saturation_point}</b> threads |
    Optimal: <b>{sa.optimal_threads}</b> threads |
    Serial fraction: <b>{sa.amdahl_serial_fraction:.4f}</b>
  </p>
</div>"""

        return html

    def _sqliosim_html(self, result: FullBenchmarkResult) -> str:
        html = ""
        for r in result.sqliosim_results:
            d = r.to_dict()
            integrity = d["integrity"]
            conc = d["concurrency"]
            audit = d["data_integrity_audit"]

            pass_cls = "pass" if integrity.get("integrity_pass", True) else "fail"
            cons_cls = "pass" if audit.get("conservation_pass", True) else "fail"

            cfg_line = _config_summary(r.config)
            cfg_row = f'<tr><td colspan="3" style="color:#8b949e;font-size:0.85rem">{cfg_line}</td></tr>' if cfg_line else ""
            html += f"""
<table>
  <tr><th colspan="3">{r.test_name}</th></tr>
  {cfg_row}
  <tr><td>Pages Written</td><td>{integrity['pages_written']:,}</td><td></td></tr>
  <tr><td>Pages Verified</td><td>{integrity['pages_verified']:,}</td><td></td></tr>
  <tr><td>Corruptions</td><td>{integrity['corruptions_detected']}</td>
      <td class="{pass_cls}">{"PASS" if integrity.get("integrity_pass") else "FAIL"}</td></tr>
  <tr><td>Committed TXN</td><td>{conc['committed']:,}</td><td></td></tr>
  <tr><td>Deadlocks</td><td>{conc['deadlocks']}</td><td></td></tr>
  <tr><td>TPS</td><td>{conc['tps']:,.0f}</td><td></td></tr>
  <tr><td>Balance Drift</td><td>${audit['balance_drift']:.2f}</td>
      <td class="{cons_cls}">{"PASS" if audit.get("conservation_pass") else "FAIL"}</td></tr>
</table>"""
        return html

    def _dsb_chart_js(self, result: FullBenchmarkResult) -> str:
        if not result.dsb_result:
            return "<p>Analytical queries not run.</p>"
        dsb = result.dsb_result
        labels = [q.query_id for q in dsb.queries]
        durations = [round(q.duration_sec, 3) for q in dsb.queries]
        colors = ["#3fb950" if q.status == "ok" else "#f85149" for q in dsb.queries]

        return f"""
<div class="card">
  <h3>Query Duration</h3>
  <canvas id="dsb_dur"></canvas>
  <script>
    new Chart(document.getElementById('dsb_dur'), {{
      type: 'bar',
      data: {{
        labels: {labels},
        datasets: [{{ label: 'Duration (s)', data: {durations},
                     backgroundColor: {colors} }}]
      }},
      options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
    }});
  </script>
  <p style="margin-top:0.75rem;color:#8b949e">
    Geo Mean: <b>{dsb.geometric_mean_sec:.3f} s</b> |
    Power Score: <b>{dsb.power_score:,.0f}</b> |
    Fastest: <b>{dsb.fastest_query}</b> |
    Slowest: <b>{dsb.slowest_query}</b>
  </p>
</div>"""

    def _network_chart_js(self, result: FullBenchmarkResult) -> str:
        if not result.network_result:
            return "<p>Network profiling not run.</p>"
        nr = result.network_result
        ping = nr.ping_latency

        percentiles = [ping.p50, ping.p75, ping.p90, ping.p95, ping.p99, ping.p999]
        return f"""
<div class="card">
  <h3>Ping Latency Distribution</h3>
  <canvas id="net_ping"></canvas>
  <script>
    new Chart(document.getElementById('net_ping'), {{
      type: 'bar',
      data: {{
        labels: ['p50','p75','p90','p95','p99','p99.9'],
        datasets: [{{ label: 'Latency (ms)', data: {[round(p,3) for p in percentiles]},
                     backgroundColor: ['#3fb950','#56d364','#e3b341','#d29922','#f0883e','#f85149'] }}]
      }},
      options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
    }});
  </script>
  <p style="margin-top:0.75rem;color:#8b949e">
    DNS: <b>{nr.dns_resolution_ms:.1f} ms</b> |
    Upload: <b>{nr.bandwidth_upload_mbps:.1f} MB/s</b> |
    Download: <b>{nr.bandwidth_download_mbps:.1f} MB/s</b>
  </p>
</div>"""
