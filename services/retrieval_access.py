"""资源级权限判定与**服务端构造**的检索过滤器（计划 4.1 / 4.3，批次 B7）。

这个模块只做一件事：把「谁在什么身份下能看什么」翻译成
:class:`~utils.vector_retriever.ChunkAccessFilter`。
它是 ``ChunkAccessFilter`` 的**唯一**生产构造点 —— 客户端没有任何入口能传 filter，
能传 filter 就等于能提权。

判定顺序（与计划 4.1 的原文一致）
--------------------------------
1. **JWT 有效** —— 在 ``services/auth_service.get_auth_context`` 里完成，本模块只接收
   已经校验过的 :class:`~services.auth_context.AuthContext`（没有 context 就没有判定输入）；
2. **租户匹配** —— 所有查询都带 ``AuthContext.tenant_id``；请求体 / 查询串里的
   ``tenant_id`` 一律忽略（``AuthContext`` 不可变，这里也没有接收它们的参数）；
3. **scope 满足操作** —— 由路由层用 ``require_*`` 判定（``document:read`` 等）；
4. **知识库成员关系** —— ``document_uploads`` 侧已有 ``_require_kb_write_member``；
   本模块提供 :func:`knowledge_base_is_readable` 供读路径复用；
5. **document ACL** —— :func:`document_is_visible` / :func:`visible_chunk_ids`；
6. **检索 filter 注入** —— :func:`build_chunk_access_filter` 注入 ``tenant_id``
   与 ``allowed_chunk_ids``。

两个方向性判断（本批拍板，登记为 ``[D-12]``）
--------------------------------------------
* **对查询者：无权限 = 零命中**，不是 403。返回 403 会泄漏"这条文档存在但你没权限"
  （C3「跨租户与不存在不可区分」的同一条原则）。因此受控文档在检索结果里
  与"不存在"完全一样：既不出现，也不报错。
* **对调用方（程序）：缺 filter = 报错**。这是契约违例，必须大声 —— 由
  ``search_chunk_index`` 抛 ``chunk_filter_required`` 实现，本模块不提供
  "不传 filter 也能检索"的便捷入口。

ACL 语义（方向标注，避免"看起来对"；本批拍板，登记为 ``[D-13]``）
-------------------------------------------------------------------
* 没有任何 ACL 记录的文档 → **租户内可见**（``document_acl`` 是"额外收紧"，
  不是"默认拒绝"）。依据：上传接口创建的文档在 ``document_acl`` 里没有行，
  若默认拒绝，则"刚上传的文档谁都检索不到"，与实际使用方式相反。
* 有 ACL 记录的文档 → 必须命中至少一条**允许读取**的记录：
  ``subject_type='tenant'`` 且 subject 等于本租户，或 ``subject_type='user'``
  且 subject 等于当前用户，或 ``subject_type='role'`` 且 subject 在当前角色集合里；
  ``permission`` 必须是 ``read``（``write`` / ``review`` / ``publish`` / ``delete``
  **不隐含读权限** —— 否则"能发布"会顺带把文档正文暴露给检索）。
* ``subject_type='group'`` **当前一律不匹配**：本服务没有组成员关系表，
  与其猜一个"可能是同组"的语义，不如 fail closed。等有组表时在这里补。
* 认证过了但用户在本服务没有建档（``users`` 表里没有）→ 仍然按
  ``AuthContext.user_id`` 与 ``roles`` 判定，不要求建档（否则 agent 角色
  会突然检索不到任何东西）。

生效期维度：**本批不适用，显式说明**
------------------------------------
计划 4.1 要求「生效期（若表里有生效字段）」。``document_versions`` / ``documents``
里**没有** ``effective_from`` / ``effective_to`` / ``expires_at`` 之类的列
（模型见 ``services/ingestion/models.py:281``，迁移 0001 也未建这些列），
因此本批**没有**生效期判定 —— 这不是漏做，而是"表里没有可判定的字段"。
真要加的话，正确做法是新开一次 Alembic 迁移 + 同步
``tests/test_ingestion_models.py`` 的 schema 对照断言（台账第 4 节既有约定），
再加一条过滤条件；本批的可见性判定已把入口收敛到 :func:`visible_chunk_ids` 一处，
届时只改那一个函数即可。归档 / 未发布 / 旧版本则**已经**被排除：
版本状态必须是 ``published``（``parsed`` / ``chunked`` / ``indexed`` 均不可见），
``archived`` 文档的版本状态不会是 ``published``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from services.auth_context import AuthContext
from services.ingestion import models, repository
from utils.vector_retriever import ChunkAccessFilter


#: 允许"读取"的 ACL 权限。其余权限不隐含读权限（方向标注见模块 docstring）。
READ_PERMISSION = "read"

#: 参与 ACL 判定的主体类型；``group`` 暂时不可判定（没有组表，fail closed）
SUPPORTED_SUBJECT_TYPES: frozenset[str] = frozenset({"tenant", "user", "role"})


def acl_allows_read(
    entries: Sequence[models.DocumentAcl],
    *,
    tenant_id: str,
    user_id: str,
    roles: frozenset[str] | set[str],
) -> bool:
    """ACL 记录集合是否允许该身份读取。

    ``entries`` 为空 → ``True``（无 ACL = 租户内可见，见模块 docstring）。
    """

    if not entries:
        return True
    role_set = {str(role) for role in roles}
    for entry in entries:
        if entry.permission != READ_PERMISSION:
            continue
        if entry.subject_type not in SUPPORTED_SUBJECT_TYPES:
            continue
        if entry.subject_type == "tenant" and entry.subject_id == tenant_id:
            return True
        if entry.subject_type == "user" and entry.subject_id == user_id:
            return True
        if entry.subject_type == "role" and entry.subject_id in role_set:
            return True
    return False


def acl_entries_by_document(
    entries: Sequence[models.DocumentAcl],
) -> dict[str, list[models.DocumentAcl]]:
    """把 ACL 记录按 document_id 分组（一次查询，判定时不再回表）。"""

    grouped: dict[str, list[models.DocumentAcl]] = {}
    for entry in entries:
        grouped.setdefault(entry.document_id, []).append(entry)
    return grouped


def visible_chunk_ids(
    session: Session,
    auth: AuthContext,
    *,
    published_refs: Sequence[tuple[str, str]] | None = None,
    acl_entries: Sequence[models.DocumentAcl] | None = None,
) -> frozenset[str]:
    """当前身份**可见**的 chunk_id 集合（三个维度：租户 / 发布状态 / ACL）。

    取数可以用参数注入，方便调用方在同一请求内复用一次查询的结果；
    不传则自己查（``published_refs`` 与 ``acl_entries`` 各一条 SQL）。
    """

    refs = published_refs if published_refs is not None else repository.list_published_chunk_refs(session, auth.tenant_id)
    entries = acl_entries if acl_entries is not None else repository.list_tenant_document_acl(session, auth.tenant_id)
    grouped = acl_entries_by_document(entries)

    visible: set[str] = set()
    for document_id, chunk_id in refs:
        if acl_allows_read(
            grouped.get(document_id, ()),
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            roles=auth.roles,
        ):
            visible.add(chunk_id)
    return frozenset(visible)


def build_chunk_access_filter(
    session: Session,
    auth: AuthContext,
    *,
    published_refs: Sequence[tuple[str, str]] | None = None,
    acl_entries: Sequence[models.DocumentAcl] | None = None,
) -> ChunkAccessFilter:
    """由**服务端身份**构造检索过滤器（唯一生产构造点）。

    注意这里**总是**给出显式的 ``allowed_chunk_ids``（即使是空集合）：
    ``None`` 的语义是"不额外限制"，只有在"本租户公开知识库、完全不用 ACL"
    的场景下才该用；而本模块的职责是把 ACL 算清楚，因此空集合就是
    "此刻什么都看不到"，fail closed，绝不退化成不限租户再检索一次。
    """

    return ChunkAccessFilter(
        tenant_id=auth.tenant_id,
        allowed_chunk_ids=visible_chunk_ids(
            session,
            auth,
            published_refs=published_refs,
            acl_entries=acl_entries,
        ),
    )


def document_is_visible(
    session: Session,
    auth: AuthContext,
    document_id: str,
    *,
    acl_entries: Sequence[models.DocumentAcl] | None = None,
) -> bool:
    """文档是否对当前身份可见（文档详情 / 版本列表等读路径复用同一条判定）。

    只做 ACL 判定，不做"文档是否存在"的判断 —— 调用方按「不可见 = 不存在」
    统一返回 404，两个情况必须是同一个响应（B2 的既有约定）。
    """

    entries = acl_entries if acl_entries is not None else repository.list_document_acl(
        session, auth.tenant_id, document_id
    )
    return acl_allows_read(
        entries,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        roles=auth.roles,
    )


def knowledge_base_is_readable(
    session: Session,
    auth: AuthContext,
    knowledge_base_id: str,
) -> bool:
    """知识库读权限：租户内可见的库，或调用者是该库的任何成员。

    与 ``routers/documents.py::_require_kb_write_member`` 配对：写路径要求
    owner / editor，读路径允许 viewer / reviewer 等只读成员。
    """

    principal = repository.get_user_by_external_id(session, auth.tenant_id, auth.user_id)
    if principal is None:
        # 本服务没有该用户的档案 → 没有成员关系可判；此时**不**默认放行，
        # 由调用方决定是否退回"租户内可见"的更宽策略。
        return False
    member = repository.get_knowledge_base_member(session, auth.tenant_id, knowledge_base_id, principal)
    return member is not None


def visibility_summary(
    session: Session,
    auth: AuthContext,
    *,
    published_refs: Sequence[tuple[str, str]] | None = None,
    acl_entries: Sequence[models.DocumentAcl] | None = None,
) -> Mapping[str, object]:
    """可见性判定的可观测摘要（写进日志 / 响应，便于排障"为什么零命中"）。

    刻意**不含**任何被过滤掉的文档标题、chunk 文本或 id ——
    "被过滤文档的标题、分数、数量、引用和 trace 均不泄漏"（计划 4.2 第 5 条），
    所以这里只给计数，不给明细。
    """

    refs = published_refs if published_refs is not None else repository.list_published_chunk_refs(session, auth.tenant_id)
    entries = acl_entries if acl_entries is not None else repository.list_tenant_document_acl(session, auth.tenant_id)
    grouped = acl_entries_by_document(entries)
    visible = visible_chunk_ids(session, auth, published_refs=refs, acl_entries=entries)
    controlled = {document_id for document_id in grouped if grouped[document_id]}
    return {
        "tenant_id": auth.tenant_id,
        "published_chunks": len(refs),
        "visible_chunks": len(visible),
        "controlled_documents": len(controlled),
    }


__all__ = [
    "READ_PERMISSION",
    "SUPPORTED_SUBJECT_TYPES",
    "acl_allows_read",
    "acl_entries_by_document",
    "build_chunk_access_filter",
    "document_is_visible",
    "knowledge_base_is_readable",
    "visibility_summary",
    "visible_chunk_ids",
]
