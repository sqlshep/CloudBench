# Data Bench — Product Design Document

**Version:** 1.1
**Date:** April 4, 2026
**Status:** In Development

---

## 1. Overview

### 1.1 Product Summary

Data Bench is a cloud database performance benchmarking service. It enables engineering teams to objectively measure and compare the performance characteristics of managed cloud databases — without requiring disk access, database agents, or privileged credentials.

The product is deployed as a stateless container on any major cloud platform (Azure Container Apps, Google Cloud Run, AWS App Runner, or self-hosted Docker) and accessed through a web browser. It is designed to run in every region on every cloud. Users point Data Bench at any reachable database endpoint, select a test profile, and receive a comprehensive performance report covering I/O throughput, query optimizer efficiency, transaction isolation correctness, data integrity verification, and network transport characteristics.

### 1.2 Problem Statement

Organizations evaluating cloud database services face several challenges:

1. **No standardized benchmarking tool exists for managed databases.** Traditional tools like SQLIO and SQLIOSim were designed for on-premises environments with direct disk access, and standard decision-support benchmarks assume local storage. Managed cloud databases abstract the storage layer, rendering these tools unusable.
2. **Cloud tier selection is guesswork.** Vendors publish theoretical IOPS and DTU limits, but real-world performance depends on network latency, noisy-neighbor effects, connection pooling behavior, and workload characteristics that are not captured in spec sheets.
3. **Comparing providers is time-consuming.** Teams manually write ad-hoc queries, spreadsheets, and scripts to compare Azure SQL vs. RDS vs. Cloud SQL. There is no consistent methodology, no standardized metrics, and no repeatable process.
4. **Performance regressions go undetected.** Without baseline benchmarks, teams cannot detect when a provider silently downgrades performance, applies throttling, or introduces latency regressions.

### 1.3 Target Users


| Persona               | Role                                                  | Primary Use Case                                                                      |
| --------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **Database Engineer** | Manages production database infrastructure            | Cloud tier selection, capacity planning, performance baselines, regression detection  |
| **Cloud Architect**   | Designs cloud infrastructure and migration strategies | Multi-provider comparison, architecture review documentation, vendor evaluation       |
| **DevOps / SRE**      | Monitors and maintains production systems             | CI/CD smoke gates, SLA validation, incident response (verifying database performance) |
| **Data Engineer**     | Builds ETL pipelines and analytical workloads         | Evaluating bulk insert throughput, analytical query performance across tiers          |


### 1.4 Key Design Principles

- **Zero installation on the target.** Data Bench operates entirely through standard SQL over the network. No agents, no drivers, no extensions, no elevated privileges.
- **Effortless onboarding.** The web interface guides users through a 4-step wizard (Connect → Configure → Run → Analyze). Every metric has a tooltip explanation. Presets eliminate configuration decisions for common use cases.
- **Expert-grade output.** Results are detailed enough for database engineers to make informed capacity planning decisions — full percentile distributions, Amdahl's serial fraction, thread scaling curves, cold/warm query comparison, isolation level anomaly detection.
- **Comparison-first.** Every output format (PDF, CSV, JSON) is designed for comparing multiple servers. Filenames include host, preset, and timestamp. CSV is flat and pivot-friendly. PDF is stakeholder-ready.
- **Stateless and disposable.** No persistent storage. All test data lives in the target database and is cleaned up after the run. Sessions and run state are held in-memory only. The Data Bench container can be destroyed and recreated at any time.
- **Cloud-agnostic.** Deploys identically on Azure, AWS, GCP, or bare Docker. No cloud-specific APIs, SDKs, or managed services are required at runtime. A single container image runs in every region on every cloud.

---

## 2. Product Architecture

