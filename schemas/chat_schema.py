# app/schemas/chat_schema.py
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    user_id: str = "demo_user"
    session_id: str | None = None
    order_id: str | None = None
    channel: str = "test"


class ChatTrace(BaseModel):
    retrieval_count: int
    request_id: str = ""
    user_id: str = ""
    session_id: str = ""
    order_id: str | None = None
    latency_ms: float = 0.0
    top1_intent: str = ""
    used_fallback_prompt: bool
    reply_rules_applied: bool
    answer_source: str = Field(description="rag or fallback")
    degraded: bool
    failure_stage: str = Field(description="none, retrieval, generation, or reply_rules")
    fallback_reason: str


class PromptContextItemResponse(BaseModel):
    role: str
    evidence_strength: str
    display_title: str = ""
    evidence_summary: str = ""
    prompt_instruction: str = ""
    source_question: str = ""
    source_answer: str = ""
    rank: int
    knowledge_id: str = ""
    title: str = ""
    version: str = ""
    updated_at: str = ""
    source: str = ""
    category: str
    intent: str
    question: str
    answer: str
    score: float
    rerank_score: float


class ChatResponse(BaseModel):
    request_id: str = ""
    reply: str
    risk_level: str = "low"
    confidence_level: str = "medium"
    need_human_review: bool = True
    human_review_reason: str = "v1 默认客服确认后发送"
    citations: list[dict] = Field(default_factory=list)
    conversation_status: str = "pending_agent_review"
    answer_basis: str = ""
    evidence_citations: list[dict] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)
    memory_snapshot: dict = Field(default_factory=dict)
    decision_trace: dict = Field(default_factory=dict)
    full_trace: list[dict] = Field(default_factory=list)
    handoff_ticket: dict | None = None
    token_usage: dict = Field(default_factory=dict)
    confidence_score: float
    session_id: str = ""
    user_id: str = "demo_user"
    order_id: str | None = None
    intent_analysis: dict = Field(default_factory=dict)
    context_used: dict = Field(default_factory=dict)
    safety_status: dict = Field(default_factory=dict)
    final_prompt: str
    prompt_version: str = ""
    retrieved_documents: list[str]
    retrieved_items: list[dict] = Field(default_factory=list)
    prompt_context_items: list[PromptContextItemResponse] = Field(default_factory=list)
    trace: ChatTrace
    expected_intent: str = ""
    expected_evidence_keywords: list[str] = Field(default_factory=list)
    matched_evidence_keywords: list[str] = Field(default_factory=list)
    missing_evidence_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    forbidden_keyword_hits: list[str] = Field(default_factory=list)
    used_primary_evidence: bool = False
    mixed_supporting_intent: bool = False
    risky_promises: list[str] = Field(default_factory=list)
    needs_manual_review: bool = False
    manual_judgment: dict = Field(default_factory=dict)
    issue_type: str = ""
    suggested_layer: str = ""
    evaluation_metrics: dict = Field(default_factory=dict)


class ChatHistoryMessage(BaseModel):
    role: str
    content: str
    intent: dict = Field(default_factory=dict)
    risk_level: str = "low"
    created_at: str = ""


class ChatHistoryResponse(BaseModel):
    user_id: str = "demo_user"
    session_id: str = ""
    order_id: str | None = None
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    latest_response: dict = Field(default_factory=dict)


ReviewAction = Literal["accepted", "edited_and_sent", "human_handoff", "marked_bad_case"]


class ChatReviewActionRequest(BaseModel):
    request_id: str = Field(min_length=1)
    action: ReviewAction
    operator_id: str = "demo_agent"
    operator_role: str = "agent"
    final_reply: str = ""
    reason: str = ""


class ChatReviewActionResponse(BaseModel):
    request_id: str
    session_id: str
    user_id: str
    order_id: str | None = None
    action: ReviewAction
    status: str
    final_reply: str = ""
    reason: str = ""
    handoff_ticket: dict | None = None
    audit_id: int | None = None
    saved: bool = True
    created_at: str
