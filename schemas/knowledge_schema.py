from pydantic import BaseModel, Field


class KnowledgeItemPayload(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    category: str = Field(min_length=1)
    intent: str = Field(min_length=1)


class KnowledgeReviewRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    review_note: str = ""


class KnowledgeItem(BaseModel):
    id: int
    base_id: str
    version: int
    question: str
    answer: str
    category: str
    intent: str
    status: str
    review_note: str
    created_at: str
    updated_at: str
    reviewed_at: str


class KnowledgeListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[KnowledgeItem]


class KnowledgeExportResponse(BaseModel):
    count: int
    jsonl: str


class KnowledgePublishHistoryItem(BaseModel):
    id: int
    publish_id: str
    action: str
    status: str
    merged_count: int
    item_ids: list[int]
    backup_path: str
    knowledge_path: str
    faiss_index_path: str
    note: str
    created_at: str


class KnowledgePublishResponse(KnowledgePublishHistoryItem):
    pass


class KnowledgePublishHistoryResponse(BaseModel):
    count: int
    items: list[KnowledgePublishHistoryItem]
