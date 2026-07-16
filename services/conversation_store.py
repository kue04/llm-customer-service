from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

from services.feedback_service import DB_PATH
from services.order_tool_service import create_handoff_ticket
from services.ops_metrics import record_review_action_metrics
from services.privacy import mask_sensitive_payload, mask_sensitive_text

REVIEW_STATUS_BY_ACTION = {
    "accepted": "accepted",
    "edited_and_sent": "edited_and_sent",
    "human_handoff": "human_handoff",
    "marked_bad_case": "marked_bad_case",
}


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
        CREATE TABLE IF NOT EXISTS conversation_turns (
            request_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            order_id TEXT,
            query TEXT NOT NULL,
            reply TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_review_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            order_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            original_reply TEXT NOT NULL,
            final_reply TEXT NOT NULL,
            reason TEXT NOT NULL,
            operator_id TEXT NOT NULL,
            operator_role TEXT NOT NULL,
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
    safe_content = mask_sensitive_text(content)
    safe_intent_analysis = mask_sensitive_payload(intent_analysis or {})
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
                safe_content,
                json.dumps(safe_intent_analysis, ensure_ascii=False),
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


def find_conversation(
    user_id: str,
    order_id: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    connection = get_connection()
    try:
        if session_id:
            row = connection.execute(
                "SELECT * FROM conversations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        elif order_id:
            row = connection.execute(
                """
                SELECT * FROM conversations
                WHERE user_id = ? AND order_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, order_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT * FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def list_messages(session_id: str, limit: int = 50) -> list[dict]:
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


def save_turn_response(
    request_id: str,
    session_id: str,
    user_id: str,
    order_id: str | None,
    query: str,
    reply: str,
    response: dict,
) -> None:
    if not request_id:
        return
    now = utc_now()
    safe_response = mask_sensitive_payload(response)
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT OR REPLACE INTO conversation_turns
            (request_id, session_id, user_id, order_id, query, reply, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                session_id,
                user_id,
                order_id,
                mask_sensitive_text(query),
                mask_sensitive_text(reply),
                json.dumps(safe_response, ensure_ascii=False),
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_turn_response(request_id: str) -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT request_id, session_id, user_id, order_id, query, reply, response_json, created_at
            FROM conversation_turns
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise KeyError(f"chat turn not found: {request_id}")

    turn = dict(row)
    try:
        turn["response"] = json.loads(turn.pop("response_json") or "{}")
    except json.JSONDecodeError:
        turn["response"] = {}
    return turn


def set_conversation_status(session_id: str, status: str) -> None:
    if not session_id:
        return
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, utc_now(), session_id),
        )
        connection.commit()
    finally:
        connection.close()


def save_review_action(payload: dict) -> dict:
    action = str(payload.get("action", ""))
    if action not in REVIEW_STATUS_BY_ACTION:
        raise ValueError("unsupported review action")

    turn = get_turn_response(str(payload["request_id"]))
    status = REVIEW_STATUS_BY_ACTION[action]
    final_reply = str(payload.get("final_reply") or turn["reply"])
    reason = str(payload.get("reason", ""))
    safe_final_reply = mask_sensitive_text(final_reply)
    safe_reason = mask_sensitive_text(reason)
    operator_id = str(payload.get("operator_id", "demo_agent"))
    operator_role = str(payload.get("operator_role", "agent"))
    now = utc_now()
    response = turn.get("response") or {}
    memory_snapshot = response.get("memory_snapshot") or {}
    short_term = memory_snapshot.get("short_term") or {}
    handoff_ticket = None
    if action == "human_handoff":
        ticket_result = create_handoff_ticket(
            safe_reason or "客服确认转人工",
            {
                "user_id": turn["user_id"],
                "session_id": turn["session_id"],
                "order_id": turn["order_id"],
                "summary": mask_sensitive_text(short_term.get("summary", "")),
                "facts": mask_sensitive_payload(short_term.get("facts", {})),
            },
        )
        handoff_ticket = mask_sensitive_payload(ticket_result.get("output", {}))

    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO conversation_review_actions
            (request_id, session_id, user_id, order_id, action, status, original_reply,
             final_reply, reason, operator_id, operator_role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn["request_id"],
                turn["session_id"],
                turn["user_id"],
                turn["order_id"],
                action,
                status,
                mask_sensitive_text(turn["reply"]),
                safe_final_reply,
                safe_reason,
                operator_id,
                operator_role,
                now,
            ),
        )

        response["conversation_status"] = status
        if handoff_ticket:
            response["handoff_ticket"] = handoff_ticket
        response["review_action"] = {
            "action": action,
            "status": status,
            "final_reply": safe_final_reply,
            "reason": safe_reason,
            "operator_id": operator_id,
            "operator_role": operator_role,
            "handoff_ticket": handoff_ticket,
            "created_at": now,
        }
        connection.execute(
            """
            UPDATE conversation_turns
            SET response_json = ?
            WHERE request_id = ?
            """,
            (json.dumps(mask_sensitive_payload(response), ensure_ascii=False), turn["request_id"]),
        )
        connection.execute(
            "UPDATE conversations SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, now, turn["session_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    record_review_action_metrics(action)
    return {
        "request_id": turn["request_id"],
        "session_id": turn["session_id"],
        "user_id": turn["user_id"],
        "order_id": turn["order_id"],
        "action": action,
        "status": status,
        "final_reply": safe_final_reply,
        "reason": safe_reason,
        "handoff_ticket": handoff_ticket,
        "saved": True,
        "created_at": now,
    }


def get_latest_turn_response(session_id: str) -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT response_json
            FROM conversation_turns
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return {}
    try:
        return json.loads(row["response_json"] or "{}")
    except json.JSONDecodeError:
        return {}


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
            (mask_sensitive_text(summary)[:500], utc_now(), session_id),
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
                (session_id, key, mask_sensitive_text(str(value)), source, now),
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
                (user_id, key, mask_sensitive_text(str(value)), now),
            )
        connection.commit()
    finally:
        connection.close()
