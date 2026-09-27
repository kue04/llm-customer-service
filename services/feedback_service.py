from __future__ import annotations

from services.runtime_db import get_connection

from datetime import datetime, timezone
import json

from services.privacy import mask_sensitive_payload, mask_sensitive_text




def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token_int(token_usage: dict, key: str) -> int:
    try:
        return int(token_usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def save_chat_session(query: str, reply: str, trace: dict, token_usage: dict | None = None) -> None:
    request_id = str(trace.get("request_id", ""))
    if not request_id:
        return
    safe_trace = mask_sensitive_payload(trace)
    safe_token_usage = token_usage or {}
    prompt_tokens = _token_int(safe_token_usage, "prompt_tokens")
    completion_tokens = _token_int(safe_token_usage, "completion_tokens")
    total_tokens = _token_int(safe_token_usage, "total_tokens")
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO runtime_chat_sessions
            (request_id, query, reply, trace_json, top1_intent, latency_ms,
             answer_source, user_id, session_id, order_id, token_usage_json,
             prompt_tokens, completion_tokens, total_tokens, token_counting_method, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :p12, :p13, :p14, :p15, :_tenant, :_actor, :_now)
             ON CONFLICT(tenant_id, request_id) DO UPDATE SET query = excluded.query, reply = excluded.reply, trace_json = excluded.trace_json, top1_intent = excluded.top1_intent, latency_ms = excluded.latency_ms, answer_source = excluded.answer_source, order_id = excluded.order_id, token_usage_json = excluded.token_usage_json, prompt_tokens = excluded.prompt_tokens, completion_tokens = excluded.completion_tokens, total_tokens = excluded.total_tokens, token_counting_method = excluded.token_counting_method, updated_at = :_now""",
            (
                request_id,
                mask_sensitive_text(query),
                mask_sensitive_text(reply),
                json.dumps(safe_trace, ensure_ascii=False),
                str(trace.get("top1_intent", "")),
                float(trace.get("latency_ms") or 0.0),
                str(trace.get("answer_source", "")),
                str(trace.get("user_id", "")),
                str(trace.get("session_id", "")),
                str(trace.get("order_id", "")),
                json.dumps(safe_token_usage, ensure_ascii=False),
                prompt_tokens,
                completion_tokens,
                total_tokens,
                str(safe_token_usage.get("counting_method", "")),
                utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_latest_chat_token_tracking_summary() -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT request_id, prompt_tokens, completion_tokens, total_tokens,
                   token_counting_method, created_at
            FROM runtime_chat_sessions
             WHERE tenant_id = :_tenant AND deleted_at IS NULL ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return {
            "source": "persisted_chat_sessions",
            "request_count": 0,
            "token_recorded_count": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "average_tokens_per_request": 0.0,
            "latest_request_id": "",
            "latest_created_at": "",
            "token_counting_method": "",
        }

    total_tokens = int(row["total_tokens"] or 0)
    return {
        "source": "persisted_chat_sessions",
        "request_count": 1,
        "token_recorded_count": 1 if total_tokens > 0 else 0,
        "total_prompt_tokens": int(row["prompt_tokens"] or 0),
        "total_completion_tokens": int(row["completion_tokens"] or 0),
        "total_tokens": total_tokens,
        "average_tokens_per_request": float(total_tokens),
        "latest_request_id": row["request_id"],
        "latest_created_at": row["created_at"],
        "token_counting_method": row["token_counting_method"],
    }


def save_feedback(payload: dict) -> int:
    trace = payload.get("trace") or {}
    safe_trace = mask_sensitive_payload(trace)
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO runtime_feedback
            (request_id, query, reply, helpful, reason, expected_reply, trace_json,
             top1_intent, latency_ms, answer_source, failure_stage, created_at, tenant_id, created_by, updated_at)
            VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :_tenant, :_actor, :_now)
             RETURNING id""",
            (
                payload["request_id"],
                mask_sensitive_text(payload["query"]),
                mask_sensitive_text(payload["reply"]),
                1 if payload["helpful"] else 0,
                mask_sensitive_text(payload.get("reason", "")),
                mask_sensitive_text(payload.get("expected_reply", "")),
                json.dumps(safe_trace, ensure_ascii=False),
                str(trace.get("top1_intent", "")),
                float(trace.get("latency_ms") or 0.0),
                str(trace.get("answer_source", "")),
                str(trace.get("failure_stage", "")),
                utc_now(),
            ),
        )
        connection.commit()
        return int(cursor.inserted_id)
    finally:
        connection.close()


def list_recent_feedback(
    limit: int = 20, helpful: bool | None = None, intent: str = "", failure_stage: str = ""
) -> list[dict]:
    clauses = ["tenant_id = :_tenant", "deleted_at IS NULL"]
    params: dict[str, object] = {}
    if helpful is not None:
        clauses.append("helpful = :helpful")
        params["helpful"] = 1 if helpful else 0
    if intent:
        clauses.append("top1_intent = :top1_intent")
        params["top1_intent"] = intent
    if failure_stage:
        clauses.append("failure_stage = :failure_stage")
        params["failure_stage"] = failure_stage
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params["limit"] = limit
    connection = get_connection()
    try:
        rows = connection.execute(
            f"\n            SELECT id, request_id, query, reply, helpful, reason, expected_reply,\n                   top1_intent, latency_ms, answer_source, failure_stage, exported, created_at\n            FROM runtime_feedback\n            {where_sql}\n            ORDER BY created_at DESC, id DESC\n            LIMIT :limit\n            ",
            params,
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) | {"helpful": bool(row["helpful"]), "exported": bool(row["exported"])} for row in rows]


def build_eval_case_from_feedback(feedback_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM runtime_feedback WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
            (feedback_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"feedback not found: {feedback_id}")
        connection.execute(
            "UPDATE runtime_feedback SET updated_at = :_now, exported = 1 WHERE tenant_id = :_tenant AND deleted_at IS NULL AND id = :p0",
            (feedback_id,),
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "id": f"feedback_{feedback_id}",
        "scenario": "feedback_bad_case",
        "case_type": "feedback",
        "query": row["query"],
        "expected_intent": row["top1_intent"] or "待人工确认",
        "expected_evidence_keywords": [],
        "forbidden_keywords": [],
        "notes": f"reason={row['reason']}; expected_reply={row['expected_reply']}",
    }
