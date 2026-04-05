"""Database connection factory with latency tracking, pool monitoring, and validation."""

from __future__ import annotations

import time
import socket
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text, pool as sa_pool
from sqlalchemy.engine import Engine


DB_TYPE_DIALECTS = {
    "Azure SQL Database": "mssql+pymssql",
    "Azure SQL Managed Instance": "mssql+pymssql",
    "Azure SQL Hyperscale": "mssql+pymssql",
    "Amazon RDS (PostgreSQL)": "postgresql+psycopg",
    "Amazon Aurora (PostgreSQL)": "postgresql+psycopg",
    "Amazon Aurora (MySQL)": "mysql+pymysql",
    "Google Cloud SQL (PostgreSQL)": "postgresql+psycopg",
    "Google Cloud SQL (MySQL)": "mysql+pymysql",
    "Generic PostgreSQL": "postgresql+psycopg",
    "Generic MySQL": "mysql+pymysql",
    "Generic SQL Server": "mssql+pymssql",
}

DB_TYPE_PORTS = {
    "Azure SQL Database": "1433",
    "Azure SQL Managed Instance": "3342",
    "Azure SQL Hyperscale": "1433",
    "Amazon RDS (PostgreSQL)": "5432",
    "Amazon Aurora (PostgreSQL)": "5432",
    "Amazon Aurora (MySQL)": "3306",
    "Google Cloud SQL (PostgreSQL)": "5432",
    "Google Cloud SQL (MySQL)": "3306",
    "Generic PostgreSQL": "5432",
    "Generic MySQL": "3306",
    "Generic SQL Server": "1433",
}


@dataclass
class ConnectionConfig:
    dialect: str
    host: str
    port: int
    database: str
    username: str
    password: str
    driver_options: dict = field(default_factory=dict)
    pool_size: int = 20
    max_overflow: int = 40
    ssl_mode: Optional[str] = "require"

    @property
    def url(self) -> str:
        user_enc = quote_plus(self.username)
        pass_enc = quote_plus(self.password)
        base = f"{self.dialect}://{user_enc}:{pass_enc}@{self.host}:{self.port}/{self.database}"
        opts = dict(self.driver_options)
        if self.ssl_mode and ("postgres" in self.dialect or "psycopg" in self.dialect):
            opts["sslmode"] = self.ssl_mode
        if opts:
            qs = "&".join(f"{k}={v}" for k, v in opts.items())
            return f"{base}?{qs}"
        return base

    @classmethod
    def from_dict(cls, d: dict) -> "ConnectionConfig":
        return cls(
            dialect=d.get("dialect", "postgresql+psycopg"),
            host=d["host"],
            port=int(d.get("port", 5432)),
            database=d.get("database", "benchmarks"),
            username=d["username"],
            password=d["password"],
            driver_options=d.get("driver_options", {}),
            pool_size=int(d.get("pool_size", 20)),
            max_overflow=int(d.get("max_overflow", 40)),
            ssl_mode=d.get("ssl_mode", "require"),
        )


