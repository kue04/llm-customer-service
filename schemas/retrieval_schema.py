from typing import Literal

from pydantic import BaseModel, Field


RetrievalMode = Literal["vector", "hybrid"]


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: RetrievalMode = "hybrid"
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.62, ge=0.0, le=1.0)


class RetrievalResultItem(BaseModel):
    rank: int
    score: float
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
