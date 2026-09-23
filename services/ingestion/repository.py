"""面向业务的读写函数（仓储层）。

约定
----
* 每个函数接收一个已开启的 ``Session``，**不自行 commit**，事务边界交给调用方
  （API 层用 ``services.ingestion.db.session_scope``，测试里自己控制）。
* 所有查询都显式带 ``tenant_id`` 条件（除按主键取租户本身），
  避免出现「忘记加租户过滤」的全库查询。
* 跨租户的写操作会被数据库复合外键拦下并抛 ``IntegrityError``，
  仓储层不再做重复校验，保持单一事实来源。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.ingestion import models
from services.ingestion.models import utcnow


# ---------------------------------------------------------------- 租户 / 用户 / 角色


def create_tenant(session: Session, *, name: str, tenant_id: str | None = None) -> models.Tenant:
    tenant = models.Tenant(id=tenant_id or models.new_id(), name=name)
    session.add(tenant)
    session.flush()
    return tenant


def get_tenant(session: Session, tenant_id: str) -> models.Tenant | None:
    return session.get(models.Tenant, tenant_id)


def create_user(
    session: Session,
    *,
    tenant_id: str,
    external_id: str,
    display_name: str = "",
    email: str | None = None,
) -> models.User:
    user = models.User(
        tenant_id=tenant_id,
        external_id=external_id,
        display_name=display_name,
        email=email,
    )
    session.add(user)
    session.flush()
    return user


def get_user_by_external_id(session: Session, tenant_id: str, external_id: str) -> models.User | None:
    stmt = select(models.User).where(
        models.User.tenant_id == tenant_id,
        models.User.external_id == external_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def get_user(session: Session, tenant_id: str, user_id: str) -> models.User | None:
    stmt = select(models.User).where(
        models.User.tenant_id == tenant_id,
        models.User.id == user_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def create_role(session: Session, *, tenant_id: str, code: str, name: str = "") -> models.Role:
    role = models.Role(tenant_id=tenant_id, code=code, name=name or code)
    session.add(role)
    session.flush()
    return role


def get_role_by_code(session: Session, tenant_id: str, code: str) -> models.Role | None:
    stmt = select(models.Role).where(
        models.Role.tenant_id == tenant_id,
        models.Role.code == code,
    )
    return session.execute(stmt).scalar_one_or_none()


def assign_role(session: Session, *, tenant_id: str, user_id: str, role_id: str) -> models.UserRole:
    link = models.UserRole(tenant_id=tenant_id, user_id=user_id, role_id=role_id)
    session.add(link)
    session.flush()
    return link


def list_user_role_codes(session: Session, tenant_id: str, user_id: str) -> list[str]:
    stmt = (
        select(models.Role.code)
        .join(models.UserRole, models.UserRole.role_id == models.Role.id)
        .where(models.UserRole.tenant_id == tenant_id, models.UserRole.user_id == user_id)
    )
    return list(session.execute(stmt).scalars().all())


# ---------------------------------------------------------------- 知识库


def create_knowledge_base(
    session: Session,
    *,
    tenant_id: str,
    slug: str,
    name: str = "",
    description: str | None = None,
    chunking_config_json: dict | None = None,
    created_by: str | None = None,
) -> models.KnowledgeBase:
    kb = models.KnowledgeBase(
        tenant_id=tenant_id,
        slug=slug,
        name=name or slug,
        description=description,
        chunking_config_json=chunking_config_json,
        created_by=created_by,
    )
    session.add(kb)
    session.flush()
    return kb


def get_knowledge_base(session: Session, tenant_id: str, knowledge_base_id: str) -> models.KnowledgeBase | None:
    stmt = select(models.KnowledgeBase).where(
        models.KnowledgeBase.tenant_id == tenant_id,
        models.KnowledgeBase.id == knowledge_base_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def get_knowledge_base_by_slug(session: Session, tenant_id: str, slug: str) -> models.KnowledgeBase | None:
    stmt = select(models.KnowledgeBase).where(
        models.KnowledgeBase.tenant_id == tenant_id,
        models.KnowledgeBase.slug == slug,
    )
    return session.execute(stmt).scalar_one_or_none()


def add_knowledge_base_member(
    session: Session,
    *,
    tenant_id: str,
    knowledge_base_id: str,
    user_id: str,
    member_role: str = "viewer",
) -> models.KnowledgeBaseMember:
    member = models.KnowledgeBaseMember(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        member_role=member_role,
    )
    session.add(member)
    session.flush()
    return member


def get_knowledge_base_member(
    session: Session,
    tenant_id: str,
    knowledge_base_id: str,
    user_id: str,
) -> models.KnowledgeBaseMember | None:
    stmt = select(models.KnowledgeBaseMember).where(
        models.KnowledgeBaseMember.tenant_id == tenant_id,
        models.KnowledgeBaseMember.knowledge_base_id == knowledge_base_id,
        models.KnowledgeBaseMember.user_id == user_id,
    )
    return session.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------- 文档与版本


def create_document(
    session: Session,
    *,
    tenant_id: str,
    knowledge_base_id: str,
    source_uri: str,
    source_type: str,
    title: str = "",
    status: str = "received",
    created_by: str | None = None,
) -> models.Document:
    document = models.Document(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        source_uri=source_uri,
        source_type=source_type,
        title=title,
        status=status,
        created_by=created_by,
    )
    session.add(document)
    session.flush()
    return document


def get_document(session: Session, tenant_id: str, document_id: str) -> models.Document | None:
    stmt = select(models.Document).where(
        models.Document.tenant_id == tenant_id,
        models.Document.id == document_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def get_document_by_source_uri(
    session: Session,
    tenant_id: str,
    source_uri: str,
) -> models.Document | None:
    """按源文件 URI 找文档。

    唯一性在租户粒度（``uq_documents(tenant_id, source_uri)``），
    因此这里不需要也不接受 knowledge_base_id —— 上传接口应当先用本函数判断
    「是新增文档还是给已有文档追加版本」。
    """

    stmt = select(models.Document).where(
        models.Document.tenant_id == tenant_id,
        models.Document.source_uri == source_uri,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_documents(
    session: Session,
    tenant_id: str,
    knowledge_base_id: str | None = None,
    *,
    limit: int = 100,
) -> Sequence[models.Document]:
    stmt = select(models.Document).where(models.Document.tenant_id == tenant_id)
    if knowledge_base_id:
        stmt = stmt.where(models.Document.knowledge_base_id == knowledge_base_id)
    stmt = stmt.order_by(models.Document.created_at.desc()).limit(limit)
    return session.execute(stmt).scalars().all()


def update_document_status(
    session: Session,
    tenant_id: str,
    document_id: str,
    status: str,
    *,
    title: str | None = None,
) -> models.Document | None:
    document = get_document(session, tenant_id, document_id)
    if document is None:
        return None
    document.status = status
    if title is not None:
        document.title = title
    document.updated_at = utcnow()
    session.flush()
    return document


def next_version_number(session: Session, tenant_id: str, document_id: str) -> int:
    """返回该文档下一个可用版本号（同文档内从 1 递增）。"""

    stmt = select(func.max(models.DocumentVersion.version)).where(
        models.DocumentVersion.tenant_id == tenant_id,
        models.DocumentVersion.document_id == document_id,
    )
    current = session.execute(stmt).scalar_one_or_none()
    return int(current or 0) + 1


def create_document_version(
    session: Session,
    *,
    tenant_id: str,
    document_id: str,
    content_hash: str,
    parser_name: str = "",
    parser_version: str = "",
    metadata_json: dict | None = None,
    status: str = "pending",
    version: int | None = None,
) -> models.DocumentVersion:
    document_version = models.DocumentVersion(
        tenant_id=tenant_id,
        document_id=document_id,
        version=version if version is not None else next_version_number(session, tenant_id, document_id),
        content_hash=content_hash,
        parser_name=parser_name,
        parser_version=parser_version,
        metadata_json=metadata_json,
        status=status,
    )
    session.add(document_version)
    session.flush()
    return document_version


def get_document_version(session: Session, tenant_id: str, document_version_id: str) -> models.DocumentVersion | None:
    stmt = select(models.DocumentVersion).where(
        models.DocumentVersion.tenant_id == tenant_id,
        models.DocumentVersion.id == document_version_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def find_version_by_content_hash(
    session: Session,
    tenant_id: str,
    content_hash: str,
) -> models.DocumentVersion | None:
    """按内容 hash 找已存在的版本 —— 重复文件检测的唯一入口。"""

    stmt = select(models.DocumentVersion).where(
        models.DocumentVersion.tenant_id == tenant_id,
        models.DocumentVersion.content_hash == content_hash,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_document_versions(session: Session, tenant_id: str, document_id: str) -> Sequence[models.DocumentVersion]:
    stmt = (
        select(models.DocumentVersion)
        .where(
            models.DocumentVersion.tenant_id == tenant_id,
            models.DocumentVersion.document_id == document_id,
        )
        .order_by(models.DocumentVersion.version.asc())
    )
    return session.execute(stmt).scalars().all()


def latest_document_version(session: Session, tenant_id: str, document_id: str) -> models.DocumentVersion | None:
    stmt = (
        select(models.DocumentVersion)
        .where(
            models.DocumentVersion.tenant_id == tenant_id,
            models.DocumentVersion.document_id == document_id,
        )
        .order_by(models.DocumentVersion.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def set_document_version_status(
    session: Session,
    tenant_id: str,
    document_version_id: str,
    status: str,
) -> models.DocumentVersion | None:
    document_version = get_document_version(session, tenant_id, document_version_id)
    if document_version is None:
        return None
    document_version.status = status
    session.flush()
    return document_version


# ---------------------------------------------------------------- ACL


def grant_document_acl(
    session: Session,
    *,
    tenant_id: str,
    document_id: str,
    subject_type: str,
    subject_id: str,
    permission: str,
) -> models.DocumentAcl:
    acl = models.DocumentAcl(
        tenant_id=tenant_id,
        document_id=document_id,
        subject_type=subject_type,
        subject_id=subject_id,
        permission=permission,
    )
    session.add(acl)
    session.flush()
    return acl


def list_document_acl(session: Session, tenant_id: str, document_id: str) -> Sequence[models.DocumentAcl]:
    stmt = select(models.DocumentAcl).where(
        models.DocumentAcl.tenant_id == tenant_id,
        models.DocumentAcl.document_id == document_id,
    )
    return session.execute(stmt).scalars().all()


# ---------------------------------------------------------------- 处理任务


def create_ingestion_job(
    session: Session,
    *,
    tenant_id: str,
    document_id: str,
    document_version_id: str | None = None,
    status: str = "pending",
    stage: str = "received",
) -> models.IngestionJob:
    job = models.IngestionJob(
        tenant_id=tenant_id,
        document_id=document_id,
        document_version_id=document_version_id,
        status=status,
        stage=stage,
    )
    session.add(job)
    session.flush()
    return job


def get_ingestion_job(session: Session, tenant_id: str, job_id: str) -> models.IngestionJob | None:
    stmt = select(models.IngestionJob).where(
        models.IngestionJob.tenant_id == tenant_id,
        models.IngestionJob.id == job_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_ingestion_jobs(
    session: Session,
    tenant_id: str,
    document_id: str | None = None,
    *,
    limit: int = 100,
) -> Sequence[models.IngestionJob]:
    stmt = select(models.IngestionJob).where(models.IngestionJob.tenant_id == tenant_id)
    if document_id:
        stmt = stmt.where(models.IngestionJob.document_id == document_id)
    stmt = stmt.order_by(models.IngestionJob.created_at.desc()).limit(limit)
    return session.execute(stmt).scalars().all()


def update_ingestion_job(
    session: Session,
    tenant_id: str,
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    document_version_id: str | None = None,
    retry_count: int | None = None,
) -> models.IngestionJob | None:
    """按阶段推进任务状态。只更新显式传入的字段，其余保持原值。"""

    job = get_ingestion_job(session, tenant_id, job_id)
    if job is None:
        return None
    if status is not None:
        job.status = status
    if stage is not None:
        job.stage = stage
    if error_code is not None:
        job.error_code = error_code
    if error_message is not None:
        job.error_message = error_message
    if document_version_id is not None:
        job.document_version_id = document_version_id
    if retry_count is not None:
        job.retry_count = retry_count
    job.updated_at = utcnow()
    session.flush()
    return job


def fail_ingestion_job(
    session: Session,
    tenant_id: str,
    job_id: str,
    *,
    stage: str,
    error_code: str,
    error_message: str,
) -> models.IngestionJob | None:
    """记录失败：状态置 failed，保留失败所在阶段，便于从该阶段幂等重试。"""

    job = get_ingestion_job(session, tenant_id, job_id)
    if job is None:
        return None
    job.status = "failed"
    job.stage = stage
    job.error_code = error_code
    job.error_message = error_message
    job.retry_count = (job.retry_count or 0) + 1
    job.updated_at = utcnow()
    session.flush()
    return job


# ---------------------------------------------------------------- Chunk


def insert_chunks(
    session: Session,
    *,
    tenant_id: str,
    document_version_id: str,
    chunks: Sequence[dict],
) -> int:
    """批量写入 chunk。

    ``chunks`` 中每个元素需含 ``chunk_id`` / ``text`` / ``token_count`` / ``content_hash``，
    可选 ``parent_chunk_id`` / ``metadata_json``。父 chunk 必须排在子 chunk 之前，
    因为 ``(document_version_id, parent_chunk_id)`` 是指向自身的复合外键。
    """

    rows = [
        models.DocumentChunk(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            chunk_id=str(item["chunk_id"]),
            parent_chunk_id=item.get("parent_chunk_id"),
            text=str(item["text"]),
            token_count=int(item.get("token_count", 0)),
            content_hash=str(item["content_hash"]),
            metadata_json=item.get("metadata_json"),
        )
        for item in chunks
    ]
    session.add_all(rows)
    session.flush()
    return len(rows)


def list_chunks(session: Session, tenant_id: str, document_version_id: str) -> Sequence[models.DocumentChunk]:
    stmt = (
        select(models.DocumentChunk)
        .where(
            models.DocumentChunk.tenant_id == tenant_id,
            models.DocumentChunk.document_version_id == document_version_id,
        )
        .order_by(models.DocumentChunk.created_at.asc(), models.DocumentChunk.chunk_id.asc())
    )
    return session.execute(stmt).scalars().all()


def count_chunks(session: Session, tenant_id: str, document_version_id: str) -> int:
    stmt = select(func.count()).select_from(models.DocumentChunk).where(
        models.DocumentChunk.tenant_id == tenant_id,
        models.DocumentChunk.document_version_id == document_version_id,
    )
    return int(session.execute(stmt).scalar_one())


# ---------------------------------------------------------------- 索引构建


def create_index_build(
    session: Session,
    *,
    tenant_id: str,
    index_name: str,
    embedding_model: str,
    embedding_dimension: int,
    chunk_count: int = 0,
    status: str = "building",
    manifest_uri: str | None = None,
) -> models.IndexBuild:
    stmt = select(func.max(models.IndexBuild.version)).where(
        models.IndexBuild.tenant_id == tenant_id,
        models.IndexBuild.index_name == index_name,
    )
    current = session.execute(stmt).scalar_one_or_none()
    build = models.IndexBuild(
        tenant_id=tenant_id,
        index_name=index_name,
        version=int(current or 0) + 1,
        status=status,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        chunk_count=chunk_count,
        manifest_uri=manifest_uri,
    )
    session.add(build)
    session.flush()
    return build


def get_index_build(session: Session, tenant_id: str, build_id: str) -> models.IndexBuild | None:
    stmt = select(models.IndexBuild).where(
        models.IndexBuild.tenant_id == tenant_id,
        models.IndexBuild.id == build_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def active_index_build(session: Session, tenant_id: str, index_name: str) -> models.IndexBuild | None:
    stmt = (
        select(models.IndexBuild)
        .where(
            models.IndexBuild.tenant_id == tenant_id,
            models.IndexBuild.index_name == index_name,
            models.IndexBuild.status == "active",
        )
        .order_by(models.IndexBuild.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def set_index_build_status(
    session: Session,
    tenant_id: str,
    build_id: str,
    status: str,
    *,
    manifest_uri: str | None = None,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> models.IndexBuild | None:
    build = get_index_build(session, tenant_id, build_id)
    if build is None:
        return None
    build.status = status
    if manifest_uri is not None:
        build.manifest_uri = manifest_uri
    if chunk_count is not None:
        build.chunk_count = chunk_count
    if error_message is not None:
        build.error_message = error_message
    build.updated_at = utcnow()
    session.flush()
    return build


# ---------------------------------------------------------------- 审计


def record_audit_event(
    session: Session,
    *,
    tenant_id: str,
    action: str,
    resource_type: str,
    actor_id: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    summary_json: dict | None = None,
    created_at: datetime | None = None,
) -> models.AuditEvent:
    event = models.AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        summary_json=summary_json,
    )
    if created_at is not None:
        event.created_at = created_at
    session.add(event)
    session.flush()
    return event


def list_audit_events(
    session: Session,
    tenant_id: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = 100,
) -> Sequence[models.AuditEvent]:
    stmt = select(models.AuditEvent).where(models.AuditEvent.tenant_id == tenant_id)
    if resource_type:
        stmt = stmt.where(models.AuditEvent.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(models.AuditEvent.resource_id == resource_id)
    stmt = stmt.order_by(models.AuditEvent.created_at.asc()).limit(limit)
    return session.execute(stmt).scalars().all()
