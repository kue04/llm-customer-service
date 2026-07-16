from __future__ import annotations

import json
import sqlite3
from threading import Lock


_lock = Lock()
_latencies: list[float] = []
_metrics = {
    "request_count": 0,
    "failure_count": 0,
    "empty_retrieval_count": 0,
    "reply_rules_hit_count": 0,
    "fallback_count": 0,
    "accepted_count": 0,
    "edited_sent_count": 0,
    "human_handoff_count": 0,
    "bad_case_count": 0,
    "token_recorded_count": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_tokens": 0,
}


def record_chat_metrics(trace: dict) -> None:
    with _lock:
        _metrics["request_count"] += 1
        failure_stage = str(trace.get("failure_stage") or "")
        if trace.get("degraded") or failure_stage not in {"", "none"}:
            _metrics["failure_count"] += 1
        if int(trace.get("retrieval_count") or 0) == 0:
            _metrics["empty_retrieval_count"] += 1
        if trace.get("reply_rules_applied"):
            _metrics["reply_rules_hit_count"] += 1
        if trace.get("used_fallback_prompt") or trace.get("answer_source") == "fallback":
            _metrics["fallback_count"] += 1

        _latencies.append(float(trace.get("latency_ms") or 0.0))
        if len(_latencies) > 1000:
            del _latencies[:-1000]


def record_token_usage(token_usage: dict) -> None:
    with _lock:
        if "total_tokens" in token_usage:
            _metrics["token_recorded_count"] += 1
        _metrics["total_prompt_tokens"] += int(token_usage.get("prompt_tokens") or 0)
        _metrics["total_completion_tokens"] += int(token_usage.get("completion_tokens") or 0)
        _metrics["total_tokens"] += int(token_usage.get("total_tokens") or 0)


def record_review_action_metrics(action: str) -> None:
    metric_by_action = {
        "accepted": "accepted_count",
        "edited_and_sent": "edited_sent_count",
        "human_handoff": "human_handoff_count",
        "marked_bad_case": "bad_case_count",
    }
    metric_name = metric_by_action.get(action)
    if not metric_name:
        return
    with _lock:
        _metrics[metric_name] += 1


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _latency_summary(latencies: list[float]) -> tuple[float, float]:
    sorted_latencies = sorted(latencies)
    count = len(sorted_latencies)
    if not count:
        return 0.0, 0.0
    average = round(sum(sorted_latencies) / count, 2)
    p95 = sorted_latencies[min(int(count * 0.95), count - 1)]
    return average, round(p95, 2)


