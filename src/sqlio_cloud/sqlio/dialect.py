"""Dialect-specific SQL fragments for SQLIO tests across PostgreSQL, MySQL, and SQL Server."""

from __future__ import annotations


def create_io_table(dialect: str) -> str:
    if dialect == "mssql":
        return """
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'sqlio_random_io')
            CREATE TABLE sqlio_random_io (
                id BIGINT PRIMARY KEY,
                payload VARBINARY(MAX),
                checksum_val BIGINT
            )
        """
    if dialect == "mysql":
        return """
            CREATE TABLE IF NOT EXISTS sqlio_random_io (
                id BIGINT PRIMARY KEY,
                payload LONGBLOB,
                checksum_val BIGINT
            )
        """
    return """
        CREATE TABLE IF NOT EXISTS sqlio_random_io (
            id BIGINT PRIMARY KEY,
            payload BYTEA,
            checksum_val BIGINT
        )
    """


def _chunked_random_bytes(func: str, block_size: int, joiner: str = "CONCAT") -> str:
    """Build an expression for random bytes, chunking calls that exceed the 1024-byte limit.

    joiner="CONCAT" uses CONCAT(a, b, ...) syntax (MySQL).
    joiner="||" uses (a || b || ...) syntax (PostgreSQL bytea).
    """
    if block_size <= 1024:
        return f"{func}({block_size})"
    full_chunks = block_size // 1024
    remainder = block_size % 1024
    parts = [f"{func}(1024)" for _ in range(full_chunks)]
    if remainder:
        parts.append(f"{func}({remainder})")
    if joiner == "||":
        return f"({' || '.join(parts)})"
    return f"CONCAT({', '.join(parts)})"


def populate_batch(dialect: str, block_size: int = 8192) -> str:
    if dialect == "mssql":
        return f"""
            SET NOCOUNT ON;
            DECLARE @i INT = :offset_start;
            WHILE @i <= :offset_end
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM sqlio_random_io WHERE id = @i)
                    INSERT INTO sqlio_random_io (id, payload, checksum_val)
                    VALUES (@i, CRYPT_GEN_RANDOM({block_size}), @i * 7 + 13);
                SET @i = @i + 1;
            END
        """
    if dialect == "mysql":
        rand_expr = _chunked_random_bytes("RANDOM_BYTES", block_size)
        return f"""
            INSERT IGNORE INTO sqlio_random_io (id, payload, checksum_val)
            SELECT
                n.num,
                {rand_expr},
                n.num * 7 + 13
            FROM (
                SELECT :offset_start + (a.N + b.N * 10 + c.N * 100) AS num
                FROM
                    (SELECT 0 AS N UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
                     UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
                    (SELECT 0 AS N UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
                     UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b,
                    (SELECT 0 AS N UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
                     UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) c
            ) n
            WHERE n.num <= :offset_end
        """
    rand_expr = _chunked_random_bytes("gen_random_bytes", block_size, joiner="||")
    return f"""
        INSERT INTO sqlio_random_io (id, payload, checksum_val)
        SELECT
            gs.n,
            {rand_expr},
            gs.n * 7 + 13
        FROM generate_series(CAST(:offset_start AS integer), CAST(:offset_end AS integer)) AS gs(n)
        ON CONFLICT (id) DO NOTHING
    """


def random_bytes_expr(dialect: str, size_param: str = ":block_size") -> str:
    if dialect == "mssql":
        return f"CRYPT_GEN_RANDOM({size_param})"
    if dialect == "mysql":
        return f"RANDOM_BYTES({size_param})"
    return f"gen_random_bytes({size_param})"


def data_length_func(dialect: str, col: str) -> str:
    if dialect == "mssql":
        return f"DATALENGTH({col})"
    if dialect == "mysql":
        return f"LENGTH({col})"
    return f"octet_length({col})"


def drop_io_table(dialect: str) -> str:
    if dialect == "mssql":
        return "IF OBJECT_ID('sqlio_random_io', 'U') IS NOT NULL DROP TABLE sqlio_random_io"
    return "DROP TABLE IF EXISTS sqlio_random_io"
