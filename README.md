# CloudBench

**Cloud database performance benchmarking as a service.**

CloudBench is a comprehensive benchmark suite for managed cloud databases where you have no direct disk access. It measures I/O throughput, query optimizer efficiency, transaction isolation correctness, and network transport characteristics — all through standard SQL operations over the wire.

---

## Before you start

> **Do not run CloudBench against QA, staging, or production databases.**

CloudBench generates synthetic load — concurrent reads, writes, bulk inserts, transaction storms, and full table scans. This will consume CPU, IOPS, memory, and transaction log throughput on the target database. Running it against a database that serves real traffic can degrade application performance, trigger throttling, or exhaust DTU/vCore budgets.

**What to do instead:**

1. **Provision a dedicated test server.** Create a new database server (or managed instance) on the same SKU/tier you plan to evaluate. This gives you an isolated environment and ensures the benchmark results reflect the true performance of that tier without interference from other workloads.
2. **Create an empty database (or let CloudBench do it).** CloudBench creates and manages its own test tables — you just need a blank database to connect to. Name it something like `benchmarks` or `cloudbench_test`. If the database doesn't exist, CloudBench will create it automatically when you test the connection, and enable snapshot isolation for SQL Server instances.
3. **Make the database endpoint reachable over the internet.** CloudBench connects to your database remotely, so the server must accept inbound connections on its SQL port (1433 for SQL Server, 5432 for PostgreSQL, 3306 for MySQL). Ensure that public network access is enabled and that any firewall rules allow traffic from external IPs. Consult your cloud provider's documentation for how to configure network access on your specific service. Remember to lock it back down or delete the test server when you're done.
4. **Use a dedicated test credential.** Create a SQL user specifically for benchmarking with only the required permissions (see "What database permissions do I need?" below). Do not reuse production service accounts.
5. **Tear down after testing.** Once you've exported your results, delete the test server to avoid ongoing charges. CloudBench cleans up its own tables, but the server/instance itself is your responsibility.

---

## How do I use it?

1. **Connect** — Enter your database host, port, credentials, and dialect (SQL Server, PostgreSQL, or MySQL). The connection page displays the CloudBench host IP address for your firewall rules. CloudBench validates the connection, detects the server version, and auto-creates the database if it doesn't exist.
2. **Configure** — Choose a preset (Smoke, Standard, or Full Stress) or customize individual tests, concurrency levels, and block sizes.
3. **Run** — CloudBench executes the selected tests with live progress streaming. No agents or software are installed on your database server — everything runs through SQL over the network.
4. **Analyze** — Interactive results with per-metric tooltips, latency distribution charts, and thread-scaling curves. Export as CSV or JSON to compare multiple servers.

---

## Which databases are supported?

| Provider | Services |
|---|---|
| **Microsoft Azure** | Azure SQL Database, Azure SQL Managed Instance*, Azure Database for PostgreSQL/MySQL |
| **Amazon Web Services** | Amazon RDS (PostgreSQL, MySQL, SQL Server), Amazon Aurora (PostgreSQL, MySQL) |
| **Google Cloud** | Cloud SQL (PostgreSQL, MySQL, SQL Server), AlloyDB |
| **Self-hosted** | Any PostgreSQL, MySQL, or SQL Server accessible over the network |

> **\*Azure SQL Managed Instance:** Managed Instance sits inside an Azure Virtual Network and has no public endpoint by default. To benchmark a MI:
>
> 1. **Enable the public endpoint** — Portal → MI → Security → Networking → Public endpoint → Enable. This can take 5–10 minutes to provision.
> 2. **Add an NSG inbound rule** — Allow TCP on port **3342** from the CloudBench IP address (displayed on the connection page) or from `0.0.0.0/0` for a throwaway test instance.
> 3. **Use the public hostname** — It contains `.public.` in the name (e.g., `yourmi.public.abc123.database.windows.net`). The default hostname without `.public.` is the private VNet endpoint and will not work from outside the VNet.
> 4. **Port is 3342** (not 1433) — CloudBench sets this automatically when you select "Azure SQL Managed Instance" from the dropdown.
> 5. **Database auto-creation** — You do not need to create the database ahead of time. If the database name you enter does not exist, CloudBench will create it automatically and enable snapshot isolation.
>
> Since you should be provisioning a dedicated test instance anyway, enabling the public endpoint is safe — just delete the instance when testing is complete.

---

## What database permissions do I need?

CloudBench connects as a regular SQL user. The account needs:

