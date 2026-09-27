from __future__ import annotations

from services.runtime_db import get_connection, scoped_identity

from datetime import datetime, timezone
import json
from uuid import uuid4

from services.order_tool_service import create_handoff_ticket
from services.ops_metrics import record_review_action_metrics
from services.privacy import mask_sensitive_payload, mask_sensitive_text

REVIEW_STATUS_BY_ACTION = {
    "accepted": "accepted",
    "edited_and_sent": "edited_and_sent",
    "human_handoff": "human_handoff",
    "marked_bad_case": "marked_bad_case",
}


class ConversationAccessError(PermissionError):
    """An existing session is outside the caller's user/tenant boundary."""


def _require_session(connection, session_id: str) -> None:
    row = connection.execute(
        "SELECT session_id FROM runtime_conversations "
        "WHERE session_id = :p0 AND tenant_id = :_tenant "
        "AND created_by = :_actor AND deleted_at IS NULL",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ConversationAccessError("conversation not found")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_user(user_id: str) -> None:
    now = utc_now()
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO runtime_users (user_id, created_at, last_seen_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :_tenant, :_actor, :_now)
            ON CONFLICT(tenant_id, user_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at, updated_at = excluded.updated_at
            """,
            (user_id, now, now),
        )
        connection.commit()
    finally:
        connection.close()


@scoped_identity
def get_or_create_conversation(
    user_id: str,
    session_id: str | None = None,
    order_id: str | None = None,
    *,
    tenant_id: str = "",
) -> dict:
    upsert_user(user_id)
    now = utc_now()
    resolved_session_id = session_id or uuid4().hex
    connection = get_connection()
    try:
        # Serialize lookup and creation: concurrent callers cannot claim a session.
        connection.lock()
        connection.lock_resource("conversation", resolved_session_id)
        row = connection.execute(
            "SELECT * FROM runtime_conversations WHERE session_id = :p0",
            (resolved_session_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO runtime_conversations
                (session_id, user_id, tenant_id, order_id, status, summary, created_at, updated_at, created_by)
                VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :_actor)
                """,
                (resolved_session_id, user_id, tenant_id, order_id, "active", "", now, now),
            )
        else:
            if row["user_id"] != user_id or row["tenant_id"] != tenant_id or row["deleted_at"]:
                raise ConversationAccessError("conversation not found")
            connection.execute(
                """
                UPDATE runtime_conversations
                SET order_id = COALESCE(:p0, order_id), updated_at = :p1
                WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p2
                """,
                (order_id, now, resolved_session_id),
            )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM runtime_conversations WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0",
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
        _require_session(connection, session_id)
        connection.execute(
            """
            INSERT INTO runtime_conversation_messages
            (session_id, role, content, intent_json, risk_level, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :_tenant, :_actor, :_now)
             RETURNING id""",
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
            "UPDATE runtime_conversations SET updated_at = :p0 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p1",
            (now, session_id),
        )
        connection.commit()
    finally:
        connection.close()


@scoped_identity
def find_conversation(
    user_id: str,
    order_id: str | None = None,
    session_id: str | None = None,
    *,
    tenant_id: str = "",
) -> dict | None:
    connection = get_connection()
    try:
        if session_id:
            row = connection.execute(
                "SELECT * FROM runtime_conversations WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0 AND user_id = :p1 AND tenant_id = :p2",
                (session_id, user_id, tenant_id),
            ).fetchone()
        elif order_id:
            row = connection.execute(
                """
                SELECT * FROM runtime_conversations
                WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND user_id = :p0 AND order_id = :p1 AND tenant_id = :p2
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, order_id, tenant_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT * FROM runtime_conversations
                WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND user_id = :p0 AND tenant_id = :p1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, tenant_id),
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
            FROM runtime_conversation_messages
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0
            ORDER BY id DESC
            LIMIT :p1
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
        _require_session(connection, session_id)
        connection.execute(
            """
            INSERT INTO runtime_conversation_turns
            (request_id, session_id, user_id, order_id, query, reply, response_json, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :_tenant, :_actor, :_now)
             ON CONFLICT(tenant_id, request_id) DO UPDATE SET order_id = excluded.order_id, query = excluded.query, reply = excluded.reply, response_json = excluded.response_json, updated_at = :_now WHERE runtime_conversation_turns.created_by = :_actor AND runtime_conversation_turns.session_id = excluded.session_id""",
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
            FROM runtime_conversation_turns
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND request_id = :p0
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
            "UPDATE runtime_conversations SET status = :p0, updated_at = :p1 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p2",
            (status, utc_now(), session_id),
        )
        connection.commit()
    finally:
        connection.close()


@scoped_identity
def save_review_action(payload: dict, *, user_id: str | None = None, tenant_id: str | None = None) -> dict:
    action = str(payload.get("action", ""))
    if action not in REVIEW_STATUS_BY_ACTION:
        raise ValueError("unsupported review action")

    turn = get_turn_response(str(payload["request_id"]))
    if user_id is not None or tenant_id is not None:
        if (
            not user_id
            or not tenant_id
            or not find_conversation(
                user_id,
                session_id=turn["session_id"],
                tenant_id=tenant_id,
            )
        ):
            raise ConversationAccessError("conversation not found")
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
            INSERT INTO runtime_conversation_review_actions
            (request_id, session_id, user_id, order_id, action, status, original_reply,
             final_reply, reason, operator_id, operator_role, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :_tenant, :_actor, :_now)
             RETURNING id""",
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
            UPDATE runtime_conversation_turns
            SET updated_at = :_now, response_json = :p0
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND request_id = :p1
            """,
            (json.dumps(mask_sensitive_payload(response), ensure_ascii=False), turn["request_id"]),
        )
        connection.execute(
            "UPDATE runtime_conversations SET status = :p0, updated_at = :p1 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p2",
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
            FROM runtime_conversation_turns
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0
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
            FROM runtime_conversation_messages
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0
            ORDER BY id DESC
            LIMIT :p1
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
            "SELECT COUNT(*) AS count FROM runtime_conversation_messages WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0",
            (session_id,),
        ).fetchone()
        return int(row["count"])
    finally:
        connection.close()


def get_summary(session_id: str) -> str:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT summary FROM runtime_conversations WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0",
            (session_id,),
        ).fetchone()
        return str(row["summary"] if row else "")
    finally:
        connection.close()


def update_summary(session_id: str, summary: str) -> None:
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE runtime_conversations SET summary = :p0, updated_at = :p1 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p2",
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
            FROM runtime_conversation_facts
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND session_id = :p0
            ORDER BY updated_at DESC
            LIMIT :p1
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
        _require_session(connection, session_id)
        for key, value in facts.items():
            connection.execute(
                """
                INSERT INTO runtime_conversation_facts (session_id, key, value, source, updated_at, tenant_id, created_by, created_at)
                VALUES (:p0, :p1, :p2, :p3, :p4, :_tenant, :_actor, :_now)
                ON CONFLICT(tenant_id, session_id, key) DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (session_id, key, mask_sensitive_text(str(value)), source, now),
            )
        connection.commit()
    finally:
        connection.close()


@scoped_identity
def get_user_memory(user_id: str, *, tenant_id: str = "") -> dict[str, str]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT key, value
            FROM runtime_user_memory
            WHERE tenant_id = :_tenant AND deleted_at IS NULL AND created_by = :_actor AND user_id = :p0 AND tenant_id = :p1
            ORDER BY updated_at DESC
            """,
            (user_id, tenant_id),
        ).fetchall()
    finally:
        connection.close()
    return {str(row["key"]): str(row["value"]) for row in rows}


@scoped_identity
def upsert_user_memory(user_id: str, memory: dict[str, str], *, tenant_id: str = "") -> None:
    if not memory:
        return

    now = utc_now()
    connection = get_connection()
    try:
        for key, value in memory.items():
            connection.execute(
                """
                INSERT INTO runtime_user_memory (tenant_id, user_id, key, value, updated_at, created_by, created_at)
                VALUES (:p0, :p1, :p2, :p3, :p4, :_actor, :_now)
                ON CONFLICT(tenant_id, user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, user_id, key, mask_sensitive_text(str(value)), now),
            )
        connection.commit()
    finally:
        connection.close()
