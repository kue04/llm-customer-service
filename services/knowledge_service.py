from __future__ import annotations

from services.runtime_db import RuntimeConnection, get_connection

from datetime import datetime, timezone
import json
from uuid import uuid4


VALID_REVIEW_STATUSES = {"pending_review", "approved", "rejected"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_item(row: dict) -> dict:
    item = dict(row)
    item["title"] = item.get("title") or item.get("question", "")
    item["owner"] = item.get("owner") or "knowledge_ops"
    item["source"] = item.get("source") or "knowledge_ops"
    item["effective_at"] = item.get("effective_at") or ""
    item["expired_at"] = item.get("expired_at") or ""
    return item


def create_knowledge_item(payload: dict) -> dict:
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO runtime_knowledge_items
            (base_id, version, title, question, answer, category, intent, status,
             owner, source, effective_at, expired_at, created_at, updated_at, tenant_id, created_by)
            VALUES (:p0, 1, :p1, :p2, :p3, :p4, :p5, 'draft', :p6, :p7, :p8, :p9, :p10, :p11, :_tenant, :_actor)
             RETURNING id""",
            (
                f"kb_{uuid4().hex[:12]}",
                payload.get("title", "") or payload["question"],
                payload["question"],
                payload["answer"],
                payload["category"],
                payload["intent"],
                payload.get("owner", "knowledge_ops") or "knowledge_ops",
                payload.get("source", "knowledge_ops") or "knowledge_ops",
                payload.get("effective_at", "") or "",
                payload.get("expired_at", "") or "",
                now,
                now,
            ),
        )
        connection.commit()
        return get_knowledge_item(int(cursor.inserted_id))
    finally:
        connection.close()


def get_knowledge_item(item_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM runtime_knowledge_items WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
            (item_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"knowledge item not found: {item_id}")
        return row_to_item(row)
    finally:
        connection.close()


def update_knowledge_item(item_id: int, payload: dict) -> dict:
    original = get_knowledge_item(item_id)
    now = utc_now()
    connection = get_connection()
    try:
        latest_version = connection.execute(
            "SELECT MAX(version) AS version FROM runtime_knowledge_items WHERE tenant_id = :_tenant AND deleted_at IS NULL AND base_id = :p0",
            (original["base_id"],),
        ).fetchone()["version"]
        cursor = connection.execute(
            """
            INSERT INTO runtime_knowledge_items
            (base_id, version, title, question, answer, category, intent, status,
             owner, source, effective_at, expired_at, created_at, updated_at, tenant_id, created_by)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, 'draft', :p7, :p8, :p9, :p10, :p11, :p12, :_tenant, :_actor)
             RETURNING id""",
            (
                original["base_id"],
                int(latest_version) + 1,
                payload.get("title", "") or payload["question"],
                payload["question"],
                payload["answer"],
                payload["category"],
                payload["intent"],
                payload.get("owner", original.get("owner", "knowledge_ops")) or "knowledge_ops",
                payload.get("source", original.get("source", "knowledge_ops")) or "knowledge_ops",
                payload.get("effective_at", original.get("effective_at", "")) or "",
                payload.get("expired_at", original.get("expired_at", "")) or "",
                now,
                now,
            ),
        )
        connection.commit()
        return get_knowledge_item(int(cursor.inserted_id))
    finally:
        connection.close()


def archive_knowledge_item(item_id: int) -> dict:
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            "UPDATE runtime_knowledge_items SET status = 'archived', updated_at = :p0 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p1",
            (now, item_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"knowledge item not found: {item_id}")
        connection.commit()
        return get_knowledge_item(item_id)
    finally:
        connection.close()


def review_knowledge_item(item_id: int, status: str, review_note: str = "") -> dict:
    if status not in VALID_REVIEW_STATUSES:
        raise ValueError("status must be pending_review, approved or rejected")
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            UPDATE runtime_knowledge_items
            SET status = :p0, review_note = :p1, reviewed_at = :p2, updated_at = :p3
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p4
            """,
            (status, review_note, now, now, item_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"knowledge item not found: {item_id}")
        connection.commit()
        return get_knowledge_item(item_id)
    finally:
        connection.close()


def list_knowledge_items(
    limit: int = 20,
    offset: int = 0,
    category: str = "",
    intent: str = "",
    status: str = "",
    keyword: str = "",
) -> dict:
    clauses = ["tenant_id = :_tenant", "deleted_at IS NULL"]
    params: dict[str, object] = {}
    if category:
        clauses.append("category = :category")
        params["category"] = category
    if intent:
        clauses.append("intent = :intent")
        params["intent"] = intent
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if keyword:
        clauses.append("(question LIKE :keyword OR answer LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    connection = get_connection()
    try:
        total = connection.execute(
            f"SELECT COUNT(*) AS total FROM runtime_knowledge_items {where_sql}",
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"\n            SELECT * FROM runtime_knowledge_items\n            {where_sql}\n            ORDER BY updated_at DESC, id DESC\n            LIMIT :limit OFFSET :offset\n            ",
            {**params, "limit": limit, "offset": offset},
        ).fetchall()
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [row_to_item(row) for row in rows],
        }
    finally:
        connection.close()