### 2.1 System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     User's Browser                       │
│                                                         │
│  ┌──────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐  │
│  │Login │→ │ Configure │→ │  Live      │→ │ Results + │  │
│  │      │  │ (Step 1-2)│  │ Progress   │  │ Export    │  │
│  └──┬───┘  └──────┬────┘  └──────┬────┘  └───────────┘  │
│     │ HTTP        │ HTTP         │ WebSocket              │
└─────┼─────────────┼──────────────┼───────────────────────┘
      │             │              │
┌─────▼─────────────▼──────────────▼───────────────────────┐
│     Data Bench Container (any cloud / any region)         │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  FastAPI Backend + Session Auth                      │ │
│  │  ┌────────┐ ┌──────────┐ ┌─────────┐ ┌────────┐    │ │
│  │  │  I/O   │ │  Stress  │ │Analytical│ │Network │    │ │
│  │  │ Tests  │ │  Tests   │ │ Queries  │ │Profiler│    │ │
│  │  └────┬───┘ └────┬─────┘ └────┬────┘ └───┬────┘    │ │
│  │       └──────────┬┴───────────┬┘          │         │ │
│  │                  │ SQLAlchemy │            │         │ │
│  └──────────────────┼────────────┼────────────┼────────┘ │
│                     │            │            │           │
└─────────────────────┼────────────┼────────────┼──────────┘
                      │ SQL Wire   │            │
                      │ Protocol   │            │
          ┌───────────▼────────────▼────────────▼──────────┐
          │         Target Cloud Database                    │
          │  (Azure SQL / RDS / Cloud SQL / AlloyDB /        │
          │   Aurora / Self-hosted)                           │
          └─────────────────────────────────────────────────┘
```

### 2.2 Component Overview


| Component            | Technology                                  | Purpose                                                                                            |
| -------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Web Frontend         | Single-page HTML/JS/CSS, Chart.js           | 4-step wizard UI, live progress, interactive results, PDF/CSV/JSON export                          |
| API Backend          | Python 3.11+, FastAPI, Uvicorn              | Connection management, test orchestration, WebSocket progress streaming                            |
| Database Abstraction | SQLAlchemy 2.0                              | Dialect-agnostic SQL execution across PostgreSQL, MySQL, SQL Server                                |
| Database Drivers     | `pymssql`, `psycopg`, `pymysql`             | Native wire protocol connections to each database engine                                           |
| Configuration        | YAML + Python presets                       | Tunable parameters with environment variable interpolation                                         |
| CLI                  | Click, Rich, Questionary                    | Interactive wizard and non-interactive (CI/CD) mode                                                |
| Authentication       | Session cookies, SHA-256 hashed credentials | Login gate for web access; credentials stored as hashes, never in plaintext                        |
| Container            | Docker                                      | Stateless deployment on Azure Container Apps, Google Cloud Run, AWS App Runner, or any Docker host |


### 2.3 Key Design Decisions

**Why SQLAlchemy instead of raw drivers?**
SQLAlchemy provides a unified interface across three database engines while still allowing raw SQL execution via `text()`. This enables a single test implementation to run unmodified against PostgreSQL, MySQL, and SQL Server. Dialect-specific SQL (DDL, data types) is handled by a thin abstraction layer.

**Why client-side PDF generation?**
Using jsPDF in the browser avoids server-side dependencies (headless Chrome, wkhtmltopdf, etc.) that would complicate the container image and increase memory requirements. The PDF includes embedded chart images captured directly from the Canvas elements, ensuring visual fidelity.

**Why WebSocket for progress?**
Benchmarks run for 5–60+ minutes. HTTP long-polling would be fragile over these durations. WebSocket provides a persistent bidirectional channel for real-time progress updates (current test name, completion percentage, operation counts) without polling overhead.

**Why stateless?**
Statelessness eliminates the need for persistent storage, session management, and data retention policies. Each benchmark run is self-contained. Results are exported by the user (PDF/CSV/JSON) and the container can be destroyed. This simplifies deployment, scaling, and compliance.

---

## 3. User Experience

### 3.1 User Flow

```
[Landing Page]
      │
      ▼
[Login]  →  Username + password authentication
      │      Session cookie issued on success
      ▼
