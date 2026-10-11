import os
import sqlite3

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('CLUB_DB', os.path.join(BASE_DIR, 'club.db'))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tiers (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    price REAL NOT NULL,
    discount_percent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    tier_id INTEGER NOT NULL REFERENCES tiers(id),
    paid INTEGER NOT NULL DEFAULT 0,
    joined_on TEXT NOT NULL,
    expires_on TEXT,
    reminded_for TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    venue TEXT,
    starts_at TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    member_price REAL NOT NULL DEFAULT 0,
    public_price REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    thumbnail_filename TEXT,
    location_address TEXT,
    maps_url TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    batch TEXT NOT NULL,
    price_paid REAL NOT NULL DEFAULT 0,
    is_member INTEGER NOT NULL DEFAULT 0,
    paid INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    checked_in_at TEXT
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    emailed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    size TEXT NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,

    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    pincode TEXT NOT NULL DEFAULT '',

    subtotal REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    delivery_status TEXT NOT NULL DEFAULT 'Order Placed',
    created_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    variant_id INTEGER NOT NULL REFERENCES variants(id),
    qty INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fundraisers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    goal REAL NOT NULL DEFAULT 0,
    deadline TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    fundraiser_id INTEGER NOT NULL REFERENCES fundraisers(id),
    title TEXT NOT NULL,
    assignee TEXT,
    status TEXT NOT NULL DEFAULT 'todo',
    due_date TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id),
    amount REAL NOT NULL,
    description TEXT NOT NULL,
    receipt_file TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    reimbursed_at TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT NOT NULL,
    ref TEXT
);

CREATE TABLE IF NOT EXISTS committee_users (
    id INTEGER PRIMARY KEY,
    member_id INTEGER REFERENCES members(id),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'committee',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customer_accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.executescript(SCHEMA)

    event_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }

    for column in ("thumbnail_filename", "location_address", "maps_url"):
        if column not in event_columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")

    # Safely add password support to existing members.
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(members)").fetchall()
    }

    if "password_hash" not in columns:
        conn.execute(
            "ALTER TABLE members ADD COLUMN password_hash TEXT"
        )


    product_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(products)").fetchall()
    }

    if "image_filename" not in product_columns:
        conn.execute(
            "ALTER TABLE products ADD COLUMN image_filename TEXT"
        )

    order_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(orders)").fetchall()
    }

    for column in ("phone", "address", "city", "pincode"):
        if column not in order_columns:
            conn.execute(
                f"ALTER TABLE orders ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    if "delivery_status" not in order_columns:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN delivery_status "
            "TEXT NOT NULL DEFAULT 'Order Placed'"
        )

    conn.commit()
    conn.close()
