# db.py
import sqlite3
import json
import hashlib
from datetime import datetime
import pandas as pd
import os # Added os import for SIM file check

DB_PATH = "warehouse.db"
# Path for the SIM IMEI database integrated from sim.py
SIM_DB_PATH = "samsung_offsets.csv"

def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def now():
    return datetime.utcnow().isoformat(timespec="seconds")

def init_db():
    with connect() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            sku TEXT PRIMARY KEY,
            product TEXT,
            stock INTEGER NOT NULL DEFAULT 0,
            location TEXT NOT NULL DEFAULT 'UNASSIGNED',
            updated_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            required_skus TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            raw_title TEXT PRIMARY KEY,
            standard_title TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pref_key TEXT NOT NULL,
            pref_value TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            ref_id TEXT,
            payload TEXT,
            user TEXT, -- Added user column based on log_action usage
            created_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS sync_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_sync TEXT
        )
        """)

        c.execute("INSERT OR IGNORE INTO sync_meta (id, last_sync) VALUES (1, NULL)")
        
        # Default Admin Account
        c.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("VINO VJ", hashlib.sha256("0088".encode()).hexdigest(), "VINO VJ")
        )

        conn.commit()

# --- EXISTING WAREHOUSE DB FUNCTIONS (UNTOUCHED) ---

def get_inventory():
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT sku, product, stock, location, updated_at FROM inventory ORDER BY updated_at DESC",
            conn,
        )

def upsert_inventory(sku, product, stock, location):
    with connect() as conn:
        conn.execute("""
            INSERT INTO inventory (sku, product, stock, location, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
            product=excluded.product,
            stock=excluded.stock,
            location=excluded.location,
            updated_at=excluded.updated_at
        """, (sku, product, int(stock), location, now()))
        conn.commit()

def get_orders():
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT order_id, status, required_skus, updated_at FROM orders ORDER BY updated_at DESC",
            conn,
        )

def create_order(order_id, status, required_skus):
    with connect() as conn:
        conn.execute("""
            INSERT INTO orders (order_id, status, required_skus, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
            status=excluded.status,
            required_skus=excluded.required_skus,
            updated_at=excluded.updated_at
        """, (order_id, status, json.dumps(required_skus), now()))
        conn.commit()

def update_order_status(order_id, status):
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
            (status, now(), order_id),
        )
        conn.commit()

def get_templates():
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT raw_title, standard_title, updated_at FROM templates ORDER BY updated_at DESC",
            conn,
        )

def save_template(raw_title, standard_title):
    with connect() as conn:
        conn.execute("""
            INSERT INTO templates (raw_title, standard_title, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(raw_title) DO UPDATE SET
            standard_title=excluded.standard_title,
            updated_at=excluded.updated_at
        """, (raw_title, standard_title, now()))
        conn.commit()

def save_memory(key, value):
    with connect() as conn:
        conn.execute("""
            INSERT INTO memory (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """, (key, value, now()))
        conn.commit()

def get_memory(key=None):
    with connect() as conn:
        if key:
            row = conn.execute(
                "SELECT key, value, updated_at FROM memory WHERE key=?",
                (key,),
            ).fetchone()
            return dict(row) if row else None
        return pd.read_sql_query(
            "SELECT key, value, updated_at FROM memory ORDER BY updated_at DESC",
            conn,
        )

def record_preference(pref_key, pref_value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO preferences (pref_key, pref_value, created_at) VALUES (?, ?, ?)",
            (pref_key, pref_value, now()),
        )
        conn.commit()

def get_recent_preferences():
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT pref_key, pref_value, created_at FROM preferences ORDER BY id DESC LIMIT 50",
            conn,
        )

def add_action_log(action_type, ref_id=None, payload=None, user=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO action_logs (action_type, ref_id, payload, user, created_at) VALUES (?, ?, ?, ?, ?)",
            (action_type, ref_id, payload, user, now()),
        )
        conn.commit()

def enqueue_sync(action_type, payload):
    with connect() as conn:
        conn.execute("""
            INSERT INTO sync_queue (action_type, payload, status, attempts, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (action_type, json.dumps(payload), "pending", now(), now()))
        conn.commit()

def fetch_pending_queue(limit=100):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_queue WHERE status IN ('pending', 'failed') ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

def mark_queue_status(queue_id, status, error=None):
    with connect() as conn:
        conn.execute(
            "UPDATE sync_queue SET status=?, attempts=attempts+1, last_error=?, updated_at=? WHERE id=?",
            (status, error, now(), queue_id),
        )
        conn.commit()

def set_last_sync(ts):
    with connect() as conn:
        conn.execute("UPDATE sync_meta SET last_sync=? WHERE id=1", (ts,))
        conn.commit()

def get_queue_stats():
    with connect() as conn:
        queued = conn.execute(
            "SELECT COUNT(*) FROM sync_queue WHERE status IN ('pending', 'failed')"
        ).fetchone()[0]
        last_sync = conn.execute(
            "SELECT last_sync FROM sync_meta WHERE id=1"
        ).fetchone()[0]
        return {"queued": queued, "last_sync": last_sync}

def auth_login(username, password):
    with connect() as conn:
        row = conn.execute(
            "SELECT username, password_hash, role FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if row and row["password_hash"] == hashlib.sha256(password.encode()).hexdigest():
            return dict(row)
    return None

def add_user(username, password, role):
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hashlib.sha256(password.encode()).hexdigest(), role),
        )
        conn.commit()

# --- SIM.PY INTEGRATION FUNCTIONS (ADMIN ONLY) ---

def load_sim_db():
    """Loads the Samsung IMEI offsets database from CSV, adhering to sim.py logic."""
    if os.path.exists(SIM_DB_PATH):
        # Force TAC_Prefix as string to keep leading zeros as per sim.py
        return pd.read_csv(SIM_DB_PATH, dtype={'TAC_Prefix': str})
    # Default structure matching sim.py image/code
    return pd.DataFrame(columns=['TAC_Prefix', 'Model_Series', 'Expected_Offset', 'Type'])

def save_sim_db(df):
    """Saves the modified SIM database back to CSV."""
    df.to_csv(SIM_DB_PATH, index=False)