def export_approved_jsonl() -> dict:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM runtime_knowledge_items
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND status = 'approved'
            ORDER BY base_id, version
            """
        ).fetchall()
    finally:
        connection.close()

    lines = []
    for row in rows:
        lines.append(json.dumps(build_jsonl_payload(row), ensure_ascii=False))
    return {"count": len(lines), "jsonl": "\n".join(lines)}


def build_jsonl_payload(row: dict) -> dict:
    return {
        "id": f"{row['base_id']}_v{row['version']}",
        "title": row["title"] or row["question"],
        "version": f"v{row['version']}",
        "status": "published",
        "owner": row["owner"] or "knowledge_ops",
        "source": row["source"] or "knowledge_ops",
        "effective_at": row["effective_at"] or "",
        "expired_at": row["expired_at"] or None,
        "updated_at": row["updated_at"],
        "dialogue_type": "single_turn",
        "quality": "reviewed",
        "question": row["question"],
        "answer": row["answer"],
        "category": row["category"],
        "intent": row["intent"],
        "sentiment": "neutral",
        "entities": {"risk": "待确认"},
    }


def insert_publish_history(
    connection: RuntimeConnection,
    *,
    publish_id: str,
    action: str,
    status: str,
    merged_count: int = 0,
    item_ids: list[int] | None = None,
    backup_path: str = "",
    note: str = "",
) -> dict:
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO runtime_knowledge_publish_history
        (publish_id, action, status, merged_count, item_ids, backup_path, knowledge_path, faiss_index_path, note, created_at, tenant_id, created_by, updated_at)
        VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :_tenant, :_actor, :_now)
         RETURNING id""",
        (
            publish_id,
            action,
            status,
            merged_count,
            json.dumps(item_ids or []),
            backup_path,
            "postgresql:runtime_knowledge_items",
            "",
            note,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM runtime_knowledge_publish_history WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
        (int(cursor.inserted_id),),
    ).fetchone()
    return row_to_publish_history(row)


def row_to_publish_history(row: dict) -> dict:
    item_ids = json.loads(row["item_ids"] or "[]")
    return {
        "id": int(row["id"]),
        "publish_id": row["publish_id"],
        "action": row["action"],
        "status": row["status"],
        "merged_count": int(row["merged_count"]),
        "item_ids": item_ids,
        "backup_path": row["backup_path"],
        "knowledge_path": row["knowledge_path"],
        "faiss_index_path": row["faiss_index_path"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def list_publish_history(limit: int = 20) -> dict:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM runtime_knowledge_publish_history
             WHERE tenant_id = :_tenant AND deleted_at IS NULL ORDER BY id DESC
            LIMIT :p0
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "items": [row_to_publish_history(row) for row in rows]}
    finally:
        connection.close()


def publish_approved_knowledge() -> dict:
    """Publish tenant knowledge and a durable snapshot atomically in PostgreSQL.

    Formal retrieval indexing is managed by the ingestion release pipeline.
    This operation never mutates a process-local or global seed index.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM runtime_knowledge_items WHERE tenant_id = :_tenant "
            "AND deleted_at IS NULL AND status = 'approved' ORDER BY base_id, version"
        ).fetchall()
        item_ids = [int(row["id"]) for row in rows]
        for item_id in item_ids:
            connection.execute(
                "UPDATE runtime_knowledge_items SET status = 'published', updated_at = :_now "
                "WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
                (item_id,),
            )
        result = insert_publish_history(
            connection,
            publish_id=f"pub_{uuid4().hex}",
            action="publish",
            status="succeeded" if rows else "skipped",
            merged_count=len(rows),
            item_ids=item_ids,
            note="Tenant knowledge snapshot; retrieval release uses ingestion pipeline.",
        )
        connection.commit()
        return result
    finally:
        connection.close()


def rollback_latest_publish() -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM runtime_knowledge_publish_history WHERE tenant_id = :_tenant "
            "AND deleted_at IS NULL AND action = 'publish' AND status = 'succeeded' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("no succeeded publish record to rollback")
        item_ids = json.loads(row["item_ids"])
        for item_id in item_ids:
            connection.execute(
                "UPDATE runtime_knowledge_items SET status = 'rollback', updated_at = :_now "
                "WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0 AND status = 'published'",
                (item_id,),
            )
        connection.execute(
            "UPDATE runtime_knowledge_publish_history SET status = 'rolled_back', updated_at = :_now "
            "WHERE tenant_id = :_tenant AND id = :p0",
            (row["id"],),
        )
        result = insert_publish_history(
            connection,
            publish_id=f"rollback_{uuid4().hex}",
            action="rollback",
            status="succeeded",
            merged_count=len(item_ids),
            item_ids=item_ids,
            note=f"rollback {row['publish_id']}",
        )
        connection.commit()
        return result
    finally:
        connection.close()
