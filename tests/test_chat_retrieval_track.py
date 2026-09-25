"""F1 切轨测试：聊天链路的检索轨道（2026-09-25）。

分三层，**缺一层就有一类故障能静默通过**：

1. **解析层** —— ``resolve_chat_retrieval_path`` 的默认值、环境变量覆盖、非法值回落；
2. **适配层** —— ``adapt_chunk_items_for_prompt`` 的字段映射，以及一条**反证**：
   不做适配直接把 chunk 形状喂给下游，一条证据都组装不出来
   （锁定"这层不是多余的抽象"）；
3. **端到端层** —— 用 ``RetrievalEnv`` 建**真实 chunk 索引**（确定性假 embedder，
   不出网、不下载模型），走 ``retrieve_chunk_items_for_chat`` → 适配 →
   ``build_prompt_context_items``，断言切轨后聊天**真的能组装出非空证据**。

第 1、2 层只能证明"接线对了"，只有第 3 层证明"接上以后拿得到东西"。
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from retrieval_fixtures import (
    EMBEDDING_MODEL,
    INDEX_NAME,
    RetrievalEnv,
    TENANT_A,
    TENANT_B,
    alembic_upgrade,
    fake_embedding,
    query_for,
)
from services import chat_service
from services.ingestion import db
from services.ingestion.object_store import LocalObjectStore
from utils.rag_context import build_prompt_context_items


def sample_chunk_hit(**overrides) -> dict:
    """一条 B 轨命中的样本：字段名与 ``retrieve_chunk_items`` 的真实输出一致。"""

    hit = {
        "rank": 1,
        "chunk_id": "chunk-001",
        "document_id": "doc-001",
        "document_version": 3,
        "document_title": "外卖平台配送服务规范（2026版）",
        "chunk_type": "text",
        "heading_path": ["第三章 超时与异常处置", "第六条 超时赔付"],
        "page_start": 4,
        "page_end": 4,
        "source_type": "pdf",
        "source_uri": "local://guide.pdf",
        "filename": "guide.pdf",
        "text": "订单实际送达时间超过承诺时间 30 分钟的，平台按订单金额的 10% 补偿。",
        "score": 0.81,
        "retrieval_origin": "chunk-index",
    }
    hit.update(overrides)
    return hit


class ChatRetrievalPathTest(unittest.TestCase):
    """轨道解析：默认值 / 覆盖 / 非法值。"""

    def test_default_track_is_chunk_index(self) -> None:
        """默认必须是 chunk —— 它和 README 的 f1-track 锚点由守卫双向校验。"""

        self.assertEqual(chat_service.DEFAULT_CHAT_RETRIEVAL_PATH, "chunk")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RAG_CHAT_RETRIEVAL_PATH", None)
            self.assertEqual(chat_service.resolve_chat_retrieval_path(), "chunk")

    def test_legacy_seed_env_is_ignored(self) -> None:
        with patch.dict(os.environ, {"RAG_CHAT_RETRIEVAL_PATH": "seed"}):
            self.assertEqual(chat_service.resolve_chat_retrieval_path(), "chunk")

    def test_uppercase_and_padding_are_accepted(self) -> None:
        with patch.dict(os.environ, {"RAG_CHAT_RETRIEVAL_PATH": "  SEED  "}):
            self.assertEqual(chat_service.resolve_chat_retrieval_path(), "chunk")

    def test_invalid_value_falls_back_instead_of_crashing(self) -> None:
        """拼错的环境变量不该把整条问答链路打挂。"""

        for value in ("", "   ", "chunk-index", "vector", "bogus", "seed-faq"):
            with patch.dict(os.environ, {"RAG_CHAT_RETRIEVAL_PATH": value}):
                self.assertEqual(
                    chat_service.resolve_chat_retrieval_path(), "chunk", f"输入 {value!r}"
                )


class ChunkItemAdaptationTest(unittest.TestCase):
    """B 轨命中 → prompt 条目：字段映射。"""

    def test_answer_is_taken_from_chunk_text(self) -> None:
        """最关键的一条：下游只认 ``answer``，B 轨的正文在 ``text``。"""

        adapted = chat_service.adapt_chunk_items_for_prompt([sample_chunk_hit()])

        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0]["answer"], sample_chunk_hit()["text"])

    def test_intent_falls_back_to_heading_leaf_not_unknown(self) -> None:
        """文档 chunk 没有业务 intent，用章节末级顶上 —— 留空会渲染成「优先按 unknown 回答」。"""

        adapted = chat_service.adapt_chunk_items_for_prompt([sample_chunk_hit()])

        self.assertEqual(adapted[0]["intent"], "第六条 超时赔付")
        self.assertNotEqual(adapted[0]["intent"], "")

    def test_knowledge_id_prefers_chunk_id_and_keeps_provenance(self) -> None:
        adapted = chat_service.adapt_chunk_items_for_prompt([sample_chunk_hit()])

        item = adapted[0]
        self.assertEqual(item["knowledge_id"], "chunk-001")
        self.assertEqual(item["document_id"], "doc-001")
        self.assertEqual(item["heading_path"], ["第三章 超时与异常处置", "第六条 超时赔付"])
        self.assertEqual(item["page_start"], 4)

    def test_missing_fields_do_not_raise(self) -> None:
        """真实语料里 heading_path 可能是空列表（无标题的 HTML / TXT）。"""

        adapted = chat_service.adapt_chunk_items_for_prompt(
            [sample_chunk_hit(heading_path=[], document_title="")]
        )
        self.assertEqual(adapted[0]["intent"], "")
        self.assertEqual(adapted[0]["title"], "chunk-001")

    def test_chunk_provenance_survives_citation_conversion(self) -> None:
        adapted = chat_service.adapt_chunk_items_for_prompt([sample_chunk_hit()])
        context_items = build_prompt_context_items(adapted)
        evidence = chat_service.build_evidence_citations(context_items)
        citations = chat_service.build_prd_citations(evidence)

        self.assertEqual(evidence[0]["chunk_id"], "chunk-001")
        self.assertEqual(evidence[0]["document_id"], "doc-001")
        self.assertEqual(evidence[0]["page_start"], 4)
        self.assertEqual(evidence[0]["page_end"], 4)
        self.assertEqual(citations[0]["chunk_id"], "chunk-001")
        self.assertEqual(citations[0]["document_id"], "doc-001")
        self.assertEqual(citations[0]["page_start"], 4)

    def test_raw_chunk_shape_would_be_dropped_by_downstream(self) -> None:
        """反证：不做适配直接喂下游 → 一条都组装不出来。

        这条锁定「适配层不是多余的抽象」：下游的判空守卫是**有意的**
        （空证据不得进 prompt），所以只能在检索侧把形状补齐。
        """

        raw = [sample_chunk_hit()]
        adapted = chat_service.adapt_chunk_items_for_prompt(raw)

        self.assertEqual(build_prompt_context_items(raw), [])
        self.assertEqual(len(build_prompt_context_items(adapted)), 1)


class ChatRetrievalDispatchTest(unittest.TestCase):
    """正式聊天只走 chunk 检索；seed 仅保留在显式 demo API。"""

    def test_chat_never_touches_seed_faq(self) -> None:
        with patch.object(chat_service, "retrieve_chunk_items_for_chat", return_value=[sample_chunk_hit()]) as chunk_mock:
            with patch.object(chat_service, "retrieve_rag_items", create=True) as seed_mock:
                items = chat_service.retrieve_chat_items("query", auth=object())

        seed_mock.assert_not_called()
        chunk_mock.assert_called_once()
        self.assertEqual(items[0]["answer"], sample_chunk_hit()["text"])

    def test_chat_without_identity_is_fail_closed(self) -> None:
        items = chat_service.retrieve_chat_items("query", auth=None)
        self.assertEqual(items, [])

    def test_query_preprocessing_has_one_traceable_contract(self) -> None:
        plan = chat_service.preprocess_retrieval_query(
            "refund status",
            {"primary_intent": "refund_progress", "secondary_intents": ["refund_amount"]},
            {"facts": {"entities": {"order_id": "o-1"}}},
        )
        self.assertEqual(plan["original_query"], "refund status")
        self.assertIn("refund_progress", plan["resolved_query"])
        self.assertTrue(plan["rewrite_applied"])
        self.assertIn("intent_hint", plan["rewrite_strategy"])
        self.assertEqual(plan["entities"]["order_id"], "o-1")

    def test_multi_hop_retrieval_returns_coverage_per_subquery(self) -> None:
        plan = {
            "sub_queries": [
                {"sub_query_id": "q1", "query": "refund rule"},
                {"sub_query_id": "q2", "query": "delivery delay compensation"},
            ],
            "original_query": "refund and delay",
        }
        with patch.object(chat_service, "retrieve_chat_items", side_effect=[[sample_chunk_hit(chunk_id="c1", score=0.9)], []]):
            items, coverage = chat_service.retrieve_with_query_plan(plan, auth=object())
        self.assertEqual(len(items), 1)
        self.assertEqual([item["status"] for item in coverage], ["covered", "missing"])
        self.assertEqual(coverage[0]["evidence_ids"], ["c1"])

    def test_evidence_gate_modes(self) -> None:
        self.assertEqual(chat_service.decide_evidence_gate([], evidence_count=1)["mode"], "complete")
        self.assertEqual(chat_service.decide_evidence_gate([], evidence_count=0)["mode"], "clarify")
        coverage = [
            {"sub_query_id": "q1", "status": "covered"},
            {"sub_query_id": "q2", "status": "missing"},
        ]
        self.assertEqual(chat_service.decide_evidence_gate(coverage, evidence_count=1)["mode"], "partial")
        self.assertEqual(
            chat_service.decide_evidence_gate(coverage, evidence_count=1, risk_level="high")["mode"],
            "human_review",
        )

    def test_query_preprocessing_is_identity_without_intent(self) -> None:
        plan = chat_service.preprocess_retrieval_query("随便问问", {}, {})
        self.assertEqual(plan["resolved_query"], plan["original_query"])
        self.assertFalse(plan["rewrite_applied"])



# ================================================================ 端到端（真实索引）


@pytest.fixture()
def chunk_env(tmp_path, monkeypatch):
    """一份**真实**的 chunk 索引（假 embedder，不出网）。

    把 ``chat_service`` 的 B 轨四个默认值换成测试替身 —— 与 ``RetrievalEnv``
    建索引时用的是同一个 embedder，否则查询向量与索引向量不在同一空间，
    "检索得到"就成了运气。
    """

    url = f"sqlite:///{(tmp_path / 'chat_track.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)

    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        environment.doc_a = environment.ingest(TENANT_A, tag="alpha")
        environment.doc_b = environment.ingest(TENANT_B, tag="beta")
        monkeypatch.setattr(chat_service, "_chunk_index_root", lambda: index_root)
        monkeypatch.setattr(chat_service, "_chunk_embedder", fake_embedding)
        monkeypatch.setattr(chat_service, "_chunk_embedding_model", lambda: EMBEDDING_MODEL)
        monkeypatch.setattr(chat_service, "_chunk_index_name", lambda: INDEX_NAME)
        yield environment
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


class ChatTrackEndToEndTest:
    """★ 核心证据：切轨后聊天**真的**能从文档 chunk 里拿到证据。"""

    def test_chunk_track_assembles_prompt_evidence(self, chunk_env) -> None:
        auth = chunk_env.auth(TENANT_A)

        raw_hits = chat_service.retrieve_chunk_items_for_chat(query_for("alpha"), auth)
        assert raw_hits, "B 轨零命中：索引没建起来，或过滤器把本租户的 chunk 也挡了"

        context_items = build_prompt_context_items(
            chat_service.adapt_chunk_items_for_prompt(raw_hits)
        )

        assert context_items, "适配后组装不出证据：字段映射漏了 answer"
        primary = context_items[0]
        assert primary.role == "primary"
        assert primary.answer, "证据摘要为空"
        assert primary.title
        # 命中必须**自证来源**：chunk id 落在本租户这次入库的 chunk 集合里
        assert primary.knowledge_id in chunk_env.doc_a.chunk_ids

    def test_chunk_track_survives_the_full_chat_entry_point(self, chunk_env, monkeypatch) -> None:
        """把分发点整条串起来跑一遍（**不生成回答**，只到证据层）。

        比上一条更接近真实调用：``retrieve_chat_items`` 是 ``get_answer_from_rag``
        实际用的入口，它自己会读轨道开关。
        """

        auth = chunk_env.auth(TENANT_A)
        monkeypatch.setenv("RAG_CHAT_RETRIEVAL_PATH", "chunk")

        items = chat_service.retrieve_chat_items(query_for("alpha"), auth)

        assert items, "分发点没把 B 轨的结果带回来"
        assert items[0]["retrieval_origin"] == "chunk-index"
        assert items[0]["answer"]

    def test_chunk_track_is_tenant_isolated(self, chunk_env) -> None:
        """跨租户零命中：B 轨的租户过滤在聊天链路上同样生效。

        聊天链路切轨之后才第一次真的走 ACL，这条是"切轨没有绕过权限"的锁。
        """

        auth_alpha = chunk_env.auth(TENANT_A)

        leaked = chat_service.retrieve_chunk_items_for_chat(query_for("beta"), auth_alpha)

        assert leaked == [], "alpha 的身份检索到了 beta 的语料"
