# CloudBench — Internal Documentation

> This file is for developers and operators. For end-user documentation, see [README.md](README.md).

---

## Local Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run the web interface locally
sqlio-cloud web

# Interactive CLI wizard
sqlio-cloud

# Non-interactive (CI/CD)
sqlio-cloud run \
  --host mydb.database.windows.net \
  --port 1433 \
  --database benchmarks \
  --username admin \
  --password secret \
  --dialect "mssql+pymssql" \
  --preset standard
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 |
| Frontend | Single-page HTML/JS, Chart.js |
| Real-time | WebSocket (live progress streaming) |
| Database drivers | `pymssql` (SQL Server), `psycopg` (PostgreSQL), `pymysql` (MySQL) |
| CLI | Click, Rich, Questionary |
| Configuration | YAML with environment variable interpolation |
| Container | Docker, Google Cloud Run |

---

## Architecture Notes

- **Stateless**: Each benchmark run is self-contained. No persistent storage is needed on the Cloud Run side — all test data lives in the target database and is cleaned up after the run.
- **WebSocket support**: Cloud Run supports WebSocket connections for live progress streaming. The `--timeout 3600` flag ensures long-running benchmarks are not terminated prematurely.
- **Concurrency**: Set `--max-instances` to control how many simultaneous benchmarks can run. Each benchmark uses a single container instance.
- **Networking**: The Cloud Run service needs network access to your database. For private databases, use a VPC connector or Cloud Run's direct VPC egress.
- **No database agents**: Nothing is installed on the target database. CloudBench operates entirely through SQL over the network.

---

## Deployment on Google Cloud Run

CloudBench runs as a stateless container on Cloud Run. It makes outbound connections to your database — no inbound access to the database is required beyond standard SQL port connectivity.

```bash
# Build the container
docker build -t cloudbench .

# Push to Artifact Registry
docker tag cloudbench us-central1-docker.pkg.dev/PROJECT_ID/cloudbench/cloudbench:latest
docker push us-central1-docker.pkg.dev/PROJECT_ID/cloudbench/cloudbench:latest

# Deploy to Cloud Run
gcloud run deploy cloudbench \
  --image us-central1-docker.pkg.dev/PROJECT_ID/cloudbench/cloudbench:latest \
  --port 8000 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --max-instances 10 \
  --allow-unauthenticated
```

### Environment Variables

| Variable | Description |
|---|---|
| `DB_HOST` | Database hostname |
| `DB_PORT` | Database port |
| `DB_USER` | Database username |
| `DB_PASSWORD` | Database password |
| `DB_NAME` | Database name |

These can be set in the Cloud Run service configuration or passed at runtime through the web UI.

---

## Project Structure

```
src/sqlio_cloud/
├── cli.py                  # Click CLI with interactive wizard and non-interactive mode
├── config.py               # Configuration loader, preset profiles, env var interpolation
├── connection.py            # SQLAlchemy engine factory, pool stats, dialect detection
├── metrics.py              # Data classes for all result types (latency, throughput, scalability). ThroughputTracker.stop() freezes elapsed time; record_batch() for bulk ops.
├── reporter.py             # Rich terminal reporter and JSON/HTML report generation
├── config/
│   └── default.yaml        # Base configuration with all tunable parameters
├── sqlio/
│   ├── random_io.py        # Random read/write tests with thread scaling
│   ├── sequential_scan.py  # Full table scan throughput test (rows/sec, MB/s)
│   ├── mixed_workload.py   # 70/30 read/write concurrent workload
│   ├── bulk_write.py       # Bulk INSERT throughput test (rows/sec, MB/s)
│   ├── dialect.py          # SQL dialect abstraction (MSSQL, PostgreSQL, MySQL)
│   └── pool_stress.py      # Connection pool stress test
├── sqliosim/
│   ├── integrity.py        # SHA-256 data integrity verification
│   ├── concurrent_stress.py # Balance-transfer concurrency test
│   └── isolation.py        # Isolation level anomaly detection
├── dsb/
│   ├── schema.py           # Decision Support Benchmark DDL (star schema)
│   ├── data_gen.py         # In-database data generation with multi-row INSERTs
│   └── runner.py           # 16 analytical queries with cold/warm execution
├── network/
│   └── profiler.py         # DNS, ping, connection setup, first byte, bandwidth
└── web/
    ├── app.py              # FastAPI backend, WebSocket progress streaming
    └── static/
        └── index.html      # Single-page web UI (HTML/JS/CSS, Chart.js)
```

---

## Building a Windows Executable

CloudBench can be packaged as a standalone Windows `.exe` using PyInstaller. No Python installation is required on the target machine.

### Prerequisites

```bash
pip install -e ".[dev]"
```

### Build

```bash
python build_exe.py
```

This produces `dist/CloudBench/` containing `CloudBench.exe` and all dependencies.

### Running the executable

```bash
# Launch the web UI (default port 8080)
CloudBench.exe web

# Custom port
CloudBench.exe web -p 9000

# Headless CLI mode
CloudBench.exe run --host mydb.database.windows.net --port 1433 ...
```

### Distribution

Zip the entire `dist/CloudBench/` folder and distribute. The recipient extracts it and runs `CloudBench.exe web` — no installation or Python required.

### Notes

- Build on Windows for a Windows executable — PyInstaller does not cross-compile.
- The `cloudbench.spec` file controls what gets bundled (static files, hidden imports, etc.).
- If you add new Python dependencies, add them to `hiddenimports` in `cloudbench.spec`.
- The static web assets (HTML, CSS, JS, sample.pdf) are bundled automatically.

---

## Configuration

Edit `config/default.yaml` or use cloud-specific profiles in `config/profiles/`.

Presets (`smoke`, `standard`, `full`) are defined in `src/sqlio_cloud/config.py` and overlay onto the base config. Custom presets can be added to the `PRESET_PROFILES` dict.

### Adding a New Test

1. Create a new module under the appropriate package (`sqlio/`, `sqliosim/`, `dsb/`, `network/`).
2. Add a result dataclass to `metrics.py` or reuse `SQLIOResult` / `SQLIOSimResult`.
3. Add the test ID to `PRESET_PROFILES` in `config.py`.
4. Add the execution block in `web/app.py` (`_execute_suite_with_ws`) and `cli.py` (`_execute_suite`).
5. Add the test to the `ALL_TESTS` array in `web/static/index.html` with a `tip` description.
6. Add rendering logic in the appropriate `render*` function in `index.html`.