| Permission | Used For |
|---|---|
| `CREATE TABLE` | Creating test tables (`bench_io`, `bench_accounts`, `bench_net_bw`, `dsb_*` analytical tables) |
| `INSERT`, `UPDATE`, `SELECT` | Running all benchmark operations |
| `DROP TABLE` | Cleaning up test tables before and after each run |
| `BEGIN` / `COMMIT` / `ROLLBACK` | Transaction isolation and concurrency tests |
| `CREATE DATABASE` | Auto-creating the benchmark database if it doesn't exist (server-level login required) |
| `ALTER DATABASE` | Enabling snapshot isolation on SQL Server / Managed Instance |

For the simplest setup, use the **server admin** login — this is the credential set when you created the server or managed instance. If you prefer a least-privilege approach, create the database manually and grant the user `db_owner` on that database.

---

## Are my credentials safe?

Yes. Credentials are held in memory only for the duration of the benchmark run and discarded when the session ends. Nothing is written to disk or logged. All communication with your database uses its native wire protocol (typically TLS-encrypted), and the web UI runs over HTTPS.

---

## Will it affect my existing data?

No. CloudBench only creates and operates on its own test tables, which use a `bench_` or `dsb_` prefix. These tables are dropped before each run and cleaned up after. Your existing tables are never read, modified, or dropped. Nothing is installed on the database server.

---

## What tests are available?

### I/O Performance Tests

These tests measure the database's storage-layer throughput and latency through SQL-level operations, using a test table with configurable binary payloads (1 KB – 64 KB block size).

| Test | What It Does | Key Metrics |
|---|---|---|
| **Random Read** | `SELECT payload WHERE id = :random_pk` across a scaling thread sweep. Each operation is a B-tree index seek + single-page buffer pool fetch + network round-trip. The fundamental cloud database performance primitive — equivalent to random read IOPS on block storage. | IOPS (ops/sec), peak IOPS, p50/p99 latency, jitter, saturation point, optimal threads, Amdahl's serial fraction, IOPS over time |
| **Random Write** | `UPDATE SET payload = :data WHERE id = :random_pk` under increasing concurrency. Exercises index seek + row lock acquisition + in-place page modification + WAL/redo log synchronous flush. Writes are typically 2–5x slower than reads due to WAL serialization and lock contention. | IOPS (ops/sec), peak IOPS, p50/p99 latency, error rate, thread scaling curve, IOPS over time |
| **Sequential Scan** | `SELECT * FROM test_table` — full table scan measuring sequential I/O bandwidth. Unlike random I/O (latency-bound), sequential scan is bandwidth-bound. Tests buffer pool read-ahead efficiency and result set streaming. IOPS counts each row returned as one operation, so IOPS = rows/sec. | IOPS (rows/sec), throughput (MB/s), duration, performance over time |
| **Mixed Read/Write** | 70% point reads / 30% single-row UPDATEs running concurrently. Simulates realistic OLTP workloads where reads and writes compete for buffer pool pages, row locks, and WAL bandwidth. The read/write interference factor reveals contention overhead. | Combined IOPS, read/write breakdown, latency under contention, IOPS over time |
| **Bulk Insert** | Multi-row `INSERT` statements measuring sustained write ingestion speed. Performance is governed by transaction log write bandwidth, B-tree page splits, index maintenance overhead, and auto-statistics triggers. Each row inserted counts as one operation. | IOPS (rows/sec), throughput (MB/s), performance over time |
| **Connection Pool Stress** | Fires bursts of `SELECT 1` at escalating concurrency (1 → 5 → 10 → 20 → … → 200 connections) to map the connection pool's latency-vs-concurrency curve. Identifies pool saturation, connection refusal thresholds, and checkout latency degradation. | Per-burst p50/p99, saturation concurrency, error threshold |

### Stress & Integrity Tests

These tests validate data correctness and ACID guarantees under concurrent load.

| Test | What It Does | Key Metrics |
|---|---|---|
| **Data Integrity** | Writes random binary pages with SHA-256 checksums under concurrent thread load, then reads a statistical sample back and recomputes hashes. Detects silent storage corruption, network transport bit flips, and driver serialization bugs. Any failure is a critical finding. | PASS/FAIL, pages written, pages verified, corruptions detected |
| **Concurrent Transactions** | Executes concurrent balance-transfer transactions (debit account A, credit account B within a single transaction) and verifies `SUM(all_balances)` is conserved post-test. Tests the database's ACID guarantees under realistic contention — detects lost updates, write skew, and serialization anomalies. | TPS, committed/total transactions, deadlock count, balance drift, commit p50/p99 |
| **Isolation Level** | Systematically tests each supported isolation level (READ UNCOMMITTED → SERIALIZABLE) by running known anomaly patterns: dirty reads, non-repeatable reads, phantom inserts, and write skew. Validates that the database's actual behavior matches its documented isolation guarantees. | Per-level anomaly detection results |

