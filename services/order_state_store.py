from __future__ import annotations

from services.runtime_db import get_connection, scoped_identity

from datetime import datetime, timezone
import json

from services.privacy import mask_sensitive_text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderNotFoundError(Exception):
    pass


class OrderConflictError(Exception):
    pass


# Existing delivery stages (including the persisted API spelling 'delivering').
# Snapshot ingestion can skip stages, but may not move backwards. Unknown states
# may be imported and refreshed unchanged; transitions need an explicit mapping.
_STAGES = {"created": 0, "merchant_preparing": 1, "rider_picked": 2, "delivering": 2, "delivered": 3}


@scoped_identity
def upsert_order_state(
    payload: dict,
    *,
    user_id: str,
    tenant_id: str,
    idempotency_key: str | None = None,
    request_id: str = "",
    operator_role: str = "",
    ip: str = "",
    device_info: str = "",
) -> dict:
    if not user_id or not tenant_id:
        raise ValueError("user_id and tenant_id are required")
    now = utc_now()
    order_id = str(payload["order_id"])
    status = str(payload.get("status", ""))
    status_label = str(payload.get("status_label") or payload.get("delivery_status") or status)
    summary = str(payload.get("summary") or payload.get("delivery_status") or status_label)
    row = {
        "order_id": order_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
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
        connection.lock()
        connection.lock_resource("order", order_id)
        previous = connection.execute("SELECT * FROM runtime_order_states WHERE order_id = :p0", (order_id,)).fetchone()
        if previous and (
            previous["user_id"] != user_id or previous["tenant_id"] != tenant_id or previous["deleted_at"]
        ):
            raise OrderNotFoundError("order state not found")
        canonical = json.dumps({k: v for k, v in row.items() if k != "updated_at"}, sort_keys=True, ensure_ascii=False)
        if idempotency_key:
            saved = connection.execute(
                "SELECT * FROM runtime_order_state_requests WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND tenant_id=:p0 AND user_id=:p1 AND idempotency_key=:p2",
                (tenant_id, user_id, idempotency_key),
            ).fetchone()
            if saved:
                if saved["payload_json"] != canonical:
                    raise OrderConflictError("idempotency key already used for a different request")
                return json.loads(saved["response_json"])
        if previous and previous["status"] != status:
            old_stage, new_stage = _STAGES.get(previous["status"]), _STAGES.get(status)
            if old_stage is None or new_stage is None or new_stage < old_stage:
                raise OrderConflictError("invalid order status transition")
        unchanged = previous and all(previous[k] == v for k, v in row.items() if k != "updated_at")
        if unchanged:
            row["updated_at"] = previous["updated_at"]
        connection.execute(
            """
            INSERT INTO runtime_order_states
            (order_id, user_id, tenant_id, status, status_label, delivery_status, summary,
             refund_status, store_name, items_json, total, updated_at, created_by, created_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :_actor, :_now)
            ON CONFLICT(order_id) DO UPDATE SET
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
        result = _decode_row(row)
        if not unchanged:
            connection.execute(
                """INSERT INTO runtime_audit_logs
                   (operator_id, operator_role, action_type, object_type, object_id,
                    request_id, before_summary, after_summary, ip, device_info, created_at, tenant_id, created_by, updated_at)
                   VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :_tenant, :_actor, :_now) RETURNING id""",
                (
                    user_id,
                    operator_role,
                    "order_state_upsert",
                    "order_state",
                    order_id,
                    request_id,
                    mask_sensitive_text(json.dumps(_decode_row(previous) if previous else {}, ensure_ascii=False)),
                    mask_sensitive_text(json.dumps(result, ensure_ascii=False)),
                    ip,
                    device_info[:300],
                    now,
                ),
            )
        if idempotency_key:
            connection.execute(
                "INSERT INTO runtime_order_state_requests (tenant_id, user_id, idempotency_key, payload_json, response_json, created_by, created_at, updated_at) VALUES (:p0, :p1, :p2, :p3, :p4, :_actor, :_now, :_now)",
                (tenant_id, user_id, idempotency_key, canonical, json.dumps(result, ensure_ascii=False)),
            )
        connection.commit()
        return result
    finally:
        connection.close()


@scoped_identity
def get_order_state(order_id: str | None, *, user_id: str, tenant_id: str) -> dict | None:
    if not order_id or not user_id or not tenant_id:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM runtime_order_states WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND order_id = :p0 AND user_id = :p1 AND tenant_id = :p2",
            (order_id, user_id, tenant_id),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    return _decode_row(row)


def _decode_row(row) -> dict:
    result = dict(row)
    try:
        result["items"] = json.loads(result.pop("items_json") or "[]")
    except json.JSONDecodeError:
        result["items"] = []
    return result
