"""B17：检索层的 **Top-K 内容级去重**（证据多样性）。

## 要解决的问题

聊天链路的证据预算是 ``limit=3``，但检索层在**去重之前**就把候选截断到 3 条。
于是"同一段内容"（父子块同文本、同一份文档的 md/html 孪生）会同时占掉 2 个名额，
用户实际只拿到 1~2 条不同证据。实测（``tmp/probe_topk_diversity.py``）：
**21 个名额里 6 个被重复内容吃掉，平均有效证据 2.14 / 3**。

## 为什么去重必须在**检索层**、且要在**权限过滤之后**

1. 组装层（``utils.rag_context.build_prompt_context_items``）本来就会按文本去重，
   但那时**候选已经被截断到 3 条**——去重只会把结果从 3 条减到 2 条，
   **不会把被浪费的名额补回来**。要真正补回来，只能在截断之前去重；
2. 去重**必须发生在权限过滤之后**：先按授权范围过滤 → 再去重，
   才能保证"留下的那条一定是他有权看的"。反过来（先去重再过滤）会让
   **无权副本挤掉有权副本**——不报错、只让结果变空或变错，是静默的越权/丢命中。
   本模块的调用点全部在 :func:`utils.vector_retriever.search_chunk_index`
   （FAISS ``IDSelectorBatch`` 预过滤）与
   :func:`utils.sparse_retriever.search_sparse_index`（FTS5 临时表 JOIN 预过滤）
   **之后**，因此天然满足这条顺序要求（有测试锁定）。

## 为什么判据是「归一化文本」而不是 ``content_hash``

``document_chunks.content_hash`` 是**原文**的 sha256
（``services.ingestion.chunkers.models.hash_text``，不做任何归一化），
而下游去重用的是**压平空白后**的文本（``utils.rag_context._normalize_text``）。
两者口径不一致，落到数字上就是：

| 判据 | 7 条 query 的有效证据 |
| --- | --- |
| 只按 ``content_hash`` | **19 / 21（2.71 / 3）** |
| 按归一化文本 | **21 / 21（3.00 / 3）** |

（``tmp/probe_b17_strategy.py``，2026-09-25）
同一份内容的 md / html 版本在空白上并不逐字节相同，所以原文 hash 会判它们"不同"，
而下游照样会把它们当同一条——**用 content_hash 去重，截断后仍会被下游再砍一刀**。

因此本模块的判据取自**下游实际使用的口径**，并遵守一条更强的原则：

    **检索层的去重键必须至少与所有下游去重口径一样严格**，
    否则"超额召回 → 去重 → 截断"这条链路会在下游被重新塌陷，名额照样填不满。

## 为什么不带 ``parent_chunk_id``、也不消「父块 ⊇ 子块」

* ``ChunkHit`` **没有** ``parent_chunk_id``（它不是 :class:`ManifestEntry` 的字段），
  要用它就得改 manifest 结构并重建索引——为一条**实测零增量**的规则付这个代价不值得；
* 实测在 ``limit=3`` 的窗口内，「父块与其子块同现且文本不完全相同」（包含关系）
  **没有产生任何额外收益**（``tmp/probe_b17_strategy.py``：加与不加都是 3.00 / 3）；
  库层面父子块同文本的有 2824 对，**100% 逐字节相同**，已被本条判据覆盖。
  这条残留重叠作为**已知且有意不处理**的部分写进审查文件。

## 超额召回系数为什么不变

``limit × 5``、下限 20 ——**这不是本批新增的**：B8 之前
``retrieve_chunk_items`` / ``retrieve_hybrid_items`` 就已经按这个量召回再截断。
本批只是把"截断"从第 3 条挪到"去重之后"，**召回条数一条没变**，
因此不引入额外延迟（实测见审查文件）。保留原系数是为了让本批的 diff
只包含行为修正、不包含召回规模调整。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

#: 与 ``utils.rag_context._normalize_text`` 同口径的空白压缩。
#: **刻意重复实现而不是 import**：检索层（``utils/``）不该依赖 prompt 组装层的私有函数，
#: 否则那边改一行归一化就会静默改变检索层的召回条数。
#: 两处口径的一致性由 ``tests/test_retrieval_dedup.py`` 的一致性测试锁定。
_WHITESPACE = re.compile(r"\s+")

#: 超额召回系数（见模块头部：这是 B8 之前的既有取值，本批刻意不变）。
DEFAULT_OVERSAMPLE_FACTOR = 5
DEFAULT_OVERSAMPLE_MIN = 20


def normalize_evidence_text(text: str) -> str:
    """把正文压成"同一段内容的规范形式"（只压空白，不改字词）。"""

    return _WHITESPACE.sub(" ", str(text or "")).strip()


def evidence_identity(text: str, chunk_id: str) -> str:
    """一条命中参与去重的身份。

    空正文**退化到 ``chunk_id``**（加一个不可能与正文相撞的前缀）：
    把多条"没有正文"的命中归成同一条会让它们互相吞掉，
    而它们本来就不该被当作彼此的证据。
    """

    normalized = normalize_evidence_text(text)
    return normalized if normalized else f"\x00empty\x00{chunk_id}"


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """去重函数**只依赖这四个字段**，``payload`` 原样带回给调用方。

    这样稠密路（``ChunkHit``）与混合路（``HybridHit``）可以共用同一份实现，
    而不是各写一遍——两份实现漂移的那一天，两条链路会给出不同的证据条数。
    """

    score: float
    chunk_id: str
    chunk_type: str
    text: str
    payload: Any = None


def _rank_key(candidate: EvidenceCandidate) -> tuple[float, int, str]:
    """保留规则：**分数高者优先；并列时用确定性兜底键**。

    为什么必须兜底：实测同 ``content_hash`` 的命中组 **20/20 分数严格相等**
    （父子块同文本 → 同一向量 → 同分）。分数相等时若依赖 FAISS / 融合的返回顺序，
    结果在不同调用路径下会不一致（踩坑 **D24**），测试会随机变红（踩坑 **D26**）。
    兜底顺序刻意选 ``parent`` 优先再按 ``chunk_id`` 升序：
    两者正文相同时留哪条在语义上等价，但**必须有一个与运行环境无关的确定答案**。
    """

    return (-float(candidate.score), 0 if candidate.chunk_type == "parent" else 1, candidate.chunk_id)


def select_diverse_evidence(
    candidates: Iterable[EvidenceCandidate],
    *,
    limit: int,
) -> list[EvidenceCandidate]:
    """按内容去重后取前 ``limit`` 条（**能拿几条拿几条，不报错**）。

    ``limit <= 0`` 返回空列表；候选全部重复时不报错、返回去重后的实际条数
    —— 超额召回"用尽"是正常情况（库里就是没有更多不同内容），
    把它变成异常会把一次"证据略少"的检索升级成一次失败。
    """

    if int(limit) <= 0:
        return []

    kept: list[EvidenceCandidate] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=_rank_key):
        identity = evidence_identity(candidate.text, candidate.chunk_id)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(candidate)
        if len(kept) >= int(limit):
            break
    return kept


def oversampled_top_k(
    limit: int,
    *,
    factor: int = DEFAULT_OVERSAMPLE_FACTOR,
    minimum: int = DEFAULT_OVERSAMPLE_MIN,
) -> int:
    """超额召回的候选池大小（``limit × factor``，下限 ``minimum``）。"""

    return max(int(limit) * int(factor), int(minimum))


def candidates_from_hits(
    hits: Sequence[Any],
    *,
    score_of: Callable[[Any], float] | None = None,
) -> list[EvidenceCandidate]:
    """把 ``ChunkHit`` / ``HybridHit`` 统一成 :class:`EvidenceCandidate`。

    两种命中的**形状不同**：``ChunkHit`` 自己就有 ``chunk_id`` / ``text``，
    而 ``HybridHit`` 把它们包在内层 ``.hit`` 里。这里统一按
    "若对象有 ``.hit`` 就下钻一层"处理，于是两个调用点不必各写一份适配。
    ``score_of`` 让混合路用**融合分** ``fused_score`` 而不是底层稠密分排序。

    ``payload`` 始终是**传进来的原对象**（``HybridHit`` 而不是内层 ``ChunkHit``），
    这样调用方取回后仍能拿到 ``dense_rank`` / ``sparse_rank`` 等路由溯源字段。
    """

    resolved: list[EvidenceCandidate] = []
    for hit in hits:
        inner = getattr(hit, "hit", hit)
        score = score_of(hit) if score_of is not None else getattr(hit, "score", 0.0)
        resolved.append(
            EvidenceCandidate(
                score=float(score),
                chunk_id=str(getattr(inner, "chunk_id", "")),
                chunk_type=str(getattr(inner, "chunk_type", "") or ""),
                text=str(getattr(inner, "text", "") or ""),
                payload=hit,
            )
        )
    return resolved


__all__ = [
    "DEFAULT_OVERSAMPLE_FACTOR",
    "DEFAULT_OVERSAMPLE_MIN",
    "EvidenceCandidate",
    "candidates_from_hits",
    "evidence_identity",
    "normalize_evidence_text",
    "oversampled_top_k",
    "select_diverse_evidence",
]