### Analytical Queries (DSB — Decision Support Benchmark)

Generates a normalized star-schema dataset (zones, countries, vendors, buyers, products, inventory, orders, order details) and executes 16 original analytical queries covering:

- Multi-way hash/merge joins across 4+ tables
- Correlated subqueries with EXISTS / NOT EXISTS
- Aggregation with GROUP BY, HAVING, CASE expressions
- Common Table Expressions (CTEs) and derived tables
- ORDER BY with LIMIT/TOP for ranking queries
- LEFT JOIN distributions and NULL handling

Each query runs cold (first execution, no plan cache) and warm (repeated execution) to measure plan compilation overhead and cache speedup ratio. Results include per-query duration, row output preview, and status.

**Composite metrics:**
- **Geometric Mean** — `exp(Σ ln(duration_i) / N)` — fairly weights queries across different magnitude ranges
- **Power Score** — `(3600 × Scale_Factor) / Geometric_Mean` — normalized throughput metric for cross-comparison across scale factors

### Network Profiling

Isolates the transport layer from the database engine to distinguish network-bound vs. compute-bound performance limits.

| Measurement | Method |
|---|---|
| **DNS Resolution** | Times hostname-to-IP resolution, detecting slow resolvers and CNAME chains |
| **Ping Latency** | `SELECT 1` round-trip with full percentile distribution (p50 → p99.9) — the irreducible floor for any operation |
| **Connection Setup** | TCP three-way handshake + TLS negotiation + database authentication — the cost your connection pool amortizes |
| **First Byte Latency** | Time from `conn.execute()` to first row received for a 1000-row query — measures query compilation + initial I/O + network |
| **Upload Bandwidth** | Client-to-database transfer rate via timed bulk INSERT (MB/s) |
| **Download Bandwidth** | Database-to-client transfer rate via timed bulk SELECT (MB/s) |

---

## Which preset should I choose?

### Smoke Test (~5–15 minutes)

A quick sanity check to validate connectivity, basic IOPS, latency floor, and query compilation.

| Parameter | Value |
|---|---|
| Test table | 2,000 rows |
| I/O operations | 100 per test |
| Thread sweep | 2 points (1, 4) |
| Analytical queries | 2 (Q01 pricing summary, Q06 revenue forecast) |
| Scale factor | 0.01 |
| Tests included | Random Read, Random Write, Data Integrity, Analytical Queries |

Best for: initial connection validation, quick regression checks, CI/CD smoke gates.

### Standard (~10–20 minutes)

Balanced coverage of all test types. **The recommended default for most benchmarks.**

| Parameter | Value |
|---|---|
| Test table | 200,000 rows |
| I/O operations | 50,000 per test |
| Thread sweep | 5 points (1, 2, 4, 8, 16) |
| Analytical queries | All 16 |
| Scale factor | 0.05 |
| Integrity cycles | 1,000 writes across 12 threads |
| Concurrent transactions | 2,000 accounts |
| Network profiling | 50 pings, 20 connections, 3K bandwidth rows |
| Tests included | All I/O tests, Data Integrity, Concurrent Transactions, Analytical Queries, Network Profiling |

Best for: cloud tier comparison, capacity planning, SLA validation, pre-production baselines.

### Full Stress (~30–90 minutes)

Comprehensive stress test with maximum concurrency. Designed for production-grade assessments.

| Parameter | Value |
|---|---|
| Test table | 500,000 rows |
| I/O operations | 100,000 per test |
| Thread sweep | 6 points (1, 2, 4, 8, 16, 32) |
| Analytical queries | All 16, 2 iterations for variance measurement |
| Scale factor | 0.1 |
| Integrity cycles | 2,000 writes across 16 threads |
| Concurrent transactions | 5,000 accounts |
| Network profiling | 100 pings, 30 connections, 5K bandwidth rows |
| Tests included | Everything in Standard plus: Isolation Level Testing, Connection Pool Stress (burst 1 → 200) |