def _gather_mssql_metadata_sa(conn) -> dict:
    """Query Azure SQL / SQL Server metadata via a SQLAlchemy connection."""
    metadata = {}
    try:
        ee = conn.execute(text("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT)")).scalar()
        metadata["engine_edition"] = int(ee) if ee else None
        pv = conn.execute(text("SELECT CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(128))")).scalar()
        metadata["product_version"] = str(pv) if pv else ""

        try:
            row = conn.execute(text("SELECT cpu_count, committed_target_kb FROM sys.dm_os_sys_info")).fetchone()
            if row:
                metadata["vcores"] = row[0]
                metadata["memory_gb"] = round(row[1] / 1048576, 1) if row[1] else None
        except Exception:
            pass

        try:
            metadata["collation"] = str(
                conn.execute(text("SELECT CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS NVARCHAR(128))")).scalar() or "")
        except Exception:
            pass

        try:
            cl = conn.execute(text("SELECT compatibility_level FROM sys.databases WHERE name = DB_NAME()")).scalar()
            if cl:
                metadata["compatibility_level"] = int(cl)
        except Exception:
            pass

        engine_ed = metadata.get("engine_edition")
        if engine_ed == 5:
            metadata["edition"] = str(
                conn.execute(text("SELECT CAST(DATABASEPROPERTYEX(DB_NAME(), 'Edition') AS NVARCHAR(128))")).scalar() or "")
            metadata["service_objective"] = str(
                conn.execute(text("SELECT CAST(DATABASEPROPERTYEX(DB_NAME(), 'ServiceObjective') AS NVARCHAR(128))")).scalar() or "")
            try:
                max_bytes = conn.execute(text(
                    "SELECT CAST(DATABASEPROPERTYEX(DB_NAME(), 'MaxSizeInBytes') AS BIGINT)")).scalar()
                if max_bytes and max_bytes > 0:
                    metadata["max_size_gb"] = round(max_bytes / (1024 ** 3), 1)
            except Exception:
                pass
            try:
                sz = conn.execute(text("SELECT SUM(size) * 8.0 / 1024 FROM sys.database_files")).scalar()
                if sz:
                    metadata["current_size_mb"] = round(float(sz), 1)
            except Exception:
                pass
            try:
                row = conn.execute(text(
                    "SELECT edition, service_objective, elastic_pool_name "
                    "FROM sys.database_service_objectives WHERE database_id = DB_ID()")).fetchone()
                if row:
                    metadata["elastic_pool"] = row[2]
            except Exception:
                metadata["elastic_pool"] = None
        elif engine_ed == 8:
            metadata["edition"] = "Managed Instance"
            try:
                row = conn.execute(text(
                    "SELECT TOP 1 sku, hardware_generation, "
                    "reserved_storage_mb, storage_space_used_mb, virtual_core_count "
                    "FROM sys.server_resource_stats ORDER BY start_time DESC")).fetchone()
                if row:
                    metadata["service_objective"] = str(row[0] or "")
                    metadata["hardware_generation"] = str(row[1] or "")
                    if row[2]:
                        metadata["max_size_gb"] = round(row[2] / 1024, 1)
                    if row[3]:
                        metadata["current_size_mb"] = round(float(row[3]), 1)
                    if row[4]:
                        metadata["vcores"] = row[4]
            except Exception:
                try:
                    metadata["service_objective"] = str(
                        conn.execute(text("SELECT CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128))")).scalar() or "")
                except Exception:
                    pass
            try:
                sz = conn.execute(text("SELECT SUM(size) * 8.0 / 1024 FROM sys.database_files")).scalar()
                if sz:
                    metadata["current_size_mb"] = round(float(sz), 1)
            except Exception:
                pass
    except Exception:
        pass
    return metadata


def _gather_mysql_metadata(conn) -> dict:
    """Query MySQL / Cloud SQL instance metadata via a SQLAlchemy connection."""
    metadata = {}
    try:
        def _var(name: str):
            row = conn.execute(text(f"SHOW VARIABLES LIKE '{name}'")).fetchone()
            return row[1] if row else None

        def _status(name: str):
            row = conn.execute(text(f"SHOW STATUS LIKE '{name}'")).fetchone()
            return row[1] if row else None

        ver_comment = _var("version_comment") or ""
        metadata["version_comment"] = ver_comment
        is_cloud = any(kw in ver_comment.lower() for kw in ("google", "cloud sql", "rds", "aurora", "azure"))
        if is_cloud:
            metadata["edition"] = ver_comment

        buf = _var("innodb_buffer_pool_size")
        if buf:
            buf_bytes = int(buf)
            metadata["innodb_buffer_pool_mb"] = round(buf_bytes / (1024 * 1024))
            metadata["memory_gb"] = round(buf_bytes / (1024 ** 3), 1)

        max_conns = _var("max_connections")
        if max_conns:
            metadata["max_connections"] = int(max_conns)

        collation = _var("collation_server")
        if collation:
            metadata["collation"] = collation

        charset = _var("character_set_server")
        if charset:
            metadata["character_set"] = charset

        innodb_ver = _var("innodb_version")
        if innodb_ver:
            metadata["innodb_version"] = innodb_ver

        read_only = _var("read_only")
        if read_only:
            metadata["read_only"] = read_only.upper() == "ON"

        io_cap = _var("innodb_io_capacity")
        if io_cap:
            metadata["innodb_io_capacity"] = int(io_cap)
        io_cap_max = _var("innodb_io_capacity_max")
        if io_cap_max:
            metadata["innodb_io_capacity_max"] = int(io_cap_max)

        rio = _var("innodb_read_io_threads")
        if rio:
            metadata["innodb_read_io_threads"] = int(rio)
        wio = _var("innodb_write_io_threads")
        if wio:
            metadata["innodb_write_io_threads"] = int(wio)

        bp_instances = _var("innodb_buffer_pool_instances")
        if bp_instances:
            metadata["innodb_buffer_pool_instances"] = int(bp_instances)

        redo = _var("innodb_redo_log_capacity")
        if not redo:
            redo = _var("innodb_log_file_size")
        if redo:
            metadata["innodb_redo_log_mb"] = round(int(redo) / (1024 * 1024))

        flush = _var("innodb_flush_log_at_trx_commit")
        if flush:
            metadata["innodb_flush_log_at_trx_commit"] = int(flush)

        tmp = _var("tmp_table_size")
        if tmp:
            metadata["tmp_table_size_mb"] = round(int(tmp) / (1024 * 1024))

        try:
            row = conn.execute(text(
                "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 1) "
                "FROM information_schema.tables WHERE table_schema = DATABASE()")).fetchone()
            if row and row[0]:
                metadata["current_size_mb"] = float(row[0])
        except Exception:
            pass

        uptime = _status("Uptime")
        if uptime:
            metadata["uptime_hours"] = round(int(uptime) / 3600, 1)

    except Exception:
        pass
    return metadata