[Step 1: Connect]  →  Enter host, port, credentials, dialect
      │                 Test Connection → validates + detects version
      ▼
[Step 2: Configure]  →  Choose preset (Smoke / Standard / Full Stress)
      │                   Optionally customize tests, block size
      ▼
[Step 3: Running]  →  Live progress bar with WebSocket updates
      │                 Per-test status with hover tooltips explaining each test
      │                 "Random Read: 150/500 (4 threads)"
      ▼
[Step 4: Results]  →  Tabbed results: Overview | I/O | Stress | Analytical | Network
      │                 Interactive charts, per-metric tooltips
      │                 Export: PDF Report | CSV | JSON
      ▼
[Done]  →  User deletes test server
```

### 3.2 Results Page Design

The results page uses a tabbed layout with five sections:


| Tab            | Contents                                                                                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Overview**   | Summary metric cards with color-coded ratings (IOPS, TPS, ping, integrity pass/fail, analytical geo mean). At-a-glance health check.                                      |
| **I/O**        | Per-test detail cards with IOPS, throughput, full latency percentile table, latency histogram chart, thread scaling table, scaling curve chart, Amdahl's serial fraction. |
| **Stress**     | Integrity pass/fail with page counts, TPS with commit ratios, deadlock counts, balance conservation audit, commit latency percentiles.                                    |
| **Analytical** | Per-query table (duration, rows, cold/warm, speedup, status), collapsible row preview, bar chart, composite metrics (geo mean, power score).                              |
| **Network**    | Ping percentile table, connection setup, DNS, first byte, upload/download bandwidth, ping distribution chart.                                                             |


Every metric label has a hover tooltip with a detailed technical explanation (methodology, what factors bound the metric, how to interpret the value, actionable guidance).

### 3.3 Export Formats


| Format   | Audience                                                   | Use Case                                                                                                           |
| -------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **PDF**  | Management, architecture review boards, vendor evaluations | Branded cover page, executive summary, per-test detail with descriptions and charts. Attach to decision documents. |
| **CSV**  | Data engineers, analysts comparing servers                 | Flat `section, metric, value, unit` rows. Pivot in Excel/Sheets or `pd.concat()` in Jupyter to compare N servers.  |
| **JSON** | Developers, CI/CD pipelines                                | Full structured result object. Parse programmatically for automated threshold checks or trend analysis.            |


---

## 4. Test Suite Specification

### 4.1 I/O Performance Tests


| Test             | SQL Pattern                                        | What It Measures                                                    | Derived Metrics                                                                                          |
| ---------------- | -------------------------------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Random Read      | `SELECT payload WHERE id = :random_pk`             | Point-lookup throughput via B-tree index seek + buffer pool fetch   | IOPS (ops/sec), peak IOPS, latency percentiles, thread scaling, Amdahl's serial fraction, IOPS over time |
| Random Write     | `UPDATE SET payload = :data WHERE id = :random_pk` | Single-row update throughput including WAL flush + lock acquisition | IOPS (ops/sec), peak IOPS, latency percentiles, error rate, thread scaling, IOPS over time               |
| Sequential Scan  | `SELECT * FROM test_table`                         | Full table scan bandwidth — each row returned = 1 operation         | IOPS (rows/sec), MB/s throughput, performance over time                                                  |
| Mixed Read/Write | 70% reads / 30% writes concurrent                  | Read/write interference factor under realistic OLTP contention      | Combined IOPS, latency under contention, IOPS over time                                                  |
| Bulk Insert      | Multi-row `INSERT ... VALUES`                      | Sustained write ingestion — each row inserted = 1 operation         | IOPS (rows/sec), MB/s, performance over time                                                             |
| Pool Stress      | `SELECT 1` at 1→5→10→…→200 concurrency             | Connection pool saturation point and checkout latency degradation   | Per-burst p50/p99, failure threshold                                                                     |


**Thread Scaling Methodology:** Each I/O test runs at multiple concurrency levels (configurable per preset). At each level, IOPS and latency are measured. The resulting curve yields:

- **Saturation point** — the thread count where IOPS plateaus (within 5%)
- **Optimal threads** — the thread count with the best IOPS-per-thread ratio
- **Amdahl's serial fraction** — `f = (1/speedup − 1/N) / (1 − 1/N)`, quantifying the non-parallelizable portion

### 4.2 Stress & Integrity Tests


| Test                    | Methodology                                                                             | Pass Criteria                                   |
| ----------------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------- |
| Data Integrity          | Write random pages with SHA-256 checksums → read sample back → recompute hash → compare | Zero hash mismatches                            |
| Concurrent Transactions | N concurrent balance transfers → verify SUM(balances) conserved                         | Balance drift < $0.01                           |
| Isolation Levels        | Execute known anomaly patterns at each isolation level → check if prevented             | Database behavior matches documented guarantees |


### 4.3 Analytical Queries

- **Schema:** 8-table star schema (zone, country, vendor, buyer, product, inventory, sale_order, order_detail)
- **Data generation:** In-database via multi-row INSERTs, configurable scale factor
- **Queries:** 16 original decision-support queries covering joins, subqueries, aggregation, CTEs, ranking
- **Execution:** Each query runs cold (no plan cache) then warm (cached plan) to measure compilation overhead
- **Composite metrics:** Geometric mean (cross-magnitude fairness), Power score (throughput normalized by scale factor)

### 4.4 Network Profiling


| Measurement        | Method                                       | Purpose                                 |
| ------------------ | -------------------------------------------- | --------------------------------------- |
| DNS Resolution     | Timed hostname lookup                        | Detect slow resolvers, CNAME chains     |
| Ping (SELECT 1)    | Round-trip with full percentile distribution | Establish the latency floor             |
| Connection Setup   | New engine → connect → close, timed          | Measure TCP + TLS + auth overhead       |
| First Byte         | Execute 1000-row query, time to first row    | Measure query compilation + initial I/O |
| Upload Bandwidth   | Timed bulk INSERT                            | Client-to-database throughput           |
| Download Bandwidth | Timed bulk SELECT                            | Database-to-client throughput           |


---

## 5. Configuration & Presets

### 5.1 Preset Profiles


| Parameter           | Smoke (~2–5 min) | Standard (~10–20 min) | Full Stress (~30–90 min) |
| ------------------- | ---------------- | --------------------- | ------------------------ |
| Test table rows     | 2,000            | 200,000               | 500,000                  |
| I/O ops per test    | 100              | 50,000                | 100,000                  |
| Thread sweep        | 1, 4             | 1, 2, 4, 8, 16        | 1, 2, 4, 8, 16, 32       |
| Analytical queries  | 2 (Q01, Q06)     | All 16                | All 16, 2 iterations     |
| Scale factor        | 0.01             | 0.05                  | 0.1                      |
| Integrity cycles    | 50               | 1,000                 | 2,000                    |
| Concurrent accounts | 100              | 2,000                 | 5,000                    |
| Network pings       | 10               | 50                    | 100                      |
| Bandwidth rows      | 1,000            | 3,000                 | 5,000                    |
| Isolation tests     | No               | No                    | Yes                      |
| Pool stress         | No               | No                    | Yes (burst 1→200)        |


### 5.2 I/O Block Size

Configurable binary payload per row: 1 KB, 4 KB, 8 KB (default), 64 KB.

- Small blocks (1–4 KB): IOPS-bound. Network RTT and index seek dominate.
- Default (8 KB): Matches common database page sizes. Balances IOPS and bandwidth.
- Large blocks (64 KB): Throughput-bound. Stresses buffer pool, TLS framing, TCP windowing.

---

## 6. Supported Platforms

### 6.1 Database Engines


| Engine     | Driver            | Wire Protocol                     | Default Port |
| ---------- | ----------------- | --------------------------------- | ------------ |
| SQL Server | `pymssql`         | TDS over TLS                      | 1433         |
| PostgreSQL | `psycopg` (libpq) | PostgreSQL wire protocol over TLS | 5432         |
| MySQL      | `pymysql`         | MySQL wire protocol over TLS      | 3306         |


### 6.2 Cloud Services


| Provider        | Fully Supported                                                 | Supported with Configuration                                       |
| --------------- | --------------------------------------------------------------- | ------------------------------------------------------------------ |
| **Azure**       | Azure SQL Database, Azure DB for PostgreSQL/MySQL               | Azure SQL Managed Instance (requires public endpoint on port 3342) |
| **AWS**         | RDS (PostgreSQL, MySQL, SQL Server), Aurora (PostgreSQL, MySQL) | —                                                                  |
| **GCP**         | Cloud SQL (PostgreSQL, MySQL, SQL Server), AlloyDB              | —                                                                  |
| **Self-hosted** | Any publicly reachable PostgreSQL, MySQL, or SQL Server         | —                                                                  |


### 6.3 Required Database Permissions


| Permission                | Purpose                               |
| ------------------------- | ------------------------------------- |
| CREATE TABLE              | Test table setup                      |
| INSERT, UPDATE, SELECT    | All benchmark operations              |
| DROP TABLE                | Pre-run cleanup and post-run teardown |
| BEGIN / COMMIT / ROLLBACK | Transaction and isolation tests       |


No sysadmin, superuser, ALTER, or GRANT required.

---

## 7. Security


| Concern                     | Mitigation                                                                                                                                                                                                                                                                                                  |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web authentication          | Login required to access the benchmark UI. Credentials stored as SHA-256 hashes with salt — plaintext password never appears in code. Sessions use `httponly` cookies with 24-hour expiry. All API endpoints and WebSocket connections require a valid session.                                             |
| Database credential storage | Target database credentials held in memory only for the benchmark duration. Never written to disk or logs. Discarded on session end.                                                                                                                                                                        |
| Data isolation              | Data Bench only operates on its own tables (`sqlio_random_io`, `sqliosim_accounts`, `sqliosim_isolation_test`, and DSB schema tables: `zone`, `country`, `vendor`, `buyer`, `product`, `inventory`, `sale_order`, `order_detail`). User tables are never accessed. All test tables are dropped on teardown. |
| Network transport           | Database connections use native wire protocol (TLS-encrypted by default on all major cloud providers). Web UI served over HTTPS via the cloud platform's ingress (Azure Container Apps, Cloud Run, etc.).                                                                                                   |
| Agent installation          | None. Data Bench is a standard SQL client. Nothing is installed on the target server.                                                                                                                                                                                                                       |
| Production safety           | Users are instructed to provision a dedicated test server. Production databases should never be targeted.                                                                                                                                                                                                   |


---

## 8. Deployment

### 8.1 Container Specification

Data Bench deploys as a single Docker container on any container platform. The same image runs on every cloud in every region.


| Parameter | Value               | Rationale                                                                                |
| --------- | ------------------- | ---------------------------------------------------------------------------------------- |
| Port      | 8000                | FastAPI / Uvicorn                                                                        |
| Memory    | 2 Gi                | Comfortable headroom for concurrent tests and analytical query result sets               |
| CPU       | 1 vCPU              | Benchmarks are I/O-bound (waiting on database), not CPU-bound                            |
| Replicas  | 1 (no scale-out)    | Sessions and run state are in-memory; multi-replica would require external session store |
| Auth      | Session-based login | SHA-256 hashed credentials with httponly cookie                                          |
| WebSocket | Required            | Live progress streaming during benchmark runs                                            |


### 8.2 Supported Platforms


| Platform                 | Deployment Method                                                    | Notes                                                                                                 |
| ------------------------ | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **Azure Container Apps** | `deploy-azure.ps1` script (ACR build + Container App)                | Tested. WebSocket support via `--transport auto`. Set proxy read timeout for long-running benchmarks. |
| **Google Cloud Run**     | `gcloud run deploy` with `--allow-unauthenticated`                   | WebSocket support enabled by default. Set `--timeout 3600` for Full Stress preset.                    |
| **AWS App Runner**       | ECR push + App Runner service                                        | WebSocket support available. Configure health check path to `/`.                                      |
| **Self-hosted Docker**   | `docker build -t cloudbench . && docker run -p 8000:8000 cloudbench` | Any Linux host with outbound network access to the target database.                                   |


### 8.3 Networking Requirements

- **Outbound only.** Data Bench makes outbound TCP connections to the target database. No inbound ports are needed beyond the platform's HTTPS ingress.
- **VPC / VNet egress.** For databases on private networks, use the platform's VNet/VPC integration (Azure VNet, GCP VPC connector, AWS VPC connector).
- **IP allowlisting.** If the target database firewall requires specific source IPs, configure static outbound IP via the cloud platform (Azure NAT Gateway, GCP Cloud NAT, AWS NAT Gateway).

### 8.4 Multi-Region Strategy

Data Bench is designed to be deployed in every region where a database is being evaluated. Deploying the benchmark container in the same region as the target database isolates database performance from cross-region network latency. To compare regions, deploy Data Bench in each region and run identical presets against identical database tiers — the Network Profiling test will capture the remaining intra-region network characteristics.

---

## 9. Metrics Reference

### 9.1 Latency Metrics


| Metric                   | Definition                                                                             |
| ------------------------ | -------------------------------------------------------------------------------------- |
| p50 (Median)             | 50% of operations complete faster. The "typical" experience.                           |
| p99                      | 99th percentile. Only 1% of operations are slower. Tail latency indicator.             |
| p99.9                    | 99.9th percentile. Extreme outlier latency.                                            |
| Jitter                   | Mean absolute difference between consecutive latency measurements. Measures stability. |
| Coefficient of Variation | (stddev / mean) × 100. Relative variability independent of absolute latency.           |


### 9.2 Throughput Metrics


| Metric     | Definition                                                                                                                                                       |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| IOPS       | Completed operations per wall-clock second across all threads. Elapsed time is frozen when the test ends via `ThroughputTracker.stop()` to prevent timing drift. |
| MB/s       | (total_bytes_transferred) / elapsed_time at the application layer.                                                                                               |
| TPS        | Committed transactions per second (for transactional tests).                                                                                                     |
| Error Rate | Failed operations / total attempted, as a percentage.                                                                                                            |
| Peak IOPS  | Highest IOPS observed across all thread counts in a scaling sweep. Reported with the thread count at which it occurred.                                          |


**IOPS Calculation per Test:**


| Test              | 1 Operation =                 | IOPS Represents      |
| ----------------- | ----------------------------- | -------------------- |
| Random Read/Write | 1 `SELECT`/`UPDATE` by PK     | Point operations/sec |
| Sequential Scan   | 1 row returned from full scan | Rows scanned/sec     |
| Bulk Insert       | 1 row inserted                | Rows inserted/sec    |
| Mixed Read/Write  | 1 read or write operation     | Combined ops/sec     |
| Pool Stress       | 1 `SELECT 1` query            | Queries/sec          |


For sequential scan and bulk insert, each row is the fundamental unit of work. This ensures IOPS and MB/s are consistent: `IOPS × block_size ≈ MB/s × 1,048,576`.

**Per-Second Time Series:**

The `ThroughputTracker` snapshots every 1 second, recording per-interval (instantaneous) and cumulative IOPS and MB/s. This data powers the Performance Over Time charts in the web UI.

### 9.3 Scalability Metrics


| Metric                   | Definition                                                                                              |
| ------------------------ | ------------------------------------------------------------------------------------------------------- |
| Peak IOPS                | Highest absolute IOPS observed across all thread counts. The maximum throughput the database delivered. |
| Saturation Point         | Thread count where IOPS plateaus (within 5% of peak).                                                   |
| Optimal Threads          | Thread count with the highest IOPS-per-thread ratio.                                                    |
| Amdahl's Serial Fraction | Non-parallelizable portion of the workload (0.0 = perfectly parallel, 1.0 = fully serial).              |


### 9.4 Performance Ratings

Metric cards display color-coded ratings with context-sensitive thresholds:

**Random Read/Write, Mixed Workload:** > 2,000 EXCELLENT, > 500 GOOD, > 100 FAIR, ≤ 100 LOW

**Sequential Scan, Bulk Insert (rows/sec):** > 50,000 EXCELLENT, > 10,000 GOOD, > 1,000 FAIR, ≤ 1,000 LOW

**Latency:** < 5 ms EXCELLENT, < 20 ms GOOD, < 100 ms FAIR, ≥ 100 ms HIGH

### 9.4 Composite Metrics


| Metric         | Formula                        | Interpretation                                                                             |
| -------------- | ------------------------------ | ------------------------------------------------------------------------------------------ |
| Geometric Mean | `exp(Σ ln(duration_i) / N)`    | Composite query duration. Fairly weights queries across magnitude ranges. Lower is better. |
| Power Score    | `(3600 × SF) / Geometric_Mean` | Normalized throughput. Higher is better. Enables cross-SF comparison.                      |


---

## 10. Future Considerations


| Area                            | Description                                                                                                                                  | Status / Priority |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| **Authentication**              | Basic session-based login with hashed credentials                                                                                            | **Done** (v1.1)   |
| **Multi-cloud deployment**      | Azure Container Apps deployment script; platform-agnostic Dockerfile                                                                         | **Done** (v1.1)   |
| **Multi-region deployment**     | Automated deploy scripts for every major region on Azure, AWS, GCP — stamp out identical Data Bench instances co-located with test databases | High              |
| **OAuth / SSO**                 | Replace hardcoded credentials with Azure AD, Google, or GitHub OAuth for multi-tenant SaaS deployment                                        | High (for SaaS)   |
| **Scheduled benchmarks**        | Cron-triggered runs for regression detection over time                                                                                       | Medium            |
| **Historical comparison**       | Store results and show trend charts across runs (requires persistent storage)                                                                | Medium            |
| **Custom queries**              | Allow users to upload their own SQL queries for benchmarking                                                                                 | Medium            |
| **Cost estimation**             | Correlate benchmark results with cloud pricing APIs to estimate $/IOPS and $/query                                                           | Medium            |
| **Result sharing**              | Shareable links to benchmark results without file download                                                                                   | Low               |
| **Read replica testing**        | Benchmark read replicas and compare to primary                                                                                               | Low               |
| **Cross-region latency matrix** | Deploy in N regions, run benchmarks against a single database from each, produce a latency heatmap                                           | Low               |


---

## 11. Glossary


| Term                  | Definition                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------------- |
| **IOPS**              | Input/Output Operations Per Second                                                           |
| **WAL**               | Write-Ahead Log — the durability mechanism for database writes                               |
| **TLS**               | Transport Layer Security — encryption for network connections                                |
| **DTU**               | Database Transaction Unit — Azure's blended performance metric                               |
| **vCore**             | Virtual CPU core — cloud compute unit                                                        |
| **Buffer Pool**       | In-memory cache of database pages                                                            |
| **B-tree**            | Balanced tree index structure used for primary key lookups                                   |
| **Amdahl's Law**      | Theoretical limit on speedup from parallelization based on the serial fraction of a workload |
| **Scale Factor (SF)** | Multiplier for analytical benchmark dataset size                                             |
| **Power Score**       | Normalized analytical throughput metric for cross-comparison                                 |
| **NSG**               | Network Security Group — Azure firewall rules for VNet resources                             |
| **VPC**               | Virtual Private Cloud — isolated network in AWS/GCP                                          |


