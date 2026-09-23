from typing import Literal

from pydantic import BaseModel, Field


RetrievalMode = Literal["vector", "hybrid"]

#: 命中的检索路径。**必须显式告诉调用方自己走的是哪条路**（踩坑 F1）：
#: ``chunk-index`` = 正式路径（文档 chunk 级 + 服务端权限过滤）；
#: ``seed-faq-demo`` = 演示 / 兼容路径（手工整理种子 FAQ，**没有权限过滤**）。
RetrievalPath = Literal["chunk-index", "seed-faq-demo"]


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: RetrievalMode = "hybrid"
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.40, ge=0.0, le=1.0)


class ChunkRetrievalRequest(BaseModel):
    """chunk 级检索请求（计划 4.3）。

    **刻意没有任何可以影响授权范围的字段**：没有 ``tenant_id``、没有 ``acl``、
    没有 filter 表达式、没有 FAISS row_id（计划 4.3 明文禁止）。
    租户与可见 chunk 集合由服务端从 JWT 身份推导。
    """

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    #: 质量阈值而非安全阈值：默认 ``None`` 表示不设阈值（权限过滤是必做的，不受它影响）
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class ChunkRetrievalItem(BaseModel):
    """一条 chunk 命中：命中即自证来源（文档 / 版本 / 租户 / ACL / 页码 / 标题路径）。"""

    rank: int
    chunk_id: str
    document_id: str
    document_version: int
    document_version_id: str
    tenant_id: str
    title: str = ""
    document_title: str = ""
    chunk_type: str = ""
    heading_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    acl: list[dict] = Field(default_factory=list)
    source_uri: str = ""
    filename: str = ""
    source_type: str = ""
    content_hash: str = ""
    token_count: int = 0
    text: str = ""
    score: float = 0.0
    retrieval_origin: str = "chunk-index"


class ChunkIndexInfo(BaseModel):
    """当前生效索引的自我描述（便于排障"为什么零命中"）。"""

    index_name: str
    index_version: int
    embedding_model: str
    embedding_dimension: int
    built_at: str
    chunk_count: int
    tokenizer_id: str = ""
    #: 索引条目的租户分布（只给计数，不给其他租户的明细）
    visible_chunk_count: int = 0


class ChunkRetrievalResponse(BaseModel):
    retrieval_path: RetrievalPath = "chunk-index"
    query: str
    count: int
    index: ChunkIndexInfo
    results: list[ChunkRetrievalItem]


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
    """**演示 / 兼容路径**的响应（种子 FAQ，无权限过滤）。"""

    retrieval_path: RetrievalPath = "seed-faq-demo"
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
