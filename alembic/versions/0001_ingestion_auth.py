"""建立数据接入、文档版本、ACL、处理任务、索引与审计相关的 13 张表

包含的表：
    tenants, users, roles, user_roles,
    knowledge_bases, knowledge_base_members,
    documents, document_versions, document_acl,
    ingestion_jobs, document_chunks, index_builds, audit_events

设计说明（与 services/ingestion/models.py 一一对应）：
1. 所有业务表带 tenant_id；子表通过 (tenant_id, parent_id) 复合外键指向父表，
   从数据库层面禁止跨租户挂载。
2. 唯一约束一律包含租户维度。
3. 租户内「一个源文件 = 一份文档」：uq_documents(tenant_id, source_uri)
   与 uq_document_versions(tenant_id, content_hash) 必须同为租户粒度，否则
   同一文件进入第二个知识库时会撞 content_hash 唯一约束。
   跨知识库共用应通过后续新增 knowledge_base_documents 关联表实现，不在 documents 里堆副本。
4. 表内约定 ``uq_/fk_/ix_/ck_`` 前缀命名，与模型层的 naming_convention 一致，
   便于用 tests/test_ingestion_models.py 做 schema 对照。
5. 迁移是方言无关写法，SQLite 与 PostgreSQL 均可 upgrade head，可重复执行。

Revision ID: 0001_ingestion_auth
Revises: None
Create Date: 2026-09-23

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001_ingestion_auth"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOURCE_TYPE_CHECK = "source_type IN ('pdf', 'docx', 'html', 'md', 'txt')"
DOCUMENT_STATUS_CHECK = (
    "status IN ('received', 'stored', 'parsed', 'chunked', 'indexed', 'published', "
    "'archived', 'failed', 'duplicate')"
)
KB_MEMBER_ROLE_CHECK = "member_role IN ('owner', 'editor', 'reviewer', 'viewer')"
ACL_SUBJECT_TYPE_CHECK = "subject_type IN ('user', 'role', 'tenant', 'group')"
ACL_PERMISSION_CHECK = "permission IN ('read', 'write', 'review', 'publish', 'delete')"
STAGE_CHECK = (
    "stage IN ('received', 'stored', 'parsed', 'normalized', 'chunked', 'persisted', "
    "'indexed', 'published')"
)
JOB_STATUS_CHECK = "status IN ('pending', 'running', 'succeeded', 'failed', 'retrying', 'cancelled')"
INDEX_BUILD_STATUS_CHECK = "status IN ('building', 'active', 'failed', 'superseded', 'rolled_back')"


def upgrade() -> None:
    # ---------------------------------------------------------- 租户与身份

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_users_tenant_id"),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_users_tenant_id_external_id"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_roles_tenant_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_id_code"),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])

    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_roles"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_user_roles_tenant_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_roles_user_id"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_user_roles_role_id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_id_role_id"),
    )
    op.create_index("ix_user_roles_tenant_id", "user_roles", ["tenant_id"])

    # ---------------------------------------------------------- 知识库

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("chunking_config_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_bases"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_knowledge_bases_tenant_id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_knowledge_bases_created_by"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_knowledge_bases_tenant_id_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id_id"),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])

    op.create_table(
        "knowledge_base_members",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("member_role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_base_members"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_knowledge_base_members_tenant_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_knowledge_base_members_user_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_base_members_tenant_id_knowledge_base_id",
        ),
        sa.UniqueConstraint(
            "knowledge_base_id", "user_id", name="uq_knowledge_base_members_knowledge_base_id_user_id"
        ),
        sa.CheckConstraint(KB_MEMBER_ROLE_CHECK, name="member_role_valid"),
    )
    op.create_index("ix_knowledge_base_members_tenant_id", "knowledge_base_members", ["tenant_id"])
    op.create_index(
        "ix_knowledge_base_members_knowledge_base_id", "knowledge_base_members", ["knowledge_base_id"]
    )

    # ---------------------------------------------------------- 文档与版本

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=1024), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_documents_tenant_id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_documents_created_by"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_documents_tenant_id_knowledge_base_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "source_uri", name="uq_documents_tenant_id_source_uri"),
        sa.CheckConstraint(SOURCE_TYPE_CHECK, name="source_type_valid"),
        sa.CheckConstraint(DOCUMENT_STATUS_CHECK, name="status_valid"),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_knowledge_base_id", "documents", ["knowledge_base_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_name", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_versions_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_versions_tenant_id_document_id",
        ),
        sa.UniqueConstraint("tenant_id", "document_id", "version", name="uq_document_versions_tenant_id_document_id_version"),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_document_versions_tenant_id_content_hash"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id_id"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index("ix_document_versions_tenant_id", "document_versions", ["tenant_id"])
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.create_table(
        "document_acl",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_acl"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_acl_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_acl_tenant_id_document_id",
        ),
        sa.UniqueConstraint(
            "document_id",
            "subject_type",
            "subject_id",
            name="uq_document_acl_document_id_subject_type_subject_id",
        ),
        sa.CheckConstraint(ACL_SUBJECT_TYPE_CHECK, name="subject_type_valid"),
        sa.CheckConstraint(ACL_PERMISSION_CHECK, name="permission_valid"),
    )
    op.create_index("ix_document_acl_tenant_id", "document_acl", ["tenant_id"])
    op.create_index("ix_document_acl_document_id", "document_acl", ["document_id"])

    # ---------------------------------------------------------- 处理任务

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("document_version_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_ingestion_jobs_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_ingestion_jobs_tenant_id_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_ingestion_jobs_tenant_id_document_version_id",
        ),
        sa.CheckConstraint(STAGE_CHECK, name="stage_valid"),
        sa.CheckConstraint(JOB_STATUS_CHECK, name="status_valid"),
        sa.CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
    )
    op.create_index("ix_ingestion_jobs_tenant_id", "ingestion_jobs", ["tenant_id"])
    op.create_index("ix_ingestion_jobs_document_id", "ingestion_jobs", ["document_id"])
    op.create_index("ix_ingestion_jobs_tenant_id_status", "ingestion_jobs", ["tenant_id", "status"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("document_version_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("parent_chunk_id", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_chunks_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_document_chunks_tenant_id_document_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "parent_chunk_id"],
            ["document_chunks.document_version_id", "document_chunks.chunk_id"],
            name="fk_document_chunks_document_version_id_parent_chunk_id",
        ),
        sa.UniqueConstraint("document_version_id", "chunk_id", name="uq_document_chunks_document_version_id_chunk_id"),
        sa.CheckConstraint("token_count >= 0", name="token_count_non_negative"),
    )
    op.create_index("ix_document_chunks_tenant_id", "document_chunks", ["tenant_id"])
    op.create_index("ix_document_chunks_document_version_id", "document_chunks", ["document_version_id"])

    # ---------------------------------------------------------- 索引构建

    op.create_table(
        "index_builds",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("index_name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("manifest_uri", sa.String(length=1024), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_index_builds"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_index_builds_tenant_id"),
        sa.UniqueConstraint("tenant_id", "index_name", "version", name="uq_index_builds_tenant_id_index_name_version"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(INDEX_BUILD_STATUS_CHECK, name="status_valid"),
        sa.CheckConstraint("embedding_dimension > 0", name="embedding_dimension_positive"),
    )
    op.create_index("ix_index_builds_tenant_id", "index_builds", ["tenant_id"])

    # ---------------------------------------------------------- 审计

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_events_tenant_id"),
    )
    op.create_index("ix_audit_events_tenant_id_created_at", "audit_events", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_tenant_id_created_at", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_index_builds_tenant_id", table_name="index_builds")
    op.drop_table("index_builds")

    op.drop_index("ix_document_chunks_document_version_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_tenant_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_ingestion_jobs_tenant_id_status", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_document_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_tenant_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")

    op.drop_index("ix_document_acl_document_id", table_name="document_acl")
    op.drop_index("ix_document_acl_tenant_id", table_name="document_acl")
    op.drop_table("document_acl")

    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_index("ix_document_versions_tenant_id", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index("ix_documents_knowledge_base_id", table_name="documents")
    op.drop_index("ix_documents_tenant_id", table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_knowledge_base_members_knowledge_base_id", table_name="knowledge_base_members")
    op.drop_index("ix_knowledge_base_members_tenant_id", table_name="knowledge_base_members")
    op.drop_table("knowledge_base_members")

    op.drop_index("ix_knowledge_bases_tenant_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")

    op.drop_index("ix_user_roles_tenant_id", table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_index("ix_roles_tenant_id", table_name="roles")
    op.drop_table("roles")

    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")

    op.drop_table("tenants")
