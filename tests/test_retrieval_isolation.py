"""检索层隔离测试（计划 4.3，批次 B7 —— **硬要求**）。

为什么必须单独一套
------------------
``tests/test_tenant_isolation.py`` 测的是**数据层**（``repository.get_document`` 带不带
``tenant_id``），它全绿**不代表**检索层是隔离的：检索层多了一条"把允许的 chunk 集合
喂给 FAISS"的路径，这条路走错了照样能把别人的文档捞出来，而数据层测试一条都不会红。
这就是复核里登记的 **F3**（数据层有隔离测试、检索层没有），B7 消除它。

最低要求（计划 4.3 逐条对应）
----------------------------
1. 两个租户各建一份文档 + 各跑一次流水线建索引 → **互相检索不到**
   （``TestCrossTenantIsolation``）；
2. 无 ACL 的用户检索受控文档 → **零命中**（不是异常）（``TestAclIsolation``）；
3. **缺 filter 时报错**，而不是回退全库（``TestFilterIsMandatory``）；
4. 未 ``published`` 的版本检索不到（``TestVersionVisibility``）；
5. 同租户 + 有权限的用户**能正常检索到**（正向对照，防止"全都没结果"也算通过）
   （``TestPositiveControl``）。

另外补了两条本批特有的回归：
* ``TestGlobalIndexScope`` —— 全局索引必须同时包含所有租户的 chunk
  （B6 的 ``rebuild_index`` 只取触发构建的租户，会让先入库的租户静默消失，见踩坑 **B14**）；
* ``TestPreFiltering`` —— 过滤必须发生在**候选暴露之前**：
  后过滤在长尾租户上会静默返回 0 条（本批实测过 top-50 筛完返回 0 条）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from retrieval_fixtures import (
    EMBEDDING_MODEL,
    RetrievalEnv,
    TENANT_A,
    TENANT_B,
    USER_A,
    alembic_upgrade,
    fake_embedding,
    query_for,
)
from services.ingestion import db
from services.ingestion.object_store import LocalObjectStore
from services.retrieval_access import build_chunk_access_filter, visible_chunk_ids
from utils.vector_retriever import (
    ERROR_FILTER_REQUIRED,
    ERROR_FILTER_TENANT_MISSING,
    ChunkAccessFilter,
    ChunkRetrievalError,
    search_chunk_index,
)


# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """两个租户各入库一份文档，共用一份全局索引（A 先、B 后 → B 是最后写入者）。"""

    url = f"sqlite:///{(tmp_path / 'retrieval.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)

    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        ingested_a = environment.ingest(TENANT_A, tag="alpha")
        ingested_b = environment.ingest(TENANT_B, tag="beta")
        yield SimpleEnv(environment, ingested_a, ingested_b, index_root)
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


class SimpleEnv:
    """把夹具里的几个对象打包，避免测试函数签名过长。"""

    def __init__(self, environment: RetrievalEnv, doc_a, doc_b, index_root) -> None:
        self.environment = environment
        self.session = environment.session
        self.index_root = index_root
        self.doc_a = doc_a
        self.doc_b = doc_b

    def access(self, tenant_id: str, *, user_id: str = "user-1", roles=("admin",)) -> ChunkAccessFilter:
        auth = self.environment.auth(tenant_id, user_id=user_id, roles=roles)
        return build_chunk_access_filter(self.session, auth)

    def search(self, tenant_id: str, query: str, *, limit: int = 5, min_score=None, **auth_kwargs):
        access = self.access(tenant_id, **auth_kwargs)
        return search_chunk_index(
            query,
            access=access,
            top_k=max(limit * 5, 20),
            min_score=min_score,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=self.index_root,
        )


# ================================================================ 1. 租户隔离


class TestCrossTenantIsolation:
    def test_manifest_contains_both_tenants(self, env) -> None:
        """索引是全局一份，两个租户的 chunk 都必须在里面（踩坑 B14 的回归）。"""

        manifest = env.environment.manifest()
        tenants = {entry.tenant_id for entry in manifest.entries}
        assert tenants == {TENANT_A, TENANT_B}
        assert manifest.extra["scope"] == "all_tenants"
        assert set(manifest.extra["tenants"]) == {TENANT_A, TENANT_B}
        # 两次构建 → 版本号必须递增（按租户计会撞成同一个目录名）
        assert manifest.index_version >= 2

    def test_tenant_cannot_retrieve_other_tenants_chunks(self, env) -> None:
        """用 B 的查询串检 A：**一条 B 的 chunk 都不能出现**。"""

        hits = env.search(TENANT_A, query_for("beta"), limit=5)
        assert hits, "A 自己的 chunk 应该还在（正向对照见 TestPositiveControl）"
        assert {hit.tenant_id for hit in hits} == {TENANT_A}
        assert not {hit.chunk_id for hit in hits} & set(env.doc_b.chunk_ids)

    def test_other_tenant_chunks_are_filtered_not_merely_outranked(self, env) -> None:
        """B 的查询串发给 A：A 的**候选集里根本没有** B 的 chunk（集合级断言，不抽样）。

        判定"被排除"而不是"排名靠后"的更强证据见
        ``TestPreFiltering.test_long_tail_tenant_survives_a_query_dominated_by_other_tenants``：
        那里用"B 的 chunk 数量远大于 top-k"的构造，把后过滤的实现逼红。
        """

        query = query_for("beta")
        hits_a = env.search(TENANT_A, query, limit=5)
        assert all(hit.tenant_id == TENANT_A for hit in hits_a)
        assert not {hit.chunk_id for hit in hits_a} & set(env.doc_b.chunk_ids)

        allowed = set(env.access(TENANT_A).allowed_chunk_ids or ())
        assert not allowed & set(env.doc_b.chunk_ids)

    def test_tenant_filter_comes_from_identity_not_from_content(self, env) -> None:
        """A 的可见集合里不能出现任何 B 的 chunk（集合级真值表，不抽样）。"""

        access = env.access(TENANT_A)
        allowed = set(access.allowed_chunk_ids or ())
        assert allowed == set(env.doc_a.chunk_ids)
        assert not allowed & set(env.doc_b.chunk_ids)

    def test_swapping_identities_swaps_the_result_set(self, env) -> None:
        """反向验证一遍，避免只写对了单向过滤。"""

        hits_b = env.search(TENANT_B, query_for("beta"), limit=5)
        assert hits_b
        assert {hit.tenant_id for hit in hits_b} == {TENANT_B}
        assert not {hit.chunk_id for hit in hits_b} & set(env.doc_a.chunk_ids)


# ================================================================ 2. ACL 隔离


class TestAclIsolation:
    def test_controlled_document_is_invisible_without_grant(self, env) -> None:
        """同租户、有 ``document:read`` 角色，但没有 ACL 记录 → **零命中**（不是异常）。"""

        controlled = env.environment.ingest(TENANT_A, tag="alpha-secret", filename="secret.md")
        env.environment.grant(TENANT_A, controlled.document.id, "user", USER_A, "read")
        env.environment.rebuild(TENANT_A)

        outsider = env.search(TENANT_A, query_for("alpha-secret"), user_id="someone-else")
        assert [hit for hit in outsider if hit.document_id == controlled.document.id] == []

        # 受控文档不能出现在结果里，但同租户的其他文档仍可检索 —— 这条区分了
        # "ACL 生效"与"整个租户都被拦了"
        allowed = env.search(TENANT_A, query_for("alpha"), user_id="someone-else")
        assert allowed

    def test_granted_user_can_retrieve_the_controlled_document(self, env) -> None:
        """拿到 ACL 的用户能正常检索 —— 否则上面那条"零命中"可能只是因为全是零命中。"""

        controlled = env.environment.ingest(TENANT_A, tag="alpha-secret", filename="secret.md")
        env.environment.grant(TENANT_A, controlled.document.id, "user", USER_A, "read")
        env.environment.rebuild(TENANT_A)

        hits = env.search(TENANT_A, query_for("alpha-secret"), user_id=USER_A)
        assert controlled.document.id in {hit.document_id for hit in hits}

    def test_role_grant_works_and_unknown_group_does_not(self, env) -> None:
        """``role`` 主体按角色集合判定；``group`` 主体当前 fail closed（没有组表）。"""

        by_role = env.environment.ingest(TENANT_A, tag="alpha-role", filename="by-role.md")
        env.environment.grant(TENANT_A, by_role.document.id, "role", "qa", "read")
        by_group = env.environment.ingest(TENANT_A, tag="alpha-group", filename="by-group.md")
        env.environment.grant(TENANT_A, by_group.document.id, "group", "g-1", "read")
        env.environment.rebuild(TENANT_A)

        qa_hits = env.search(TENANT_A, query_for("alpha-role"), roles=("qa",))
        assert by_role.document.id in {hit.document_id for hit in qa_hits}

        group_hits = env.search(TENANT_A, query_for("alpha-group"), roles=("admin",))
        assert by_group.document.id not in {hit.document_id for hit in group_hits}

    def test_write_permission_does_not_grant_read(self, env) -> None:
        """``permission='write'`` 不隐含读权限（方向标注，防止"能写就能看全文"）。"""

        doc = env.environment.ingest(TENANT_A, tag="alpha-write", filename="write-only.md")
        env.environment.grant(TENANT_A, doc.document.id, "user", USER_A, "write")
        env.environment.rebuild(TENANT_A)

        hits = env.search(TENANT_A, query_for("alpha-write"), user_id=USER_A)
        assert doc.document.id not in {hit.document_id for hit in hits}

    def test_tenant_subject_grant_covers_the_whole_tenant(self, env) -> None:
        """``subject_type='tenant'`` 是"全租户可见"的显式写法。"""

        doc = env.environment.ingest(TENANT_A, tag="alpha-public", filename="public.md")
        env.environment.grant(TENANT_A, doc.document.id, "tenant", TENANT_A, "read")
        env.environment.rebuild(TENANT_A)

        hits = env.search(TENANT_A, query_for("alpha-public"), user_id="anyone")
        assert doc.document.id in {hit.document_id for hit in hits}


# ================================================================ 3. 缺 filter 必须报错


class TestFilterIsMandatory:
    def test_missing_filter_raises_instead_of_searching_everything(self, env) -> None:
        with pytest.raises(ChunkRetrievalError) as error:
            search_chunk_index(query_for("beta"), access=None, root=env.index_root)
        assert error.value.error_code == ERROR_FILTER_REQUIRED

    def test_filter_must_be_a_server_built_object(self, env) -> None:
        """传个同形状的假对象也不行 —— 只认服务端构造的 ``ChunkAccessFilter``。"""

        class FakeFilter:
            tenant_id = TENANT_B
            allowed_chunk_ids = None

        with pytest.raises(ChunkRetrievalError) as error:
            search_chunk_index(query_for("beta"), access=FakeFilter(), root=env.index_root)
        assert error.value.error_code == ERROR_FILTER_REQUIRED

    def test_filter_without_tenant_is_rejected_at_construction(self) -> None:
        with pytest.raises(ChunkRetrievalError) as error:
            ChunkAccessFilter(tenant_id="   ")
        assert error.value.error_code == ERROR_FILTER_TENANT_MISSING

    def test_empty_allow_list_is_zero_hits_not_unrestricted(self, env) -> None:
        """空集合语义是"什么都看不到"，绝不退化成"不限租户"。"""

        empty = ChunkAccessFilter(tenant_id=TENANT_B, allowed_chunk_ids=frozenset())
        assert search_chunk_index(query_for("beta"), access=empty, root=env.index_root) == []

    def test_tenant_without_any_visible_chunk_gets_zero_hits(self, env) -> None:
        """库里没有该租户的已发布 chunk → 可见集合为空 → 零命中（fail closed）。"""

        env.environment.ensure_tenant("tenant-gamma")
        access = env.access("tenant-gamma")
        assert access.allowed_chunk_ids == frozenset()
        assert search_chunk_index(query_for("beta"), access=access, root=env.index_root) == []


# ================================================================ 4. 版本状态


class TestVersionVisibility:
    def test_unpublished_version_is_not_retrievable(self, env) -> None:
        """把版本从 ``published`` 退回 ``indexed``：chunk 还在索引里，但检索不到。

        这个用例的价值在于**索引里确实有这条 chunk**（manifest 条目数不变），
        可见性的唯一判据是版本状态 —— 否则"检索不到"可能只是"索引里没有"。
        """

        chunk_ids_before = set(env.doc_a.chunk_ids)
        manifest_before = {entry.chunk_id for entry in env.environment.manifest().entries}
        assert chunk_ids_before <= manifest_before

        env.environment.set_version_status(TENANT_A, env.doc_a.version.id, "indexed")

        access = env.access(TENANT_A)
        assert set(access.allowed_chunk_ids or ()) == set()
        assert env.search(TENANT_A, query_for("alpha")) == []
        # 索引本身没有被改动 —— 判定发生在检索层，不是构建期
        assert {entry.chunk_id for entry in env.environment.manifest().entries} == manifest_before

    def test_republishing_brings_it_back(self, env) -> None:
        """状态是唯一判据：退回 → 零命中；再发布 → 又能检索到。"""

        env.environment.set_version_status(TENANT_A, env.doc_a.version.id, "indexed")
        assert env.search(TENANT_A, query_for("alpha")) == []

        env.environment.set_version_status(TENANT_A, env.doc_a.version.id, "published")
        assert env.search(TENANT_A, query_for("alpha"))

    def test_only_published_chunks_enter_the_visible_set(self, env) -> None:
        """真值表：四个状态逐个验，只有 ``published`` 放行（集合驱动，不抽样）。"""

        auth = env.environment.auth(TENANT_A)
        expected = {
            "received": 0,
            "stored": 0,
            "parsed": 0,
            "chunked": 0,
            "indexed": 0,
            "published": len(env.doc_a.chunk_ids),
        }
        for status, count in expected.items():
            env.environment.set_version_status(TENANT_A, env.doc_a.version.id, status)
            visible = visible_chunk_ids(env.session, auth)
            assert len(visible) == count, status
        env.environment.set_version_status(TENANT_A, env.doc_a.version.id, "published")


# ================================================================ 5. 正向对照


class TestPositiveControl:
    def test_own_tenant_can_still_retrieve_after_another_tenant_ingests(self, env) -> None:
        """**这批最容易漏的一条**：B 入库之后，A 必须还能检索到自己的 chunk。

        只测"互相检索不到"是不够的 —— 如果索引被后入库的租户整体覆盖
        （B6 的行为，见踩坑 B14），A 的检索也是"零命中"，测试照样绿，
        而真实后果是「先入库租户的资料全部消失」。正向对照把它钉死。
        """

        hits = env.search(TENANT_A, query_for("alpha"), limit=3)
        assert hits, "B 入库后 A 必须仍能检索到自己的 chunk"
        assert {hit.tenant_id for hit in hits} == {TENANT_A}
        assert env.doc_a.document.id in {hit.document_id for hit in hits}

    def test_hits_carry_full_provenance(self, env) -> None:
        """命中必须自证来源：chunk / document / version / tenant / ACL / 页码 / 标题路径。"""

        hit = env.search(TENANT_A, query_for("alpha"), limit=1)[0]
        assert hit.chunk_id in set(env.doc_a.chunk_ids)
        assert hit.document_id == env.doc_a.document.id
        assert hit.document_version == env.doc_a.version.version
        assert hit.document_version_id == env.doc_a.version.id
        assert hit.tenant_id == TENANT_A
        assert hit.ordinal >= 0
        assert hit.source_uri == env.doc_a.document.source_uri
        payload = hit.to_dict()
        for key in ("chunk_id", "document_id", "tenant_id", "heading_path", "acl", "page_start", "page_end"):
            assert key in payload

    def test_acl_metadata_travels_with_the_chunk(self, env) -> None:
        """chunk 自带的 ACL 快照来自构建期的 ``document_acl``（可审计"当时谁被允许"）。"""

        hit = env.search(TENANT_A, query_for("alpha"), limit=1)[0]
        assert isinstance(hit.acl, tuple)
        for entry in hit.acl:
            assert set(entry) >= {"subject_type", "subject_id", "permission"}


# ================================================================ 6. 前置过滤


class TestPreFiltering:
    def test_long_tail_tenant_survives_a_query_dominated_by_other_tenants(self, env) -> None:
        """长尾租户在前置过滤下必须能检索到 —— 后过滤会静默返回 0 条。

        构造（专门为"逼红后过滤"设计）：
        * 给 B 再灌两份大文档，让 B 的 chunk 数**远超 top-k**（``limit*5``）；
        * 查询串 = 12 个 B 的词 + 3 个 A 的词 —— B 的 chunk 得分整体高于 A 的 chunk。

        于是"先全局 top-k 再按租户筛"会让 A 的 chunk 全部落在 top-k 之外 →
        A 返回 0 条（系统对用户说"没找到资料"，而资料就在库里，且**没有任何报错**）。
        前置过滤（``IDSelectorBatch``）只在 A 的候选集里检索，因此这里必须非空。
        """

        env.environment.ingest(TENANT_B, tag="beta", filename="beta-big-1.md", sections=8)
        env.environment.ingest(TENANT_B, tag="beta", filename="beta-big-2.md", sections=8)
        manifest = env.environment.manifest()
        beta_chunks = [entry for entry in manifest.entries if entry.tenant_id == TENANT_B]
        assert len(beta_chunks) > 20, "需要足够多的 B chunk 才有机会把 A 挤出 top-k"

        mixed_query = f"{' '.join(f'beta正文{index}' for index in range(1, 13))} alpha正文1 alpha正文2 alpha正文3"

        # 不加分数阈值：前置过滤下 A 一定能拿到自己的候选；
        # 后过滤下全局 top-20 全是 B，A 会拿到 0 条。
        hits = env.search(TENANT_A, mixed_query, limit=4)
        assert hits, "长尾租户被其他租户的语料挤出了 top-k（说明过滤发生在候选暴露之后）"
        assert {hit.tenant_id for hit in hits} == {TENANT_A}
        assert not {hit.chunk_id for hit in hits} & set(env.doc_b.chunk_ids)

    def test_acl_narrowing_is_computed_per_query_not_cached_across_grants(self, env) -> None:
        """ACL 变化后**不需要重建索引**：可见集合每次由服务端重新算。

        这正是 [D-11] 选择"检索层过滤"而不是"构建期过滤"的理由 ——
        授权变化立即生效，而索引可以保持不变。
        """

        doc = env.environment.ingest(TENANT_A, tag="alpha-late", filename="late.md")
        # 先把文档变成"受控文档"：只授权给别人 → 当前用户看不见
        env.environment.grant(TENANT_A, doc.document.id, "user", "someone-else", "read")
        before = env.search(TENANT_A, query_for("alpha-late"))
        assert doc.document.id not in {hit.document_id for hit in before}
        # 再授权给当前用户（**不重建索引**）：立即可见
        env.environment.grant(TENANT_A, doc.document.id, "user", "user-1", "read")
        after = env.search(TENANT_A, query_for("alpha-late"))
        assert doc.document.id in {hit.document_id for hit in after}
