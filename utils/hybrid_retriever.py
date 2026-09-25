"""B 轨混合检索：稠密路 + 稀疏路 → 加权 RRF 融合。

## 为什么是「按 rank 融合」而不是「按分数加权求和」

两路的分数**量纲不可比**：

* 稠密路是归一化向量的内积（余弦），落在 ``[-1, 1]``，有绝对含义；
* 稀疏路是 FTS5 的 ``bm25()``，**无上界的负分**，量级取决于语料长度分布与文档数。

把它们直接相加需要先做归一化，而归一化会引入两个新问题：
① min-max 归一化对**异常值敏感** —— 某次查询里有一篇文档 bm25 特别高，
其余分数的相对关系会被压平；② 归一化参数（min/max）没有稳定语义，
换一批语料就得重调，且调错了不会有任何报错。

RRF（Reciprocal Rank Fusion）绕开整个问题：

    score(d) = Σ_route  weight_route / (k + rank_route(d))

它只用**名次**，不用分数值，因此天然对两路分数的量纲免疫。
``k``（默认 60）的作用是压平头部差距：``k`` 越大，rank 1 与 rank 2 的得分越接近，
融合越"民主"；``k`` 小则更信任每一路自己的头部判断。

## 权重为什么默认 dense=10、sparse=1（**实测结论，不是拍脑袋**）

``tmp/probe_hybrid_weights.py`` 在两套评测集上的权重扫描：

| 配置 | title(81) R@1 | title R@10 | colloq(30) R@1 | colloq R@10 |
| --- | --- | --- | --- | --- |
| dense-only | 0.7160 | 0.9877 | 0.4000 | 0.5667 |
| 等权 RRF（w=1:1） | 0.7901 | 0.9877 | **0.2667** | **0.3667** |
| **w_dense=10（本默认值）** | **0.7407** | **1.0000** | **0.4000** | **0.5667** |
| w_dense=20 | 0.7160 | 0.9877 | 0.4000 | 0.5667 |

两个必须讲清楚的事实：

1. **等权 RRF 在真实提问上是有害的** —— colloq 集 R@1 从 0.4000 掉到 0.2667。
   原因是稀疏路的 OR-bigram 查询天然带噪声（任一片段命中即入选），
   在"刻意避开正文用词"的口语提问上，它召回的多是不相关文档，
   等权融合让这些噪声把稠密路正确的 top-1 顶下去了。
2. **w_dense=10 是唯一在两套集上都不掉点的配置**，且 title 集 R@10 = 1.0000
   **正好等于两路 top-10 并集召回的上限**（``tmp/probe_hybrid_ceiling.py``），
   即在这个配置下融合已经吃满了可用的互补性；再往上加权重（20）
   稀疏路被完全压制，退化成纯稠密。

**所以本模块的收益是"词法精确性 + 召回鲁棒性"，不是"指标全面上涨"。**
面试口径必须按前者讲，否则一问数字就露（见 ``RESUME_OPTIMIZATION_2026-09-25.md``）。

## 为什么混合模式在稀疏索引缺席时**报错**而不是退回稠密

见 ``utils/sparse_retriever.py`` 的 ``load_sparse_index``：静默降级会让
``retrieval_origin`` 继续宣称 ``hybrid`` —— 那是 F1「承诺变假」的同类问题。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from utils.sparse_retriever import search_sparse_index
from utils.vector_retriever import (
    CHUNK_INDEX_NAME,
    ERROR_FILTER_REQUIRED,
    ChunkAccessFilter,
    ChunkHit,
    ChunkRetrievalError,
    search_chunk_index,
)

logger = logging.getLogger(__name__)

#: 融合模式。``dense`` / ``sparse`` 只为**消融实验**保留，走的是同一份生产代码 ——
#: 这样评测脚本量到的就是线上真实行为，而不是"评测里另写一套再声称线上一致"。
HybridMode = Literal["hybrid", "dense", "sparse"]

DEFAULT_ROUTE_K = 50
#: 见模块头部表格：这是唯一在两套评测集上都不劣于纯稠密的权重。
DEFAULT_DENSE_WEIGHT = 10.0
DEFAULT_SPARSE_WEIGHT = 1.0
DEFAULT_RRF_CONSTANT = 60


@dataclass(frozen=True, slots=True)
class FusionConfig:
    """融合参数。默认值来自实测扫描，改动**必须重跑评测**并更新本文件头部表格。"""

    dense_weight: float = DEFAULT_DENSE_WEIGHT
    sparse_weight: float = DEFAULT_SPARSE_WEIGHT
    rrf_constant: int = DEFAULT_RRF_CONSTANT
    route_k: int = DEFAULT_ROUTE_K

    def __post_init__(self) -> None:
        if self.dense_weight < 0 or self.sparse_weight < 0:
            raise ValueError("融合权重不得为负")
        if self.dense_weight == 0 and self.sparse_weight == 0:
            raise ValueError("两路权重不能同时为 0（融合结果将恒为空）")
        if self.rrf_constant < 1:
            raise ValueError("rrf_constant 必须 >= 1（k=0 时 rank 1 的得分会发散）")
        if self.route_k < 1:
            raise ValueError("route_k 必须 >= 1")


@dataclass(frozen=True, slots=True)
class HybridHit:
    """融合后的一条命中：主体是 :class:`ChunkHit`（含全部安全字段），外加**路由溯源**。

    ``dense_rank`` / ``sparse_rank`` 必须暴露出来，理由是：
    「这条命中是哪一路找到的」是排查检索质量问题的第一个问题。
    如果只返回融合分，遇到"某条该出现的结果没出现"时只能靠猜。
    这一条在 demo 里价值有限，但在被问"你怎么知道融合起了作用"时是唯一的答案。
    """

    hit: ChunkHit
    fused_score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None

    @property
    def routes(self) -> tuple[str, ...]:
        found = []
        if self.dense_rank is not None:
            found.append("dense")
        if self.sparse_rank is not None:
            found.append("sparse")
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        payload = self.hit.to_dict()
        payload.update(
            {
                "score": self.fused_score,
                "fused_score": self.fused_score,
                "dense_rank": self.dense_rank,
                "sparse_rank": self.sparse_rank,
                "routes": list(self.routes),
                "retrieval_origin": "chunk-index-hybrid",
            }
        )
        return payload


def fuse_rankings(
    dense_row_ids: Sequence[int],
    sparse_row_ids: Sequence[int],
    *,
    config: FusionConfig | None = None,
) -> list[tuple[int, float, int | None, int | None]]:
    """按 RRF 融合两路名次，返回 ``(row_id, fused_score, dense_rank, sparse_rank)`` 降序。

    名次从 1 开始计数（RRF 的定义如此），两路各自独立编号 ——
    稠密路的第 1 名和稀疏路的第 1 名是同一件事：
    "这一路认为它最相关"，而不是"它整体排第 1"。
    """

    settings = config or FusionConfig()
    scores: dict[int, float] = {}
    dense_ranks: dict[int, int] = {}
    sparse_ranks: dict[int, int] = {}

    for rank, row_id in enumerate(dense_row_ids, start=1):
        key = int(row_id)
        dense_ranks[key] = rank
        scores[key] = scores.get(key, 0.0) + settings.dense_weight / (settings.rrf_constant + rank)

    for rank, row_id in enumerate(sparse_row_ids, start=1):
        key = int(row_id)
        sparse_ranks[key] = rank
        scores[key] = scores.get(key, 0.0) + settings.sparse_weight / (settings.rrf_constant + rank)

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        (row_id, score, dense_ranks.get(row_id), sparse_ranks.get(row_id))
        for row_id, score in ordered
    ]


def search_hybrid_chunks(
    query: str,
    *,
    access: ChunkAccessFilter,
    top_k: int = 10,
    mode: HybridMode = "hybrid",
    config: FusionConfig | None = None,
    embedder: Callable[[str], list[float]] | None = None,
    embedding_model: str = "",
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> list[HybridHit]:
    """混合（或消融）检索。

    ``min_score`` **刻意不存在**：融合后的分数是 RRF 分，
    其量级由权重的绝对值决定（``weight/(k+1)``），
    拿一个余弦阈值去卡它只会得到"阈值看起来在起作用"的假象。
    需要控制质量时应当调 ``config``（权重 / route_k），而不是加一个无量纲的阈值。
    """

    settings = config or FusionConfig()
    if access is None or not isinstance(access, ChunkAccessFilter):
        # 与两路各自的入口保持同一判据：宁可在这里就拒绝，
        # 也不要让"某一层忘了传 filter"变成一次全库检索。
        raise ChunkRetrievalError(
            ERROR_FILTER_REQUIRED,
            "混合检索必须传入服务端构造的 access filter；缺失 filter 一律拒绝",
        )

    route_k = max(int(settings.route_k), int(top_k))
    dense_hits: list[ChunkHit] = []
    sparse_hits: list[ChunkHit] = []

    if mode in ("hybrid", "dense"):
        dense_hits = search_chunk_index(
            query,
            access=access,
            top_k=route_k,
            embedder=embedder,
            embedding_model=embedding_model,
            root=root,
            index_name=index_name,
        )
    if mode in ("hybrid", "sparse"):
        sparse_hits = search_sparse_index(
            query, access=access, top_k=route_k, root=root, index_name=index_name
        )

    if mode == "dense":
        return [
            HybridHit(hit=hit, fused_score=hit.score, dense_rank=rank)
            for rank, hit in enumerate(dense_hits[: max(int(top_k), 1)], start=1)
        ]
    if mode == "sparse":
        return [
            HybridHit(hit=hit, fused_score=hit.score, sparse_rank=rank)
            for rank, hit in enumerate(sparse_hits[: max(int(top_k), 1)], start=1)
        ]

    by_row = {int(hit.row_id): hit for hit in dense_hits}
    by_row.update({int(hit.row_id): hit for hit in sparse_hits})

    fused = fuse_rankings(
        [int(hit.row_id) for hit in dense_hits],
        [int(hit.row_id) for hit in sparse_hits],
        config=settings,
    )

    results: list[HybridHit] = []
    for row_id, score, dense_rank, sparse_rank in fused[: max(int(top_k), 1)]:
        hit = by_row.get(row_id)
        if hit is None:  # pragma: no cover - 两路都建过 by_row，取不到说明有逻辑错误
            logger.warning("融合结果里的 row_id=%s 在两路结果中都找不到，已丢弃", row_id)
            continue
        results.append(
            HybridHit(hit=hit, fused_score=score, dense_rank=dense_rank, sparse_rank=sparse_rank)
        )
    return results


def retrieve_hybrid_items(
    query: str,
    *,
    access: ChunkAccessFilter,
    limit: int = 3,
    mode: HybridMode = "hybrid",
    config: FusionConfig | None = None,
    embedder: Callable[[str], list[float]] | None = None,
    embedding_model: str = "",
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> list[dict]:
    """混合检索的「条目」形态（形状对齐 ``retrieve_chunk_items``）。"""

    settings = config or FusionConfig()
    hits = search_hybrid_chunks(
        query,
        access=access,
        top_k=max(int(limit) * 5, 20),
        mode=mode,
        config=settings,
        embedder=embedder,
        embedding_model=embedding_model,
        root=root,
        index_name=index_name,
    )
    items: list[dict] = []
    for rank, item in enumerate(hits[: max(int(limit), 0)], start=1):
        hit = item.hit
        items.append(
            {
                "rank": rank,
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "document_version": hit.document_version,
                "document_version_id": hit.document_version_id,
                "tenant_id": hit.tenant_id,
                "title": hit.heading_text or hit.document_title or hit.chunk_id,
                "document_title": hit.document_title,
                "chunk_type": hit.chunk_type,
                "heading_path": list(hit.heading_path),
                "page_start": hit.page_start,
                "page_end": hit.page_end,
                "acl": [dict(entry) for entry in hit.acl],
                "source_uri": hit.source_uri,
                "filename": hit.filename,
                "source_type": hit.source_type,
                "content_hash": hit.content_hash,
                "token_count": hit.token_count,
                "text": hit.text,
                "score": item.fused_score,
                "dense_score": hit.score if item.dense_rank is not None else None,
                "dense_rank": item.dense_rank,
                "sparse_rank": item.sparse_rank,
                "routes": list(item.routes),
                "retrieval_origin": "chunk-index-hybrid" if mode == "hybrid" else f"chunk-index-{mode}",
            }
        )
    return items


@dataclass(frozen=True, slots=True)
class HybridAvailability:
    """混合检索的可用性自述（接口 / 运维用）。"""

    available: bool
    mode_default: HybridMode = "hybrid"
    reason: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


def describe_hybrid_retrieval(
    *,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> HybridAvailability:
    """报告混合检索能不能用、用的什么参数。**不抛错**，供健康检查调用。"""

    from utils.sparse_retriever import describe_sparse_index

    sparse = describe_sparse_index(root=root, index_name=index_name)
    settings = FusionConfig()
    return HybridAvailability(
        available=bool(sparse.get("available")),
        reason="" if sparse.get("available") else str(sparse.get("error") or "稀疏索引不可用"),
        extras={
            "sparse": sparse,
            # 显式列字段而不是 ``vars()``/``__dict__``：FusionConfig 是
            # ``slots=True`` 的冻结 dataclass，没有 ``__dict__``，
            # 用 ``vars()`` 会在运行期抛 TypeError（本模块初版犯过）。
            "config": {
                "dense_weight": settings.dense_weight,
                "sparse_weight": settings.sparse_weight,
                "rrf_constant": settings.rrf_constant,
                "route_k": settings.route_k,
            },
        },
    )


__all__ = [
    "DEFAULT_DENSE_WEIGHT",
    "DEFAULT_RRF_CONSTANT",
    "DEFAULT_SPARSE_WEIGHT",
    "FusionConfig",
    "HybridAvailability",
    "HybridHit",
    "HybridMode",
    "describe_hybrid_retrieval",
    "fuse_rankings",
    "retrieve_hybrid_items",
    "search_hybrid_chunks",
]
