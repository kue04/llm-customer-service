"""文档上传与异步任务 API（计划 2.3，批次 B4；阶段 4.2 补 ACL 与索引重建）。

端点
----
```
POST /knowledge-bases/{knowledge_base_id}/documents   # 上传，202 + document_id/job_id
GET  /documents/{document_id}                         # 文档详情（含最新版本摘要）
GET  /documents/{document_id}/versions                # 版本列表
POST /documents/{document_id}/reprocess               # 重新处理（新建 job）
GET  /ingestion-jobs/{job_id}                         # 任务查询（含解析警告）
POST /ingestion/indexes/rebuild                       # 整份重建 chunk 索引（B7，index:rebuild）
```

**上传只做四件事：鉴权 → 类型/大小检查 → 对象存储 → 建任务。**
不解析、不切分、不算 chunk、不做内容 hash 的重复判定，也不建 ``document_versions``
（决策见台账 [D-6]：版本由流水线在 ``parsed`` 阶段创建）。
真正解析在 Redis Stream worker（阶段 3.3）里跑，走
``services.ingestion.parser_registry.parse_document()`` —— 本模块**不 import 任何具体解析器**。

三条贯穿全部端点的约束
----------------------
1. ``tenant_id`` **只从 ``AuthContext`` 读取**。请求体/查询串里的同名字段一律忽略，
   跨租户资源返回 404（与「不存在」完全相同的响应，不泄漏存在性）。
2. 扩展名白名单直接取 ``parser_registry.ALLOWED_EXTENSIONS``，
   保证「允许上传的扩展名」与「能解析的扩展名」永远同源。
3. ``filename`` 只作展示：对象 key 由租户 ID + 文档 ID + 净化后的文件名拼成，
   永不进入 shell 命令。
4. **资源级权限 + document ACL**（阶段 4.2，B7）：写接口要求 ``document:upload`` 与
   知识库写成员；读接口（详情 / 版本 / 任务）要求 ``document:read`` **且**
   ``document_acl`` 允许 —— 无权者与"不存在"返回**逐字相同**的 404
   （计划 4.2 第 5 条：被过滤文档的标题、数量、引用与 trace 均不泄漏）。
5. **索引重建单独授权**（阶段 4.2，B7）：``POST /ingestion/indexes/rebuild`` 要
   ``index:rebuild``，与发布知识（``document:publish``）分开 ——
   一次重建影响所有租户的检索结果（发布只影响一条知识）。
6. **索引回滚再单独授权**（B8）：``POST /ingestion/indexes/rollback`` 要
   ``index:rollback``。「能重建索引」≠「能把线上检索切回旧版本」——
   回滚只改指针、不重建，但同样影响所有租户，因此两个动作各自成键。
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from schemas.document_schema import (
    DocumentDetail,
    DocumentReprocessResponse,
    DocumentUploadResponse,
    DocumentVersionListResponse,
    DocumentVersionSummary,
    IndexRebuildResponse,
    IndexRollbackRequest,
    IndexRollbackResponse,
    IngestionJobDetail,
    ParseWarningItem,
)
from services.auth_service import (
    AuthContext,
    RequestMeta,
    get_auth_context,
    get_request_meta,
    require_read_operation_role,
    require_resource_scope,
    require_write_operation_role,
)
from services.ingestion import db, models, parser_registry, repository
from services.ingestion.content_sniff import matches_declared_type
from services.ingestion.index_builder import (
    ERROR_INDEX_BUILD_FAILED,
    ERROR_INDEX_BUILD_NOT_FOUND,
    ERROR_INDEX_ROLLBACK_FAILED,
    IndexBuildError,
    active_index_version,
    available_versions,
    rebuild_index,
    rollback_index,
)
from services.ingestion.index_manifest import DEFAULT_INDEX_NAME, IndexManifestError, load_active_manifest
from services.ingestion.object_store import (
    ObjectStore,
    build_object_key,
    get_object_store,
    logical_source_uri,
    sanitize_filename,
)
from services.ingestion.parsers.base import ParserError
from services.ingestion.pipeline import default_embedder, default_embedding_model, default_index_root
from services.ingestion.queue import IngestionQueue, QueueUnavailableError, get_queue
from services.privacy import mask_sensitive_text
from services.retrieval_access import document_is_visible


logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------- 常量

#: 上传 / 重处理用的 operation 名。
#:
#: ``document:upload`` / ``index:rebuild`` 这类资源级权限枚举已在阶段 4.1（B7）
#: 落地到 ``services.auth_context.RESOURCE_SCOPE_ROLES``，因此这里两层都用：
#: **操作维度**（沿用既有权，保证既有调用方的授权行为不变）+
#: **资源维度**（``require_resource_scope``，表达"这是哪个资源的哪个动作"）。
#: 上传是「在知识库里建内容」，故操作维度复用 ``knowledge_create``；
#: 重处理比上传更重（会重跑解析与索引），故复用角色集合**严格更窄**的
#: ``knowledge_rollback``（仅 supervisor / admin，见 [D-6]），
#: 它的资源维度等价物是 ``index:rebuild``（角色集合相同，因此这里不重复判定）。
UPLOAD_OPERATION = "knowledge_create"
READ_OPERATION = "knowledge_read"
REPROCESS_OPERATION = "knowledge_rollback"

#: 资源级权限名（阶段 4.1 枚举，键名即完整 scope）
DOCUMENT_UPLOAD_PERMISSION = "document:upload"
DOCUMENT_READ_PERMISSION = "document:read"
INDEX_REBUILD_PERMISSION = "index:rebuild"
#: 索引回滚**单独授权**（B8）：能重建索引 ≠ 能把线上检索切回旧版本。
#: 两者角色集合当前相同，但各自成键，理由见 ``services/auth_context`` 的三条划分。
INDEX_ROLLBACK_PERMISSION = "index:rollback"

#: 允许写知识库的成员角色。存在成员关系但只是 viewer / reviewer 时，仍应 403 ——
#: 「有成员关系」不等于「有写权限」。
KB_WRITE_MEMBER_ROLES = frozenset({"owner", "editor"})

#: 大小上限，默认 50 MB（计划 2.3），可由环境变量覆盖
MAX_UPLOAD_MB_ENV = "RAG_UPLOAD_MAX_MB"
DEFAULT_MAX_UPLOAD_MB = 50

#: 流式累计的读取粒度
READ_CHUNK_BYTES = 1024 * 1024

#: ``metadata_json`` 里存放解析警告的固定键（与 [D-6] 的决定一致）
WARNINGS_METADATA_KEY = "warnings"

#: ``documents.title`` 的长度上限（模型里是 String(512)）
TITLE_MAX_LENGTH = 512


# ---------------------------------------------------------------- 依赖


def get_session() -> Iterator[Session]:
    """每个请求一个事务（``session_scope`` 正常提交、异常回滚）。"""

    with db.session_scope() as session:
        yield session


# ---------------------------------------------------------------- 小工具


def _max_upload_bytes() -> int:
    """读取上传大小上限。

    配置写错时**报 500 而不是静默用默认值**：静默回退会让「运维改了个错值、
    以为限制生效了」这种情况完全不可观测。
    """

    raw = (os.getenv(MAX_UPLOAD_MB_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    try:
        megabytes = int(raw)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"上传大小限制配置错误：{MAX_UPLOAD_MB_ENV}={raw!r} 不是整数",
        ) from error
    if megabytes <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"上传大小限制配置错误：{MAX_UPLOAD_MB_ENV} 必须为正整数",
        )
    return megabytes * 1024 * 1024


def _extension_of(filename: str) -> str:
    """取小写扩展名（含点），两种路径分隔符都当分隔符看。"""

    return Path(str(filename or "").replace("\\", "/")).suffix.lower()


def _document_title(filename: str) -> str:
    """首次写入 ``documents.title``：文件名主干（模板保证解析后会被真实标题覆盖）。"""

    safe_name = sanitize_filename(filename)
    stem = Path(safe_name).stem or safe_name
    return stem[:TITLE_MAX_LENGTH]


async def _read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """流式累计读取上传内容，超限立即中止。

    不用 ``await file.read()`` 一把梭：50 MB × 并发会把内存打爆。
    分块读取的意义在于**超限时立刻失败**，而不是先分配完整个文件再判断。
    """

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"文件超过上限 {max_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _resolve_principal_user_id(session: Session, auth: AuthContext) -> str | None:
    """把 JWT 的 ``sub`` 映射成本服务的 ``users.id``。

    **JWT 里带的是身份提供方的 ``external_id``，与本服务的 ``users.id`` 不是一回事。**
    两处地方都需要这个映射，所以集中在这里：

    * ``knowledge_base_members.user_id`` 有指向 ``users.id`` 的外键 —— 拿 JWT 的 sub
      直接去比对成员关系，即使成员关系存在也永远匹配不上，写权限会被无声地全判成 403；
    * ``documents.created_by`` 同样是 ``users.id`` 的外键 —— 直接写 sub 会撞外键
      （SQLite 已开启 ``PRAGMA foreign_keys=ON``，测试里也会真实报错）。

    用户尚未在本服务建档时返回 ``None``：宁可少一个可选的展示字段，
    也不伪造一个不存在的用户 ID。调用方据此判定「该身份在本租户没有任何资源权限」。
    """

    user = repository.get_user_by_external_id(session, auth.tenant_id, auth.user_id)
    return user.id if user is not None else None


#: 审计摘要里需要脱敏的键：**只包括可能含个人信息的自由文本**。
#:
#: 刻意不做「对所有字符串统一脱敏」：``mask_sensitive_text`` 会把 12~20 位的连续数字
#: 当作订单号打码，而 ``document_id`` / ``job_id`` / ``knowledge_base_id`` 是 32 位 hex，
#: 几乎必然含这么长的数字串 —— 统一脱敏会把审计摘要里的 ID 变成不可关联的乱码，
#: 使「按 ID 追一条操作」这件事直接失效。结构化 ID 由服务端生成、不含个人信息，
#: 因此保持原样；自由文本（客户端文件名、User-Agent）必须脱敏。
MASKED_SUMMARY_KEYS = frozenset({"filename", "user_agent", "error_message", "reason"})


def _record_audit(
    session: Session,
    auth: AuthContext,
    meta: RequestMeta,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    summary: dict[str, Any],
) -> None:
    """写 ``audit_events``（actor / tenant / action / resource / request_id / 脱敏摘要）。

    摘要里的**自由文本**统一过 ``mask_sensitive_text``：文件名可能是
    「13800138000_账单.pdf」这类含个人信息的名字，审计表不允许成为绕开脱敏的旁路；
    服务端生成的结构化 ID 保持不变（理由见 ``MASKED_SUMMARY_KEYS``）。
    写在与业务同一个事务里，保证「有文档就有审计」，不会出现只办成一半的状态。
    """

    masked: dict[str, Any] = {
        key: mask_sensitive_text(value) if key in MASKED_SUMMARY_KEYS and isinstance(value, str) else value
        for key, value in summary.items()
    }
    masked["actor_role"] = auth.primary_role
    masked["ip"] = meta.ip
    masked["user_agent"] = mask_sensitive_text(meta.user_agent)

    repository.record_audit_event(
        session,
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=auth.request_id,
        summary_json=masked,
    )


# ---------------------------------------------------------------- 资源校验


def _load_knowledge_base(session: Session, auth: AuthContext, knowledge_base_id: str) -> models.KnowledgeBase:
    """按租户取知识库。查不到一律 404（跨租户与不存在返回相同响应，不泄漏存在性）。"""

    knowledge_base = repository.get_knowledge_base(session, auth.tenant_id, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return knowledge_base


def _require_kb_write_member(session: Session, auth: AuthContext, knowledge_base_id: str) -> None:
    """要求调用者是该知识库的写成员（owner / editor）。

    「有成员关系」不等于「有写权限」：reviewer / viewer 一律拒绝。
    身份在本服务没有建档（映射不到 ``users.id``）时也没有任何成员关系，同样 403。
    """

    principal = _resolve_principal_user_id(session, auth)
    if principal is not None:
        member = repository.get_knowledge_base_member(session, auth.tenant_id, knowledge_base_id, principal)
        if member is not None and member.member_role in KB_WRITE_MEMBER_ROLES:
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="缺少该知识库的写权限",
    )


def _load_document(session: Session, auth: AuthContext, document_id: str) -> models.Document:
    """按租户取文档。查不到一律 404（不区分「不存在」与「属于别的租户」）。"""

    document = repository.get_document(session, auth.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return document


def _load_readable_document(session: Session, auth: AuthContext, document_id: str) -> models.Document:
    """读路径取文档：租户匹配 **且** document ACL 允许（阶段 4.1 / 4.2）。

    被 ACL 拦下时返回的 404 与「文档不存在」**逐字相同**（同一个 detail、同一个状态码）：
    否则调用方可以通过「404 还是 403」推断出"这个 id 在别人的受控文档里存在"，
    这正是计划 4.2 第 5 条要防的泄漏（也是 C3 的既有原则）。
    """

    document = _load_document(session, auth, document_id)
    if not document_is_visible(session, auth, document.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return document


def _publish_job(job_id: str, queue: IngestionQueue) -> None:
    """投递任务。队列不可用时返回 503，但**不删除已入库的 job**。

    删除 job 会让对象存储里的字节变成无人引用的孤儿数据，也让用户失去这次操作的痕迹；
    保留 ``pending`` 的 job 意味着稍后可以用 ``/reprocess`` 重投，是可恢复状态。
    """

    try:
        queue.publish(job_id)
    except QueueUnavailableError as error:
        logger.error("failed to publish ingestion job %s: %s", job_id, error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务队列暂不可用，上传已记录，请稍后重试",
        ) from error


# ---------------------------------------------------------------- 响应组装


def _version_summary(version: models.DocumentVersion) -> DocumentVersionSummary:
    return DocumentVersionSummary(
        id=version.id,
        version=version.version,
        content_hash=version.content_hash,
        parser_name=version.parser_name,
        parser_version=version.parser_version,
        status=version.status,
        created_at=version.created_at,
    )


def _document_detail(document: models.Document, latest: models.DocumentVersion | None) -> DocumentDetail:
    return DocumentDetail(
        id=document.id,
        tenant_id=document.tenant_id,
        knowledge_base_id=document.knowledge_base_id,
        source_uri=document.source_uri,
        source_type=document.source_type,
        title=document.title,
        status=document.status,
        created_by=document.created_by,
        created_at=document.created_at,
        updated_at=document.updated_at,
        latest_version=_version_summary(latest) if latest is not None else None,
    )


def _extract_warnings(version: models.DocumentVersion | None) -> list[ParseWarningItem]:
    """从版本 ``metadata_json["warnings"]`` 里展开解析警告。

    形状与 ``ParseWarning.to_dict()`` 一一对应（``{code, message, detail}``），
    因此这里只做形状兜底，不做字段改名 —— 前端按 ``code`` 分类展示即可。
    """

    if version is None or not isinstance(version.metadata_json, dict):
        return []
    raw = version.metadata_json.get(WARNINGS_METADATA_KEY)
    if not isinstance(raw, list):
        return []

    warnings: list[ParseWarningItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        detail = item.get("detail")
        warnings.append(
            ParseWarningItem(
                code=str(item.get("code") or ""),
                message=str(item.get("message") or ""),
                detail=detail if isinstance(detail, dict) else None,
            )
        )
    return warnings


# ---------------------------------------------------------------- 端点


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传文档并创建异步解析任务",
)
async def upload_document(
    knowledge_base_id: str,
    file: UploadFile = File(..., description="待接入的文档：PDF / DOCX / HTML / MD / TXT"),
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_object_store),
    queue: IngestionQueue = Depends(get_queue),
) -> DocumentUploadResponse:
    """上传只做「鉴权 → 类型/大小检查 → 对象存储 → 建任务」，202 即刻返回。

    解析、切分、内容 hash 重复判定都在 worker 与流水线（3.3）里异步完成。
    """

    require_write_operation_role(UPLOAD_OPERATION, auth)
    require_resource_scope(DOCUMENT_UPLOAD_PERMISSION, auth)
    knowledge_base = _load_knowledge_base(session, auth, knowledge_base_id)
    _require_kb_write_member(session, auth, knowledge_base.id)

    filename = file.filename or ""
    extension = _extension_of(filename)
    if extension not in parser_registry.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型 {extension or '（无扩展名）'}；允许：{', '.join(parser_registry.ALLOWED_EXTENSIONS)}",
        )

    data = await _read_upload_limited(file, _max_upload_bytes())

    # 类型判定与解析路径同源：都走注册表，扩展名优先、MIME 兜底
    try:
        source_type = parser_registry.detect_source_type(filename, file.content_type)
    except ParserError as error:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=error.message) from error

    if not matches_declared_type(source_type, data):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"文件内容与扩展名 {extension} 不一致（{source_type} 需要对应的文件头特征）",
        )

    # 同一租户内「同名 = 同一份文档」（[D-1]）：命中则追加版本，不再新建文档
    source_uri = logical_source_uri(filename)
    existing = repository.get_document_by_source_uri(session, auth.tenant_id, source_uri)
    if existing is not None and existing.knowledge_base_id != knowledge_base.id:
        # 命中但归属另一个知识库：给别人的库写内容需要那个库的写权限，
        # 否则「KB-A 的成员」可以借同名文件给 KB-B 的文档追加版本。
        _require_kb_write_member(session, auth, existing.knowledge_base_id)

    document = existing
    if document is None:
        document = repository.create_document(
            session,
            tenant_id=auth.tenant_id,
            knowledge_base_id=knowledge_base.id,
            source_uri=source_uri,
            source_type=source_type,
            title=_document_title(filename),
            created_by=_resolve_principal_user_id(session, auth),
        )

    object_key = build_object_key(auth.tenant_id, document.id, filename)
    store.put(object_key, data, content_type=file.content_type)

    job = repository.create_ingestion_job(
        session,
        tenant_id=auth.tenant_id,
        document_id=document.id,
        status="pending",
        stage="received",
    )
    _record_audit(
        session,
        auth,
        meta,
        action="document_upload",
        resource_type="document",
        resource_id=document.id,
        summary={
            "filename": filename,
            "size_bytes": len(data),
            "source_type": source_type,
            "knowledge_base_id": knowledge_base.id,
            "job_id": job.id,
            "reused_document": existing is not None,
        },
    )

    # 先提交再投递：worker 可能在投递成功后立刻执行，那时 job 必须已经查得到
    session.commit()
    _publish_job(job.id, queue)

    return DocumentUploadResponse(
        document_id=document.id,
        job_id=job.id,
        knowledge_base_id=document.knowledge_base_id,
        source_uri=document.source_uri,
        source_type=document.source_type,
        filename=sanitize_filename(filename),
        size_bytes=len(data),
        job_status=job.status,
        job_stage=job.stage,
        reused_document=existing is not None,
    )


@router.get("/documents/{document_id}", response_model=DocumentDetail, summary="查询文档详情")
def get_document_detail(
    document_id: str,
    auth: AuthContext = Depends(get_auth_context),
    session: Session = Depends(get_session),
) -> DocumentDetail:
    require_read_operation_role(READ_OPERATION, auth)
    require_resource_scope(DOCUMENT_READ_PERMISSION, auth)
    document = _load_readable_document(session, auth, document_id)
    latest = repository.latest_document_version(session, auth.tenant_id, document.id)
    return _document_detail(document, latest)


@router.get(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionListResponse,
    summary="查询文档版本列表",
)
def list_document_versions(
    document_id: str,
    auth: AuthContext = Depends(get_auth_context),
    session: Session = Depends(get_session),
) -> DocumentVersionListResponse:
    require_read_operation_role(READ_OPERATION, auth)
    require_resource_scope(DOCUMENT_READ_PERMISSION, auth)
    document = _load_readable_document(session, auth, document_id)
    versions = repository.list_document_versions(session, auth.tenant_id, document.id)
    return DocumentVersionListResponse(
        document_id=document.id,
        total=len(versions),
        items=[_version_summary(version) for version in versions],
    )


@router.post(
    "/documents/{document_id}/reprocess",
    response_model=DocumentReprocessResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新处理文档（新建任务）",
)
def reprocess_document(
    document_id: str,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
    session: Session = Depends(get_session),
    queue: IngestionQueue = Depends(get_queue),
) -> DocumentReprocessResponse:
    """重跑消费索引资源，因此用比上传更严的授权与独立的审计动作名。

    **新建 job，不复用旧 job 改状态**：``retry_count`` 的语义是「同一个 job 的失败重试
    次数」，与「用户又点了一次重处理」不是一回事，混用会让重试次数失去意义。
    流水线侧的「从失败阶段幂等重试」属于 3.3，这里只把新 job 的起点设为 ``received``。
    """

    require_write_operation_role(REPROCESS_OPERATION, auth)
    document = _load_document(session, auth, document_id)
    _require_kb_write_member(session, auth, document.knowledge_base_id)

    job = repository.create_ingestion_job(
        session,
        tenant_id=auth.tenant_id,
        document_id=document.id,
        status="pending",
        stage="received",
    )
    _record_audit(
        session,
        auth,
        meta,
        action="document_reprocess",
        resource_type="document",
        resource_id=document.id,
        summary={
            "document_id": document.id,
            "knowledge_base_id": document.knowledge_base_id,
            "job_id": job.id,
        },
    )

    session.commit()
    _publish_job(job.id, queue)

    return DocumentReprocessResponse(
        document_id=document.id,
        job_id=job.id,
        job_status=job.status,
        job_stage=job.stage,
    )


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobDetail, summary="查询接入任务")
def get_ingestion_job(
    job_id: str,
    auth: AuthContext = Depends(get_auth_context),
    session: Session = Depends(get_session),
) -> IngestionJobDetail:
    require_read_operation_role(READ_OPERATION, auth)
    require_resource_scope(DOCUMENT_READ_PERMISSION, auth)
    job = repository.get_ingestion_job(session, auth.tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    # 任务详情里带 document_id 与解析警告（可能含文档内容片段），因此同样要过 ACL：
    # 否则"检索不到"的受控文档可以通过任务接口把标题与警告读出来。
    if not document_is_visible(session, auth, job.document_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    # 解析警告挂在版本上（ingestion_jobs 没有 warnings 列）：优先取 job 绑定的版本，
    # 尚未绑定（上传期建的 job）时退回文档的最新版本，保证警告一旦产生就可查询。
    version = None
    if job.document_version_id:
        version = repository.get_document_version(session, auth.tenant_id, job.document_version_id)
    if version is None:
        version = repository.latest_document_version(session, auth.tenant_id, job.document_id)

    return IngestionJobDetail(
        id=job.id,
        tenant_id=job.tenant_id,
        document_id=job.document_id,
        document_version_id=job.document_version_id,
        status=job.status,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message,
        retry_count=job.retry_count,
        created_at=job.created_at,
        updated_at=job.updated_at,
        warnings=_extract_warnings(version),
    )


# ---------------------------------------------------------------- 索引重建（4.2）


@router.post(
    "/ingestion/indexes/rebuild",
    response_model=IndexRebuildResponse,
    summary="重建 chunk 索引（需要 index:rebuild）",
    description=(
        "整份重建全局 chunk 索引并原子切换（构建到临时目录 → 校验 → 改指针）。\n\n"
        "与**发布知识**分开授权（计划 4.2）：本端点要求资源级权限 `index:rebuild`"
        "（supervisor / admin），因为一次重建会影响**所有租户**的检索结果；\n"
        "发布一条审核过的知识用 `POST /knowledge/publish-approved`"
        "（`document:publish`，knowledge_ops 也有）。"
    ),
)
def rebuild_chunk_index(
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
    session: Session = Depends(get_session),
) -> IndexRebuildResponse:
    """重建索引。**只重建，不改知识内容**；失败时旧索引与指针保持不动。

    构建失败（``IndexBuildError`` / ``IndexManifestError``）一律转成
    503 + 稳定 ``error_code``：这类失败的原因是"环境没准备好"（模型缺失、
    磁盘只读…）而不是请求不合法，重试有意义，且旧索引仍然可用 ——
    这正是 3.4 的「失败不得覆盖当前索引」在 API 层的表现。
    """

    require_resource_scope(INDEX_REBUILD_PERMISSION, auth)

    try:
        result = rebuild_index(
            session,
            tenant_id=auth.tenant_id,
            root=default_index_root(),
            embedder=default_embedder,
            embedding_model=default_embedding_model(),
            index_name=DEFAULT_INDEX_NAME,
        )
    except (IndexBuildError, IndexManifestError) as error:
        logger.error("index rebuild failed for tenant %s: %s", auth.tenant_id, error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": getattr(error, "error_code", ERROR_INDEX_BUILD_FAILED), "message": error.message},
        ) from error

    # 读回"检索层将要看到的那一份"manifest 来统计数据：响应与审计都不依赖
    # 构建过程中的中间对象，而是以**已生效的索引**为准（否则可能报一份没生效的版本）。
    tenant_count = 0
    if result.switched:
        manifest, _pointer, _index_file = load_active_manifest(default_index_root(), DEFAULT_INDEX_NAME)
        tenant_count = len({entry.tenant_id for entry in manifest.entries})

    _record_audit(
        session,
        auth,
        meta,
        action="index_rebuild",
        resource_type="index",
        resource_id=result.index_name,
        summary={
            "index_name": result.index_name,
            "index_version": result.index_version,
            "chunk_count": result.chunk_count,
            "switched": result.switched,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
            "embedding_model": result.embedding_model,
            "tenant_count": tenant_count,
        },
    )
    session.commit()

    return IndexRebuildResponse(
        index_name=result.index_name,
        index_version=result.index_version,
        chunk_count=result.chunk_count,
        embedding_model=result.embedding_model,
        embedding_dimension=result.embedding_dimension,
        manifest_uri=result.manifest_uri,
        switched=result.switched,
        skipped=result.skipped,
        skip_reason=result.skip_reason,
        tenant_count=tenant_count,
    )


# ---------------------------------------------------------------- 索引回滚（B8）


@router.post(
    "/ingestion/indexes/rollback",
    response_model=IndexRollbackResponse,
    summary="回滚索引到指定版本（需要 index:rollback）",
    description=(
        "把**生效指针**切回磁盘上已存在的旧版本：不重建、不删文件，只改指针。\n\n"
        "与**重建**分开授权（B8）：`index:rebuild` 是「建一份新的」，"
        "`index:rollback` 是「把线上切回旧的」—— 两者都影响所有租户的检索结果，"
        "但动作不同，因此各自成键（当前角色集合相同：supervisor / admin）。\n\n"
        "目标版本**必须已存在且通过校验**：回滚前会重新校验旧索引文件，"
        "宁可在回滚时就明确失败，也不让指针指向一份坏索引 ——"
        "后者要等下一次检索才炸，排查难度高得多。"
    ),
)
def rollback_chunk_index(
    payload: IndexRollbackRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
    session: Session = Depends(get_session),
) -> IndexRollbackResponse:
    """回滚索引。**只改指针**；失败时指针保持不动（与发布共用同一段切换代码）。

    错误分两类而不是一律 503，调用方能据此决定「换个版本重试」还是「等一会再来」：

    * **409**：目标版本不存在 / 不属于该租户 / ``index_builds`` 里没有记录
      （请求指向了一个不可达的状态）；
    * **503**：环境没准备好（faiss 或模型缺失等，与重建端点一致）。
    """

    require_resource_scope(INDEX_ROLLBACK_PERMISSION, auth)

    root = default_index_root()

    # 先记下"当前生效的是哪一版"：它既是响应里 previous_version 的来源，
    # 也是审计里判断"这次回滚到底生效没有"的依据。还没有生效索引时取不到，不算错误。
    try:
        previous_version: int | None = active_index_version(root, DEFAULT_INDEX_NAME)
    except IndexManifestError:
        previous_version = None

    try:
        result = rollback_index(
            session,
            tenant_id=auth.tenant_id,
            root=root,
            index_version=payload.index_version,
            index_name=DEFAULT_INDEX_NAME,
        )
    except (IndexBuildError, IndexManifestError) as error:
        error_code = getattr(error, "error_code", ERROR_INDEX_ROLLBACK_FAILED)
        logger.warning(
            "index rollback rejected for tenant %s -> v%s: %s",
            auth.tenant_id,
            payload.index_version,
            error,
        )
        request_side = error_code in (ERROR_INDEX_ROLLBACK_FAILED, ERROR_INDEX_BUILD_NOT_FOUND)
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT if request_side else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail={"error_code": error_code, "message": error.message},
        ) from error

    # 回滚成功后给出"现在还能切到哪些版本"，让运维不必猜；取不到也不影响回滚结果本身
    try:
        versions = available_versions(root, DEFAULT_INDEX_NAME)
    except (IndexManifestError, OSError):
        versions = []

    _record_audit(
        session,
        auth,
        meta,
        action="index_rollback",
        resource_type="index",
        resource_id=result.index_name,
        summary={
            "index_name": result.index_name,
            "from_version": previous_version,
            "to_version": result.index_version,
            "chunk_count": result.chunk_count,
            "switched": result.switched,
            "reason": payload.reason,
        },
    )
    session.commit()

    return IndexRollbackResponse(
        index_name=result.index_name,
        index_version=result.index_version,
        previous_version=previous_version,
        chunk_count=result.chunk_count,
        embedding_model=result.embedding_model,
        manifest_uri=result.manifest_uri,
        switched=result.switched,
        available_versions=versions,
    )
