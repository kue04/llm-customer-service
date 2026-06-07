from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    reply: str = Field(min_length=1)
    helpful: bool
    reason: str = ""
    expected_reply: str = ""
    trace: dict = Field(default_factory=dict)


class FeedbackResponse(BaseModel):
    feedback_id: int
    saved: bool


class FeedbackItem(BaseModel):
    id: int
    request_id: str
    query: str
    reply: str
    helpful: bool
    reason: str
    expected_reply: str
    top1_intent: str
    latency_ms: float
    answer_source: str
    failure_stage: str
    exported: bool
    created_at: str


class RecentFeedbackResponse(BaseModel):
    count: int
    items: list[FeedbackItem]


class ExportEvalCaseRequest(BaseModel):
    feedback_id: int


class ExportEvalCaseResponse(BaseModel):
    feedback_id: int
    eval_case: dict
