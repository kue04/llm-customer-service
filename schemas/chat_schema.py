# app/schemas/chat_schema.py
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str


class ChatTrace(BaseModel):
    retrieval_count: int
    request_id: str = ""
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
    category: str
    intent: str
    question: str
    answer: str
    score: float
    rerank_score: float


class ChatResponse(BaseModel):
    reply: str
    confidence_score: float
    final_prompt: str
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
