"""In-database data generator for the decision-support benchmark.

Generates reference data and randomised fact tables entirely through SQL,
scaled by a configurable scale factor (SF).  No external tools required.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from sqlalchemy import text

from sqlio_cloud.connection import DatabaseConnection
from sqlio_cloud.dsb.schema import get_create_statements, get_drop_statements, DSB_TABLE_ORDER, DSB_DROP_ORDER

ZONES = [
    (0, "AFRICA"), (1, "AMERICA"), (2, "ASIA"), (3, "EUROPE"), (4, "MIDDLE EAST"),
]
COUNTRIES = [
    (0, "ALGERIA", 0), (1, "ARGENTINA", 1), (2, "BRAZIL", 1), (3, "CANADA", 1),
    (4, "EGYPT", 4), (5, "ETHIOPIA", 0), (6, "FRANCE", 3), (7, "GERMANY", 3),
    (8, "INDIA", 2), (9, "INDONESIA", 2), (10, "IRAN", 4), (11, "IRAQ", 4),
    (12, "JAPAN", 2), (13, "JORDAN", 4), (14, "KENYA", 0), (15, "MOROCCO", 0),
    (16, "MOZAMBIQUE", 0), (17, "PERU", 1), (18, "CHINA", 2), (19, "ROMANIA", 3),
    (20, "SAUDI ARABIA", 4), (21, "VIETNAM", 2), (22, "RUSSIA", 3),
    (23, "UNITED KINGDOM", 3), (24, "UNITED STATES", 1),
]
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
PRIORITIES = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
SHIP_MODES = ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"]
SHIP_INSTRUCT = ["DELIVER IN PERSON", "COLLECT COD", "NONE", "TAKE BACK RETURN"]
CATEGORY_ADJ = ["STANDARD", "SMALL", "MEDIUM", "LARGE", "ECONOMY", "PROMO"]
CATEGORY_MAT = ["ANODIZED", "BURNISHED", "PLATED", "POLISHED", "BRUSHED"]
CATEGORY_NOUN = ["TIN", "NICKEL", "BRASS", "STEEL", "COPPER"]
PACKAGING_SZ = ["SM", "MED", "LG", "WRAP", "JUMBO"]
PACKAGING_TY = ["CASE", "BOX", "BAG", "JAR", "PKG", "PACK", "CAN", "DRUM"]
BRANDS = [f"Brand#{i}{j}" for i in range(1, 6) for j in range(1, 6)]


class DSBDataGenerator:
    def __init__(self, db: DatabaseConnection, scale_factor: float = 1.0):
        self.db = db
        self.sf = scale_factor
        self.dialect = db.dialect_family
        self._rng = random.Random(42)

    @property
    def num_vendors(self) -> int:
        return max(1, int(10_000 * self.sf))

    @property
    def num_buyers(self) -> int:
        return max(1, int(150_000 * self.sf))

    @property
    def num_orders(self) -> int:
        return max(1, int(1_500_000 * self.sf))

    @property
    def num_products(self) -> int:
        return max(1, int(200_000 * self.sf))

    def create_schema(self):
        stmts = get_create_statements(self.dialect)
        with self.db.engine.begin() as conn:
            for tbl in DSB_TABLE_ORDER:
                conn.execute(text(stmts[tbl]))

    def drop_schema(self):
        stmts = get_drop_statements(self.dialect)
        with self.db.engine.begin() as conn:
            for tbl in DSB_DROP_ORDER:
                conn.execute(text(stmts[tbl]))

    def generate_all(self, progress_callback=None):
        """Generate all benchmark data.  progress_callback(table_name, pct)."""
        ref_steps = [
            ("zone", self._gen_zones),
            ("country", self._gen_countries),
            ("vendor", self._gen_vendors),
            ("buyer", self._gen_buyers),
            ("product", self._gen_products),
            ("inventory", self._gen_inventory),
        ]
        for i, (name, fn) in enumerate(ref_steps):
            if progress_callback:
                progress_callback(name, int(i / (len(ref_steps) + 1) * 50))
            fn()

        if progress_callback:
            progress_callback("sale_order+order_detail", 50)

        def _order_progress(done, total):
            if progress_callback:
                pct = 50 + int(done / total * 50)
                progress_callback(f"orders ({done}/{total})", pct)

        self._gen_orders_details(progress_callback=_order_progress)

        if progress_callback:
            progress_callback("complete", 100)

    def _gen_zones(self):
        rows = [{"k": z[0], "n": z[1], "m": "generated"} for z in ZONES]
        with self.db.engine.begin() as conn:
            self._multi_insert(conn, "zone", ["z_id", "z_name", "z_memo"], rows)

    def _gen_countries(self):
        rows = [{"k": c[0], "n": c[1], "z": c[2], "m": "generated"} for c in COUNTRIES]
        with self.db.engine.begin() as conn:
            self._multi_insert(conn, "country", ["ct_id", "ct_name", "ct_zone_id", "ct_memo"], rows)

    def _gen_vendors(self):
        cols = ["v_id", "v_name", "v_address", "v_country_id", "v_phone", "v_balance", "v_memo"]
        batch_size = 5000
        for offset in range(0, self.num_vendors, batch_size):
            rows = []
            for i in range(offset + 1, min(offset + batch_size + 1, self.num_vendors + 1)):
                rows.append({
                    "k": i,
                    "n": f"Vendor#{i:09d}",
                    "a": self._rng_string(20, 35),
                    "c": self._rng.randint(0, 24),
                    "p": self._rng_phone(),
                    "b": round(self._rng.uniform(-999.99, 9999.99), 2),
                    "m": self._rng_string(30, 80),
                })
            with self.db.engine.begin() as conn:
                self._multi_insert(conn, "vendor", cols, rows)

    def _gen_buyers(self):
        cols = ["b_id", "b_name", "b_address", "b_country_id", "b_phone", "b_balance", "b_segment", "b_memo"]
        batch_size = 5000
        for offset in range(0, self.num_buyers, batch_size):
            rows = []
            for i in range(offset + 1, min(offset + batch_size + 1, self.num_buyers + 1)):
                rows.append({
                    "k": i,
                    "n": f"Buyer#{i:09d}",
                    "a": self._rng_string(15, 35),
                    "c": self._rng.randint(0, 24),
                    "p": self._rng_phone(),
                    "b": round(self._rng.uniform(-999.99, 9999.99), 2),
                    "s": self._rng.choice(SEGMENTS),
                    "m": self._rng_string(40, 100),
                })
            with self.db.engine.begin() as conn:
                self._multi_insert(conn, "buyer", cols, rows)

    def _gen_products(self):
        cols = ["p_id", "p_name", "p_maker", "p_brand", "p_category", "p_size", "p_packaging", "p_retail_price", "p_memo"]
        batch_size = 5000
        for offset in range(0, self.num_products, batch_size):
            rows = []
            for i in range(offset + 1, min(offset + batch_size + 1, self.num_products + 1)):
                adj = self._rng.choice(CATEGORY_ADJ)
                mat = self._rng.choice(CATEGORY_MAT)
                noun = self._rng.choice(CATEGORY_NOUN)
                rows.append({
                    "k": i,
                    "n": f"{adj} {mat} {noun} item {i}",
                    "mk": f"Manufacturer#{self._rng.randint(1, 5)}",
                    "br": self._rng.choice(BRANDS),
                    "ct": f"{adj} {mat} {noun}",
                    "sz": self._rng.randint(1, 50),
                    "pk": f"{self._rng.choice(PACKAGING_SZ)} {self._rng.choice(PACKAGING_TY)}",
                    "rp": round(90000 + i * 0.01, 2),
                    "m": self._rng_string(10, 20),
                })
            with self.db.engine.begin() as conn:
                self._multi_insert(conn, "product", cols, rows)

    def _gen_inventory(self):
        batch_size = 5000
        rows = []
        for pk in range(1, self.num_products + 1):
            for j in range(4):
                vk = ((pk + j * (self.num_vendors // 4 + 1)) % self.num_vendors) + 1
                rows.append({
                    "pk": pk, "vk": vk,
                    "q": self._rng.randint(1, 9999),
                    "uc": round(self._rng.uniform(1.0, 1000.0), 2),
                    "m": self._rng_string(50, 150),
                })
                if len(rows) >= batch_size:
                    self._flush_inventory(rows)
                    rows = []
        if rows:
            self._flush_inventory(rows)

    def _flush_inventory(self, rows):
        cols = ["inv_product_id", "inv_vendor_id", "inv_avail_qty", "inv_unit_cost", "inv_memo"]
        with self.db.engine.begin() as conn:
            self._multi_insert(conn, "inventory", cols, rows)

    def _gen_orders_details(self, progress_callback=None):
        batch_orders = 2000
        batch_details: list[dict] = []
        batch_order_rows: list[dict] = []
        base_date = date(1992, 1, 1)
        date_range = (date(1998, 8, 2) - base_date).days
        total_orders = self.num_orders

        for ok in range(1, total_orders + 1):
            bk = self._rng.randint(1, self.num_buyers)
            odate = base_date + timedelta(days=self._rng.randint(0, date_range))
            num_lines = self._rng.randint(1, 7)
            total = 0.0
            status_chars = []

            for ln in range(1, num_lines + 1):
                pk = self._rng.randint(1, self.num_products)
                vk = ((pk + self._rng.randint(0, 3) * (self.num_vendors // 4 + 1)) % self.num_vendors) + 1
                qty = self._rng.randint(1, 50)
                price = round(self._rng.uniform(900, 105000) / 100, 2)
                disc = round(self._rng.uniform(0, 0.10), 2)
                tax = round(self._rng.uniform(0, 0.08), 2)
                ext_price = round(qty * price, 2)
                total += ext_price * (1 - disc) * (1 + tax)

                ship_date = odate + timedelta(days=self._rng.randint(1, 121))
                commit_date = odate + timedelta(days=self._rng.randint(30, 90))
                receipt_date = ship_date + timedelta(days=self._rng.randint(1, 30))

                if receipt_date <= date(1998, 6, 1):
                    rf = self._rng.choice(["R", "A"])
                    ls = "F"
                else:
                    rf = "N"
                    ls = "O"
                status_chars.append(ls)

                batch_details.append({
                    "ok": ok, "pk": pk, "vk": vk, "ln": ln,
                    "q": qty, "up": price, "d": disc, "t": tax,
                    "rf": rf, "ls": ls,
                    "sd": ship_date.isoformat(), "cd": commit_date.isoformat(), "rd": receipt_date.isoformat(),
                    "si": self._rng.choice(SHIP_INSTRUCT),
                    "sm": self._rng.choice(SHIP_MODES),
                    "m": self._rng_string(10, 40),
                })

            o_status = "F" if all(s == "F" for s in status_chars) else ("O" if all(s == "O" for s in status_chars) else "P")
            batch_order_rows.append({
                "ok": ok, "bk": bk, "os": o_status,
                "tot": round(total, 2),
                "od": odate.isoformat(),
                "pr": self._rng.choice(PRIORITIES),
                "cl": f"Clerk#{self._rng.randint(1, max(1, int(1000 * self.sf))):09d}",
                "sp": 0,
                "m": self._rng_string(20, 70),
            })

            if len(batch_order_rows) >= batch_orders:
                self._flush_orders(batch_order_rows)
                self._flush_details(batch_details)
                batch_order_rows = []
                batch_details = []
                if progress_callback:
                    progress_callback(ok, total_orders)

        if batch_order_rows:
            self._flush_orders(batch_order_rows)
            self._flush_details(batch_details)
            if progress_callback:
                progress_callback(total_orders, total_orders)

    def _flush_orders(self, rows):
        cols = ["so_id", "so_buyer_id", "so_status", "so_total", "so_date",
                "so_priority", "so_clerk", "so_ship_priority", "so_memo"]
        with self.db.engine.begin() as conn:
            self._multi_insert(conn, "sale_order", cols, rows)

    def _flush_details(self, rows):
        cols = ["od_order_id", "od_product_id", "od_vendor_id", "od_line_num",
                "od_qty", "od_unit_price", "od_discount", "od_tax", "od_return_flag", "od_line_status",
                "od_ship_date", "od_commit_date", "od_receipt_date", "od_ship_instruct", "od_ship_mode", "od_memo"]
        with self.db.engine.begin() as conn:
            self._multi_insert(conn, "order_detail", cols, rows)

    def _multi_insert(self, conn, table: str, columns: list[str], rows: list[dict]):
        """Insert using multi-row VALUES clauses for far fewer round-trips.

        MSSQL limits parameters to 2100 per statement, so chunk size is
        capped at 2100 // num_columns.
        """
        if not rows:
            return
        max_per_stmt = min(500, 2000 // len(columns))
        col_csv = ", ".join(columns)
        keys = list(rows[0].keys())

        for i in range(0, len(rows), max_per_stmt):
            chunk = rows[i:i + max_per_stmt]
            placeholders = []
            params: dict = {}
            for ri, row in enumerate(chunk):
                parts = []
                for ci, key in enumerate(keys):
                    pname = f"r{ri}c{ci}"
                    parts.append(f":{pname}")
                    params[pname] = row[key]
                placeholders.append(f"({', '.join(parts)})")
            sql = f"INSERT INTO {table} ({col_csv}) VALUES {', '.join(placeholders)}"
            conn.execute(text(sql), params)

    def _rng_string(self, lo: int, hi: int) -> str:
        length = self._rng.randint(lo, hi)
        return "".join(self._rng.choices("abcdefghijklmnopqrstuvwxyz ", k=length))

    def _rng_phone(self) -> str:
        cc = self._rng.randint(10, 34)
        return f"{cc}-{self._rng.randint(100,999)}-{self._rng.randint(100,999)}-{self._rng.randint(1000,9999)}"
