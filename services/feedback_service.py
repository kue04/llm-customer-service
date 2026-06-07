from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_chat_session(query: str, reply: str, trace: dict) -> None:
    request_id = str(trace.get("request_id", ""))
    if not request_id:
        return
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT OR REPLACE INTO chat_sessions
            (request_id, query, reply, trace_json, top1_intent, latency_ms, answer_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                query,
                reply,
                json.dumps(trace, ensure_ascii=False),
                str(trace.get("top1_intent", "")),
                float(trace.get("latency_ms") or 0.0),
                str(trace.get("answer_source", "")),
                utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def save_feedback(payload: dict) -> int:
    trace = payload.get("trace") or {}
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
                payload["query"],
                payload["reply"],
                1 if payload["helpful"] else 0,
                payload.get("reason", ""),
                payload.get("expected_reply", ""),
                json.dumps(trace, ensure_ascii=False),
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
