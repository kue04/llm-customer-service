from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from services.feedback_service import DB_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS order_states (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            status_label TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            summary TEXT NOT NULL,
            refund_status TEXT NOT NULL,
            store_name TEXT NOT NULL,
            items_json TEXT NOT NULL,
            total REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def upsert_order_state(payload: dict) -> dict:
    now = utc_now()
    order_id = str(payload["order_id"])
    status = str(payload.get("status", ""))
    status_label = str(payload.get("status_label") or payload.get("delivery_status") or status)
    summary = str(payload.get("summary") or payload.get("delivery_status") or status_label)
    row = {
        "order_id": order_id,
        "user_id": str(payload.get("user_id") or "demo_user"),
        "status": status,
        "status_label": status_label,
        "delivery_status": str(payload.get("delivery_status") or status_label),
        "summary": summary,
        "refund_status": str(payload.get("refund_status") or "none"),
        "store_name": str(payload.get("store_name") or ""),
        "items_json": json.dumps(payload.get("items") or [], ensure_ascii=False),
        "total": float(payload.get("total") or 0.0),
        "updated_at": now,
    }
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO order_states
            (order_id, user_id, status, status_label, delivery_status, summary,
             refund_status, store_name, items_json, total, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                user_id = excluded.user_id,
                status = excluded.status,
                status_label = excluded.status_label,
                delivery_status = excluded.delivery_status,
                summary = excluded.summary,
                refund_status = excluded.refund_status,
                store_name = excluded.store_name,
                items_json = excluded.items_json,
                total = excluded.total,
                updated_at = excluded.updated_at
            """,
            tuple(row.values()),
        )
        connection.commit()
    finally:
        connection.close()
    return get_order_state(order_id) or row


def get_order_state(order_id: str | None) -> dict | None:
    if not order_id:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM order_states WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    result = dict(row)
    try:
        result["items"] = json.loads(result.pop("items_json") or "[]")
    except json.JSONDecodeError:
        result["items"] = []
    return result
