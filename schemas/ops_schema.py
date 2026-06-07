from pydantic import BaseModel


class OpsMetricsResponse(BaseModel):
    request_count: int
    failure_count: int
    average_latency_ms: float
    p95_latency_ms: float
    empty_retrieval_count: int
    reply_rules_hit_count: int
    fallback_count: int
