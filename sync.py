import json
import time
from datetime import datetime
# Important: import get_setting to check online access status
from memory import get_setting
from db import (
    fetch_pending_queue,
    mark_queue_status,
    set_last_sync,
    enqueue_sync,
    get_queue_stats,
    connect,
)

def queue_status():
    return get_queue_stats()

def can_sync_now():
    # Integrated requirement: Check 'online_access' setting
    # Default to True (online) if the setting isn't found.
    return get_setting("online_access", "True") == "True"

def enqueue_action(action_type, payload):
    enqueue_sync(action_type, payload)

def apply_action(action_type, payload):
    with connect() as conn:
        if action_type == "inventory_upsert":
            conn.execute("""
                INSERT INTO inventory (sku, product, stock, location, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sku) DO UPDATE SET
                product=excluded.product,
                stock=excluded.stock,
                location=excluded.location,
                updated_at=excluded.updated_at
            """, (
                payload["sku"],
                payload.get("product", ""),
                int(payload.get("stock", 0)),
                payload.get("location", "UNASSIGNED"),
                datetime.utcnow().isoformat(timespec="seconds"),
            ))
        elif action_type == "order_create":
            conn.execute("""
                INSERT INTO orders (order_id, status, required_skus, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                status=excluded.status,
                required_skus=excluded.required_skus,
                updated_at=excluded.updated_at
            """, (
                payload["order_id"],
                payload.get("status", "Pending"),
                json.dumps(payload.get("required_skus", [])),
                datetime.utcnow().isoformat(timespec="seconds"),
            ))
        elif action_type == "order_update":
            conn.execute(
                "UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
                (
                    payload.get("status", "Pending"),
                    datetime.utcnow().isoformat(timespec="seconds"),
                    payload["order_id"],
                ),
            )
        elif action_type == "template_save":
            conn.execute("""
                INSERT INTO templates (raw_title, standard_title, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(raw_title) DO UPDATE SET
                standard_title=excluded.standard_title,
                updated_at=excluded.updated_at
            """, (
                payload["raw"],
                payload["standard"],
                datetime.utcnow().isoformat(timespec="seconds"),
            ))
        elif action_type == "memory_save":
            conn.execute("""
                INSERT INTO memory (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """, (
                payload["key"],
                payload["value"],
                datetime.utcnow().isoformat(timespec="seconds"),
            ))
        conn.commit()

def process_queue(batch_size=100):
    # Integrated requirement: Check if sync is allowed (online access)
    if not can_sync_now():
        return 0, 0 # Return 0 synced, 0 failed immediately

    synced = 0
    failed = 0
    for row in fetch_pending_queue(batch_size):
        try:
            payload = json.loads(row["payload"])
            apply_action(row["action_type"], payload)
            mark_queue_status(row["id"], "synced", None)
            synced += 1
        except Exception as e:
            mark_queue_status(row["id"], "failed", str(e))
            failed += 1
            time.sleep(0.1)
    if synced:
        set_last_sync(datetime.utcnow().isoformat(timespec="seconds"))
    return synced, failed