Best for: production tier selection, regression testing, cloud provider comparison, identifying limits under sustained load.

> **Note:** Execution time varies by instance tier. Burstable/Basic tiers with high network latency will take longer; Premium/Business Critical tiers will complete faster.

---

## What does each metric mean?

Every test produces detailed metrics. Hover over any metric in the results page for a detailed tooltip explanation. Here's a summary:

| Category | Metrics |
|---|---|
| **Latency** | min, max, mean, stddev, jitter, coefficient of variation, p50, p75, p90, p95, p99, p99.9, full distribution histogram, time-series |
| **Throughput** | IOPS, MB/s, rows/sec, TPS, error rate, peak vs sustained, per-second time-series |
| **Scalability** | Per-thread-count IOPS and latency, efficiency %, peak IOPS, saturation point, optimal thread count, Amdahl's serial fraction |
| **Integrity** | SHA-256 verification pass/fail, corruption count, balance conservation audit with drift |
| **Network** | DNS resolution, ping distribution, connection setup, first byte latency, bidirectional bandwidth |

### How is IOPS calculated?

IOPS (I/O Operations Per Second) is calculated as `total_operations / elapsed_seconds`, where elapsed time is measured precisely using `time.perf_counter()` and frozen when the test completes. This prevents timing drift from subsequent tests inflating the elapsed time.

What counts as an "operation" depends on the test:

| Test | What Counts as 1 Operation | IOPS Meaning |
|---|---|---|
| **Random Read** | One `SELECT payload WHERE id = :pk` | Point lookups per second |
| **Random Write** | One `UPDATE SET payload = :data WHERE id = :pk` | Single-row updates per second |
| **Sequential Scan** | One row returned from `SELECT * FROM table` | Rows scanned per second |
| **Mixed Read/Write** | One read or write operation | Combined operations per second |
| **Bulk Insert** | One row inserted via batch `INSERT` | Rows inserted per second |
| **Pool Stress** | One `SELECT 1` query | Queries per second |

For sequential scan and bulk insert, IOPS represents **rows per second** because each row processed is the fundamental unit of work. This makes the IOPS and MB/s numbers consistent — if you see 10,000 IOPS at 8 KB block size, you should see approximately 78 MB/s throughput (10,000 × 8 KB / 1,024).

### Performance Over Time Charts

Every I/O test produces a **Performance Over Time** chart showing per-second IOPS and MB/s throughput throughout the test duration. The throughput tracker takes snapshots every second, recording:

- **IOPS (per interval)** — instantaneous operations/sec during that 1-second window
- **Avg IOPS (cumulative)** — running average from test start to that point
- **MB/s** — instantaneous data throughput during that window

These charts reveal performance variability that summary numbers hide — such as throttling events, noisy-neighbor interference, buffer pool warm-up effects, and cloud provider burst credit depletion.

### Performance Ratings

Metric cards display color-coded ratings. The thresholds are context-sensitive — what's "GOOD" for a random point lookup is different from a sequential scan:

**Random Read, Random Write, Mixed Workload:**

| IOPS | Rating |
|---|---|
| > 2,000 | EXCELLENT |
| > 500 | GOOD |
| > 100 | FAIR |
| ≤ 100 | LOW |

**Sequential Scan, Bulk Insert** (rows/sec — naturally higher throughput):

| IOPS (rows/sec) | Rating |
|---|---|
| > 50,000 | EXCELLENT |
| > 10,000 | GOOD |
| > 1,000 | FAIR |
| ≤ 1,000 | LOW |

**Latency (p50 and p99):**

| Latency | Rating |
|---|---|
| < 5 ms | EXCELLENT |
| < 20 ms | GOOD |
| < 100 ms | FAIR |
| ≥ 100 ms | HIGH |

These ratings are relative guidelines. Actual expectations depend on your cloud tier, network distance, and workload. A Basic-tier database at 200 IOPS is operating normally; a Premium-tier database at 200 IOPS warrants investigation.

### Scalability Metrics

The I/O tests run each workload at increasing thread counts (e.g., 1 → 4 → 8 → 16 → 32) and measure IOPS at each level. The scaling sweep produces:

| Metric | Definition |
|---|---|
| **Peak IOPS** | The highest IOPS observed across all thread counts in the sweep. This is the maximum throughput the database delivered. Shown with the thread count at which it occurred. |
| **Saturation Point** | The thread count where IOPS stops increasing (< 5% improvement). Beyond this point, adding connections only increases latency. |
| **Optimal Threads** | The thread count with the best IOPS-per-thread efficiency ratio. Use this to size your application's connection pool. |
| **Amdahl's Serial Fraction** | The non-parallelizable portion of the workload (see below). |

