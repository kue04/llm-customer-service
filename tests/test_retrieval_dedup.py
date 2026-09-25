"""B17：Top-K 内容级去重的单元与接线测试（2026-09-25）。

三层各测一件事，**不混**：

1. **判据层**（:class:`TestEvidenceIdentity` / :class:`TestDedupeUnit`）——
   去重函数本身的语义：同内容折叠、并列时确定性、用尽时不报错、空正文不互吞；
2. **接线层**（:class:`TestDensePathWiring` / :class:`TestHybridPathWiring`）——
   两条生产路径**真的调了**去重，且去重**只看到权限过滤之后的候选**；
3. **端到端**（:class:`TestRealIndexChatBudget`）——
   真实索引上，聊天链路的 ``limit=3`` 拿到的是 **3 条不同内容**，
   且不含任何未授权 chunk。

守卫设计上的两条纪律（承踩坑 D20 / E8）：
* 「判据与下游一致」这条**不靠读 docstring**，而是拿同一批刁钻字符串
  同时喂给两边、比对结果——锚点读不到（不一致）即失败；
* 并列场景**不依赖输入顺序**：正序与逆序两种输入必须给出同一条结果
  （踩坑 D24：并列顺序在不同调用路径下不一致）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from retrieval_fixtures import (
    EMBEDDING_MODEL,
    INDEX_NAME,
    RetrievalEnv,
    TENANT_A,
    alembic_upgrade,
    fake_embedding,
    query_for,
)
from services.ingestion import db
from services.ingestion.object_store import LocalObjectStore
from services.retrieval_access import build_chunk_access_filter, visible_chunk_ids
from utils.rag_context import _normalize_text
from utils.retrieval_dedup import (
    DEFAULT_OVERSAMPLE_FACTOR,
    DEFAULT_OVERSAMPLE_MIN,
    EvidenceCandidate,
    candidates_from_hits,
    evidence_identity,
    normalize_evidence_text,
    oversampled_top_k,
    select_diverse_evidence,
)
from utils.vector_retriever import ChunkAccessFilter, ChunkHit


# ---------------------------------------------------------------- 工具


def make_hit(
    *,
    score: float,
    chunk_id: str,
    text: str,
    chunk_type: str = "child",
    tenant_id: str = "tenant-x",
    document_id: str = "doc-1",
) -> ChunkHit:
    return ChunkHit(
        score=score,
        row_id=abs(hash(chunk_id)) % 10_000,
        chunk_id=chunk_id,
        text=text,
        tenant_id=tenant_id,
        document_id=document_id,
        document_version=1,
        document_version_id="ver-1",
        chunk_type=chunk_type,
        ordinal=1,
    )


def candidate(score: float, chunk_id: str, text: str, chunk_type: str | None = None) -> EvidenceCandidate:
    """不显式给 ``chunk_type`` 时**按 ``chunk_id`` 前缀推**（``p_`` = parent）。

    别把默认值写成 ``"child"``：那样 ``candidate(0.9, "p_b", …)`` 会静默变成
    child，并列兜底键就全塌到 ``chunk_id`` 升序，
    用例会以"实现写错了"的样子红掉（2026-09-25 实际踩到）。
    """

    resolved = chunk_type if chunk_type is not None else ("parent" if chunk_id.startswith("p_") else "child")
    return EvidenceCandidate(score=score, chunk_id=chunk_id, chunk_type=resolved, text=text)


def ids(cands) -> list[str]:
    return [c.chunk_id for c in cands]


# ---------------------------------------------------------------- 判据层


class TestEvidenceIdentity:
    def test_whitespace_only_difference_is_same_evidence(self) -> None:
        """同一段内容的 md / html 孪生只差空白 —— 必须判为同一条。"""

        assert evidence_identity("政策  正文\n\n第一条", "c_1") == evidence_identity(
            " 政策 正文 第一条 ", "p_2"
        )

    def test_blank_text_falls_back_to_chunk_id(self) -> None:
        """空正文没有可比内容：退化为 chunk_id，**不能**把两条空正文判成同一条。"""

        assert evidence_identity("", "c_1") != evidence_identity("   ", "c_2")
        assert evidence_identity("", "c_1") == evidence_identity("\n\t", "c_1")


class TestDedupeUnit:
    def test_same_text_is_collapsed_and_limit_respected(self) -> None:
        cands = [
            candidate(0.9, "c_a", "同一段内容 第一节"),
            candidate(0.9, "p_b", "同一段内容  第一节"),  # 只差空白 → 同一条
            candidate(0.8, "c_c", "另一段内容 第二节"),
            candidate(0.7, "c_d", "第三段内容 第三节"),
        ]
        assert ids(select_diverse_evidence(cands, limit=3)) == ["p_b", "c_c", "c_d"]

    def test_equal_scores_use_deterministic_tiebreak(self) -> None:
        """同分时：parent 优先 → 再按 chunk_id 升序。必须与输入顺序无关。"""

        forward = [
            candidate(0.5, "c_z", "内容 A"),
            candidate(0.5, "p_z", "内容  A"),
            candidate(0.5, "p_a", "内容 A "),
        ]
        assert ids(select_diverse_evidence(forward, limit=1)) == ["p_a"]
        assert ids(select_diverse_evidence(list(reversed(forward)), limit=1)) == ["p_a"]

    def test_result_is_independent_of_input_order(self) -> None:
        """整体结果（不只是并列那一条）不得依赖输入顺序 —— 防踩坑 D24 / D26。"""

        cands = [
            candidate(0.4, "c_3", "内容 C"),
            candidate(0.9, "c_1", "内容 A"),
            candidate(0.9, "p_1", " 内容 A "),
            candidate(0.6, "c_2", "内容 B"),
        ]
        assert ids(select_diverse_evidence(cands, limit=3)) == ids(
            select_diverse_evidence(list(reversed(cands)), limit=3)
        )

    def test_pool_exhausted_returns_what_is_available_without_error(self) -> None:
        """超额召回用尽（库里就是没有更多不同内容）是正常情况，不是异常。"""

        cands = [candidate(0.9, "c_1", "同一条"), candidate(0.8, "p_2", "同一条")]
        assert ids(select_diverse_evidence(cands, limit=3)) == ["c_1"]

    @pytest.mark.parametrize("limit", [0, -1])
    def test_non_positive_limit_returns_empty(self, limit: int) -> None:
        cands = [candidate(0.9, "c_1", "内容 A")]
        assert select_diverse_evidence(cands, limit=limit) == []

    def test_empty_texts_do_not_swallow_each_other(self) -> None:
        cands = [candidate(0.9, "c_1", ""), candidate(0.8, "c_2", "   ")]
        assert ids(select_diverse_evidence(cands, limit=3)) == ["c_1", "c_2"]

    def test_oversampled_top_k_keeps_legacy_shape(self) -> None:
        """超额召回系数是 B8 之前的既有取值 —— 本批刻意不改，这里把它钉住。"""

        assert oversampled_top_k(3) == 20
        assert oversampled_top_k(100) == 100 * DEFAULT_OVERSAMPLE_FACTOR
        assert oversampled_top_k(0) == DEFAULT_OVERSAMPLE_MIN
        assert oversampled_top_k(3) == max(3 * DEFAULT_OVERSAMPLE_FACTOR, DEFAULT_OVERSAMPLE_MIN)


class TestNormalizationConsistency:
    """去重判据必须与下游（prompt 组装）**同一口径**，否则截断后还会被下游再砍一刀。

    这条守卫刻意**不**用自然语言措辞当锚点（踩坑 D20）：它拿同一批字符串喂给两边比对取值，
    两边一旦漂移就会失败——包括"探针本身读不到"的情况（比对不到即失败）。
    """

    SAMPLES = [
        "政策  正文\n\n第一条",
        "\t 换行\t与空格 \r\n 混排 ",
        "中文　全角空格分隔",
        "连续    多空格",
        "",
        "   ",
        "尾随空白 ",
    ]

    @pytest.mark.parametrize("sample", SAMPLES)
    def test_same_normalization_as_prompt_layer(self, sample: str) -> None:
        assert normalize_evidence_text(sample) == _normalize_text(sample)

    def test_prompt_layer_still_uses_that_normalization(self) -> None:
        """反向断言：下游确实会按这个口径去重（否则上面的比对没有意义）。"""

        from utils.rag_context import build_prompt_context_items

        items = [
            {"answer": "同一条证据  正文", "rank": 1, "score": 1.0, "rerank_score": 1.0},
            {"answer": "同一条证据 正文", "rank": 2, "score": 0.9, "rerank_score": 0.9},
        ]
        assert len(build_prompt_context_items(items)) == 1


# ---------------------------------------------------------------- 接线层


def _patch_search(monkeypatch, hits):
    """把 ``search_chunk_index`` 换成回放固定命中的探针，并记录它收到的参数。"""

    from utils import vector_retriever

    calls: list[dict] = []

    def fake(query, **kwargs):
        calls.append({"query": query, **kwargs})
        return list(hits)

    monkeypatch.setattr(vector_retriever, "search_chunk_index", fake)
    return calls


class TestDensePathWiring:
    def test_retrieve_chunk_items_dedupes_before_truncating(self, monkeypatch) -> None:
        """**本批的核心回归**：截断前先去重 —— 3 个名额装 3 条不同内容。"""

        from utils import vector_retriever

        hits = [
            make_hit(score=0.90, chunk_id="c_1", text="证据一  正文"),
            make_hit(score=0.90, chunk_id="p_1", text="证据一 正文", chunk_type="parent"),
            make_hit(score=0.85, chunk_id="p_2", text="证据二 正文", chunk_type="parent"),
            make_hit(score=0.85, chunk_id="c_2", text="证据二  正文"),
            make_hit(score=0.80, chunk_id="c_3", text="证据三 正文"),
        ]
        _patch_search(monkeypatch, hits)

        items = vector_retriever.retrieve_chunk_items(
            "查询", access=ChunkAccessFilter(tenant_id="tenant-x"), limit=3
        )

        assert len(items) == 3
        assert [item["chunk_id"] for item in items] == ["p_1", "p_2", "c_3"]
        assert [item["rank"] for item in items] == [1, 2, 3]  # 名次必须连续
        assert len({normalize_evidence_text(item["text"]) for item in items}) == 3

    def test_oversampled_recall_is_requested_from_search(self, monkeypatch) -> None:
        """去重依赖超额召回；召回量没变（对比 B8 之前的取值），只是截断点后移。"""

        from utils import vector_retriever

        calls = _patch_search(monkeypatch, [make_hit(score=0.9, chunk_id="c_1", text="A")])
        vector_retriever.retrieve_chunk_items(
            "查询", access=ChunkAccessFilter(tenant_id="tenant-x"), limit=3
        )
        assert calls[0]["top_k"] == 20

    def test_access_filter_is_passed_through_unchanged(self, monkeypatch) -> None:
        """去重层不得自己构造/放宽过滤器：它只消费 search 的输出。"""

        from utils import vector_retriever

        given = ChunkAccessFilter(tenant_id="tenant-x", allowed_chunk_ids=frozenset({"c_1"}))
        calls = _patch_search(monkeypatch, [make_hit(score=0.9, chunk_id="c_1", text="A")])
        vector_retriever.retrieve_chunk_items("查询", access=given, limit=3)
        assert calls[0]["access"] is given

    def test_kept_duplicate_is_always_one_of_the_filtered_hits(self, monkeypatch) -> None:
        """留下的那条必须在**过滤后的候选**里 —— 去重不可能凭空造出或捞出别的命中。

        顺序保证是结构性的：去重消费的是 ``search_chunk_index`` 的返回值，
        而那个函数内部已完成 FAISS ``IDSelectorBatch`` 预过滤（先过滤 → 再去重）。
        反过来（先去重再过滤）会让无权副本挤掉有权副本，且**静默**。
        """

        from utils import vector_retriever

        allowed = {"p_1"}
        hits = [
            make_hit(score=0.9, chunk_id="c_1", text="同一段内容"),
            make_hit(score=0.9, chunk_id="p_1", text="同一段内容 ", chunk_type="parent"),
        ]
        _patch_search(monkeypatch, hits)
        items = vector_retriever.retrieve_chunk_items(
            "查询",
            access=ChunkAccessFilter(tenant_id="tenant-x", allowed_chunk_ids=frozenset(allowed)),
            limit=3,
        )
        assert {item["chunk_id"] for item in items} <= allowed


class TestHybridPathWiring:
    def test_retrieve_hybrid_items_dedupes_by_fused_score(self, monkeypatch) -> None:
        """混合路复用同一份去重实现，且按**融合分**排序（不是底层稠密分）。"""

        from utils import hybrid_retriever
        from utils.vector_retriever import ChunkHit

        def hybrid_hit(row_id: int, chunk_id: str, text: str, fused: float, dense: float):
            return hybrid_retriever.HybridHit(
                hit=ChunkHit(
                    score=dense,
                    row_id=row_id,
                    chunk_id=chunk_id,
                    text=text,
                    tenant_id="tenant-x",
                    document_id="doc-1",
                    document_version=1,
                    document_version_id="ver-1",
                    chunk_type="child",
                    ordinal=1,
                ),
                fused_score=fused,
                dense_rank=row_id,
            )

        hits = [
            # 稠密分最低、融合分最高 → 必须靠融合分排到第一
            hybrid_hit(1, "c_low_dense", "证据一", fused=0.9, dense=0.10),
            hybrid_hit(2, "c_dup", "证据一 ", fused=0.8, dense=0.99),
            hybrid_hit(3, "c_two", "证据二", fused=0.7, dense=0.50),
        ]
        monkeypatch.setattr(hybrid_retriever, "search_hybrid_chunks", lambda query, **kw: list(hits))

        items = hybrid_retriever.retrieve_hybrid_items(
            "查询",
            access=ChunkAccessFilter(tenant_id="tenant-x"),
            limit=3,
            mode="dense",
        )
        assert [item["chunk_id"] for item in items] == ["c_low_dense", "c_two"]


# ---------------------------------------------------------------- 端到端


@pytest.fixture()
def real_env(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'dedup.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)
    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        ingested = environment.ingest(TENANT_A, tag="alpha", sections=3)
        yield environment, ingested, index_root
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


class TestRealIndexChatBudget:
    def test_chat_limit_is_filled_with_distinct_evidence(self, real_env) -> None:
        """真实索引 + 真实切分：``limit=3`` 必须拿到 3 条**内容不同**的证据。

        这条同时是「生产调用点」的证明（B17 的判据：定义在、测试在、**有人调用**）——
        走的是 ``retrieve_chunk_items``（``fetch`` 与聊天链路共用的那个入口）。
        """

        from utils import vector_retriever

        environment, ingested, index_root = real_env
        access = build_chunk_access_filter(environment.session, environment.auth(TENANT_A))
        items = vector_retriever.retrieve_chunk_items(
            query_for("alpha"),
            access=access,
            limit=3,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=index_root,
            index_name=INDEX_NAME,
        )

        texts = [normalize_evidence_text(item["text"]) for item in items]
        assert len(items) == 3, f"超额召回应当填满 3 个名额，实际 {len(items)}"
        assert len(set(texts)) == 3, "三条证据必须内容互不相同"

        allowed = visible_chunk_ids(environment.session, environment.auth(TENANT_A))
        assert {item["chunk_id"] for item in items} <= set(allowed)

    def test_narrowed_allow_list_is_respected_after_dedupe(self, real_env) -> None:
        """定向授权收窄后，去重结果不得越出白名单（顺序：先过滤 → 再去重）。"""

        from utils import vector_retriever

        environment, ingested, index_root = real_env
        auth = environment.auth(TENANT_A)
        full = build_chunk_access_filter(environment.session, auth)
        narrowed_ids = frozenset(sorted(full.allowed_chunk_ids)[:2])
        narrowed = ChunkAccessFilter(tenant_id=TENANT_A, allowed_chunk_ids=narrowed_ids)

        items = vector_retriever.retrieve_chunk_items(
            query_for("alpha"),
            access=narrowed,
            limit=3,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert {item["chunk_id"] for item in items} <= narrowed_ids
        assert items, "白名单里的 chunk 必须仍能被检索到（过滤不能把结果打成空）"


def test_candidates_from_hits_unwraps_hybrid_shape() -> None:
    """适配层：``HybridHit`` 的字段在内层 ``.hit`` 里，``payload`` 必须是**原对象**。"""

    from utils.hybrid_retriever import HybridHit

    inner = make_hit(score=0.5, chunk_id="p_9", text="正文", chunk_type="parent")
    wrapper = HybridHit(hit=inner, fused_score=1.25, dense_rank=1, sparse_rank=3)
    resolved = candidates_from_hits([wrapper], score_of=lambda item: item.fused_score)
    assert resolved[0].score == 1.25
    assert resolved[0].chunk_id == "p_9"
    assert resolved[0].chunk_type == "parent"
    assert resolved[0].payload is wrapper
