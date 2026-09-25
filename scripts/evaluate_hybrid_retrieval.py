"""B 轨混合检索的三模式对比评测（走**生产代码路径**）。

## 为什么必须调生产代码

评测脚本最容易犯的错是"为了好写，自己再实现一遍检索"：
那样量到的数字属于**评测脚本里的那个检索器**，而不是线上跑的那个。
两者一旦分叉（改了权重忘了同步、过滤条件写得不一致），
报告会一直给出乐观数字，而线上是另一回事 —— 且没有任何信号。

因此这里只调 ``utils.hybrid_retriever.search_hybrid_chunks``，
``mode`` 参数走 ``dense`` / ``sparse`` / ``hybrid`` 三种取值，
用的是同一个索引、同一个 :class:`ChunkAccessFilter`、同一份融合实现。

## 两套评测集缺一不可

* ``data/retrieval_gold_cases.jsonl``（81 条）—— 弱监督构造，query 是**小节标题**，
  与正文用词高度重合。词法匹配天然占优。
* ``data/retrieval_colloquial_cases.jsonl``（30 条）—— 人工口语化改写，
  **刻意换掉正文用词**，才接近真实用户形态。

只看标题集会得出"混合检索全面优于稠密"的错误结论（实测：标题集上稀疏路 R@1
反而更高）。两套集放一起看，才会看到稀疏路在真实提问上崩到 0.1000，
从而得出"必须大幅降权"这个正确结论。

## 指标口径

* 命中判据：**gold_span 出现在命中 chunk 的 text 里**（span-based，
  不用 chunk_id —— 后者在重新切分后即失效，会让金标悄悄过期）；
* ``R@k``：前 k 条里至少有一条命中；
* ``MRR`` / ``NDCG@10``：只对命中的条目计分（未命中计 0）；
* 负样本集：不参与 R@k（它**没有**正确答案），只报稠密路 top-1 相似度的分布
  与稀疏路的命中情况，用来看"有答案 / 没答案"是否可区分。

## 用法

```bash
RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+ \\
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
./venv/Scripts/python.exe scripts/evaluate_hybrid_retrieval.py
```
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.ingestion.pipeline import default_embedder, default_embedding_model, default_index_root
from utils.hybrid_retriever import HybridMode, search_hybrid_chunks
from utils.sparse_retriever import describe_sparse_index
from utils.vector_retriever import CHUNK_INDEX_NAME, ChunkAccessFilter, describe_chunk_index

GOLD_PATH = PROJECT_ROOT / "data" / "retrieval_gold_cases.jsonl"
COLLOQUIAL_PATH = PROJECT_ROOT / "data" / "retrieval_colloquial_cases.jsonl"
NEGATIVE_PATH = PROJECT_ROOT / "data" / "retrieval_negative_cases.jsonl"
REPORT_DIR = PROJECT_ROOT / "reports" / "retrieval_hybrid"

MODES: tuple[HybridMode, ...] = ("dense", "sparse", "hybrid")
CUTOFFS = (1, 5, 10)


@dataclass(frozen=True, slots=True)
class RankingOutcome:
    """一条 query 的检索结果与命中名次（None = 前 10 未命中）。"""

    case_id: str
    ranks: dict[str, int | None]  # mode -> rank
    #: 仅稠密路的「原始文本比对」名次，用于量化口径差异（见 :func:`normalize`）
    dense_rank_raw_text: int | None = None


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize(text: str) -> str:
    """把连续空白（空格 / 换行 / 制表符）压成单个空格。

    为什么评测要比**压平后**的文本（这是本批第二个实测结论，见踩坑 D22）：

    语料里同一份 FAQ 常有 md 与 html **两份孪生 chunk**，内容相同但换行位置不同
    （md 用 ``\\n``、html 抽出来用空格）。金标 span 抽自其中一份的原始文本，
    于是"用原始文本比对"会把命中与否绑在**格式差异**上，而不是检索质量上：

        实测 title 集：压平比对 dense R@1 = 0.7160，原始比对 = 0.6296
        （差了 7 条 —— 全是"同一个答案的孪生 chunk 排在前面却被判为未命中"）

    两边同时压平后，判据变成「这段文字在不在里面」，与换行无关。
    注意：这只会让判定**更宽松**，不会制造假命中 —— span 仍是原文片段，
    压平只影响空白字符。
    """

    return " ".join((text or "").split())


def span_matches(span: str, text: str) -> bool:
    """金标命中的唯一判据：**答案片段**出现在命中 chunk 里（两边都先压平）。

    刻意不用 ``chunk_id``：重新切分后 id 会变，金标会悄悄过期，
    而"过期金标"的表现是检索质量莫名下降，没有任何报错。
    """

    if not span:
        return False
    return normalize(span) in normalize(text)


def evaluate_dataset(
    cases: list[dict],
    *,
    access: ChunkAccessFilter,
    index_root: Path,
    embedding_model: str,
    top_k: int = 10,
) -> tuple[dict[str, Any], list[RankingOutcome], dict[str, list[float]]]:
    """跑三种模式，返回 (指标表, 逐条名次, mode -> 单条耗时列表)。"""

    outcomes: list[RankingOutcome] = []
    latencies: dict[str, list[float]] = {mode: [] for mode in MODES}

    for case in cases:
        span = case.get("gold_span") or ""
        ranks: dict[str, int | None] = {}
        dense_hits: list = []
        for mode in MODES:
            started = time.perf_counter()
            hits = search_hybrid_chunks(
                case["query"],
                access=access,
                top_k=top_k,
                mode=mode,
                embedder=default_embedder,
                embedding_model=embedding_model,
                root=index_root,
                index_name=CHUNK_INDEX_NAME,
            )
            latencies[mode].append(time.perf_counter() - started)
            if mode == "dense":
                dense_hits = hits
            found: int | None = None
            for rank, item in enumerate(hits, start=1):
                if span_matches(span, item.hit.text):
                    found = rank
                    break
            ranks[mode] = found

        raw_rank: int | None = None
        if span:
            for rank, item in enumerate(dense_hits, start=1):
                if span in (item.hit.text or ""):
                    raw_rank = rank
                    break
        outcomes.append(
            RankingOutcome(case_id=str(case.get("id", "")), ranks=ranks, dense_rank_raw_text=raw_rank)
        )

    metrics: dict[str, Any] = {}
    total = len(outcomes)
    for mode in MODES:
        recall = {k: 0 for k in CUTOFFS}
        reciprocal = 0.0
        ndcg = 0.0
        for outcome in outcomes:
            rank = outcome.ranks[mode]
            if rank is None:
                continue
            for k in CUTOFFS:
                if rank <= k:
                    recall[k] += 1
            reciprocal += 1.0 / rank
            if rank <= 10:
                ndcg += 1.0 / math.log2(rank + 1)
        metrics[mode] = {
            **{f"recall@{k}": recall[k] / total for k in CUTOFFS},
            "mrr": reciprocal / total,
            "ndcg@10": ndcg / total,
            "hits_in_top10": sum(1 for o in outcomes if o.ranks[mode] is not None),
            "latency_ms_mean": statistics.mean(latencies[mode]) * 1000,
            "latency_ms_p50": statistics.median(latencies[mode]) * 1000,
            "latency_ms_max": max(latencies[mode]) * 1000,
        }
    return metrics, outcomes, latencies


def complementarity(outcomes: list[RankingOutcome]) -> dict[str, int]:
    """top-10 互补性：只看稠密 / 只看稀疏 / 都命中 / 都没命中 / 并集。"""

    both = dense_only = sparse_only = neither = 0
    for outcome in outcomes:
        dense = outcome.ranks["dense"] is not None
        sparse = outcome.ranks["sparse"] is not None
        if dense and sparse:
            both += 1
        elif dense:
            dense_only += 1
        elif sparse:
            sparse_only += 1
        else:
            neither += 1
    return {
        "both": both,
        "dense_only": dense_only,
        "sparse_only": sparse_only,
        "neither": neither,
        "union": both + dense_only + sparse_only,
        "dense_total": both + dense_only,
        "sparse_total": both + sparse_only,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B 轨混合检索三模式对比评测")
    parser.add_argument("--tenant-id", default="tenant-dev")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out-prefix", default="", help="报告文件前缀，默认按日期")
    args = parser.parse_args()

    index_root = default_index_root()
    embedding_model = default_embedding_model()

    index_info = describe_chunk_index(root=index_root, index_name=CHUNK_INDEX_NAME)
    sparse_info = describe_sparse_index(root=index_root, index_name=CHUNK_INDEX_NAME)
    if not sparse_info.get("available"):
        print(
            f"稀疏索引不可用，无法做混合检索对比：{sparse_info.get('error')}\n"
            f"请先运行：./venv/Scripts/python.exe scripts/rebuild_chunk_index.py",
            file=sys.stderr,
        )
        return 2

    access = ChunkAccessFilter(tenant_id=args.tenant_id)

    datasets = {
        "title": load_jsonl(GOLD_PATH),
        "colloquial": load_jsonl(COLLOQUIAL_PATH),
    }

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tenant_id": args.tenant_id,
        "index": {
            "index_name": index_info["index_name"],
            "index_version": index_info["index_version"],
            "chunk_count": index_info["chunk_count"],
            "embedding_model": index_info["embedding_model"],
            "embedding_dimension": index_info["embedding_dimension"],
            "sparse_available": index_info["sparse_available"],
            "sparse_gram_algorithm": index_info["sparse_gram_algorithm"],
            "sparse_chunk_count": sparse_info.get("chunk_count"),
        },
        "datasets": {},
    }

    lines: list[str] = []
    lines.append("B 轨混合检索评测（生产代码路径：utils.hybrid_retriever.search_hybrid_chunks）")
    lines.append("=" * 88)
    lines.append(f"生成时间：{report['generated_at']}")
    lines.append(
        f"索引：v{index_info['index_version']} · {index_info['chunk_count']} chunk · "
        f"{index_info['embedding_model']} ({index_info['embedding_dimension']} 维)"
    )
    lines.append(
        f"稀疏路：{index_info['sparse_gram_algorithm']} · "
        f"{sparse_info.get('chunk_count')} 条 · sqlite {sparse_info.get('sqlite_version')}"
    )

    for label, cases in datasets.items():
        metrics, outcomes, _lat = evaluate_dataset(
            cases,
            access=access,
            index_root=index_root,
            embedding_model=embedding_model,
            top_k=args.top_k,
        )
        comp = complementarity(outcomes)
        raw_hits_top1 = sum(1 for o in outcomes if o.dense_rank_raw_text == 1)
        raw_hits_top10 = sum(1 for o in outcomes if o.dense_rank_raw_text is not None)
        report["datasets"][label] = {
            "case_count": len(cases),
            "metrics": metrics,
            "complementarity_top10": comp,
            "raw_text_match_baseline": {
                "note": "用未压平的原始文本比对（本批修正前的口径），仅稠密路，用于量化口径差异",
                "dense_recall@1": raw_hits_top1 / len(outcomes),
                "dense_recall@10": raw_hits_top10 / len(outcomes),
            },
            "details": [
                {"id": o.case_id, **{f"rank_{m}": o.ranks[m] for m in MODES}}
                for o in outcomes
            ],
        }

        lines.append("")
        lines.append(f"数据集 {label}（{len(cases)} 条）")
        lines.append("-" * 88)
        header = f"{'mode':<10}{'R@1':>9}{'R@5':>9}{'R@10':>9}{'MRR':>9}{'NDCG@10':>10}{'命中条数':>10}"
        lines.append(header)
        for mode in MODES:
            m = metrics[mode]
            lines.append(
                f"{mode:<10}{m['recall@1']:>9.4f}{m['recall@5']:>9.4f}{m['recall@10']:>9.4f}"
                f"{m['mrr']:>9.4f}{m['ndcg@10']:>10.4f}{m['hits_in_top10']:>10}"
            )

        baseline = metrics["dense"]
        lines.append("")
        lines.append("相对 dense 的差值（正 = 该模式更好）")
        for mode in ("sparse", "hybrid"):
            m = metrics[mode]
            lines.append(
                f"{mode:<10}{m['recall@1'] - baseline['recall@1']:>+9.4f}"
                f"{m['recall@5'] - baseline['recall@5']:>+9.4f}"
                f"{m['recall@10'] - baseline['recall@10']:>+9.4f}"
                f"{m['mrr'] - baseline['mrr']:>+9.4f}{m['ndcg@10'] - baseline['ndcg@10']:>+10.4f}"
            )

        lines.append("")
        lines.append("口径说明：命中判据 = 金标 span 出现在命中 chunk 里（**两边先压平空白**）。")
        lines.append(
            f"  若改用「未压平的原始文本」比对，dense-only 会变成 "
            f"R@1={raw_hits_top1 / len(outcomes):.4f}、R@10={raw_hits_top10 / len(outcomes):.4f}"
        )
        lines.append(
            "  差异来源：同一份 FAQ 的 md / html 孪生 chunk 换行位置不同，"
            "span 抽自其中一份；按原始文本比会把判据绑在格式差异上而非检索质量上。"
        )

        lines.append("")
        lines.append(
            f"top-10 互补性：都命中 {comp['both']} | 仅稠密 {comp['dense_only']} | "
            f"仅稀疏 {comp['sparse_only']} | 都没命中 {comp['neither']}"
        )
        lines.append(
            f"  并集召回 {comp['union']}/{len(cases)} = {comp['union'] / len(cases):.4f}"
            f" → **任何融合策略的上限**；dense-only {comp['dense_total']}/{len(cases)} "
            f"= {comp['dense_total'] / len(cases):.4f}"
        )

        lines.append("")
        lines.append("单条检索耗时（毫秒，含每次重新加载 manifest 与索引）")
        for mode in MODES:
            m = metrics[mode]
            lines.append(
                f"  {mode:<10} 均值 {m['latency_ms_mean']:>8.1f}  "
                f"中位 {m['latency_ms_p50']:>8.1f}  最大 {m['latency_ms_max']:>8.1f}"
            )

    # ---------------------------------------------------------- 负样本
    negatives = load_jsonl(NEGATIVE_PATH)
    lines.append("")
    lines.append(f"负样本（语料外问题，{len(negatives)} 条）")
    lines.append("-" * 88)
    negative_detail: list[dict[str, Any]] = []
    for case in negatives:
        hits = search_hybrid_chunks(
            case["query"],
            access=access,
            top_k=1,
            mode="hybrid",
            embedder=default_embedder,
            embedding_model=embedding_model,
            root=index_root,
            index_name=CHUNK_INDEX_NAME,
        )
        if not hits:
            continue
        top = hits[0]
        negative_detail.append(
            {
                "id": case.get("id"),
                "query": case["query"],
                "dense_rank": top.dense_rank,
                "sparse_rank": top.sparse_rank,
                "routes": list(top.routes),
            }
        )
    dense_only_share = (
        sum(1 for item in negative_detail if item["dense_rank"] is not None) / len(negative_detail)
        if negative_detail
        else 0.0
    )
    lines.append(
        f"  hybrid top-1 里由稠密路贡献的比例：{dense_only_share:.3f}"
        f"（稀疏路在负样本上的命中越少越好：命中多说明它把「语料外问题」匹配到了具体文档）"
    )
    report["negative_cases"] = {"count": len(negatives), "top1_detail": negative_detail}

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = args.out_prefix or time.strftime("%Y%m%d")
    json_path = REPORT_DIR / f"evaluation_{prefix}.json"
    txt_path = REPORT_DIR / f"evaluation_{prefix}.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print()
    print(f"报告已写入：{json_path}")
    print(f"           {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
