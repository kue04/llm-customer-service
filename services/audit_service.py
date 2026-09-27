from __future__ import annotations

from services.runtime_db import get_connection

from datetime import datetime, timezone

from services.privacy import mask_sensitive_text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            INSERT INTO runtime_audit_logs
            (operator_id, operator_role, action_type, object_type, object_id, request_id,
             before_summary, after_summary, ip, device_info, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :_tenant, :_actor, :_now)
             RETURNING id""",
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
        return int(cursor.inserted_id)
    finally:
        connection.close()


def list_audit_logs(
    limit: int = 50,
    action_type: str = "",
    object_type: str = "",
    operator_role: str = "",
    request_id: str = "",
) -> dict:
    clauses = ["tenant_id = :_tenant", "deleted_at IS NULL"]
    params: dict[str, object] = {}
    if action_type:
        clauses.append("action_type = :action_type")
        params["action_type"] = action_type
    if object_type:
        clauses.append("object_type = :object_type")
        params["object_type"] = object_type
    if operator_role:
        clauses.append("operator_role = :operator_role")
        params["operator_role"] = operator_role
    if request_id:
        clauses.append("request_id = :request_id")
        params["request_id"] = request_id
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params["limit"] = limit

    connection = get_connection()
    try:
        rows = connection.execute(
            f"\n            SELECT id, operator_id, operator_role, action_type, object_type, object_id,\n                   request_id, before_summary, after_summary, ip, device_info, created_at\n            FROM runtime_audit_logs\n            {where_sql}\n            ORDER BY id DESC\n            LIMIT :limit\n            ",
            params,
        ).fetchall()
    finally:
        connection.close()
    return {"count": len(rows), "items": [dict(row) for row in rows]}
