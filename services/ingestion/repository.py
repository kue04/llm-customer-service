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

from sqlalchemy import delete, func, select
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


def update_document_version_metadata(
    session: Session,
    tenant_id: str,
    document_version_id: str,
    metadata_json: dict,
) -> models.DocumentVersion | None:
    """**整体替换**版本的 ``metadata_json``（流水线 3.3 的唯一写入点）。

    为什么不做「按 key 合并」：合并需要先读一次、改一个 key、再写回，
    而流水线在一批里要写三处（解析警告 / 生效切分配置 / 切分统计），
    每次都是「读-改-写」，重试与并发下必然互相覆盖。
    改为「流水线组装整份 dict、本函数整体替换」，同一事实只有一处组装逻辑。
    """

    document_version = get_document_version(session, tenant_id, document_version_id)
    if document_version is None:
        return None
    document_version.metadata_json = metadata_json
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


def list_tenant_document_acl(session: Session, tenant_id: str) -> Sequence[models.DocumentAcl]:
    """整个租户的 ACL 记录（B7 新增，只读）。

    「一个租户的所有文档 ACL」是**一次查询**，不是按文档 N 次查询 ——
    检索过滤要用它判断"哪些受控文档对我开放"，逐个文档查会把一次检索
    变成 O(文档数) 次 SQL。判定逻辑不在本函数里，见 ``services/retrieval_access.py``。
    """

    stmt = select(models.DocumentAcl).where(models.DocumentAcl.tenant_id == tenant_id)
    return session.execute(stmt).scalars().all()