### What is Amdahl's Serial Fraction?

From the scaling curve, CloudBench calculates **Amdahl's serial fraction** — the portion of the workload that cannot be parallelized, no matter how many concurrent connections are used.

The formula: `Speedup = 1 / (f + (1 - f) / N)`, where **f** is the serial fraction and **N** is the thread count.

| Serial Fraction | Interpretation |
|---|---|
| 0.01 – 0.05 | Excellent parallelism. Typical for indexed reads. Nearly all work runs independently across threads. |
| 0.05 – 0.15 | Moderate serialization. Common for writes — the WAL (write-ahead log) flush is a serialization point all writers share. |
| 0.15 – 0.30 | Significant contention. Lock escalation, page latch waits, or transaction log bottleneck. Adding threads yields diminishing returns. |
| 0.30+ | Heavily serialized. Most work is forced through a single-threaded bottleneck. Investigate lock contention, hot-row updates, or table-level locks. |

This metric is directly actionable for capacity planning: if writes show a serial fraction of 0.25, throughput will never exceed ~4x single-threaded performance regardless of connection count. The solution is to reduce write contention (batching, partitioning) or upgrade to a tier with a faster log subsystem.

---

## What does the I/O Block Size option do?

The block size controls the binary payload stored in each row of the I/O test table. It affects whether the benchmark is latency-bound or throughput-bound:

| Size | Behavior |
|---|---|
| **1 KB** | IOPS-bound. Network RTT and index seek dominate. Low MB/s even at high IOPS. |
| **4 KB** | Standard small-block workload. Comparable to 4K random I/O on block storage. |
| **8 KB** (default) | Matches common database page sizes. Balances IOPS and bandwidth measurement. |
| **64 KB** | Throughput-bound. Fewer ops/sec but higher MB/s. Stresses buffer pool, TLS framing, and TCP window scaling. |

The block size is also used as the page size for SHA-256 integrity verification.

---

## How do I export and compare results?

CloudBench is built for teams benchmarking multiple servers. From the results page you have three export options:

- **PDF Report** — A professionally formatted, multi-page document ready to share with stakeholders. Includes a branded cover page with server details, an executive summary table, detailed per-test metrics with latency distributions, thread-scaling tables, embedded charts, and any errors encountered. Suitable for attaching to architecture review documents, vendor evaluations, or cloud migration assessments.
- **CSV** — Flat `section, metric, value, unit` format. Every metric from every test is flattened into rows with server metadata (host, dialect, version, timestamp, preset). Import into Excel, Google Sheets, or a Jupyter notebook and pivot to compare servers side-by-side.
- **JSON** — Complete structured result object with full nesting for programmatic analysis.

Filenames include the server host, preset, and timestamp for easy identification:
`benchmark_myserver.database.azure.com_standard_2026-03-30T14-25-00.pdf`

---

## Why is my test taking longer than expected?

Execution time depends on several factors:

- **Instance tier** — Burstable and Basic tiers have lower IOPS limits and higher per-operation latency than Premium or Business Critical tiers. A Smoke test that takes 5 minutes on a premium instance may take 15 minutes on a Basic tier.
- **Network latency** — CloudBench connects to your database over the network. The physical distance between CloudBench and your database adds round-trip time to every operation. Higher latency means each individual operation takes longer, which compounds across thousands of operations.
- **Database load** — If other workloads are running concurrently on the same database, they compete for resources and increase latency. This is why we recommend a dedicated test server (see "Before you start").
- **Scale factor** — Higher analytical scale factors generate more data and run longer queries.

If a test appears stuck, check the progress indicator — it updates with the current operation count (e.g., "150 / 500").

---

## Can I customize which tests run?

Yes. After choosing a preset, you can check or uncheck individual tests on the configure page. The preset is just a starting point — you can add or remove any test before starting the benchmark. You can also adjust the I/O block size independently.

---

## What happens to the test data after the benchmark?

All test tables are dropped when the benchmark completes. If the benchmark is interrupted (browser closed, timeout), the tables remain in the database but are automatically dropped at the start of the next run. The tables use distinctive prefixes (`bench_io`, `bench_accounts`, `bench_net_bw`, `dsb_zone`, `dsb_country`, etc.) so they're easy to identify and drop manually if needed.
