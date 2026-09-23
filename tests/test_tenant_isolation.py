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

from auth_helpers import auth_headers
from services.auth_context import AuthContext
from services.auth_service import get_auth_context, require_write_operation_role
from services.ingestion import db, repository


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
