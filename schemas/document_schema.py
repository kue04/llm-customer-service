"""文档上传与异步任务 API 的请求 / 响应模型（计划 2.3）。

风格与 ``schemas/knowledge_schema.py`` 一致：``BaseModel`` + ``Field``，
**每个路由显式声明 ``response_model=``**，避免把内部结构（对象 key、
整份 ``metadata_json``）顺手透出去。

两个刻意的隐藏决定
------------------
1. **不返回对象 key。** 它属于内部寻址细节，暴露出去会诱导调用方按它拼 URL，
   将来把本地目录换成 S3 就是破坏性变更。返回值里只给 ``document_id`` /
   ``job_id``，后续查询走 ``GET /documents/{id}``。
2. ``metadata_json`` **不整体返回。** 版本列表只给 ``warnings`` 摘要
   （形状与 ``ParseWarning.to_dict()`` 一一对应），因为 metadata 可能很大，
   且其中含有只该由流水线消费的字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 上传


class DocumentUploadResponse(BaseModel):
    """``POST /knowledge-bases/{kb_id}/documents`` 的 202 响应。

    ``reused_document`` 说明这次上传是「给已有文档追加新版本」还是「新建文档」——
    对调用方是可观测信息（同名文件在租户内即同一份文档，见 [D-1]）。
    """

    document_id: str
    job_id: str
    knowledge_base_id: str
    source_uri: str
    source_type: str
    filename: str
    size_bytes: int = Field(ge=0)
    status: Literal["accepted"] = "accepted"
    job_status: str = Field(description="任务初始状态，上传期为 pending")
    job_stage: str = Field(description="任务初始阶段，上传期为 received")
    reused_document: bool


# ---------------------------------------------------------------- 文档详情 / 版本


class DocumentVersionSummary(BaseModel):
    """版本摘要。**不含** ``metadata_json`` 全量内容。"""

    id: str
    version: int = Field(ge=1)
    content_hash: str
    parser_name: str
    parser_version: str
    status: str
    created_at: datetime


class DocumentDetail(BaseModel):
    """``GET /documents/{document_id}`` 的响应：文档 + 最新版本摘要。"""

    id: str
    tenant_id: str
    knowledge_base_id: str
    source_uri: str
    source_type: str
    title: str
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    latest_version: DocumentVersionSummary | None = None


class DocumentVersionListResponse(BaseModel):
    document_id: str
    total: int = Field(ge=0)
    items: list[DocumentVersionSummary]


# ---------------------------------------------------------------- 重处理


class DocumentReprocessResponse(BaseModel):
    """``POST /documents/{document_id}/reprocess`` 的 202 响应。

    每次重处理都**新建一个 job**（不复用旧 job 改状态），
    因为 ``retry_count`` 的语义是「同一个 job 的失败重试次数」。
    """

    document_id: str
    job_id: str
    status: Literal["accepted"] = "accepted"
    job_status: str
    job_stage: str


# ---------------------------------------------------------------- 索引重建


class IndexRebuildResponse(BaseModel):
    """``POST /ingestion/indexes/rebuild`` 的响应（阶段 4.2，B7 新增）。

    索引是**全局一份**（所有租户共用、由检索层按租户与 ACL 前置过滤），
    因此这里返回的是"这份全局索引整体重建后"的状态，而不是某一个租户的子集。
    ``skipped=True`` 表示库里没有任何可索引的 chunk（不是错误）。
    """

    index_name: str
    index_version: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    embedding_model: str = ""
    embedding_dimension: int = Field(default=0, ge=0)
    manifest_uri: str = ""
    switched: bool = False
    skipped: bool = False
    skip_reason: str = ""
    #: 索引条目的租户分布（只给计数，便于确认"全局索引"这件事真的生效了）
    tenant_count: int = Field(default=0, ge=0)


# ---------------------------------------------------------------- 任务查询


class ParseWarningItem(BaseModel):
    """解析警告。形状与 ``services.ingestion.parsers.base.ParseWarning.to_dict()`` 一致。"""

    code: str
    message: str
    detail: dict | None = None


class IngestionJobDetail(BaseModel):
    """``GET /ingestion-jobs/{job_id}`` 的响应。

    ``warnings`` 是「解析警告可由任务查询接口读取」这条要求的落点：
    它来自 ``document_versions.metadata_json["warnings"]``（``ingestion_jobs``
    没有 warnings 列，也不打算为它加列），前端可以按 ``code`` 分类展示
    （例如 ``no_text_layer`` -> 「疑似扫描件」）。
    """

    id: str
    tenant_id: str
    document_id: str
    document_version_id: str | None = None
    status: str
    stage: str
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    warnings: list[ParseWarningItem] = Field(default_factory=list)


__all__ = [
    "DocumentDetail",
    "DocumentReprocessResponse",
    "DocumentUploadResponse",
    "DocumentVersionListResponse",
    "DocumentVersionSummary",
    "IndexRebuildResponse",
    "IngestionJobDetail",
    "ParseWarningItem",
]
