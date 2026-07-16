from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from services import feedback_service
from services.privacy import mask_sensitive_text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    feedback_service.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(feedback_service.DB_PATH)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id TEXT NOT NULL,
            operator_role TEXT NOT NULL,
            action_type TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            before_summary TEXT NOT NULL DEFAULT '',
            after_summary TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            device_info TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def record_audit_log(
    *,
    operator_id: str,
    operator_role: str,
    action_type: str,
    object_type: str,
    object_id: str,
    request_id: str = "",
    before_summary: str = "",
    after_summary: str = "",
    ip: str = "",
    device_info: str = "",
) -> int:
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO audit_logs
            (operator_id, operator_role, action_type, object_type, object_id, request_id,
             before_summary, after_summary, ip, device_info, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operator_id,
                operator_role,
                action_type,
                object_type,
                object_id,
                request_id,
                mask_sensitive_text(before_summary),
                mask_sensitive_text(after_summary),
                ip,
                device_info[:300],
                utc_now(),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def list_audit_logs(
    limit: int = 50,
    action_type: str = "",
    object_type: str = "",
    operator_role: str = "",
    request_id: str = "",
) -> dict:
    clauses = []
    params: list[object] = []
    if action_type:
        clauses.append("action_type = ?")
        params.append(action_type)
    if object_type:
        clauses.append("object_type = ?")
        params.append(object_type)
    if operator_role:
        clauses.append("operator_role = ?")
        params.append(operator_role)
    if request_id:
        clauses.append("request_id = ?")
        params.append(request_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, operator_id, operator_role, action_type, object_type, object_id,
                   request_id, before_summary, after_summary, ip, device_info, created_at
            FROM audit_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return {"count": len(rows), "items": [dict(row) for row in rows]}