def _gather_pg_metadata(conn) -> dict:
    """Query PostgreSQL / Cloud SQL instance metadata via a SQLAlchemy connection."""
    metadata = {}
    try:
        ver = conn.execute(text("SHOW server_version")).scalar() or ""
        metadata["product_version"] = ver

        try:
            metadata["max_connections"] = int(
                conn.execute(text("SHOW max_connections")).scalar() or 0)
        except Exception:
            pass

        try:
            metadata["shared_buffers"] = conn.execute(
                text("SHOW shared_buffers")).scalar() or ""
        except Exception:
            pass

        try:
            metadata["work_mem"] = conn.execute(
                text("SHOW work_mem")).scalar() or ""
        except Exception:
            pass

        try:
            metadata["effective_cache_size"] = conn.execute(
                text("SHOW effective_cache_size")).scalar() or ""
        except Exception:
            pass

        try:
            metadata["collation"] = conn.execute(text(
                "SELECT datcollate FROM pg_database WHERE datname = current_database()")).scalar() or ""
        except Exception:
            pass

        try:
            row = conn.execute(text(
                "SELECT pg_size_pretty(pg_database_size(current_database())), "
                "pg_database_size(current_database())")).fetchone()
            if row:
                metadata["current_size_pretty"] = row[0]
                metadata["current_size_mb"] = round(row[1] / (1024 * 1024), 1)
        except Exception:
            pass

        ver_lower = ver.lower()
        if "cloud" in ver_lower or "cloudsql" in ver_lower:
            metadata["edition"] = "Google Cloud SQL"
        elif "rds" in ver_lower:
            metadata["edition"] = "Amazon RDS"

    except Exception:
        pass
    return metadata


@dataclass
class PoolStats:
    """Snapshot of connection pool state."""
    size: int = 0
    checked_in: int = 0
    checked_out: int = 0
    overflow: int = 0
    high_water_checked_out: int = 0


@dataclass
class ValidationResult:
    success: bool
    server_version: str = ""
    ping_ms: float = 0.0
    max_connections: int = 0
    error: str = ""
    dialect_family: str = ""
    database_created: bool = False
    server_metadata: dict = field(default_factory=dict)


