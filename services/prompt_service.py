from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "prompt_versions.db"
DEFAULT_SYSTEM_PROMPT = (
    "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，"
    "最后给出可执行的下一步。不要编造平台规则；遇到支付、隐私、食品安全、"
    "站外交易等高风险问题时要提醒用户保留证据并通过官方渠道处理。"
)
DEFAULT_DEVELOPER_PROMPT = (
    "订单工具结果优先于用户描述；已发布知识库优先于模型常识；"
    "证据不足时必须保守表达并建议人工审核。"
)
VERSION_STATUSES = {"draft", "evaluation", "approved", "canary", "production", "rollback"}


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
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            developer_prompt TEXT NOT NULL DEFAULT '',
            change_reason TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            evaluation_result TEXT NOT NULL DEFAULT '',
            effective_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            activated_at TEXT NOT NULL DEFAULT '',
            rolled_back_from TEXT NOT NULL DEFAULT ''
        )
        """
    )
    row = connection.execute("SELECT COUNT(*) AS count FROM prompt_versions").fetchone()
    if int(row["count"]) == 0:
        now = utc_now()
        connection.execute(
            """
            INSERT INTO prompt_versions
            (version, status, system_prompt, developer_prompt, change_reason,
             author, evaluation_result, effective_at, created_at, activated_at)
            VALUES (?, 'production', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prompt_v1",
                DEFAULT_SYSTEM_PROMPT,
                DEFAULT_DEVELOPER_PROMPT,
                "initial default prompt",
                "system",
                "seed",
                now,
                now,
                now,
            ),
        )
    connection.commit()


def row_to_prompt_version(row: sqlite3.Row) -> dict:
    return dict(row)


def list_prompt_versions(limit: int = 20) -> dict:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM prompt_versions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "items": [row_to_prompt_version(row) for row in rows]}
    finally:
        connection.close()


def get_prompt_version(version_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM prompt_versions WHERE id = ?", (version_id,)).fetchone()
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
            SELECT * FROM prompt_versions
            WHERE status = 'production'
            ORDER BY activated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("no production prompt version")
        return row_to_prompt_version(row)
    finally:
        connection.close()


def create_prompt_version(payload: dict, author: str) -> dict:
    now = utc_now()
    version = payload.get("version") or f"prompt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO prompt_versions
            (version, status, system_prompt, developer_prompt, change_reason,
             author, evaluation_result, effective_at, created_at)
            VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?)
            """,
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
        return get_prompt_version(int(cursor.lastrowid))
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
            UPDATE prompt_versions
            SET status = ?, evaluation_result = COALESCE(NULLIF(?, ''), evaluation_result),
                effective_at = COALESCE(NULLIF(effective_at, ''), ?)
            WHERE id = ?
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
        row = connection.execute("SELECT * FROM prompt_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(f"prompt version not found: {version_id}")
        if row["status"] not in {"approved", "canary", "production"}:
            raise ValueError("prompt version must be approved or canary before production")
        connection.execute(
            "UPDATE prompt_versions SET status = 'approved' WHERE status = 'production' AND id != ?",
            (version_id,),
        )
        connection.execute(
            """
            UPDATE prompt_versions
            SET status = 'production', effective_at = COALESCE(NULLIF(effective_at, ''), ?), activated_at = ?
            WHERE id = ?
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
            SELECT * FROM prompt_versions
            WHERE status = 'production'
            ORDER BY activated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if current is None:
            raise ValueError("no production prompt version to rollback")
        previous = connection.execute(
            """
            SELECT * FROM prompt_versions
            WHERE id != ? AND status IN ('approved', 'canary')
            ORDER BY activated_at DESC, id DESC
            LIMIT 1
            """,
            (int(current["id"]),),
        ).fetchone()
        if previous is None:
            raise ValueError("no previous prompt version to rollback")
        connection.execute(
            "UPDATE prompt_versions SET status = 'rollback', rolled_back_from = ? WHERE id = ?",
            (previous["version"], int(current["id"])),
        )
        connection.execute(
            """
            UPDATE prompt_versions
            SET status = 'production', effective_at = COALESCE(NULLIF(effective_at, ''), ?), activated_at = ?
            WHERE id = ?
            """,
            (now, now, int(previous["id"])),
        )
        connection.commit()
        return get_prompt_version(int(previous["id"]))
    finally:
        connection.close()