def list_published_chunk_refs(session: Session, tenant_id: str) -> Sequence[tuple[str, str]]:
    """已发布版本的 ``(document_id, chunk_id)`` 列表（B7 新增，只读）。

    这是「版本状态」这一维度的判定来源：只有 ``published`` 版本可以进入检索可见集合，
    ``parsed`` / ``chunked`` / ``indexed`` 都不行。用一条 join 查询而不是
    "先查版本、再查 chunk"，避免两次往返之间版本状态发生变化导致的半新半旧快照。
    """

    stmt = (
        select(models.DocumentVersion.document_id, models.DocumentChunk.chunk_id)
        .join(
            models.DocumentVersion,
            (models.DocumentVersion.id == models.DocumentChunk.document_version_id)
            & (models.DocumentVersion.tenant_id == models.DocumentChunk.tenant_id),
        )
        .where(
            models.DocumentChunk.tenant_id == tenant_id,
            models.DocumentVersion.status == "published",
        )
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


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


def get_ingestion_job_unscoped(session: Session, job_id: str) -> models.IngestionJob | None:
    """按主键取任务，**不带租户条件** —— 仅供队列 worker（3.3）使用。

    为什么必须有这个"例外"：队列消息里只有 ``job_id``（B4 定的载荷），
    而 worker 是**系统进程**，不是携带令牌的租户请求，它没有"当前租户"。
    若强行要求 worker 先知道 tenant，就得把 tenant 也写进消息载荷 ——
    那等于让"谁的任务"变成消息内容，消息一旦被伪造就成了跨租户触发器。

    这里的安全边界不靠调用方自觉，靠**后续所有读写都用 job.tenant_id**：
    worker 只把这行记录当作"任务的身份证"，拿到的 tenant 是自己查出来的，
    不是别人告诉它的。因此即使有人往队列里塞别的租户的 job_id，
    最坏结果也只是让那个租户自己的任务被处理（它本来就会被处理）。
    """

    return session.get(models.IngestionJob, str(job_id))


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


def delete_chunks(session: Session, tenant_id: str, document_version_id: str) -> int:
    """删除某版本的全部 chunk，返回删除行数（流水线 3.3 幂等重写的第 1 步）。

    为什么**必须**先删再写：``chunk_id`` 是确定性哈希，重跑同一版本会算出同一批 id，
    直接插会撞 ``uq_document_chunks(document_version_id, chunk_id)``。
    「重跑 = 删旧 + 写新」让重试的**结果**与首次执行完全一致（行数、id、内容都相同），
    而不是变成「首次成功、重试报错」这种会让人以为数据坏了的形态。

    为什么**必须是一条 DELETE 语句**，而不是逐行 ``session.delete()``
    ---------------------------------------------------------------
    ``(document_version_id, parent_chunk_id)`` 是指向本表的复合自引用外键
    （见 ``models.DocumentChunk.__table_args__``）。父 chunk 与子 chunk 同属一个版本，
    删除时无论先删谁，只要对方还在，就构成外键违规。

    逐行删会让 ORM 发出 ``DELETE FROM document_chunks WHERE id = ?`` 的 executemany ——
    **每一行都是一条独立语句**；而 SQLite 对即时（immediate）外键约束的检查发生在
    **每条语句结束时**，于是"删掉父 chunk"这条语句结束时子 chunk 仍指向它：

        sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed
        [SQL: DELETE FROM document_chunks WHERE document_chunks.id = ?]

    （这不是推演，是 B6 基线实测：2 条幂等用例正是这样红的 —— 首次执行全绿，
    重跑在 ``persisted`` 阶段炸，见 ``reports/rag_ingestion_auth_review/B6_baseline_test.txt``。）

    一条 ``DELETE ... WHERE tenant_id = ? AND document_version_id = ?`` 让整个版本的行
    在**同一个语句内**一起消失，约束在语句结束时看到的是"全部删完"的一致状态。
    因此既不需要「先删子再删父」的拓扑排序，也不需要 ``ON DELETE CASCADE``
    或 ``PRAGMA defer_foreign_keys``。

    ``synchronize_session="fetch"`` 让 session 里已被删掉的 ORM 实例同步失效：
    同一个 session 紧接着会写入一批**新的** ``DocumentChunk``（``id`` 是新 uuid、
    ``chunk_id`` 与旧行相同），留着旧实例在 identity map 里会让"删干净了没有"变得不可信。
    """

    statement = (
        delete(models.DocumentChunk)
        .where(
            models.DocumentChunk.tenant_id == tenant_id,
            models.DocumentChunk.document_version_id == document_version_id,
        )
        .execution_options(synchronize_session="fetch")
    )
    result = session.execute(statement)
    session.flush()
    return int(result.rowcount or 0)


def list_indexable_chunks(
    session: Session,
    tenant_id: str,
    *,
    version_statuses: Sequence[str] | None = None,
    all_tenants: bool = False,
) -> Sequence[tuple[models.DocumentChunk, models.DocumentVersion, models.Document]]:
    """索引构建（3.4）的数据来源：chunk + 版本 + 文档三元组。

    ``version_statuses`` 为 ``None`` 时不做版本状态过滤（索引包含本租户全部 chunk），
    传入集合时只取这些状态的版本 —— 两种用法在测试里都有覆盖。
    默认不过滤的理由与后果见台账 ``[D-11]``：
    「索引文件里有什么」与「谁能检索到」是两件事，后者由检索层的 fail-closed
    filter 决定；若把可见性只押在「构建时过滤」上，ACL / 生效期变化时就得
    重建整个索引，而且仍然挡不住「同一份索引被两个租户共用」这类问题。

    ``all_tenants``（B7 新增，默认 ``False`` 即保持原行为）：
    索引**指针、版本目录、索引文件**都在 ``root/chunk_index/{index_name}/`` 这个
    **全局命名空间**下，一份索引被所有租户共用、由检索层按 ``tenant_id`` 过滤。
    因此全局索引的构建必须取**全部租户**的 chunk —— 只取触发构建的那个租户，
    会把其他租户的 chunk 从生效索引里挤出去（现象见踩坑 **B14**：静默的"查不到"）。
    带 ``tenant_id`` 的取数路径保留给按租户分片的场景与既有测试。

    另外**刻意不按 ``created_at`` 排序**（B5 发现的读回顺序问题）：
    索引顺序由调用方按 ``metadata_json["ordinal"]`` 决定，本函数只负责取数。
    """

    stmt = (
        select(models.DocumentChunk, models.DocumentVersion, models.Document)
        .join(
            models.DocumentVersion,
            (models.DocumentVersion.id == models.DocumentChunk.document_version_id)
            & (models.DocumentVersion.tenant_id == models.DocumentChunk.tenant_id),
        )
        .join(
            models.Document,
            (models.Document.id == models.DocumentVersion.document_id)
            & (models.Document.tenant_id == models.DocumentVersion.tenant_id),
        )
    )
    if not all_tenants:
        stmt = stmt.where(models.DocumentChunk.tenant_id == tenant_id)
    if version_statuses:
        stmt = stmt.where(models.DocumentVersion.status.in_(list(version_statuses)))
    return session.execute(stmt).all()


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
    """建一条 ``building`` 记录，版本号取**该 index_name 的全局最大版本 + 1**。

    版本号为什么是全局的（B7 修复）：``index_builds.version`` 直接决定版本目录名
    ``root/chunk_index/{index_name}/v{version}/``，而这个目录与指针都是**全局命名空间**。
    若版本号按 ``(tenant_id, index_name)`` 计，两个租户都会拿到 v1，
    ``publish_build_directory`` 就会**删掉另一个租户的 v1 目录**再改名 ——
    表现为「索引版本号对不上目录内容」，回滚时会加载到别人的索引（踩坑 **B14**）。
    表里仍保留 ``tenant_id``（记录"是谁触发的这次构建"，用于审计与排障），
    但它不参与版本号计算。
    """

    stmt = select(func.max(models.IndexBuild.version)).where(
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


def list_index_builds(
    session: Session,
    tenant_id: str,
    index_name: str,
    *,
    limit: int = 20,
    statuses: Sequence[str] | None = None,
    all_tenants: bool = False,
) -> Sequence[models.IndexBuild]:
    """按版本倒序列出索引构建记录（回滚时用来找「上一个 active 版本」）。

    ``all_tenants``（B7 新增，默认 ``False`` 保持原行为）：索引是**全局一份**、
    指针只有一个，因此「把旧的 active 降级为 superseded」必须跨租户做 ——
    否则另一个租户的旧记录会永远停在 ``active``，``active_index_build()``
    会指向一份早已被取代的索引（踩坑 **B14** 的连带问题）。
    """

    stmt = select(models.IndexBuild).where(
        models.IndexBuild.index_name == index_name,
    )
    if not all_tenants:
        stmt = stmt.where(models.IndexBuild.tenant_id == tenant_id)
    if statuses:
        stmt = stmt.where(models.IndexBuild.status.in_(list(statuses)))
    stmt = stmt.order_by(models.IndexBuild.version.desc()).limit(limit)
    return session.execute(stmt).scalars().all()


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
