from __future__ import annotations

from threading import Lock


_lock = Lock()
_latencies: list[float] = []
_metrics = {
    "request_count": 0,
    "failure_count": 0,
    "empty_retrieval_count": 0,
    "reply_rules_hit_count": 0,
    "fallback_count": 0,
}


def record_chat_metrics(trace: dict) -> None:
    with _lock:
        _metrics["request_count"] += 1
        if trace.get("degraded") or trace.get("failure_stage") not in {"", "none"}:
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


def get_ops_metrics() -> dict:
    with _lock:
        latencies = sorted(_latencies)
        count = len(latencies)
        average = round(sum(latencies) / count, 2) if count else 0.0
        p95 = latencies[min(int(count * 0.95), count - 1)] if count else 0.0
        return {
            **_metrics,
            "average_latency_ms": average,
            "p95_latency_ms": round(p95, 2),
        }
