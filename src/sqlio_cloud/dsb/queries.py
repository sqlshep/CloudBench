"""Original decision-support benchmark queries.

16 analytical queries covering standard SQL patterns: aggregation, multi-table
JOINs, correlated subqueries, EXISTS/NOT EXISTS, CASE WHEN, CTEs, HAVING,
LEFT JOIN distributions, and derived tables.

All queries are written against the supply-chain/e-commerce schema defined in
schema.py.  The SQL uses standard syntax for PostgreSQL; the runner applies
light dialect translation for MySQL and SQL Server.
"""

from __future__ import annotations

DSB_QUERIES: dict[str, tuple[str, dict]] = {

    # Q01 — Status Summary: aggregate order lines by year and delivery status
    "Q01": ("""
        SELECT
            EXTRACT(YEAR FROM od_ship_date) AS ship_year,
            od_line_status,
            COUNT(*)                                                   AS num_lines,
            SUM(od_qty)                                                AS total_qty,
            SUM(od_unit_price * od_qty * (1 - od_discount))            AS net_value,
            ROUND(AVG(od_discount)::NUMERIC, 4)                        AS avg_discount,
            SUM(od_tax * od_unit_price * od_qty)                       AS total_tax
        FROM order_detail
        WHERE od_ship_date >= DATE '1993-01-01'
        GROUP BY EXTRACT(YEAR FROM od_ship_date), od_line_status
        ORDER BY ship_year, od_line_status
    """, {}),

    # Q02 — Cheapest Vendor: lowest-cost vendor for products in a zone
    "Q02": ("""
        SELECT v_name, ct_name, p_name, p_brand, inv_unit_cost
        FROM product
            JOIN inventory ON p_id = inv_product_id
            JOIN vendor    ON v_id = inv_vendor_id
            JOIN country   ON v_country_id = ct_id
            JOIN zone      ON ct_zone_id = z_id
        WHERE
            z_name = :zone
            AND p_category LIKE :category_pat
            AND inv_unit_cost = (
                SELECT MIN(i2.inv_unit_cost)
                FROM inventory i2
                    JOIN vendor v2  ON v2.v_id = i2.inv_vendor_id
                    JOIN country c2 ON v2.v_country_id = c2.ct_id
                    JOIN zone z2    ON c2.ct_zone_id = z2.z_id
                WHERE i2.inv_product_id = p_id AND z2.z_name = :zone
            )
        ORDER BY inv_unit_cost, ct_name, v_name
    """, {"zone": "EUROPE", "category_pat": "%STEEL%"}),

    # Q03 — Unshipped Revenue: highest pending revenue by segment
    "Q03": ("""
        SELECT
            so_id,
            SUM(od_unit_price * od_qty * (1 - od_discount)) AS pending_revenue,
            so_date
        FROM buyer
            JOIN sale_order   ON b_id = so_buyer_id
            JOIN order_detail ON so_id = od_order_id
        WHERE
            b_segment = :segment
            AND so_date BETWEEN DATE :start_date AND DATE :end_date
            AND od_ship_date > DATE :end_date
        GROUP BY so_id, so_date
        ORDER BY pending_revenue DESC, so_date
    """, {"segment": "BUILDING", "start_date": "1995-01-01", "end_date": "1995-06-30"}),

    # Q04 — Late Deliveries: orders with at least one late line per priority
    "Q04": ("""
        SELECT
            so_priority,
            COUNT(*) AS late_order_count
        FROM sale_order
        WHERE
            so_date >= DATE :start_date
            AND so_date < DATE :start_date + INTERVAL '1 YEAR'
            AND EXISTS (
                SELECT 1 FROM order_detail
                WHERE od_order_id = so_id AND od_receipt_date > od_commit_date
            )
        GROUP BY so_priority
        ORDER BY so_priority
    """, {"start_date": "1994-01-01"}),

    # Q05 — Zone Revenue: revenue by country within a geographic zone
    "Q05": ("""
        SELECT
            ct_name,
            SUM(od_unit_price * od_qty * (1 - od_discount)) AS revenue
        FROM buyer
            JOIN sale_order   ON b_id = so_buyer_id
            JOIN order_detail ON so_id = od_order_id
            JOIN vendor       ON od_vendor_id = v_id AND b_country_id = v_country_id
            JOIN country      ON v_country_id = ct_id
            JOIN zone         ON ct_zone_id = z_id
        WHERE
            z_name = :zone
            AND so_date >= DATE :start_date
            AND so_date < DATE :start_date + INTERVAL '1 YEAR'
        GROUP BY ct_name
        ORDER BY revenue DESC
    """, {"zone": "ASIA", "start_date": "1994-01-01"}),

    # Q06 — Discount Impact: total revenue lost to discounts in a period
    "Q06": ("""
        SELECT
            SUM(od_unit_price * od_qty * od_discount) AS discount_amount
        FROM order_detail
        WHERE
            od_ship_date >= DATE :start_date
            AND od_ship_date < DATE :start_date + INTERVAL '1 YEAR'
            AND od_discount BETWEEN :min_disc AND :max_disc
            AND od_qty < :max_qty
    """, {"start_date": "1994-01-01", "min_disc": 0.05, "max_disc": 0.07, "max_qty": 24}),

    # Q07 — Cross-Border Trade: bilateral trade volume between two countries
    "Q07": ("""
        SELECT
            vendor_country, buyer_country, trade_year,
            SUM(trade_value) AS total_trade
        FROM (
            SELECT
                vc.ct_name AS vendor_country,
                bc.ct_name AS buyer_country,
                EXTRACT(YEAR FROM od_ship_date) AS trade_year,
                od_unit_price * od_qty * (1 - od_discount) AS trade_value
            FROM vendor
                JOIN order_detail ON v_id = od_vendor_id
                JOIN sale_order   ON so_id = od_order_id
                JOIN buyer        ON b_id = so_buyer_id
                JOIN country vc   ON v_country_id = vc.ct_id
                JOIN country bc   ON b_country_id = bc.ct_id
            WHERE
                ((vc.ct_name = :country1 AND bc.ct_name = :country2)
                 OR (vc.ct_name = :country2 AND bc.ct_name = :country1))
                AND od_ship_date BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
        ) AS cross_trade
        GROUP BY vendor_country, buyer_country, trade_year
        ORDER BY vendor_country, buyer_country, trade_year
    """, {"country1": "FRANCE", "country2": "GERMANY"}),

    # Q08 — Market Concentration: a country's share of zone revenue
    "Q08": ("""
        SELECT
            order_year,
            SUM(CASE WHEN vendor_nation = :country THEN line_value ELSE 0 END)
                / SUM(line_value) AS market_share
        FROM (
            SELECT
                EXTRACT(YEAR FROM so_date) AS order_year,
                od_unit_price * od_qty * (1 - od_discount) AS line_value,
                vc.ct_name AS vendor_nation
            FROM product
                JOIN order_detail ON p_id = od_product_id
                JOIN vendor       ON v_id = od_vendor_id
                JOIN sale_order   ON so_id = od_order_id
                JOIN buyer        ON b_id = so_buyer_id
                JOIN country bc   ON b_country_id = bc.ct_id
                JOIN zone         ON bc.ct_zone_id = z_id
                JOIN country vc   ON v_country_id = vc.ct_id
            WHERE
                z_name = :zone
                AND so_date BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
                AND p_category = :category
        ) AS zone_sales
        GROUP BY order_year
        ORDER BY order_year
    """, {"country": "BRAZIL", "zone": "AMERICA", "category": "ECONOMY ANODIZED STEEL"}),

    # Q09 — Vendor Margins: profit by country and year
    "Q09": ("""
        SELECT
            country_name, order_year,
            SUM(margin) AS total_margin
        FROM (
            SELECT
                ct_name AS country_name,
                EXTRACT(YEAR FROM so_date) AS order_year,
                od_unit_price * od_qty * (1 - od_discount)
                    - inv_unit_cost * od_qty AS margin
            FROM product
                JOIN order_detail ON p_id = od_product_id
                JOIN vendor       ON v_id = od_vendor_id
                JOIN inventory    ON inv_product_id = od_product_id
                                    AND inv_vendor_id = od_vendor_id
                JOIN sale_order   ON so_id = od_order_id
                JOIN country      ON v_country_id = ct_id
            WHERE p_name LIKE :name_pattern
        ) AS margins
        GROUP BY country_name, order_year
        ORDER BY country_name, order_year DESC
    """, {"name_pattern": "%green%"}),

    # Q10 — Returned Value: revenue from returned items ranked by buyer
    "Q10": ("""
        SELECT
            b_id, b_name,
            SUM(od_unit_price * od_qty * (1 - od_discount)) AS returned_value,
            b_balance, ct_name, b_phone
        FROM buyer
            JOIN sale_order   ON b_id = so_buyer_id
            JOIN order_detail ON so_id = od_order_id
            JOIN country      ON b_country_id = ct_id
        WHERE
            so_date >= DATE :start_date
            AND so_date < DATE :start_date + INTERVAL '3 MONTH'
            AND od_return_flag = 'R'
        GROUP BY b_id, b_name, b_balance, ct_name, b_phone
        ORDER BY returned_value DESC
    """, {"start_date": "1993-10-01"}),

    # Q11 — Valuable Inventory: products with stock value above a threshold
    "Q11": ("""
        SELECT
            inv_product_id,
            SUM(inv_unit_cost * inv_avail_qty) AS stock_value
        FROM inventory
            JOIN vendor  ON inv_vendor_id = v_id
            JOIN country ON v_country_id = ct_id
        WHERE ct_name = :country
        GROUP BY inv_product_id
        HAVING SUM(inv_unit_cost * inv_avail_qty) > (
            SELECT SUM(inv_unit_cost * inv_avail_qty) * :threshold
            FROM inventory
                JOIN vendor  ON inv_vendor_id = v_id
                JOIN country ON v_country_id = ct_id
            WHERE ct_name = :country
        )
        ORDER BY stock_value DESC
    """, {"country": "GERMANY", "threshold": 0.0001}),

    # Q12 — Shipping Mode Analysis: priority distribution by ship mode
    "Q12": ("""
        SELECT
            od_ship_mode,
            SUM(CASE WHEN so_priority IN ('1-URGENT', '2-HIGH')
                     THEN 1 ELSE 0 END) AS urgent_count,
            SUM(CASE WHEN so_priority NOT IN ('1-URGENT', '2-HIGH')
                     THEN 1 ELSE 0 END) AS routine_count
        FROM sale_order
            JOIN order_detail ON so_id = od_order_id
        WHERE
            od_ship_mode IN (:mode1, :mode2)
            AND od_commit_date < od_receipt_date
            AND od_ship_date < od_commit_date
            AND od_receipt_date >= DATE :start_date
            AND od_receipt_date < DATE :start_date + INTERVAL '1 YEAR'
        GROUP BY od_ship_mode
        ORDER BY od_ship_mode
    """, {"mode1": "MAIL", "mode2": "SHIP", "start_date": "1994-01-01"}),

    # Q13 — Order Frequency: distribution of order counts per buyer
    "Q13": ("""
        SELECT
            order_count, COUNT(*) AS num_buyers
        FROM (
            SELECT b_id, COUNT(so_id) AS order_count
            FROM buyer
                LEFT JOIN sale_order ON b_id = so_buyer_id
                    AND so_memo NOT LIKE :exclude_pattern
            GROUP BY b_id
        ) AS buyer_orders
        GROUP BY order_count
        ORDER BY num_buyers DESC, order_count DESC
    """, {"exclude_pattern": "%special%request%"}),

    # Q14 — Promo Revenue Share: percentage of revenue from promotional items
    "Q14": ("""
        SELECT
            100.00 * SUM(CASE WHEN p_category LIKE 'PROMO%'
                              THEN od_unit_price * od_qty * (1 - od_discount)
                              ELSE 0 END)
            / SUM(od_unit_price * od_qty * (1 - od_discount)) AS promo_share_pct
        FROM order_detail
            JOIN product ON od_product_id = p_id
        WHERE
            od_ship_date >= DATE :start_date
            AND od_ship_date < DATE :start_date + INTERVAL '1 MONTH'
    """, {"start_date": "1995-09-01"}),

    # Q15 — Top Vendor Revenue: vendor with maximum quarterly revenue (CTE)
    "Q15": ("""
        WITH vendor_revenue AS (
            SELECT
                od_vendor_id AS vid,
                SUM(od_unit_price * od_qty * (1 - od_discount)) AS total_rev
            FROM order_detail
            WHERE
                od_ship_date >= DATE :start_date
                AND od_ship_date < DATE :start_date + INTERVAL '3 MONTH'
            GROUP BY od_vendor_id
        )
        SELECT v_id, v_name, v_address, v_phone, total_rev
        FROM vendor
            JOIN vendor_revenue ON v_id = vid
        WHERE total_rev = (SELECT MAX(total_rev) FROM vendor_revenue)
        ORDER BY v_id
    """, {"start_date": "1996-01-01"}),

    # Q16 — Vendor Diversity: distinct vendors per product profile
    "Q16": ("""
        SELECT
            p_brand, p_category, p_size,
            COUNT(DISTINCT inv_vendor_id) AS vendor_count
        FROM inventory
            JOIN product ON p_id = inv_product_id
        WHERE
            p_brand <> :brand
            AND p_category NOT LIKE :category_pat
            AND p_size IN (:s1, :s2, :s3, :s4, :s5, :s6, :s7, :s8)
            AND inv_vendor_id NOT IN (
                SELECT v_id FROM vendor WHERE v_memo LIKE '%Complaint%Filed%'
            )
        GROUP BY p_brand, p_category, p_size
        ORDER BY vendor_count DESC, p_brand, p_category, p_size
    """, {"brand": "Brand#45", "category_pat": "MEDIUM POLISHED%",
          "s1": 49, "s2": 14, "s3": 23, "s4": 45, "s5": 19, "s6": 3, "s7": 36, "s8": 9}),
}