class DatabaseConnection:
    """Central database connection manager with built-in instrumentation."""

    def __init__(self, config: ConnectionConfig):
        self.config = config
        connect_args = self._build_connect_args()
        engine_kwargs: dict[str, Any] = {
            "pool_size": config.pool_size,
            "max_overflow": config.max_overflow,
            "pool_pre_ping": True,
        }
        if "pymssql" in config.dialect:
            import pymssql as _pymssql
            cfg = config
            def _mssql_creator():
                return _pymssql.connect(
                    server=cfg.host,
                    port=cfg.port,
                    user=cfg.username,
                    password=cfg.password,
                    database=cfg.database,
                    tds_version="7.3",
                    login_timeout=15,
                    timeout=30,
                )
            engine_kwargs["creator"] = _mssql_creator
            self.engine: Engine = create_engine(
                config.url, **engine_kwargs,
            )
        else:
            engine_kwargs["connect_args"] = connect_args
            self.engine: Engine = create_engine(
                config.url, **engine_kwargs,
            )
        self.pool_stats = PoolStats()
        self._attach_instrumentation()

    def _build_connect_args(self) -> dict:
        args: dict[str, Any] = {}
        if "pymssql" in self.config.dialect:
            args["tds_version"] = "7.3"
            args["login_timeout"] = 15
            args["timeout"] = 30
        elif "pymysql" in self.config.dialect:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            args["ssl"] = ctx
        return args

    def _attach_instrumentation(self):
        @event.listens_for(self.engine, "before_cursor_execute")
        def _before(conn, cursor, stmt, params, context, executemany):
            conn.info["_q_start"] = time.perf_counter()

        @event.listens_for(self.engine, "after_cursor_execute")
        def _after(conn, cursor, stmt, params, context, executemany):
            conn.info["_q_latency"] = time.perf_counter() - conn.info.get("_q_start", time.perf_counter())

        @event.listens_for(self.engine, "checkout")
        def _checkout(dbapi_conn, connection_record, connection_proxy):
            p = self.engine.pool
            co = p.checkedout()
            self.pool_stats.checked_out = co
            if co > self.pool_stats.high_water_checked_out:
                self.pool_stats.high_water_checked_out = co

        @event.listens_for(self.engine, "checkin")
        def _checkin(dbapi_conn, connection_record):
            self.pool_stats.checked_out = self.engine.pool.checkedout()

    @property
    def dialect_family(self) -> str:
        d = self.config.dialect.lower()
        if "postgres" in d or "psycopg" in d:
            return "postgresql"
        if "mysql" in d or "pymysql" in d:
            return "mysql"
        if "mssql" in d or "pyodbc" in d:
            return "mssql"
        return "unknown"

    def _try_auto_create_database(self) -> tuple[bool, Optional[str]]:
        """If the target database doesn't exist, connect to a default DB and create it.

        Returns (created, error) — created is True if a new database was made,
        error is None on success or an error string on failure.
        """
        family = self.dialect_family
        if family not in ("mssql", "postgresql", "mysql"):
            return False, None

        target_db = self.config.database

        if family == "mssql":
            return self._auto_create_mssql(target_db)

        if family == "postgresql":
            fallback_db = "postgres"
        else:
            fallback_db = "mysql"

        fallback_url = self.config.url.replace(
            f"@{self.config.host}:{self.config.port}/{target_db}",
            f"@{self.config.host}:{self.config.port}/{fallback_db}",
        )
        try:
            fb_engine = create_engine(
                fallback_url,
                poolclass=sa_pool.NullPool,
                connect_args=self._build_connect_args(),
                isolation_level="AUTOCOMMIT",
            )
            created = False
            with fb_engine.connect() as conn:
                if family == "postgresql":
                    exists = conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :db"),
                        {"db": target_db},
                    ).scalar()
                else:
                    exists = conn.execute(
                        text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :db"),
                        {"db": target_db},
                    ).scalar()

                if not exists:
                    q = {"mysql": f"CREATE DATABASE `{target_db}`",
                         "postgresql": f'CREATE DATABASE "{target_db}"'}
                    conn.execute(text(q[family]))
                    created = True

            fb_engine.dispose()
            self._rebuild_engine()
            return created, None
        except Exception as e:
            return False, str(e)

    def _auto_create_mssql(self, target_db: str) -> tuple[bool, Optional[str]]:
        """Auto-create for SQL Server / SQL MI using raw pymssql.

        Connects without specifying a database so it works on SQL MI public
        endpoints where direct access to 'master' via URL may be blocked.
        """
        try:
            import pymssql
            conn = pymssql.connect(
                server=self.config.host,
                port=self.config.port,
                user=self.config.username,
                password=self.config.password,
                tds_version="7.3",
                login_timeout=30,
                autocommit=True,
            )
            cursor = conn.cursor()
            cursor.execute("SELECT DB_ID(%s)", (target_db,))
            row = cursor.fetchone()
            exists = row and row[0] is not None

            created = False
            if not exists:
                cursor.execute(f"CREATE DATABASE [{target_db}]")
                created = True

            cursor.close()
            conn.close()
            self._rebuild_engine()
            return created, None
        except Exception as e:
            return False, str(e)

    def _rebuild_engine(self):
        """Dispose the current engine and create a fresh one."""
        self.engine.dispose()
        self.engine = create_engine(
            self.config.url,
            pool_size=self.config.pool_size,
            max_overflow=self.config.max_overflow,
            pool_pre_ping=True,
            connect_args=self._build_connect_args(),
        )

    def _enable_mssql_snapshot_isolation(self):
        """Enable snapshot isolation on the target MSSQL database if not already on.

        Skipped for Azure SQL MI / Azure SQL DB where READ_COMMITTED_SNAPSHOT
        is already ON by default and ALTER DATABASE can hang or require
        elevated permissions on the public endpoint.
        """
        if self.dialect_family != "mssql":
            return
        try:
            import pymssql
            conn = pymssql.connect(
                server=self.config.host,
                port=self.config.port,
                user=self.config.username,
                password=self.config.password,
                database=self.config.database,
                tds_version="7.3",
                login_timeout=15,
                autocommit=True,
            )
            cursor = conn.cursor()
            cursor.execute("SELECT SERVERPROPERTY('EngineEdition')")
            edition = cursor.fetchone()[0]
            # 5 = Azure SQL DB, 8 = Azure SQL MI — snapshot is on by default
            if edition in (5, 8):
                cursor.close()
                conn.close()
                return
            db = self.config.database
            cursor.execute(f"ALTER DATABASE [{db}] SET ALLOW_SNAPSHOT_ISOLATION ON")
            cursor.execute(f"ALTER DATABASE [{db}] SET READ_COMMITTED_SNAPSHOT ON")
            cursor.close()
            conn.close()
        except Exception:
            pass

    def validate(self) -> ValidationResult:
        """Test connectivity and gather server metadata."""
        try:
            with self.engine.connect() as conn:
                t0 = time.perf_counter()
                conn.execute(text("SELECT 1"))
                ping = (time.perf_counter() - t0) * 1000

                version = ""
                max_conns = 0
                family = self.dialect_family

                if family == "postgresql":
                    version = conn.execute(text("SHOW server_version")).scalar() or ""
                    mc = conn.execute(text("SHOW max_connections")).scalar()
                    max_conns = int(mc) if mc else 0
                elif family == "mysql":
                    version = conn.execute(text("SELECT VERSION()")).scalar() or ""
                    mc = conn.execute(text("SHOW VARIABLES LIKE 'max_connections'")).fetchone()
                    max_conns = int(mc[1]) if mc else 0
                elif family == "mssql":
                    version = conn.execute(text("SELECT @@VERSION")).scalar() or ""
                    max_conns = 0
                    self._enable_mssql_snapshot_isolation()

                metadata = {}
                if family == "mssql":
                    metadata = _gather_mssql_metadata_sa(conn)
                elif family == "mysql":
                    metadata = _gather_mysql_metadata(conn)
                elif family == "postgresql":
                    metadata = _gather_pg_metadata(conn)

                return ValidationResult(
                    success=True,
                    server_version=version,
                    ping_ms=ping,
                    max_connections=max_conns,
                    dialect_family=family,
                    server_metadata=metadata,
                )
        except Exception as e:
            err_str = str(e)
            is_db_missing = "18456" in err_str or "40615" in err_str or "does not exist" in err_str.lower() or "3D000" in err_str or "1049" in err_str
            if is_db_missing:
                created, create_err = self._try_auto_create_database()
                if create_err is None:
                    result = self.validate()
                    if result.success and created:
                        result.database_created = True
                    return result
                return ValidationResult(
                    success=False,
                    error=f"Database '{self.config.database}' does not exist and auto-create failed: {create_err}",
                )
            return ValidationResult(success=False, error=err_str)

    def dns_resolve_ms(self) -> float:
        t0 = time.perf_counter()
        try:
            socket.getaddrinfo(self.config.host, self.config.port)
        except socket.gaierror:
            pass
        return (time.perf_counter() - t0) * 1000

    def measure_connection_setup(self) -> float:
        """Time to create a brand-new raw connection (bypassing the pool)."""
        raw_engine = create_engine(
            self.config.url,
            poolclass=sa_pool.NullPool,
            connect_args=self._build_connect_args(),
        )
        t0 = time.perf_counter()
        try:
            conn = raw_engine.connect()
            conn.execute(text("SELECT 1"))
            elapsed = (time.perf_counter() - t0) * 1000
            conn.close()
            return elapsed
        except Exception:
            return -1.0
        finally:
            raw_engine.dispose()

    def dispose(self):
        self.engine.dispose()
