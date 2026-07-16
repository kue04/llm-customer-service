from pydantic import BaseModel


class OpsMetricsResponse(BaseModel):
    source: str = ""
    request_count: int
    failure_count: int
    average_latency_ms: float
    p95_latency_ms: float
    empty_retrieval_count: int
    reply_rules_hit_count: int
    fallback_count: int
    accepted_count: int
    edited_sent_count: int
    human_handoff_count: int
    bad_case_count: int
    reviewed_count: int
    accepted_rate: float
    edited_sent_rate: float
    human_handoff_rate: float
    bad_case_rate: float
    token_recorded_count: int
    token_record_rate: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    average_tokens_per_request: float
