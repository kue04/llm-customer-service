from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

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
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            order_id TEXT,
            status TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            intent_json TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_facts (
            session_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (session_id, key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
        """
    )
    connection.commit()


def upsert_user(user_id: str) -> None:
    now = utc_now()
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO users (user_id, created_at, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (user_id, now, now),
        )
        connection.commit()
    finally:
        connection.close()


def get_or_create_conversation(
    user_id: str,
    session_id: str | None = None,
    order_id: str | None = None,
) -> dict:
    upsert_user(user_id)
    now = utc_now()
    resolved_session_id = session_id or uuid4().hex
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM conversations WHERE session_id = ?",
            (resolved_session_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO conversations
                (session_id, user_id, order_id, status, summary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (resolved_session_id, user_id, order_id, "active", "", now, now),
            )
        else:
            connection.execute(
                """
                UPDATE conversations
                SET user_id = ?, order_id = COALESCE(?, order_id), updated_at = ?
                WHERE session_id = ?
                """,
                (user_id, order_id, now, resolved_session_id),
            )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM conversations WHERE session_id = ?",
            (resolved_session_id,),
        ).fetchone()
        return dict(row)
    finally:
        connection.close()


def append_message(
    session_id: str,
    role: str,
    content: str,
    intent_analysis: dict | None = None,
    risk_level: str = "low",
) -> None:
    now = utc_now()
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO conversation_messages
            (session_id, role, content, intent_json, risk_level, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                content,
                json.dumps(intent_analysis or {}, ensure_ascii=False),
                risk_level,
                now,
            ),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        connection.commit()
    finally:
        connection.close()


def list_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT role, content, intent_json, risk_level, created_at
            FROM conversation_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    finally:
        connection.close()
    messages = [dict(row) for row in reversed(rows)]
    for message in messages:
        try:
            message["intent"] = json.loads(message.pop("intent_json") or "{}")
        except json.JSONDecodeError:
            message["intent"] = {}
    return messages


def count_messages(session_id: str) -> int:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["count"])
    finally:
        connection.close()


def get_summary(session_id: str) -> str:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT summary FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return str(row["summary"] if row else "")
    finally:
        connection.close()


def update_summary(session_id: str, summary: str) -> None:
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE conversations SET summary = ?, updated_at = ? WHERE session_id = ?",
            (summary[:500], utc_now(), session_id),
        )
        connection.commit()
    finally:
        connection.close()


def get_facts(session_id: str, limit: int = 10) -> dict[str, str]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT key, value
            FROM conversation_facts
            WHERE session_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    finally:
        connection.close()
    return {str(row["key"]): str(row["value"]) for row in rows}


def upsert_facts(session_id: str, facts: dict[str, str], source: str = "system") -> None:
    if not facts:
        return

    now = utc_now()
    connection = get_connection()
    try:
        for key, value in facts.items():
            connection.execute(
                """
                INSERT INTO conversation_facts (session_id, key, value, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, key) DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (session_id, key, str(value), source, now),
            )
        connection.commit()
    finally:
        connection.close()


def get_user_memory(user_id: str) -> dict[str, str]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT key, value
            FROM user_memory
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    finally:
        connection.close()
    return {str(row["key"]): str(row["value"]) for row in rows}


def upsert_user_memory(user_id: str, memory: dict[str, str]) -> None:
    if not memory:
        return

    now = utc_now()
    connection = get_connection()
    try:
        for key, value in memory.items():
            connection.execute(
                """
                INSERT INTO user_memory (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (user_id, key, str(value), now),
            )
        connection.commit()
    finally:
        connection.close()
