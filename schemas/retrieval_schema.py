from typing import Literal

from pydantic import BaseModel, Field


#: ⚠️ **A 轨（演示路径）的模式**：``vector`` = 纯向量；``hybrid`` = 向量 + 关键词加分。
#: 这里的 ``hybrid`` 指的是 ``utils/vector_retriever.calculate_keyword_bonus``
#: 那套**规则加权**（关键词只加不召回），**不是** B 轨的双路召回 + RRF。
#: 两者名字相同、机制不同，是本项目最容易误读的一处 —— 因此 B 轨另起
#: :data:`ChunkRetrievalMode`，不复用这个类型。
RetrievalMode = Literal["vector", "hybrid"]

#: B 轨（正式路径）的检索模式：
#: ``dense`` = 纯稠密单路；``hybrid`` = 稠密 + 稀疏双路 RRF 融合；``sparse`` = 纯稀疏单路。
ChunkRetrievalMode = Literal["dense", "hybrid", "sparse"]

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
    #: 质量阈值而非安全阈值：默认 ``None`` 表示不设阈值（权限过滤是必做的，不受它影响）。
    #: **只在 ``mode="dense"`` 下有意义**：非稠密模式的分数是 RRF 融合分，
    #: 量纲与余弦相似度不同，同传会被显式拒绝（见路由层），而不是被静默忽略。
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    #: 检索模式（B8 新增）。**字段名叫 ``retrieval_mode`` 而不是 ``mode``，是刻意的**：
    #:
    #: * A 轨（``POST /retrieval/search-demo``）已经有一个 ``mode`` 字段，
    #:   取值 ``vector`` / ``hybrid``，含义是「纯向量 vs 向量+关键词加分」；
    #: * 两者若同名，同一个请求体在两个端点间复用时会**静默改变含义** ——
    #:   A 轨的 ``hybrid``（规则加权）会被 B 轨当成双路召回，而 ``vector``
    #:   在 B 轨里直接 422。这类"同名字段、不同语义"正是踩坑 E8 那一类问题的源头。
    #: * 另外，B 轨端点此前的 ``mode`` 是**未知字段（被忽略）**，
    #:   就此把它变成实义字段会造成静默的行为变更（老调用方传什么都会被当真）。
    #:
    #: 取值：
    #:
    #: * ``dense`` —— 纯稠密单路，与加混合检索之前的行为**完全一致**；
    #: * ``hybrid`` —— 稠密 + 稀疏（FTS5）双路，加权 RRF 融合；
    #: * ``sparse`` —— 纯稀疏单路，仅供消融对比；
    #: * 省略 / ``null`` —— 跟随服务端默认（当前是 ``dense``）。
    #:
    #: 默认之所以不是 ``hybrid``：假设现有部署的生效索引是**纯稠密**构建，
    #: 把默认改成 hybrid 会让所有检索请求在发版瞬间变成 503
    #: （稀疏索引不可用是显式失败，不降级 —— 见 ``utils/sparse_retriever.py``）。
    #: 上线顺序因此是：**先用默认参数重建索引 → 确认接口返回的
    #: ``index.sparse_available=true`` → 再把服务端默认切过去**（一处常量：
    #: ``routers/retrieval.py::DEFAULT_RETRIEVAL_MODE``）。
    #:
    #: 这里给的是 ``None`` 而不是具体默认值，是**故意的**：
    #: 若 schema 也写一个默认值，服务端就有两个默认值，
    #: 改了一个忘了另一个时，接口文档与实际行为不一致且不会报错。
    retrieval_mode: ChunkRetrievalMode | None = None


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
    #: 以下三项是 B8 混合检索的**路由溯源**（``mode="dense"`` 时稀疏项为 ``None``）。
    #: 「这条命中是哪一路找到的」是排查检索质量问题的第一个问题 ——
    #: 只给融合分的话，遇到"该出现的结果没出现"就只能靠猜。
    dense_rank: int | None = None
    sparse_rank: int | None = None
    routes: list[str] = Field(default_factory=list)


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
    #: B8：这份索引是否带词法（稀疏）路。调用方据此判断能不能用 ``mode="hybrid"``。
    #: 由 manifest 的 ``extra["sparse"]`` 声明决定，不靠"文件是否存在"来猜。
    sparse_available: bool = False
    sparse_index_file: str = ""
    sparse_gram_algorithm: str = ""


class ChunkRetrievalResponse(BaseModel):
    retrieval_path: RetrievalPath = "chunk-index"
    query: str
    count: int
    index: ChunkIndexInfo
    results: list[ChunkRetrievalItem]
    #: **实际生效**的检索模式。回显它是为了让"请求了 hybrid 但拿到的是什么"
    #: 有据可查 —— 本项目在 F1 上吃过"接口宣称一套、实际跑另一套"的亏。
    retrieval_mode: str = "dense"


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
