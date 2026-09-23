"""数据接入 / 鉴权相关的 SQLAlchemy 2.x 数据模型（对应 Alembic 迁移 0001）。

设计要点
--------
* **租户可追溯**：所有业务表都带 ``tenant_id``，并且子表通过**复合外键**
  ``(tenant_id, parent_id) -> parent(tenant_id, id)`` 指向父表，
  这样数据库层面就禁止了「A 租户的文档挂到 B 租户的知识库」这类脏数据。
* **唯一约束带租户维度**：``uq_users(tenant_id, external_id)``、
  ``uq_documents(tenant_id, source_uri)`` 等，
  保证不同租户可以使用相同的外部标识 / 文件名，而同租户内必须唯一。
* **一个源文件 = 一份文档（租户内）**：``uq_documents(tenant_id, source_uri)``
  与 ``uq_document_versions(tenant_id, content_hash)`` 是**配套**的，两者必须同为租户粒度。
  若把 source_uri 的唯一性收窄到知识库维度，同一文件进入第二个知识库时，
  新建的文档会撞上 content_hash 唯一约束（报错，或产生一个永远没有内容的 duplicate 文档）。
  跨知识库共用的正确做法是以后加 ``knowledge_base_documents`` 关联表（纯增量），
  而不是在 documents 里堆多份互不相干的副本。
* **原文件不入库**：只保存 ``source_uri``（对象存储 URI），文件本体在对象存储。
* **不自动改表**：这里是模型定义，真正的建表只走 Alembic 迁移，
  运行时不调用 ``create_all``（测试里为了对照 schema 才会用）。
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ---------------------------------------------------------------- 常量与工具

#: 文档来源类型（与解析器注册表的 key 一致）
DOCUMENT_SOURCE_TYPES: tuple[str, ...] = ("pdf", "docx", "html", "md", "txt")

#: ACL 主体类型。``tenant`` 表示全租户可见，``user`` / ``role`` / ``group`` 为受限主体
ACL_SUBJECT_TYPES: tuple[str, ...] = ("user", "role", "tenant", "group")

#: ACL 权限动作
ACL_PERMISSIONS: tuple[str, ...] = ("read", "write", "review", "publish", "delete")

#: 处理流水线固定状态流转（计划 3.3）
INGESTION_STAGES: tuple[str, ...] = (
    "received",
    "stored",
    "parsed",
    "normalized",
    "chunked",
    "persisted",
    "indexed",
    "published",
)

#: 任务状态
JOB_STATUSES: tuple[str, ...] = ("pending", "running", "succeeded", "failed", "retrying", "cancelled")

#: 索引构建状态
INDEX_BUILD_STATUSES: tuple[str, ...] = ("building", "active", "failed", "superseded", "rolled_back")

#: 知识库成员角色
KB_MEMBER_ROLES: tuple[str, ...] = ("owner", "editor", "reviewer", "viewer")

#: 文档状态
DOCUMENT_STATUSES: tuple[str, ...] = (
    "received",
    "stored",
    "parsed",
    "chunked",
    "indexed",
    "published",
    "archived",
    "failed",
    "duplicate",
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    """生成 32 位十六进制的业务主键（对象存储 key 也用它，避免暴露自增序号）。"""

    return uuid.uuid4().hex


def utcnow() -> datetime:
    """统一的 UTC 时间戳。

    存成 naive UTC（不带 tzinfo）是为了让 SQLite 与 PostgreSQL 的行为一致：
    SQLite 不保存时区信息，若带 tzinfo 会在两种后端上产生不同的比较结果。
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)


def in_clause(column: str, values: tuple[str, ...]) -> str:
    """拼一条 ``column IN ('a','b')`` 的 CHECK 表达式（值来自代码内常量，无注入面）。"""

    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------- 租户与身份


class Tenant(Base):
    """租户（第一期的隔离边界）。"""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class User(Base):
    """租户内的用户。``external_id`` 是身份提供方（JWT ``sub``）的稳定标识。"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class Role(Base):
    """租户内的角色定义（角色编码带租户维度，避免跨租户命名冲突）。"""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class UserRole(Base):
    """用户与角色的关联表（携带 tenant_id 便于审计与查询）。"""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), ForeignKey("roles.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


# ---------------------------------------------------------------- 知识库


class KnowledgeBase(Base):
    """知识库。``chunking_config_json`` 允许按库覆盖切分参数（计划 3.1）。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        # 供子表建立 (tenant_id, id) 复合外键，从数据库层面锁死跨租户挂载
        UniqueConstraint("tenant_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    chunking_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class KnowledgeBaseMember(Base):
    """知识库成员关系（决定 knowledge_base:read / write 等权限）。"""

    __tablename__ = "knowledge_base_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
        ),
        UniqueConstraint("knowledge_base_id", "user_id"),
        CheckConstraint(in_clause("member_role", KB_MEMBER_ROLES), name="member_role_valid"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False)
    member_role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


