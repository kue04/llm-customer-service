from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from services.privacy import mask_sensitive_payload, mask_sensitive_text


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "ops_feedback.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            request_id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            reply TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            top1_intent TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            answer_source TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    _ensure_column(connection, "chat_sessions", "user_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "chat_sessions", "session_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "chat_sessions", "order_id", "TEXT")
    _ensure_column(connection, "chat_sessions", "token_usage_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "chat_sessions", "prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "chat_sessions", "completion_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "chat_sessions", "total_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "chat_sessions", "token_counting_method", "TEXT NOT NULL DEFAULT ''")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            query TEXT NOT NULL,
            reply TEXT NOT NULL,
            helpful INTEGER NOT NULL,
            reason TEXT NOT NULL,
            expected_reply TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            top1_intent TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            answer_source TEXT NOT NULL,
            failure_stage TEXT NOT NULL,
            exported INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


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
            INSERT OR REPLACE INTO chat_sessions
            (request_id, query, reply, trace_json, top1_intent, latency_ms,
             answer_source, user_id, session_id, order_id, token_usage_json,
             prompt_tokens, completion_tokens, total_tokens, token_counting_method, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
            FROM chat_sessions
            ORDER BY created_at DESC
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
            INSERT INTO feedback
            (request_id, query, reply, helpful, reason, expected_reply, trace_json,
             top1_intent, latency_ms, answer_source, failure_stage, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
        return int(cursor.lastrowid)
    finally:
        connection.close()


def list_recent_feedback(limit: int = 20, helpful: bool | None = None, intent: str = "", failure_stage: str = "") -> list[dict]:
    clauses = []
    params: list[object] = []
    if helpful is not None:
        clauses.append("helpful = ?")
        params.append(1 if helpful else 0)
    if intent:
        clauses.append("top1_intent = ?")
        params.append(intent)
    if failure_stage:
        clauses.append("failure_stage = ?")
        params.append(failure_stage)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, request_id, query, reply, helpful, reason, expected_reply,
                   top1_intent, latency_ms, answer_source, failure_stage, exported, created_at
            FROM feedback
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) | {"helpful": bool(row["helpful"]), "exported": bool(row["exported"])} for row in rows]


def build_eval_case_from_feedback(feedback_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        if row is None:
            raise KeyError(f"feedback not found: {feedback_id}")
        connection.execute("UPDATE feedback SET exported = 1 WHERE id = ?", (feedback_id,))
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
