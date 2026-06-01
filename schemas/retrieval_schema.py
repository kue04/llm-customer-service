from typing import Literal

from pydantic import BaseModel, Field


RetrievalMode = Literal["vector", "hybrid"]


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: RetrievalMode = "hybrid"
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.40, ge=0.0, le=1.0)


class RetrievalResultItem(BaseModel):
    role: str = ""
    evidence_strength: str = "normal"
    display_title: str = ""
    evidence_summary: str = ""
    prompt_instruction: str = ""
    source_question: str = ""
    source_answer: str = ""
    rank: int
    score: float
    rerank_score: float
    model_rerank_score: float
    vector_score: float
    keyword_bonus: float
    direction_penalty: float
    category: str
    intent: str
    question: str
    answer: str


class RetrievalSearchResponse(BaseModel):
    query: str
    mode: RetrievalMode
    count: int
    results: list[RetrievalResultItem]


class PromptPreviewResponse(BaseModel):
    query: str
    mode: RetrievalMode
    count: int
    prompt: str
    prompt_context_items: list[RetrievalResultItem]
    results: list[RetrievalResultItem]


class RagConfigResponse(BaseModel):
    embedding_model_name: str
    reranker_model_name: str
    model_rerank_weight: float
    min_vector_score: float
    faiss_store_dir: str
    faiss_index_path: str
    faiss_docs_path: str
    reply_rules_enabled: bool
