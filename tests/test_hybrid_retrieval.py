"""B 轨混合检索（B8）：切词 / 稀疏索引构建与校验 / RRF 融合 / **权限隔离**。

为什么单独一个文件、且以权限隔离为重心
--------------------------------------
混合检索是**新加的一条召回路**。本项目的核心不变量是
「过滤发生在候选暴露之前」（``search_chunk_index`` 的 FAISS 预过滤）。
新加的稀疏路如果漏了这件事，就是一个**绕过 ACL 的后门** ——
而且它不会报错：表现只是"某些查询能查到不该看到的文档"，或者反过来
"授权范围内明明有候选却返回 0 条"（排序在全库上做，top-k 被无权限内容占满）。

因此这里的隔离测试分两个方向，缺一不可：

* **不能多给**：查询词只存在于另一个租户的语料里 → 必须 0 条；
* **不能少给**：授权范围内有候选 → 必须能查到（否则是"静默变空"，
  与泄漏一样是缺陷，只是方向相反）。

另外，``search_sparse_index`` 的注入式防护（``access=None`` / 类型不对）也在这里锁定：
这两条是"编程错误"，必须抛而不是退化成全库检索。
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from sqlalchemy.orm import sessionmaker

from retrieval_fixtures import (
    EMBEDDING_MODEL,
    INDEX_NAME,
    TENANT_A,
    TENANT_B,
    RetrievalEnv,
    alembic_upgrade,
    fake_embedding,
    query_for,
)
from services.ingestion import db
from services.ingestion.index_builder import rebuild_index, rollback_index
from services.ingestion.index_manifest import (
    IndexManifest,
    IndexManifestError,
    ManifestEntry,
    version_dir,
)
from services.ingestion.object_store import LocalObjectStore
from services.ingestion.sparse_index import (
    GRAM_ALGORITHM,
    SPARSE_FILENAME,
    SPARSE_META_TABLE,
    build_match_query,
    build_sparse_index,
    read_sparse_meta,
    tokenize_grams,
    verify_sparse_index,
)
from utils.hybrid_retriever import (
    FusionConfig,
    describe_hybrid_retrieval,
    fuse_rankings,
    search_hybrid_chunks,
)
from utils.sparse_retriever import (
    SparseIndexUnavailable,
    describe_sparse_index,
    load_sparse_index,
    search_sparse_index,
)
from utils.vector_retriever import (
    ChunkAccessFilter,
    ChunkRetrievalError,
    describe_chunk_index,
)


# ==================================================================== 单元：切词


class TestTokenizeGrams:
    def test_chinese_becomes_bigrams(self) -> None:
        assert tokenize_grams("退款流程") == ["退款", "款流", "流程"]

    def test_single_chinese_char_is_kept(self) -> None:
        # 单字不能"切成没有" —— 切成空会让该 chunk 在稀疏索引里彻底消失
        assert tokenize_grams("退") == ["退"]

    def test_ascii_words_kept_whole(self) -> None:
        # 英文整词保留：切成 ra/ag 会让 "rag" 命中一堆无关词
        assert tokenize_grams("RAG hybrid search") == ["rag", "hybrid", "search"]

    def test_mixed_content(self) -> None:
        assert tokenize_grams("混合检索 v2") == ["混合", "合检", "检索", "v2"]

    def test_punctuation_is_a_boundary_not_a_bridge(self) -> None:
        """标点**切断** bigram，不跨界拼词。

        ``退款，流程`` 不该产出 ``款流`` —— 那个 bigram 在原文里根本不相邻，
        产出它会让"款流"这种跨标点的查询命中无关文档。
        """

        assert tokenize_grams("退款，流程！  ") == ["退款", "流程"]

    def test_empty_input(self) -> None:
        assert tokenize_grams("") == []
        assert tokenize_grams("   ") == []


class TestBuildMatchQuery:
    def test_or_semantics_with_quotes(self) -> None:
        assert build_match_query("退款流程") == '"退款" OR "款流" OR "流程"'

    def test_fts5_operators_are_neutralised(self) -> None:
        """FTS5 里 ``OR``/``AND``/``NOT``/``NEAR`` 是运算符，裸词会让查询语法出错。

        这既是可用性问题（用户输入含 "or" 就 500），也算半个注入面：
        不加引号时，用户输入能改变查询的**语义结构**而不只是取值。
        注意 ``AND`` 会被小写化成 ``and`` —— 切词统一小写，否则
        ``RAG`` 与 ``rag`` 会被当成两个不同的 gram（索引侧也小写，必须一致）。
        """

        assert build_match_query("or 退款") == '"or" OR "退款"'
        assert build_match_query("退款 AND 流程") == '"退款" OR "and" OR "流程"'

    def test_empty_query_returns_empty_string(self) -> None:
        # 空查询必须返回空串 → 调用方据此返回空结果，而不是"不限条件"
        assert build_match_query("") == ""
        assert build_match_query("！？，。") == ""


# ======================================================== 单元：索引构建与校验


def _entries(texts: list[str]) -> list[ManifestEntry]:
    return [
        ManifestEntry(
            row_id=index,
            chunk_id=f"chunk-{index}",
            tenant_id="tenant-x",
            document_id="doc-1",
            document_version=1,
            document_version_id="ver-1",
            chunk_type="child",
            ordinal=index,
            text=text,
        )
        for index, text in enumerate(texts)
    ]


def _manifest(entries: list[ManifestEntry], *, extra: dict | None = None) -> IndexManifest:
    return IndexManifest(
        index_name="document_chunks",
        tenant_id="tenant-x",
        index_version=1,
        embedding_model="fake",
        embedding_dimension=4,
        built_at="2026-09-25T00:00:00Z",
        chunk_ids=tuple(entry.chunk_id for entry in entries),
        entries=tuple(entries),
        extra=extra or {},
    )


class TestSparseIndexBuildAndVerify:
    def test_build_then_verify_passes(self, tmp_path) -> None:
        entries = _entries(["退款流程说明", "发票开具规则"])
        meta = build_sparse_index(tmp_path, entries)
        assert meta["gram_algorithm"] == GRAM_ALGORITHM
        assert meta["chunk_count"] == 2
        assert verify_sparse_index(tmp_path / SPARSE_FILENAME, _manifest(entries)) == 2

    def test_rowid_equals_manifest_row_id(self, tmp_path) -> None:
        """稀疏索引的 ``rowid`` **必须**等于 manifest 的 ``row_id``。

        这是融合能对齐两路的前提：一旦两边不是同一个整数键，融合就会把 A 的
        稀疏分算给 B —— 结果整体错位且不报错。

        注：``IndexManifest.verify()`` 另外强制了 ``entry.row_id == 位置``，
        所以本断言在正常构建下等价于"rowid == 位置"。但**不能**据此省掉它：
        稀疏索引是另一个模块产出的，它有没有遵守同一个约定必须自己证明，
        而不是靠"另一个模块会保证"来推断。
        """

        entries = _entries(["甲", "乙", "丙"])
        build_sparse_index(tmp_path, entries)

        connection = sqlite3.connect(str(tmp_path / SPARSE_FILENAME))
        try:
            row_ids = [
                row[0] for row in connection.execute("SELECT rowid FROM chunk_grams ORDER BY rowid")
            ]
        finally:
            connection.close()
        assert row_ids == [entry.row_id for entry in entries]
        assert verify_sparse_index(tmp_path / SPARSE_FILENAME, _manifest(entries)) == 3

    def test_verify_detects_count_mismatch(self, tmp_path) -> None:
        entries = _entries(["甲", "乙"])
        build_sparse_index(tmp_path, entries)
        with pytest.raises(IndexManifestError) as excinfo:
            verify_sparse_index(tmp_path / SPARSE_FILENAME, _manifest(entries[:1]))
        assert excinfo.value.error_code == "sparse_index_mismatch"

    def test_verify_detects_row_id_base_mismatch(self, tmp_path) -> None:
        """**条数相同、``row_id`` 基准不同** —— 只查条数是抓不到的。

        构造方式：用一个从 1 开始编号的条目集合去建索引（等价于"另一个构建器
        用了不同的 id 基准"），再拿一个从 0 开始编号的 manifest 去校验。
        条数都是 2，只有 ``row_id_checksum`` 能分辨。
        """

        off_by_one = [replace(entry, row_id=entry.row_id + 1) for entry in _entries(["甲", "乙"])]
        assert [entry.row_id for entry in off_by_one] == [1, 2]
        build_sparse_index(tmp_path, off_by_one)

        normal = _manifest(_entries(["甲", "乙"]))  # row_id = 0, 1
        assert normal.chunk_count == 2
        with pytest.raises(IndexManifestError) as excinfo:
            verify_sparse_index(tmp_path / SPARSE_FILENAME, normal)
        assert excinfo.value.error_code == "sparse_index_mismatch"

    def test_verify_detects_missing_file(self, tmp_path) -> None:
        with pytest.raises(IndexManifestError) as excinfo:
            verify_sparse_index(tmp_path / "nope.sqlite", _manifest(_entries(["甲"])))
        assert excinfo.value.error_code == "sparse_index_unavailable"

    def test_empty_text_chunk_still_occupies_a_row(self, tmp_path) -> None:
        """空正文的 chunk 也要占一行 —— 否则 row_id 集合与 manifest 不等，校验会红。"""

        entries = _entries(["正常正文", ""])
        build_sparse_index(tmp_path, entries)
        assert verify_sparse_index(tmp_path / SPARSE_FILENAME, _manifest(entries)) == 2
        assert read_sparse_meta(tmp_path / SPARSE_FILENAME)["empty_text_rows"] == "1"


# ==================================================================== 单元：融合


class TestFuseRankings:
    def test_rrf_math_matches_definition(self) -> None:
        """手工按 ``Σ w/(k+rank)`` 算一遍，确认实现与定义一致。"""

        config = FusionConfig(dense_weight=1.0, sparse_weight=1.0, rrf_constant=60)
        fused = fuse_rankings([10, 20], [20, 10], config=config)
        # 10: 1/61 (dense #1) + 1/62 (sparse #2)
        # 20: 1/62 (dense #2) + 1/61 (sparse #1)  → 两者相等，按 row_id 升序稳定输出
        expected = 1 / 61 + 1 / 62
        assert fused[0][0] == 10 and fused[1][0] == 20
        assert fused[0][1] == pytest.approx(expected)
        assert fused[1][1] == pytest.approx(expected)

    def test_single_route_is_preserved_in_order(self) -> None:
        config = FusionConfig(dense_weight=10.0, sparse_weight=1.0)
        fused = fuse_rankings([5, 6, 7], [], config=config)
        assert [row[0] for row in fused] == [5, 6, 7]
        assert all(row[3] is None for row in fused)  # sparse_rank 全为 None

    def test_higher_dense_weight_pushes_sparse_down(self) -> None:
        """权重扫描的结论在这个最小例子上可复现：降权能压住稀疏路的干扰。"""

        rank = fuse_rankings([1], [1], config=FusionConfig(dense_weight=1.0, sparse_weight=1.0))
        assert rank[0][0] == 1  # 并集只有一条

        equal = fuse_rankings([1, 2], [3], config=FusionConfig(dense_weight=1.0, sparse_weight=1.0))
        assert equal[0][0] == 1

        heavy = fuse_rankings([1, 2], [3], config=FusionConfig(dense_weight=10.0, sparse_weight=1.0))
        assert heavy[0][0] == 1
        # 稠密加权后，稀疏路单独找到的 3 被压到最后
        assert [row[0] for row in heavy][-1] == 3

    def test_ranks_are_recorded_per_route(self) -> None:
        fused = fuse_rankings([1, 2], [2, 3], config=FusionConfig())
        by_id = {row[0]: row for row in fused}
        assert by_id[1][2] == 1 and by_id[1][3] is None
        assert by_id[2][2] == 2 and by_id[2][3] == 1
        assert by_id[3][2] is None and by_id[3][3] == 2

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"dense_weight": 0.0, "sparse_weight": 0.0},
            {"rrf_constant": 0},
            {"route_k": 0},
            {"dense_weight": -1.0},
        ],
    )
    def test_invalid_config_is_rejected(self, kwargs) -> None:
        with pytest.raises(ValueError):
            FusionConfig(**kwargs)


# ============================================================== 集成：起真索引


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """两个租户共用一份全局 chunk 索引（稀疏路默认开启）。

    产出 ``(environment, index_root)``。

    **刻意不把入库结果的 ``chunk_ids`` 一起产出**：``list_chunks`` 在
    ``created_at`` 相同时按 ``chunk_id`` 兜底排序，而 ``chunk_id`` 含随机
    version uuid —— 于是"第一个 chunk 是谁"每个进程都不一样（本批实测踩坑，
    见 ``test_allowed_set_narrows_results`` 的 docstring）。
    需要"某个真实命中的 chunk"时，**从不带白名单的检索结果里取**，
    而不是从"入库返回的第一条"取。
    """

    url = f"sqlite:///{(tmp_path / 'hybrid.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)

    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        environment.ingest(TENANT_A, tag="alpha")
        environment.ingest(TENANT_B, tag="beta")
        environment.rebuild(TENANT_A)
        yield environment, index_root
    finally:
        session.close()
        engine.dispose()


class TestSparseIndexInBuildPipeline:
    def test_rebuild_produces_sparse_index_and_declares_it(self, env) -> None:
        _environment, index_root = env
        info = describe_chunk_index(root=index_root, index_name=INDEX_NAME)
        assert info["sparse_available"] is True
        assert info["sparse_index_file"] == SPARSE_FILENAME
        assert info["sparse_gram_algorithm"] == GRAM_ALGORITHM

    def test_sparse_file_sits_in_the_same_version_directory(self, env) -> None:
        """稀疏索引必须与稠密索引**同版本目录** —— 回滚时两路一起回滚。"""

        _environment, index_root = env
        info = describe_chunk_index(root=index_root, index_name=INDEX_NAME)
        directory = version_dir(index_root, INDEX_NAME, int(info["index_version"]))
        assert (directory / SPARSE_FILENAME).exists()
        assert (directory / "vectors.faiss").exists()

    def test_describe_reports_unavailable_for_dense_only_build(self, env) -> None:
        environment, index_root = env
        rebuild_index(
            environment.session,
            tenant_id=TENANT_A,
            root=index_root,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            index_name=INDEX_NAME,
            sparse=False,
        )
        info = describe_chunk_index(root=index_root, index_name=INDEX_NAME)
        assert info["sparse_available"] is False
        assert describe_hybrid_retrieval(root=index_root, index_name=INDEX_NAME).available is False
        assert describe_sparse_index(root=index_root, index_name=INDEX_NAME)["available"] is False


# ==================================================== 集成：稀疏路的权限隔离


class TestSparseIsolation:
    """稀疏路的权限隔离。**这是本文件最重要的四个测试。**"""

    def test_query_matching_only_another_tenant_returns_nothing(self, env) -> None:
        """查询词只存在于 B 租户语料里，访问方是 A → 必须 0 条。

        为什么用 ``beta`` 这个 ASCII 词（第一版用 ``query_for("beta")`` 是错的）：
        夹具里两个租户的语料**共享** ``正文N`` 这组词组
        （``markdown_body`` 用 ``prefix=f"{tag}正文"``，只有 ASCII tag 不同），
        所以任何含中文的查询在两边都能命中 —— 拿它测不出跨租户隔离，
        第一版测试因此在有泄漏风险时也能"通过"。

        ``beta`` 只出现在 B 的**文件名 / 附录 salt** 里（那部分是 ASCII 片段，
        切词时整词保留），是干净的"仅 B 有"的词。
        """

        _environment, index_root = env
        hits_for_a = search_sparse_index(
            "beta",
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=10,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits_for_a == []

        # 配套断言：同一个查询换 B 的身份必须查得到。
        # 没有这一条，上面的空结果也可能只是"这个词根本不存在于索引" —— 假绿。
        hits_for_b = search_sparse_index(
            "beta",
            access=ChunkAccessFilter(tenant_id=TENANT_B),
            top_k=10,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits_for_b, "该词必须存在于 B 租户语料中，否则上一条断言不成立"
        assert all(hit.tenant_id == TENANT_B for hit in hits_for_b)

    def test_authorised_tenant_still_finds_its_own_content(self, env) -> None:
        """方向相反的断言：授权范围内有候选，就必须查得到（防"静默变空"）。"""

        _environment, index_root = env
        hits = search_sparse_index(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=10,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits, "同一租户的词法查询必须能召回自己的 chunk"
        assert all(hit.tenant_id == TENANT_A for hit in hits)

    def test_empty_allowed_set_returns_nothing_not_everything(self, env) -> None:
        """空集合 = 「什么都看不到」，**不是**「不限」。这个方向的默认值选错就是全量泄漏。"""

        _environment, index_root = env
        hits = search_sparse_index(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A, allowed_chunk_ids=frozenset()),
            top_k=10,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits == []

    def test_allowed_set_narrows_results(self, env) -> None:
        """给一个只含「真实命中」的单个 chunk 的白名单 → 结果只能是它。

        **为什么不用 ``chunk_ids[0]``**（本批实测踩到的坑）：
        夹具的 ``chunk_ids`` 来自 ``repository.list_chunks``，它按
        ``created_at ASC, chunk_id ASC`` 排序 —— 而同一事务插入的 chunk
        ``created_at`` 相同，兜底键 ``chunk_id`` 是**含随机 version uuid 的哈希**，
        于是"第一个 chunk 是谁"在每个进程里都不一样。
        语料里恰好有一块正文是纯 ASCII 的「附录」块（``salt<文件名> 补充说明``），
        切词器对纯 ASCII token 走**整词**路径、不产出 CJK bigram，
        所以它**本来就检索不到中文查询**。撞上它这条测试就红
        （实测 6 次里红了 2 次），而红的原因跟被测代码毫无关系。

        改法：先不带白名单跑一次，拿**真实命中的那个 chunk** 当靶子 ——
        断言变成"白名单把结果收窄到它自己"，不依赖任何顺序假设。
        """

        _environment, index_root = env
        baseline = search_sparse_index(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=10,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert baseline, "同一租户的词法查询必须能召回自己的 chunk"
        target = baseline[0].chunk_id

        hits = search_sparse_index(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A, allowed_chunk_ids=frozenset({target})),
            top_k=10,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert {hit.chunk_id for hit in hits} == {target}

    def test_missing_filter_is_rejected(self, env) -> None:
        _environment, index_root = env
        with pytest.raises(ChunkRetrievalError) as excinfo:
            search_sparse_index("退款", access=None, root=index_root, index_name=INDEX_NAME)
        assert excinfo.value.error_code == "chunk_filter_required"

    def test_wrong_filter_type_is_rejected(self, env) -> None:
        _environment, index_root = env
        with pytest.raises(ChunkRetrievalError) as excinfo:
            search_sparse_index("退款", access={"tenant_id": TENANT_A}, root=index_root, index_name=INDEX_NAME)
        assert excinfo.value.error_code == "chunk_filter_required"

    def test_blank_tenant_is_rejected(self) -> None:
        with pytest.raises(ChunkRetrievalError) as excinfo:
            ChunkAccessFilter(tenant_id="   ")
        assert excinfo.value.error_code == "chunk_filter_tenant_missing"

    def test_hybrid_mode_applies_the_same_isolation(self, env) -> None:
        """混合模式不能是绕过隔离的第二条路。

        用同一个"仅 B 有"的词，走 hybrid：结果里**不允许**出现任何非 A 租户的命中。
        """

        _environment, index_root = env
        hits = search_hybrid_chunks(
            "beta",
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=10,
            mode="hybrid",
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert all(item.hit.tenant_id == TENANT_A for item in hits)

    def test_hybrid_mode_requires_filter(self, env) -> None:
        _environment, index_root = env
        with pytest.raises(ChunkRetrievalError) as excinfo:
            search_hybrid_chunks(
                "退款",
                access=None,
                mode="hybrid",
                embedder=fake_embedding,
                embedding_model=EMBEDDING_MODEL,
                root=index_root,
                index_name=INDEX_NAME,
            )
        assert excinfo.value.error_code == "chunk_filter_required"


# ================================================== 集成：失败模式与一致性守卫


class TestSparseFailureModes:
    def test_hybrid_refuses_when_sparse_index_is_absent(self, env) -> None:
        """纯稠密索引上请求 hybrid → **显式失败**，不静默退回稠密。

        静默降级会让接口继续宣称 ``retrieval_origin=hybrid`` —— 那是 F1
        「承诺变假」的同类问题：调用方以为自己拿到两路融合结果，其实不是，
        而且从返回内容上完全看不出来。
        """

        environment, index_root = env
        rebuild_index(
            environment.session,
            tenant_id=TENANT_A,
            root=index_root,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            index_name=INDEX_NAME,
            sparse=False,
        )
        with pytest.raises(SparseIndexUnavailable) as excinfo:
            search_hybrid_chunks(
                "退款",
                access=ChunkAccessFilter(tenant_id=TENANT_A),
                mode="hybrid",
                embedder=fake_embedding,
                embedding_model=EMBEDDING_MODEL,
                root=index_root,
                index_name=INDEX_NAME,
            )
        assert excinfo.value.error_code == "sparse_index_unavailable"

        # 同一份索引上 dense 模式必须照常工作（能力可降级，但**必须显式选择**）
        hits = search_hybrid_chunks(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            mode="dense",
            top_k=5,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits

    def test_tampered_row_id_checksum_is_detected(self, env) -> None:
        """把稀疏索引的校验和改掉 → 加载时必须失败。

        这条是**对守卫本身的守卫**：如果 ``load_sparse_index`` 只检查文件存在，
        那么"文件被换成另一份索引"（备份恢复拷错目录、人工替换）就会静默生效，
        融合把 A 的稀疏分算给 B。
        """

        _environment, index_root = env
        info = describe_chunk_index(root=index_root, index_name=INDEX_NAME)
        path = version_dir(index_root, INDEX_NAME, int(info["index_version"])) / SPARSE_FILENAME
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                f"UPDATE {SPARSE_META_TABLE} SET value = ? WHERE key = 'row_id_checksum'",
                ("0" * 64,),
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(SparseIndexUnavailable) as excinfo:
            load_sparse_index(root=index_root, index_name=INDEX_NAME)
        assert excinfo.value.error_code == "sparse_index_mismatch"

    def test_missing_sparse_file_is_detected(self, env) -> None:
        _environment, index_root = env
        info = describe_chunk_index(root=index_root, index_name=INDEX_NAME)
        path = version_dir(index_root, INDEX_NAME, int(info["index_version"])) / SPARSE_FILENAME
        path.unlink()
        with pytest.raises(SparseIndexUnavailable) as excinfo:
            load_sparse_index(root=index_root, index_name=INDEX_NAME)
        assert excinfo.value.error_code == "sparse_index_unavailable"

    def test_rollback_verifies_sparse_index_too(self, env) -> None:
        """回滚到旧版本时，若 manifest 声明了稀疏索引而文件没了 → 当场失败。

        与稠密索引同一条理由：**"回滚成功但索引缺了一半"比回滚失败难排查得多**。
        """

        environment, index_root = env
        environment.rebuild(TENANT_A)  # 造出第 2 个版本
        from services.ingestion.index_builder import available_versions

        versions = available_versions(index_root, INDEX_NAME)
        assert len(versions) >= 2, versions
        target = versions[0]
        path = version_dir(index_root, INDEX_NAME, target) / SPARSE_FILENAME
        assert path.exists(), "旧版本应当也有稀疏索引"
        path.unlink()

        with pytest.raises(Exception) as excinfo:  # noqa: B017 - 断言 error_code 而非类型
            rollback_index(
                environment.session,
                tenant_id=TENANT_A,
                root=index_root,
                index_version=target,
                index_name=INDEX_NAME,
            )
        assert getattr(excinfo.value, "error_code", "") == "sparse_index_unavailable"

    def test_hybrid_hits_carry_route_provenance(self, env) -> None:
        """命中必须能说清"是哪一路找到的" —— 排查检索质量全靠它。"""

        _environment, index_root = env
        hits = search_hybrid_chunks(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=5,
            mode="hybrid",
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits
        for item in hits:
            assert item.routes, "每条融合命中至少要来自一路"
            assert item.fused_score > 0
            if item.dense_rank is not None:
                assert item.dense_rank >= 1
            if item.sparse_rank is not None:
                assert item.sparse_rank >= 1
        # 至少有一条同时被两路找到（同一份语料上这是常态）
        assert any(len(item.routes) == 2 for item in hits)

    def test_sparse_only_mode_returns_sparse_ranks(self, env) -> None:
        _environment, index_root = env
        hits = search_hybrid_chunks(
            query_for("alpha"),
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=5,
            mode="sparse",
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            root=index_root,
            index_name=INDEX_NAME,
        )
        assert hits
        assert all(item.sparse_rank is not None and item.dense_rank is None for item in hits)

    def test_decision_trace_is_machine_readable(self) -> None:
        """把本批的关键取舍做成可断言的事实，防止后续被无声改掉。"""

        config = FusionConfig()
        assert config.dense_weight == 10.0, "默认权重来自权重扫描，改动必须重跑评测"
        assert config.sparse_weight == 1.0
        assert config.rrf_constant == 60

        from routers.retrieval import DEFAULT_RETRIEVAL_MODE

        assert DEFAULT_RETRIEVAL_MODE == "hybrid", (
            "默认模式改成 hybrid 之前，必须先确认索引已重建且 sparse_available=true；"
            "否则现有部署会在发版瞬间 503"
        )


# ============================================ 集成：聊天链路的召回模式开关


class TestChatRetrievalModeWiring:
    """聊天链路（生产问答入口）必须能切到混合检索，且默认不变。

    这组测试存在的原因是本项目的验收判据：
    **「定义在、测试在、无人调用 = 未完成」**。混合检索如果只挂在
    ``/retrieval/search`` 上、而真正的问答链路永远走稠密，那它就是个演示件。
    这里用替身锁住"选中的是哪个实现"，不需要真索引与真库。
    """

    def test_default_mode_is_hybrid(self, monkeypatch) -> None:
        from services import chat_service

        monkeypatch.delenv("RAG_CHAT_RETRIEVAL_MODE", raising=False)
        assert chat_service.resolve_chat_retrieval_mode() == "hybrid"
        assert chat_service.DEFAULT_CHAT_RETRIEVAL_MODE == "hybrid"

    @pytest.mark.parametrize("value", ["hybrid", "HYBRID", " hybrid ", "sparse"])
    def test_valid_values_are_honoured(self, monkeypatch, value) -> None:
        from services import chat_service

        monkeypatch.setenv("RAG_CHAT_RETRIEVAL_MODE", value)
        assert chat_service.resolve_chat_retrieval_mode() == value.strip().lower()

    @pytest.mark.parametrize("value", ["", "densen", "semantic", "RAG"])
    def test_invalid_value_falls_back_instead_of_raising(self, monkeypatch, value) -> None:
        """拼错的环境变量不能让整条问答链路打挂 —— 回落默认，与轨道开关同一取舍。"""

        from services import chat_service

        monkeypatch.setenv("RAG_CHAT_RETRIEVAL_MODE", value)
        assert chat_service.resolve_chat_retrieval_mode() == "hybrid"

    def _run_chat_retrieval(self, monkeypatch, mode: str) -> list[tuple[str, dict]]:
        """在无真库条件下跑一次 ``retrieve_chunk_items_for_chat``，记录它调了谁。"""

        from services import chat_service
        from services.ingestion import db as ingestion_db
        from services import retrieval_access
        from utils import hybrid_retriever, vector_retriever

        calls: list[tuple[str, dict]] = []

        class _NullSession:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setenv("RAG_CHAT_RETRIEVAL_MODE", mode)
        monkeypatch.setattr(ingestion_db, "session_scope", lambda: _NullSession())
        monkeypatch.setattr(
            retrieval_access,
            "build_chunk_access_filter",
            lambda session, auth: ChunkAccessFilter(tenant_id=auth.tenant_id),
        )
        monkeypatch.setattr(
            vector_retriever,
            "retrieve_chunk_items",
            lambda query, **kwargs: calls.append(("dense", kwargs)) or [],
        )
        monkeypatch.setattr(
            hybrid_retriever,
            "retrieve_hybrid_items",
            lambda query, **kwargs: calls.append(("hybrid", kwargs)) or [],
        )

        class _Auth:
            tenant_id = "tenant-x"

        chat_service.retrieve_chunk_items_for_chat("测试查询", _Auth(), limit=3)
        return calls

    def test_dense_mode_calls_the_dense_retriever(self, monkeypatch) -> None:
        calls = self._run_chat_retrieval(monkeypatch, "dense")
        assert [name for name, _ in calls] == ["dense"]

    def test_hybrid_mode_calls_the_hybrid_retriever(self, monkeypatch) -> None:
        calls = self._run_chat_retrieval(monkeypatch, "hybrid")
        assert [name for name, _ in calls] == ["hybrid"]
        assert calls[0][1]["mode"] == "hybrid"
        # 关键：混合路收到的 filter 与稠密路同源，不是"另一条绕过隔离的路"
        assert isinstance(calls[0][1]["access"], ChunkAccessFilter)
        assert calls[0][1]["access"].tenant_id == "tenant-x"
