"""检索 API 测试。

两支：
* ``RetrievalSearchApiTest`` —— **演示 / 兼容路径**（A 轨种子 FAQ）的既有测试，
  断言重命名后的 handler（``search_retrieval_demo`` / ``preview_demo_prompt``）行为不变，
  并确认响应里标了 ``retrieval_path="seed-faq-demo"``；
`TestChunkRetrievalApi`（B7 新增）—— **正式路径**（chunk 级 + 服务端权限过滤）的
  API 层测试：401 / 403、无索引 → 503 + 稳定 ``error_code``、跨租户零泄漏、
  客户端传 tenant / filter / row_id 都不能扩大授权范围。
"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from retrieval_fixtures import (
    EMBEDDING_MODEL,
    RetrievalEnv,
    TENANT_A,
    TENANT_B,
    USER_A,
    alembic_upgrade,
    build_retrieval_app,
    fake_embedding,
    query_for,
)
from routers import retrieval
from routers.retrieval import preview_demo_prompt, search_retrieval_demo
from schemas.retrieval_schema import RetrievalSearchRequest
from services.ingestion import db
from services.ingestion.object_store import LocalObjectStore

from auth_helpers import auth_headers, make_auth_context


AUTH_HEADERS = auth_headers(roles=["agent"], user_id="agent_1")
AGENT_CONTEXT = make_auth_context(roles=["agent"], user_id="agent_1")


class RetrievalSearchApiTest(unittest.TestCase):
    def test_config_endpoint_returns_current_rag_config(self) -> None:
        app = FastAPI()
        app.include_router(retrieval.router, prefix="/retrieval")
        client = TestClient(app)

        response = client.get("/retrieval/config", headers=AUTH_HEADERS)

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["embedding_model_name"], "BAAI/bge-small-zh-v1.5")
        self.assertEqual(body["reranker_model_name"], "BAAI/bge-reranker-base")
        self.assertEqual(body["model_rerank_weight"], 0.01)
        self.assertEqual(body["min_vector_score"], 0.40)
        self.assertTrue(body["reply_rules_enabled"])
        self.assertIn("real_vector.index", body["faiss_index_path"])
        self.assertIn("real_vector_docs.json", body["faiss_docs_path"])

    def test_search_response_includes_rerank_debug_scores(self) -> None:
        candidate = {
            "score": 0.78,
            "rerank_score": 0.792,
            "model_rerank_score": 0.95,
            "vector_score": 0.72,
            "keyword_bonus": 0.06,
            "direction_penalty": 0.0,
            "answer": "Refunds usually arrive within 1-3 business days.",
            "source": {
                "category": "refund",
                "intent": "refund_progress",
                "question": "When will the refund arrive?",
            },
        }

        request = RetrievalSearchRequest(
            query="refund arrival time",
            mode="hybrid",
            limit=1,
            min_score=0.62,
        )

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=[candidate]):
            response = search_retrieval_demo(request, auth=AGENT_CONTEXT)

        result = response.results[0]
        self.assertEqual(result.score, 0.78)
        self.assertEqual(result.rerank_score, 0.792)
        self.assertEqual(result.model_rerank_score, 0.95)
        self.assertEqual(response.retrieval_path, "seed-faq-demo")

    def test_prompt_preview_response_includes_prompt_context_items(self) -> None:
        candidates = [
            {
                "score": 0.88,
                "rerank_score": 0.891,
                "model_rerank_score": 0.91,
                "vector_score": 0.82,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Refunds return to the original payment method.",
                "source": {
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                },
            },
            {
                "score": 0.77,
                "rerank_score": 0.776,
                "model_rerank_score": 0.6,
                "vector_score": 0.73,
                "keyword_bonus": 0.04,
                "direction_penalty": 0.0,
                "answer": "Check the order detail page for refund status.",
                "source": {
                    "category": "order",
                    "intent": "refund_status",
                    "question": "Where can I check refund status?",
                },
            },
        ]

        request = RetrievalSearchRequest(
            query="refund arrival time",
            mode="hybrid",
            limit=2,
            min_score=0.62,
        )

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=candidates) as mocked_retrieve:
            response = preview_demo_prompt(request, auth=AGENT_CONTEXT)

        mocked_retrieve.assert_called_once_with(
            "refund arrival time",
            limit=2,
            min_score=0.62,
            use_hybrid=True,
        )
        self.assertEqual(response.count, 2)
        self.assertEqual(len(response.prompt_context_items), 2)
        self.assertEqual(response.prompt_context_items[0].role, "primary")
        self.assertEqual(response.prompt_context_items[0].evidence_strength, "normal")
        self.assertEqual(response.prompt_context_items[1].role, "supporting")
        self.assertEqual(response.prompt_context_items[1].evidence_strength, "normal")
        self.assertEqual(response.prompt_context_items[0].intent, "refund_progress")
        self.assertIn("最相关参考资料", response.prompt)
        self.assertIn("补充参考资料", response.prompt)
        self.assertIn("intent: refund_progress", response.prompt)
        self.assertIn("question: When will the refund arrive?", response.prompt)

    def test_prompt_preview_http_endpoint_returns_context_json(self) -> None:
        app = FastAPI()
        app.include_router(retrieval.router, prefix="/retrieval")
        client = TestClient(app)
        candidates = [
            {
                "score": 0.88,
                "rerank_score": 0.891,
                "model_rerank_score": 0.91,
                "vector_score": 0.82,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Refunds return to the original payment method.",
                "source": {
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                },
            },
        ]

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=candidates):
            response = client.post(
                "/retrieval/prompt-preview",
                headers=AUTH_HEADERS,
                json={
                    "query": "refund arrival time",
                    "mode": "hybrid",
                    "limit": 1,
                    "min_score": 0.62,
                },
            )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["prompt_context_items"][0]["role"], "primary")
        self.assertEqual(body["prompt_context_items"][0]["evidence_strength"], "normal")
        self.assertEqual(body["prompt_context_items"][0]["intent"], "refund_progress")
        self.assertIn("最相关参考资料", body["prompt"])
        self.assertIn("intent: refund_progress", body["prompt"])

    def test_prompt_preview_response_marks_close_match_primary(self) -> None:
        candidates = [
            {
                "score": 0.90,
                "rerank_score": 0.82,
                "model_rerank_score": 0.70,
                "vector_score": 0.84,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Refund timing depends on payment method.",
                "source": {
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                },
            },
            {
                "score": 0.89,
                "rerank_score": 0.78,
                "model_rerank_score": 0.68,
                "vector_score": 0.83,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Check the order detail page for refund progress.",
                "source": {
                    "category": "refund",
                    "intent": "refund_status",
                    "question": "Where can I check refund status?",
                },
            },
        ]

        request = RetrievalSearchRequest(
            query="refund arrival time",
            mode="hybrid",
            limit=2,
            min_score=0.62,
        )

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=candidates):
            response = preview_demo_prompt(request, auth=AGENT_CONTEXT)

        self.assertEqual(response.prompt_context_items[0].role, "primary")
        self.assertEqual(response.prompt_context_items[0].evidence_strength, "close_match")
        self.assertIn("与补充资料较接近", response.prompt)


# ================================================================ 正式路径（B7）


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    """两个租户 + 全局索引 + 已经接好线的检索路由。

    路由读的三个生产默认值（索引根 / embedder / 模型标识）在这里被替换成
    测试替身：**不下载模型、不出网**，并且与流水线建索引时用的是同一个 embedder ——
    否则查询向量与索引向量不在同一空间，测出来的分数毫无意义。
    """

    url = f"sqlite:///{(tmp_path / 'retrieval_api.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)

    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        # 两个租户各入库一份（A 先、B 后）→ 库里只有一份全局索引
        environment.doc_a = environment.ingest(TENANT_A, tag="alpha")
        environment.doc_b = environment.ingest(TENANT_B, tag="beta")
        monkeypatch.setattr(retrieval, "default_index_root", lambda: index_root)
        monkeypatch.setattr(retrieval, "default_embedder", fake_embedding)
        monkeypatch.setattr(retrieval, "default_embedding_model", lambda: EMBEDDING_MODEL)
        yield environment
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


def alpha_headers(**kwargs) -> dict:
    return auth_headers(tenant_id=TENANT_A, user_id=USER_A, roles=["admin"], **kwargs)


def beta_headers(**kwargs) -> dict:
    return auth_headers(tenant_id=TENANT_B, user_id="user-beta", roles=["admin"], **kwargs)


class TestChunkRetrievalApi:
    def test_requires_access_token(self, api_env) -> None:
        client = TestClient(build_retrieval_app())
        assert client.post("/retrieval/search", json={"query": "x"}).status_code == 401

    def test_role_without_retrieval_scope_gets_403(self, api_env) -> None:
        """``knowledge_ops`` 不在 ``retrieval_read`` 的角色表里（权限表决定，不是路由决定）。"""

        client = TestClient(build_retrieval_app())
        headers = auth_headers(tenant_id=TENANT_A, user_id="ops", roles=["knowledge_ops"])
        response = client.post("/retrieval/search", headers=headers, json={"query": query_for("alpha")})
        assert response.status_code == 403

    def test_search_returns_only_own_tenants_chunks(self, api_env) -> None:
        client = TestClient(build_retrieval_app())
        response = client.post(
            "/retrieval/search",
            headers=alpha_headers(),
            json={"query": query_for("beta"), "limit": 5},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["retrieval_path"] == "chunk-index"
        assert body["count"] >= 1
        assert {item["tenant_id"] for item in body["results"]} == {TENANT_A}
        assert not {item["chunk_id"] for item in body["results"]} & set(api_env.doc_b.chunk_ids)

    def test_response_reports_index_provenance_and_visible_count(self, api_env) -> None:
        client = TestClient(build_retrieval_app())
        body = client.post(
            "/retrieval/search",
            headers=alpha_headers(),
            json={"query": query_for("alpha"), "limit": 3},
        ).json()
        index = body["index"]
        assert index["index_name"] == "document_chunks"
        assert index["index_version"] >= 2
        assert index["embedding_model"] == EMBEDDING_MODEL
        # 全局索引里有多个租户的条目，但"本身份可见"的只有自己租户的
        assert index["chunk_count"] > index["visible_chunk_count"] > 0

    def test_client_supplied_tenant_and_filter_are_ignored(self, api_env) -> None:
        """计划 4.3：客户端不得传 tenant / filter 表达式 / FAISS ID。

        这里把"越权参数"全塞进请求体：模型里没有这些字段，它们被忽略；
        结果仍只含本租户的 chunk（授权范围只来自 JWT）。
        """

        client = TestClient(build_retrieval_app())
        response = client.post(
            "/retrieval/search",
            headers=alpha_headers(),
            json={
                "query": query_for("beta"),
                "limit": 5,
                "tenant_id": TENANT_B,
                "allowed_chunk_ids": list(api_env.doc_b.chunk_ids),
                "row_ids": list(range(0, 50)),
                "filter": "1=1",
                "acl": [{"subject_type": "tenant", "subject_id": TENANT_B}],
            },
        )
        assert response.status_code == 200
        assert {item["tenant_id"] for item in response.json()["results"]} == {TENANT_A}

    def test_beta_sees_its_own_chunks_and_not_alpha(self, api_env) -> None:
        client = TestClient(build_retrieval_app())
        body = client.post(
            "/retrieval/search",
            headers=beta_headers(),
            json={"query": query_for("alpha"), "limit": 5},
        ).json()
        assert body["results"]
        assert {item["tenant_id"] for item in body["results"]} == {TENANT_B}

    def test_missing_index_returns_503_with_stable_error_code(self, tmp_path, monkeypatch) -> None:
        """索引不存在 → 503 + ``chunk_index_unavailable``（不是 500 一把梭）。"""

        url = f"sqlite:///{(tmp_path / 'no_index.db').as_posix()}"
        monkeypatch.setenv(db.DATABASE_URL_ENV, url)
        alembic_upgrade(url)
        monkeypatch.setattr(retrieval, "default_index_root", lambda: tmp_path / "empty_store")
        monkeypatch.setattr(retrieval, "default_embedder", fake_embedding)
        monkeypatch.setattr(retrieval, "default_embedding_model", lambda: EMBEDDING_MODEL)

        client = TestClient(build_retrieval_app())
        response = client.post("/retrieval/search", headers=alpha_headers(), json={"query": "任意问题"})
        assert response.status_code == 503
        assert response.json()["detail"]["error_code"] == "chunk_index_unavailable"
        db.dispose_engines()

    def test_demo_endpoint_is_explicitly_labeled(self, api_env) -> None:
        """A 轨保留但**显式标注**：响应里必须能看出自己走的是演示路径。"""

        client = TestClient(build_retrieval_app())
        with patch("routers.retrieval.retrieve_by_real_vector", return_value=[]):
            body = client.post(
                "/retrieval/search-demo",
                headers=alpha_headers(),
                json={"query": "退款多久到账", "limit": 3},
            ).json()
        assert body["retrieval_path"] == "seed-faq-demo"

    def test_unknown_body_fields_do_not_break_the_request(self, api_env) -> None:
        """未知字段被忽略而不是报错（兼容旧调试台多传字段）。"""

        client = TestClient(build_retrieval_app())
        response = client.post(
            "/retrieval/search",
            headers=alpha_headers(),
            json={"query": query_for("alpha"), "debug": True, "mode": "hybrid"},
        )
        assert response.status_code == 200


if __name__ == "__main__":
    unittest.main()