def _empty_metrics(source: str = "") -> dict:
    metrics = {key: 0 for key in _metrics}
    metrics.update(
        {
            "average_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "reviewed_count": 0,
            "accepted_rate": 0.0,
            "edited_sent_rate": 0.0,
            "human_handoff_rate": 0.0,
            "bad_case_rate": 0.0,
            "token_record_rate": 0.0,
            "average_tokens_per_request": 0.0,
        }
    )
    if source:
        metrics["source"] = source
    return metrics


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _safe_json_loads(payload: str) -> dict:
    try:
        value = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def get_persisted_ops_metrics() -> dict:
    from services import feedback_service

    connection = feedback_service.get_connection()
    try:
        rows = connection.execute(
            """
            SELECT trace_json, latency_ms, answer_source,
                   prompt_tokens, completion_tokens, total_tokens
            FROM chat_sessions
            """
        ).fetchall()

        metrics = _empty_metrics(source="persisted_chat_sessions")
        latencies: list[float] = []
        for row in rows:
            trace = _safe_json_loads(str(row["trace_json"] or "{}"))
            metrics["request_count"] += 1

            failure_stage = str(trace.get("failure_stage") or "")
            if trace.get("degraded") or failure_stage not in {"", "none"}:
                metrics["failure_count"] += 1
            if int(trace.get("retrieval_count") or 0) == 0:
                metrics["empty_retrieval_count"] += 1
            if trace.get("reply_rules_applied"):
                metrics["reply_rules_hit_count"] += 1

            answer_source = str(trace.get("answer_source") or row["answer_source"] or "")
            if trace.get("used_fallback_prompt") or answer_source == "fallback":
                metrics["fallback_count"] += 1

            latencies.append(float(row["latency_ms"] or 0.0))
            prompt_tokens = int(row["prompt_tokens"] or 0)
            completion_tokens = int(row["completion_tokens"] or 0)
            total_tokens = int(row["total_tokens"] or 0)
            if total_tokens > 0:
                metrics["token_recorded_count"] += 1
            metrics["total_prompt_tokens"] += prompt_tokens
            metrics["total_completion_tokens"] += completion_tokens
            metrics["total_tokens"] += total_tokens

        if _table_exists(connection, "conversation_review_actions"):
            review_rows = connection.execute(
                """
                SELECT action, COUNT(*) AS count
                FROM conversation_review_actions
                GROUP BY action
                """
            ).fetchall()
            metric_by_action = {
                "accepted": "accepted_count",
                "edited_and_sent": "edited_sent_count",
                "human_handoff": "human_handoff_count",
                "marked_bad_case": "bad_case_count",
            }
            for row in review_rows:
                metric_name = metric_by_action.get(str(row["action"]))
                if metric_name:
                    metrics[metric_name] = int(row["count"] or 0)
    finally:
        connection.close()

    request_count = int(metrics["request_count"])
    reviewed_count = (
        int(metrics["accepted_count"])
        + int(metrics["edited_sent_count"])
        + int(metrics["human_handoff_count"])
        + int(metrics["bad_case_count"])
    )
    average_latency_ms, p95_latency_ms = _latency_summary(latencies)
    metrics.update(
        {
            "average_latency_ms": average_latency_ms,
            "p95_latency_ms": p95_latency_ms,
            "reviewed_count": reviewed_count,
            "accepted_rate": _rate(int(metrics["accepted_count"]), request_count),
            "edited_sent_rate": _rate(int(metrics["edited_sent_count"]), request_count),
            "human_handoff_rate": _rate(int(metrics["human_handoff_count"]), request_count),
            "bad_case_rate": _rate(int(metrics["bad_case_count"]), request_count),
            "token_record_rate": _rate(int(metrics["token_recorded_count"]), request_count),
            "average_tokens_per_request": (
                round(int(metrics["total_tokens"]) / request_count, 2)
                if request_count
                else 0.0
            ),
        }
    )
    return metrics


def get_ops_metrics() -> dict:
    persisted_metrics = get_persisted_ops_metrics()
    if (
        int(persisted_metrics.get("request_count") or 0) > 0
        or int(persisted_metrics.get("reviewed_count") or 0) > 0
    ):
        return persisted_metrics

    with _lock:
        average, p95 = _latency_summary(_latencies)
        request_count = int(_metrics["request_count"])
        reviewed_count = (
            int(_metrics["accepted_count"])
            + int(_metrics["edited_sent_count"])
            + int(_metrics["human_handoff_count"])
            + int(_metrics["bad_case_count"])
        )
        return {
            **_metrics,
            "average_latency_ms": average,
            "p95_latency_ms": p95,
            "reviewed_count": reviewed_count,
            "accepted_rate": _rate(int(_metrics["accepted_count"]), request_count),
            "edited_sent_rate": _rate(int(_metrics["edited_sent_count"]), request_count),
            "human_handoff_rate": _rate(int(_metrics["human_handoff_count"]), request_count),
            "bad_case_rate": _rate(int(_metrics["bad_case_count"]), request_count),
            "token_record_rate": _rate(int(_metrics["token_recorded_count"]), request_count),
            "average_tokens_per_request": round(
                int(_metrics["total_tokens"]) / request_count,
                2,
            ) if request_count else 0.0,
        }