# ---------------------------------------------------------------- 文档与版本


class Document(Base):
    """文档逻辑实体。原文件只存 ``source_uri``，不写数据库。

    一个源文件在租户内**只对应一份文档**（``uq(tenant_id, source_uri)``）。
    重新上传同一 ``source_uri`` 的更新内容，应当在**同一份文档上追加新版本**，
    而不是新建文档 —— 这样同一份政策文件的版本历史是连续的，
    不会出现两条版本链各自演进、答案互相矛盾的情况。
    """

    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
        ),
        # 供 document_versions / document_acl / ingestion_jobs 建立复合外键
        UniqueConstraint("tenant_id", "id"),
        # 租户粒度的源文件唯一性，与 uq_document_versions(tenant_id, content_hash) 配套
        UniqueConstraint("tenant_id", "source_uri"),
        CheckConstraint(in_clause("source_type", DOCUMENT_SOURCE_TYPES), name="source_type_valid"),
        CheckConstraint(in_clause("status", DOCUMENT_STATUSES), name="status_valid"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    created_by: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class DocumentVersion(Base):
    """文档版本。解析器、解析器版本、内容 hash 都落在这一层。

    ``uq_document_versions(tenant_id, content_hash)`` 就是「重复文件不得产生
    第二份有效文档版本」的数据库级保证。
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
        ),
        UniqueConstraint("tenant_id", "document_id", "version"),
        UniqueConstraint("tenant_id", "content_hash"),
        # 供 document_chunks / ingestion_jobs 建立复合外键
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class DocumentAcl(Base):
    """文档资源级 ACL。同一 (subject_type, subject_id) 只能有一条授权记录。"""

    __tablename__ = "document_acl"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
        ),
        UniqueConstraint("document_id", "subject_type", "subject_id"),
        CheckConstraint(in_clause("subject_type", ACL_SUBJECT_TYPES), name="subject_type_valid"),
        CheckConstraint(in_clause("permission", ACL_PERMISSIONS), name="permission_valid"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


# ---------------------------------------------------------------- 处理任务


class IngestionJob(Base):
    """接入 / 解析 / 切分 / 索引的任务记录，支持按阶段幂等重试。"""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
        ),
        Index("ix_ingestion_jobs_tenant_id_status", "tenant_id", "status"),
        CheckConstraint(in_clause("stage", INGESTION_STAGES), name="stage_valid"),
        CheckConstraint(in_clause("status", JOB_STATUSES), name="status_valid"),
        CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class DocumentChunk(Base):
    """切分后的 chunk。``parent_chunk_id`` 指向同一版本内的父 chunk（``chunk_id``）。"""

    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
        ),
        # 父子 chunk 必须在同一文档版本内，防止跨版本串味
        ForeignKeyConstraint(
            ["document_version_id", "parent_chunk_id"],
            ["document_chunks.document_version_id", "document_chunks.chunk_id"],
        ),
        UniqueConstraint("document_version_id", "chunk_id"),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    document_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class IndexBuild(Base):
    """索引构建记录（配合计划 3.4 的 manifest 与原子切换 / 回滚）。"""

    __tablename__ = "index_builds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "index_name", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(in_clause("status", INDEX_BUILD_STATUSES), name="status_valid"),
        CheckConstraint("embedding_dimension > 0", name="embedding_dimension_positive"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="building")
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


# ---------------------------------------------------------------- 审计


class AuditEvent(Base):
    """审计事件。``summary_json`` 只放脱敏摘要，禁止写入原文 / token / 凭据。"""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


#: 迁移与测试用：全部业务表
ALL_TABLES: tuple[str, ...] = (
    "tenants",
    "users",
    "roles",
    "user_roles",
    "knowledge_bases",
    "knowledge_base_members",
    "documents",
    "document_versions",
    "document_acl",
    "ingestion_jobs",
    "document_chunks",
    "index_builds",
    "audit_events",
)
