"""Create or refresh Queryly's small, deterministic SQLite demo database.

Run from any working directory with ``python backend/scripts/create_sample_db.py``.
The file is placed in ``backend/data`` so the local connection-test endpoint can
open it under its default SQLite allowlist.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
DATABASE_PATH = DATA_DIR / "sample.db"

CUSTOMERS = [
    (1, "Maya Chen", "maya.chen@example.com", "San Francisco"),
    (2, "Ethan Brooks", "ethan.brooks@example.com", "Austin"),
    (3, "Aisha Patel", "aisha.patel@example.com", "New York"),
    (4, "Noah Williams", "noah.williams@example.com", "Seattle"),
    (5, "Sofia Garcia", "sofia.garcia@example.com", "Chicago"),
    (6, "Liam O'Connor", "liam.oconnor@example.com", "Boston"),
    (7, "Zoe Kim", "zoe.kim@example.com", "Los Angeles"),
    (8, "Oliver Smith", "oliver.smith@example.com", "Denver"),
    (9, "Amara Johnson", "amara.johnson@example.com", "Atlanta"),
    (10, "Lucas Martin", "lucas.martin@example.com", "Portland"),
    (11, "Priya Shah", "priya.shah@example.com", "San Jose"),
    (12, "Theo Anderson", "theo.anderson@example.com", "Miami"),
]

PRODUCTS = [
    (1, "Trailhead Backpack", "Outdoor", 89.00),
    (2, "Alpine Water Bottle", "Outdoor", 24.50),
    (3, "Everyday Notebook", "Stationery", 12.00),
    (4, "Studio Desk Lamp", "Home", 64.00),
    (5, "Wireless Headphones", "Electronics", 129.99),
    (6, "Ceramic Travel Mug", "Home", 32.00),
    (7, "USB-C Hub", "Electronics", 48.00),
    (8, "Merino Wool Socks", "Apparel", 18.00),
    (9, "Canvas Tote", "Apparel", 22.00),
    (10, "Pocket Sketchbook", "Stationery", 9.50),
]

# Order tuples: id, customer_id, date, status, then (product_id, quantity) items.
ORDERS = [
    (1001, 1, "2026-01-08", "delivered", [(1, 1), (2, 2)]),
    (1002, 2, "2026-01-12", "delivered", [(5, 1), (7, 1), (3, 2)]),
    (1003, 3, "2026-01-18", "shipped", [(4, 1), (6, 1)]),
    (1004, 4, "2026-01-23", "delivered", [(1, 1), (8, 3)]),
    (1005, 5, "2026-02-02", "delivered", [(9, 2), (10, 3)]),
    (1006, 6, "2026-02-07", "processing", [(5, 1), (6, 2)]),
    (1007, 7, "2026-02-11", "delivered", [(2, 2), (8, 2), (9, 1)]),
    (1008, 8, "2026-02-16", "shipped", [(3, 4), (10, 2)]),
    (1009, 9, "2026-02-21", "delivered", [(4, 1), (7, 1)]),
    (1010, 10, "2026-03-01", "delivered", [(1, 1), (6, 1), (8, 2)]),
    (1011, 11, "2026-03-05", "processing", [(5, 1), (7, 1)]),
    (1012, 12, "2026-03-09", "delivered", [(2, 3), (9, 2)]),
    (1013, 1, "2026-03-14", "shipped", [(3, 2), (10, 4)]),
    (1014, 3, "2026-03-19", "delivered", [(4, 1), (6, 1), (9, 1)]),
    (1015, 5, "2026-03-25", "processing", [(1, 1), (2, 1)]),
    (1016, 7, "2026-04-02", "delivered", [(5, 1), (8, 2)]),
    (1017, 9, "2026-04-08", "shipped", [(6, 2), (7, 1), (10, 2)]),
    (1018, 12, "2026-04-14", "processing", [(1, 1), (3, 1), (9, 1)]),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price NUMERIC NOT NULL CHECK (price >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'shipped', 'delivered', 'cancelled'))
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
    UNIQUE (order_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_customers_city ON customers(city);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);
"""


def seed_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)

        connection.executemany(
            """INSERT INTO customers (id, name, email, city) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, email=excluded.email, city=excluded.city""",
            CUSTOMERS,
        )
        connection.executemany(
            """INSERT INTO products (id, name, category, price) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, category=excluded.category, price=excluded.price""",
            PRODUCTS,
        )
        connection.executemany(
            """INSERT INTO orders (id, customer_id, order_date, status) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET customer_id=excluded.customer_id,
                order_date=excluded.order_date, status=excluded.status""",
            [(order_id, customer_id, order_date, status) for order_id, customer_id, order_date, status, _ in ORDERS],
        )

        # Rebuild seeded line items so reruns also refresh quantities and prices.
        order_ids = [order[0] for order in ORDERS]
        placeholders = ",".join("?" for _ in order_ids)
        connection.execute(f"DELETE FROM order_items WHERE order_id IN ({placeholders})", order_ids)
        product_prices = {product_id: price for product_id, _, _, price in PRODUCTS}
        items = [
            (order_id * 10 + index, order_id, product_id, quantity, product_prices[product_id])
            for order_id, _, _, _, order_items in ORDERS
            for index, (product_id, quantity) in enumerate(order_items, start=1)
        ]
        connection.executemany(
            "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
            items,
        )

    print(f"Sample database ready: {DATABASE_PATH}")


if __name__ == "__main__":
    seed_database()
