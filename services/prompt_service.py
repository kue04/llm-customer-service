from __future__ import annotations

from services.runtime_db import get_connection

from datetime import datetime, timezone
from uuid import uuid4


DEFAULT_SYSTEM_PROMPT = (
    "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，"
    "最后给出可执行的下一步。不要编造平台规则；遇到支付、隐私、食品安全、"
    "站外交易等高风险问题时要提醒用户保留证据并通过官方渠道处理。"
)
DEFAULT_DEVELOPER_PROMPT = (
    "订单工具结果优先于用户描述；已发布知识库优先于模型常识；证据不足时必须保守表达并建议人工审核。"
)
VERSION_STATUSES = {"draft", "evaluation", "approved", "canary", "production", "rollback"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_prompt_version(row: dict) -> dict:
    return dict(row)


def list_prompt_versions(limit: int = 20) -> dict:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM runtime_prompt_versions
             WHERE tenant_id = :_tenant AND deleted_at IS NULL ORDER BY id DESC
            LIMIT :p0
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "items": [row_to_prompt_version(row) for row in rows]}
    finally:
        connection.close()


def get_prompt_version(version_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM runtime_prompt_versions WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"prompt version not found: {version_id}")
        return row_to_prompt_version(row)
    finally:
        connection.close()


def get_active_prompt_config() -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT * FROM runtime_prompt_versions
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND status = 'production'
            ORDER BY activated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return {
                "id": 0,
                "version": "builtin_v1",
                "status": "production",
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "developer_prompt": DEFAULT_DEVELOPER_PROMPT,
                "change_reason": "read-only built-in default",
                "author": "system",
                "evaluation_result": "",
                "effective_at": "",
                "created_at": "",
                "activated_at": "",
                "rolled_back_from": "",
            }
        return row_to_prompt_version(row)
    finally:
        connection.close()


def create_prompt_version(payload: dict, author: str) -> dict:
    now = utc_now()
    version = (
        payload.get("version") or f"prompt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"
    )
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO runtime_prompt_versions
            (version, status, system_prompt, developer_prompt, change_reason,
             author, evaluation_result, effective_at, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, 'draft', :p1, :p2, :p3, :p4, :p5, :p6, :p7, :_tenant, :_actor, :_now)
             RETURNING id""",
            (
                version,
                payload["system_prompt"],
                payload.get("developer_prompt", ""),
                payload.get("change_reason", ""),
                author,
                payload.get("evaluation_result", ""),
                payload.get("effective_at", ""),
                now,
            ),
        )
        connection.commit()
        return get_prompt_version(int(cursor.inserted_id))
    finally:
        connection.close()


def update_prompt_version_status(version_id: int, status: str, evaluation_result: str = "") -> dict:
    if status not in {"evaluation", "approved", "canary"}:
        raise ValueError("status must be evaluation, approved or canary")
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            UPDATE runtime_prompt_versions
            SET updated_at = :_now, status = :p0, evaluation_result = COALESCE(NULLIF(:p1, ''), evaluation_result),
                effective_at = COALESCE(NULLIF(effective_at, ''), :p2)
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p3
            """,
            (status, evaluation_result, now, version_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"prompt version not found: {version_id}")
        connection.commit()
        return get_prompt_version(version_id)
    finally:
        connection.close()


def activate_prompt_version(version_id: int) -> dict:
    now = utc_now()
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM runtime_prompt_versions WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"prompt version not found: {version_id}")
        if row["status"] not in {"approved", "canary", "production"}:
            raise ValueError("prompt version must be approved or canary before production")
        connection.execute(
            "UPDATE runtime_prompt_versions SET updated_at = :_now, status = 'approved' WHERE tenant_id = :_tenant AND deleted_at IS NULL AND status = 'production' AND id != :p0",
            (version_id,),
        )
        connection.execute(
            """
            UPDATE runtime_prompt_versions
            SET updated_at = :_now, status = 'production', effective_at = COALESCE(NULLIF(effective_at, ''), :p0), activated_at = :p1
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p2
            """,
            (now, now, version_id),
        )
        connection.commit()
        return get_prompt_version(version_id)
    finally:
        connection.close()


def rollback_latest_prompt_version() -> dict:
    now = utc_now()
    connection = get_connection()
    try:
        current = connection.execute(
            """
            SELECT * FROM runtime_prompt_versions
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND status = 'production'
            ORDER BY activated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if current is None:
            raise ValueError("no production prompt version to rollback")
        previous = connection.execute(
            """
            SELECT * FROM runtime_prompt_versions
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id != :p0 AND status IN ('approved', 'canary')
            ORDER BY activated_at DESC, id DESC
            LIMIT 1
            """,
            (int(current["id"]),),
        ).fetchone()
        if previous is None:
            raise ValueError("no previous prompt version to rollback")
        connection.execute(
            "UPDATE runtime_prompt_versions SET updated_at = :_now, status = 'rollback', rolled_back_from = :p0 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p1",
            (previous["version"], int(current["id"])),
        )
        connection.execute(
            """
            UPDATE runtime_prompt_versions
            SET updated_at = :_now, status = 'production', effective_at = COALESCE(NULLIF(effective_at, ''), :p0), activated_at = :p1
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p2
            """,
            (now, now, int(previous["id"])),
        )
        connection.commit()
        return get_prompt_version(int(previous["id"]))
    finally:
        connection.close()
