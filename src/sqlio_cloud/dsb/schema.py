"""Decision-support benchmark schema for PostgreSQL, MySQL, and SQL Server.

An original star-schema modelling a global supply-chain / e-commerce domain.
Tables and columns are intentionally distinct from any third-party benchmark
specification.
"""

from __future__ import annotations

import re

DSB_TABLE_ORDER = [
    "zone", "country", "vendor", "buyer",
    "product", "inventory", "sale_order", "order_detail",
]

DSB_DROP_ORDER = list(reversed(DSB_TABLE_ORDER))


def get_create_statements(dialect: str) -> dict[str, str]:
    if dialect == "mssql":
        return _MSSQL_TABLES
    if dialect == "mysql":
        return _MYSQL_TABLES
    return _PG_TABLES


def get_drop_statements(dialect: str) -> dict[str, str]:
    drops = {}
    for t in DSB_DROP_ORDER:
        if dialect == "mssql":
            drops[t] = f"IF OBJECT_ID('{t}', 'U') IS NOT NULL DROP TABLE {t}"
        else:
            drops[t] = f"DROP TABLE IF EXISTS {t} CASCADE"
    return drops


_PG_TABLES = {
    "zone": """
        CREATE TABLE IF NOT EXISTS zone (
            z_id       INTEGER PRIMARY KEY,
            z_name     CHAR(25) NOT NULL,
            z_memo     VARCHAR(152)
        )
    """,
    "country": """
        CREATE TABLE IF NOT EXISTS country (
            ct_id      INTEGER PRIMARY KEY,
            ct_name    CHAR(25) NOT NULL,
            ct_zone_id INTEGER NOT NULL REFERENCES zone(z_id),
            ct_memo    VARCHAR(152)
        )
    """,
    "vendor": """
        CREATE TABLE IF NOT EXISTS vendor (
            v_id         INTEGER PRIMARY KEY,
            v_name       CHAR(25) NOT NULL,
            v_address    VARCHAR(40) NOT NULL,
            v_country_id INTEGER NOT NULL REFERENCES country(ct_id),
            v_phone      CHAR(15) NOT NULL,
            v_balance    DECIMAL(15,2) NOT NULL,
            v_memo       VARCHAR(101)
        )
    """,
    "buyer": """
        CREATE TABLE IF NOT EXISTS buyer (
            b_id         INTEGER PRIMARY KEY,
            b_name       VARCHAR(25) NOT NULL,
            b_address    VARCHAR(40) NOT NULL,
            b_country_id INTEGER NOT NULL REFERENCES country(ct_id),
            b_phone      CHAR(15) NOT NULL,
            b_balance    DECIMAL(15,2) NOT NULL,
            b_segment    CHAR(10) NOT NULL,
            b_memo       VARCHAR(117)
        )
    """,
    "product": """
        CREATE TABLE IF NOT EXISTS product (
            p_id           INTEGER PRIMARY KEY,
            p_name         VARCHAR(55) NOT NULL,
            p_maker        CHAR(25) NOT NULL,
            p_brand        CHAR(10) NOT NULL,
            p_category     VARCHAR(25) NOT NULL,
            p_size         INTEGER NOT NULL,
            p_packaging    CHAR(10) NOT NULL,
            p_retail_price DECIMAL(15,2) NOT NULL,
            p_memo         VARCHAR(23)
        )
    """,
    "inventory": """
        CREATE TABLE IF NOT EXISTS inventory (
            inv_product_id INTEGER NOT NULL REFERENCES product(p_id),
            inv_vendor_id  INTEGER NOT NULL REFERENCES vendor(v_id),
            inv_avail_qty  INTEGER NOT NULL,
            inv_unit_cost  DECIMAL(15,2) NOT NULL,
            inv_memo       VARCHAR(199),
            PRIMARY KEY (inv_product_id, inv_vendor_id)
        )
    """,
    "sale_order": """
        CREATE TABLE IF NOT EXISTS sale_order (
            so_id            INTEGER PRIMARY KEY,
            so_buyer_id      INTEGER NOT NULL REFERENCES buyer(b_id),
            so_status        CHAR(1) NOT NULL,
            so_total         DECIMAL(15,2) NOT NULL,
            so_date          DATE NOT NULL,
            so_priority      CHAR(15) NOT NULL,
            so_clerk         CHAR(15) NOT NULL,
            so_ship_priority INTEGER NOT NULL,
            so_memo          VARCHAR(79)
        )
    """,
    "order_detail": """
        CREATE TABLE IF NOT EXISTS order_detail (
            od_order_id      INTEGER NOT NULL REFERENCES sale_order(so_id),
            od_product_id    INTEGER NOT NULL,
            od_vendor_id     INTEGER NOT NULL,
            od_line_num      INTEGER NOT NULL,
            od_qty           DECIMAL(15,2) NOT NULL,
            od_unit_price    DECIMAL(15,2) NOT NULL,
            od_discount      DECIMAL(15,2) NOT NULL,
            od_tax           DECIMAL(15,2) NOT NULL,
            od_return_flag   CHAR(1) NOT NULL,
            od_line_status   CHAR(1) NOT NULL,
            od_ship_date     DATE NOT NULL,
            od_commit_date   DATE NOT NULL,
            od_receipt_date  DATE NOT NULL,
            od_ship_instruct CHAR(25) NOT NULL,
            od_ship_mode     CHAR(10) NOT NULL,
            od_memo          VARCHAR(44) NOT NULL,
            PRIMARY KEY (od_order_id, od_line_num)
        )
    """,
}

def _strip_fk_refs(sql: str) -> str:
    """Remove inline REFERENCES clauses (e.g. 'REFERENCES zone(z_id)')."""
    return re.sub(r"\s+REFERENCES\s+\w+\(\w+\)", "", sql)

_MYSQL_TABLES = {k: _strip_fk_refs(v) for k, v in _PG_TABLES.items()}

_MSSQL_TABLES = {}
for _t, _sql in _PG_TABLES.items():
    s = _sql.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
    s = _strip_fk_refs(s)
    s = s.replace("CASCADE", "")
    _MSSQL_TABLES[_t] = f"""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = '{_t}')
        {s.strip()}
    """
