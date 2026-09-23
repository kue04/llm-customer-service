"""阶段 1.2 的租户隔离测试。

这一批要证明的是一条规则：
**``tenant_id`` 只能来自 AuthContext（即已校验的 JWT），
任何请求参数、请求头、请求体里的同名字段都不得影响授权范围。**

为什么用探针路由
----------------
真正的文档接口要到阶段 2.3 / 4.2 才出现，因此这里用一组 /documents 探针路由，
把「已经落地的部分」——鉴权依赖 + 仓储层租户过滤 + 状态码约定——串起来验证：

* 查询一律用 ``repository.get_document(session, auth.tenant_id, document_id)``，
  仓储层本身就带 tenant 条件；
* 查不到时统一返回 404，**跨租户与不存在返回完全相同的响应**，
  不泄漏资源是否存在；
* 写入的 tenant 永远取 ``auth.tenant_id``，与请求体无关。

阶段 4.2 把同样的模式搬到真实路由上时，这组断言可以直接复用。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
import pytest
from sqlalchemy.orm import sessionmaker

from auth_helpers import auth_headers, make_auth_context
from services.auth_context import AuthContext
from services.auth_service import get_auth_context, require_write_operation_role
from services.ingestion import db, repository
from services.ingestion.object_store import LocalObjectStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
DOC_A_TITLE = "alpha-refund-policy"
DOC_B_TITLE = "beta-secret-pricing"


def _alembic_config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


class TenantClaimRequest(BaseModel):
    """请求体里故意留一个 tenant_id 字段，用来验证它不会被采信。"""

    tenant_id: str = ""
    note: str = ""


def build_isolation_app() -> FastAPI:
    app = FastAPI()

    @app.get("/documents")
    def list_documents(auth: AuthContext = Depends(get_auth_context)) -> dict:
        with db.session_scope() as session:
            documents = repository.list_documents(session, auth.tenant_id)
            return {
                "tenant_id": auth.tenant_id,
                "count": len(documents),
                "titles": [document.title for document in documents],
            }

    @app.get("/documents/{document_id}")
    def read_document(
        document_id: str,
        auth: AuthContext = Depends(get_auth_context),
        # 刻意显式接收一个同名 query 参数：它必须对结果毫无影响
        tenant_id: str = Query(default=""),
    ) -> dict:
        with db.session_scope() as session:
            document = repository.get_document(session, auth.tenant_id, document_id)
            if document is None:
                # 不泄漏存在性：跨租户与不存在走同一个分支、同一个响应
                raise HTTPException(status_code=404, detail="document not found")
            return {
                "document_id": document.id,
                "tenant_id": document.tenant_id,
                "title": document.title,
                "ignored_query_tenant_id": tenant_id,
            }

    @app.post("/documents/{document_id}/claim")
    def claim_document(
        document_id: str,
        payload: TenantClaimRequest,
        auth: AuthContext = Depends(get_auth_context),
    ) -> dict:
        with db.session_scope() as session:
            document = repository.get_document(session, auth.tenant_id, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="document not found")
            return {
                # 真正生效的租户永远是 token 里的
                "resolved_tenant_id": document.tenant_id,
                "requested_tenant_id": payload.tenant_id,
                "note": payload.note,
            }

    @app.post("/documents/{document_id}/publish")
    def publish_document(document_id: str, auth: AuthContext = Depends(get_auth_context)) -> dict:
        require_write_operation_role("knowledge_publish", auth)
        with db.session_scope() as session:
            document = repository.get_document(session, auth.tenant_id, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="document not found")
            return {"published": document.id}

    return app


@pytest.fixture()
def seeded(tmp_path, monkeypatch) -> SimpleNamespace:
    url = f"sqlite:///{(tmp_path / 'tenant_isolation.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    command.upgrade(_alembic_config(url), "head")

    with db.session_scope(url) as session:
        for tenant_id in (TENANT_A, TENANT_B):
            repository.create_tenant(session, name=tenant_id, tenant_id=tenant_id)
        kb_a = repository.create_knowledge_base(session, tenant_id=TENANT_A, slug="kb-a")
        kb_b = repository.create_knowledge_base(session, tenant_id=TENANT_B, slug="kb-b")
        doc_a = repository.create_document(
            session,
            tenant_id=TENANT_A,
            knowledge_base_id=kb_a.id,
            source_uri="s3://alpha/refund.md",
            source_type="md",
            title=DOC_A_TITLE,
        )
        doc_b = repository.create_document(
            session,
            tenant_id=TENANT_B,
            knowledge_base_id=kb_b.id,
            source_uri="s3://beta/pricing.md",
            source_type="md",
            title=DOC_B_TITLE,
        )
        doc_a_id, doc_b_id = doc_a.id, doc_b.id

    try:
        yield SimpleNamespace(
            url=url,
            client=TestClient(build_isolation_app()),
            doc_a_id=doc_a_id,
            doc_b_id=doc_b_id,
        )
    finally:
        db.dispose_engines()


def alpha_headers(**kwargs) -> dict:
    return auth_headers(tenant_id=TENANT_A, user_id="alpha-user", roles=["admin"], **kwargs)


def beta_headers(**kwargs) -> dict:
    return auth_headers(tenant_id=TENANT_B, user_id="beta-user", roles=["admin"], **kwargs)


# ---------------------------------------------------------------- 自有资源可访问


def test_tenant_can_read_its_own_document(seeded) -> None:
    response = seeded.client.get(f"/documents/{seeded.doc_a_id}", headers=alpha_headers())
    assert response.status_code == 200
    assert response.json()["title"] == DOC_A_TITLE


def test_document_list_only_returns_own_tenant(seeded) -> None:
    body = seeded.client.get("/documents", headers=alpha_headers()).json()
    assert body["tenant_id"] == TENANT_A
    assert body["titles"] == [DOC_A_TITLE]


# ---------------------------------------------------------------- 跨租户被拦


def test_cannot_read_other_tenants_document(seeded) -> None:
    response = seeded.client.get(f"/documents/{seeded.doc_b_id}", headers=alpha_headers())
    assert response.status_code in {403, 404}


def test_cross_tenant_and_missing_resource_are_indistinguishable(seeded) -> None:
    """不能通过响应差异推断出「这个 id 在别的租户里存在」。"""

    cross_tenant = seeded.client.get(f"/documents/{seeded.doc_b_id}", headers=alpha_headers())
    nonexistent = seeded.client.get("/documents/does-not-exist", headers=alpha_headers())

    assert cross_tenant.status_code == nonexistent.status_code == 404
    assert cross_tenant.json() == nonexistent.json()


def test_cross_tenant_response_does_not_leak_resource_details(seeded) -> None:
    response = seeded.client.get(f"/documents/{seeded.doc_b_id}", headers=alpha_headers())
    raw = response.text
    assert DOC_B_TITLE not in raw
    assert TENANT_B not in raw
    assert seeded.doc_b_id not in raw


def test_beta_user_cannot_read_alpha_document(seeded) -> None:
    """反向验证一遍，避免只写对了单向过滤。"""

    assert seeded.client.get(f"/documents/{seeded.doc_a_id}", headers=beta_headers()).status_code == 404
    assert seeded.client.get(f"/documents/{seeded.doc_b_id}", headers=beta_headers()).status_code == 200


# ---------------------------------------------------------------- 参数不能改授权范围


def test_query_tenant_id_does_not_widen_scope(seeded) -> None:
    response = seeded.client.get(
        f"/documents/{seeded.doc_b_id}",
        params={"tenant_id": TENANT_B},
        headers=alpha_headers(),
    )
    assert response.status_code == 404


def test_body_tenant_id_does_not_widen_scope(seeded) -> None:
    response = seeded.client.post(
        f"/documents/{seeded.doc_b_id}/claim",
        json={"tenant_id": TENANT_B, "note": "pretend to be beta"},
        headers=alpha_headers(),
    )
    assert response.status_code == 404


def test_body_tenant_id_is_reported_but_never_authoritative(seeded) -> None:
    """即使请求体声称自己是 beta，落库/返回的仍是 token 里的租户。"""

    body = seeded.client.post(
        f"/documents/{seeded.doc_a_id}/claim",
        json={"tenant_id": TENANT_B, "note": "spoof"},
        headers=alpha_headers(),
    ).json()

    assert body["resolved_tenant_id"] == TENANT_A
    assert body["requested_tenant_id"] == TENANT_B


def test_forged_tenant_header_cannot_change_scope(seeded) -> None:
    headers = alpha_headers()
    headers["X-Tenant-Id"] = TENANT_B
    assert seeded.client.get(f"/documents/{seeded.doc_b_id}", headers=headers).status_code == 404


# ---------------------------------------------------------------- 身份缺失与越权


def test_missing_token_cannot_reach_tenant_resources(seeded) -> None:
    assert seeded.client.get("/documents").status_code == 401
    assert seeded.client.get(f"/documents/{seeded.doc_a_id}").status_code == 401


def test_token_without_tenant_claim_is_rejected(seeded) -> None:
    """令牌本身合法，但没有 tenant_id claim —— 无法确定租户边界，必须拒绝。"""

    headers = auth_headers(omit_claims=("tenant_id",))
    response = seeded.client.get("/documents", headers=headers)
    assert response.status_code == 401


def test_role_without_publish_scope_gets_403_even_for_own_document(seeded) -> None:
    """越权角色 —— 即使是自己的文档也不能发布。"""

    headers = auth_headers(tenant_id=TENANT_A, user_id="alpha-agent", roles=["agent"])
    response = seeded.client.post(f"/documents/{seeded.doc_a_id}/publish", headers=headers)
    assert response.status_code == 403


def test_publish_scope_holder_still_limited_to_own_tenant(seeded) -> None:
    """有发布权限也只是「能发布自己的」—— 权限与租户边界是两道独立的门。"""

    assert seeded.client.post(f"/documents/{seeded.doc_a_id}/publish", headers=alpha_headers()).status_code == 200
    assert seeded.client.post(f"/documents/{seeded.doc_b_id}/publish", headers=alpha_headers()).status_code == 404


# ================================================================ 阶段 4.2：真实路由上的隔离
#
# 上面那组用探针路由验证"鉴权依赖 + 仓储层租户过滤 + 状态码约定"这套模式；
# 下面这组把**真实路由**（routers/documents.py 与 routers/retrieval.py）挂起来，
# 覆盖计划 4.2 要求的六条，其中第 2/3/4/5 条是 B7 新增的：
#   1. 用户 A 不可读取租户 B 文档（已有：test_cannot_read_other_tenants_document）
#   2. 同租户但无 ACL 的用户不能检索受限文档
#   3. 只有 document:publish 可发布
#   4. 只有 index:rebuild 可重建
#   5. 被过滤文档的标题、分数、数量、引用和 trace 均不泄漏
#   6. 修改请求 body 的 tenant_id 不改变授权范围（已有：test_body_tenant_id_does_not_widen_scope）


@pytest.fixture()
def routed(tmp_path, monkeypatch):
    """两个租户 + 全局 chunk 索引 + **真实路由**（documents / retrieval）。"""

    from retrieval_fixtures import (
        EMBEDDING_MODEL,
        RetrievalEnv,
        TENANT_A as RT_A,
        TENANT_B as RT_B,
        USER_A,
        alembic_upgrade,
        fake_embedding,
        query_for as rt_query_for,
    )
    from routers import documents as documents_router
    from routers import knowledge as knowledge_router
    from routers import retrieval as retrieval_router
    from services.auth_service import require_resource_scope

    url = f"sqlite:///{(tmp_path / 'routed_isolation.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)

    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        environment.ingest(RT_A, tag="alpha")
        beta_doc = environment.ingest(RT_B, tag="beta")
        # A 租户的受控文档：只授权给 user-alpha（其他人检索不到）
        controlled = environment.ingest(RT_A, tag="alpha-secret", filename="secret.md")
        environment.grant(RT_A, controlled.document.id, "user", USER_A, "read")
        environment.rebuild(RT_A)

        # 三个"生产默认值"（索引根 / embedder / 模型标识）在检索与文档两个路由模块里
        # 都各有引用，两边都要换成替身 —— 只换一边会让"重建索引"真的去加载模型。
        for module in (retrieval_router, documents_router):
            monkeypatch.setattr(module, "default_index_root", lambda: index_root)
            monkeypatch.setattr(module, "default_embedder", fake_embedding)
            monkeypatch.setattr(module, "default_embedding_model", lambda: EMBEDDING_MODEL)

        app = FastAPI()
        app.include_router(documents_router.router)
        app.include_router(knowledge_router.router, prefix="/knowledge")
        app.include_router(retrieval_router.router, prefix="/retrieval")
        yield SimpleNamespace(
            client=TestClient(app),
            env=environment,
            alpha=RT_A,
            beta=RT_B,
            beta_doc=beta_doc,
            controlled=controlled,
            query_for=rt_query_for,
            require_resource_scope=require_resource_scope,
            EMBEDDING_MODEL=EMBEDDING_MODEL,
        )
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


def routed_headers(tenant_id: str, *, user_id: str = "user-1", roles=("admin",)) -> dict:
    return auth_headers(tenant_id=tenant_id, user_id=user_id, roles=list(roles))


class TestAclOnRealRoutes:
    def test_same_tenant_without_acl_cannot_retrieve_controlled_document(self, routed) -> None:
        """计划 4.2 第 2 条：同租户、有 ``document:read``，但没有 ACL → 零命中（不是 403）。"""

        response = routed.client.post(
            "/retrieval/search",
            headers=routed_headers(routed.alpha, user_id="someone-else"),
            json={"query": routed.query_for("alpha-secret"), "limit": 5},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] >= 1, "同租户的非受控文档仍应可检索（否则这条测试只是在测'全空'）"
        assert routed.controlled.document.id not in {item["document_id"] for item in body["results"]}

    def test_granted_user_retrieves_controlled_document(self, routed) -> None:
        response = routed.client.post(
            "/retrieval/search",
            headers=routed_headers(routed.alpha, user_id="user-alpha"),
            json={"query": routed.query_for("alpha-secret"), "limit": 5},
        )
        body = response.json()
        assert routed.controlled.document.id in {item["document_id"] for item in body["results"]}

    def test_controlled_document_does_not_leak_through_job_or_detail_endpoints(self, routed) -> None:
        """计划 4.2 第 5 条：标题 / 数量 / 引用都不泄漏 —— 404 与"不存在"逐字相同。"""

        outsider = routed_headers(routed.alpha, user_id="someone-else")
        owner = routed_headers(routed.alpha, user_id="user-alpha")
        document_id = routed.controlled.document.id

        blocked = routed.client.get(f"/documents/{document_id}", headers=outsider)
        missing = routed.client.get("/documents/does-not-exist", headers=outsider)
        assert blocked.status_code == missing.status_code == 404
        assert blocked.json() == missing.json()
        assert document_id not in blocked.text
        assert "secret.md" not in blocked.text

        assert routed.client.get(f"/documents/{document_id}/versions", headers=outsider).status_code == 404
        assert routed.client.get(f"/documents/{document_id}", headers=owner).status_code == 200

        job_id = routed.controlled.job.id
        blocked_job = routed.client.get(f"/ingestion-jobs/{job_id}", headers=outsider)
        assert blocked_job.status_code == 404
        assert job_id not in blocked_job.text
        assert routed.client.get(f"/ingestion-jobs/{job_id}", headers=owner).status_code == 200

    def test_cross_tenant_document_is_invisible_on_real_routes(self, routed) -> None:
        response = routed.client.get(
            f"/documents/{routed.beta_doc.document.id}",
            headers=routed_headers(routed.alpha),
        )
        assert response.status_code == 404
        assert routed.beta_doc.document.id not in response.text

    def test_index_rebuild_requires_its_own_permission(self, routed) -> None:
        """计划 4.2 第 4 条：只有 ``index:rebuild`` 能重建（发布权限不够）。"""

        app_client = routed.client
        payload = None

        denied_agent = app_client.post(
            "/ingestion/indexes/rebuild", headers=routed_headers(routed.alpha, roles=("agent",))
        )
        assert denied_agent.status_code == 403

        denied_ops = app_client.post(
            "/ingestion/indexes/rebuild", headers=routed_headers(routed.alpha, roles=("knowledge_ops",))
        )
        assert denied_ops.status_code == 403

        allowed = app_client.post(
            "/ingestion/indexes/rebuild", headers=routed_headers(routed.alpha, roles=("supervisor",))
        )
        assert allowed.status_code == 200, allowed.text
        payload = allowed.json()
        assert payload["switched"] is True
        assert payload["chunk_count"] > 0
        # 索引是全局一份：重建后条目仍然覆盖两个租户（B14 的回归）
        assert payload["tenant_count"] == 2

    def test_publish_requires_document_publish_permission(self, routed) -> None:
        """计划 4.2 第 3 条：只有 ``document:publish`` 可发布。

        **只验负向**（agent 在鉴权阶段就被拦下，不会碰到知识库存储）：
        正向会真的写入 ``data/takeout_customer_service_seed.jsonl`` 并重建 A 轨 FAISS
        （手工旁路），隔离测试不该改仓库数据。正向由权限表断言 +
        ``tests/test_knowledge_ops.py``（它把数据路径指到 tmp）覆盖。
        """

        response = routed.client.post("/knowledge/publish-approved", headers=routed_headers(routed.alpha, roles=("agent",)))
        assert response.status_code == 403

        # 有 document:publish 的角色不会被拦（这里只判定权限，不触发真实发布）
        routed.require_resource_scope("document:publish", make_auth_context(roles=["knowledge_ops"]))

    def test_retrieval_filter_is_per_identity_not_per_request(self, routed) -> None:
        """同一请求体、只换身份 → 结果集不同：授权只来自 JWT。"""

        payload = {"query": routed.query_for("beta"), "limit": 5}
        alpha_body = routed.client.post("/retrieval/search", headers=routed_headers(routed.alpha), json=payload).json()
        beta_body = routed.client.post(
            "/retrieval/search", headers=routed_headers(routed.beta), json=payload
        ).json()

        assert {item["tenant_id"] for item in alpha_body["results"]} == {routed.alpha}
        assert {item["tenant_id"] for item in beta_body["results"]} == {routed.beta}
        assert set(routed.beta_doc.chunk_ids) & {item["chunk_id"] for item in beta_body["results"]}
