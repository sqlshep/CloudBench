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

                return ValidationResult(
                    success=True,
                    server_version=version,
                    ping_ms=ping,
                    max_connections=max_conns,
                    dialect_family=family,
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
