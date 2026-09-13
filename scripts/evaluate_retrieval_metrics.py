"""检索指标评测：在固定评测集上计算 Recall@K、MRR@K、NDCG@K。

为什么需要这个脚本
------------------
scripts/evaluate_vector_retrieval.py 只统计 Top1 命中 / Top3 召回但 Top1 错误 / 未命中，
无法回答「前 K 条里到底捞回来了多少」「正确答案平均排在第几位」。本脚本补齐这些指标。

相关性定义
----------
每个 case 标注了 expected_intents（允许的意图集合）。候选文档的 intent 命中该集合即视为
相关（二元相关，rel=1），否则 rel=0。这与 evaluate_vector_retrieval.py 的 judge_result()
使用同一套标签，保证两个脚本口径一致。

指标定义
--------
- Recall@K：Top-K 中至少出现 1 条相关文档的比例。
- MRR@K：1/（首条相关文档的排名）的均值；Top-K 内无相关文档记 0。
- NDCG@K：DCG = rel_i / log2(i+1) 求和；IDCG 按「单条正确答案」计算，即 IDCG = 1。
  因此 NDCG@K = 1 / log2(first_relevant_rank + 1)，首条命中即 1.0。
  这个简化是刻意的：评测集每条只标注一个主意图，不适合造多档 graded relevance。
  若后续评测集改为多档标注，需要同步改这里的 IDCG 计算。

用法
----
    python scripts/evaluate_retrieval_metrics.py
    python scripts/evaluate_retrieval_metrics.py --limit 10 --save-report
    python scripts/evaluate_retrieval_metrics.py --compare-modes
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.rag_config import get_rag_config_dict  # noqa: E402
from scripts.evaluate_vector_retrieval import EVAL_QUERIES  # noqa: E402
from utils.vector_retriever import (  # noqa: E402
    DEFAULT_MIN_VECTOR_SCORE,
    DEFAULT_MODEL_RERANK_WEIGHT,
    retrieve_by_real_vector,
)

DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "retrieval_metrics"
DEFAULT_KS = (1, 5, 10)


def is_relevant(candidate: dict, expected_intents: list[str]) -> bool:
    intent = candidate.get("source", {}).get("intent", "")
    return intent in expected_intents


def relevance_grades(candidates: list[dict], expected_intents: list[str]) -> list[int]:
    return [1 if is_relevant(item, expected_intents) else 0 for item in candidates]


def first_relevant_rank(grades: list[int]) -> int:
    for index, grade in enumerate(grades, start=1):
        if grade == 1:
            return index
    return 0


def recall_at_k(grades: list[int], k: int) -> float:
    return 1.0 if any(grade == 1 for grade in grades[:k]) else 0.0


def reciprocal_rank(grades: list[int], k: int) -> float:
    rank = first_relevant_rank(grades[:k])
    return 1.0 / rank if rank else 0.0


def ndcg_at_k(grades: list[int], k: int) -> float:
    rank = first_relevant_rank(grades[:k])
    if not rank:
        return 0.0
    # 单条正确答案假设下 IDCG = 1 / log2(1 + 1) = 1
    return 1.0 / math.log2(rank + 1)


def evaluate_case(
    case: dict,
    limit: int,
    use_hybrid: bool,
    rerank_weight: float,
) -> dict:
    candidates = retrieve_by_real_vector(
        case["query"],
        limit=limit,
        min_score=DEFAULT_MIN_VECTOR_SCORE,
        use_hybrid=use_hybrid,
        rerank_weight=rerank_weight,
    )
    grades = relevance_grades(candidates, case["expected_intents"])

    return {
        "query": case["query"],
        "expected_intents": case["expected_intents"],
        "error_type": case.get("error_type", ""),
        "returned": len(candidates),
        "first_relevant_rank": first_relevant_rank(grades),
        "top1_intent": candidates[0]["source"].get("intent", "") if candidates else "",
        "recall": {f"@{k}": recall_at_k(grades, k) for k in DEFAULT_KS},
        "rr": reciprocal_rank(grades, limit),
        "ndcg": {f"@{k}": ndcg_at_k(grades, k) for k in DEFAULT_KS},
        "top_intents": [item["source"].get("intent", "") for item in candidates],
    }


def summarize(results: list[dict], limit: int) -> dict:
    total = len(results)
    if total == 0:
        return {}

    summary = {
        "total_queries": total,
        "limit": limit,
        "recall": {},
        "mrr": round(sum(item["rr"] for item in results) / total, 4),
        "ndcg": {},
    }
    for k in DEFAULT_KS:
        if k > limit:
            continue
        summary["recall"][f"@{k}"] = round(
            sum(item["recall"][f"@{k}"] for item in results) / total, 4
        )
        summary["ndcg"][f"@{k}"] = round(
            sum(item["ndcg"][f"@{k}"] for item in results) / total, 4
        )
    return summary


def run_evaluation(
    limit: int,
    use_hybrid: bool,
    rerank_weight: float,
    verbose: bool = True,
) -> tuple[list[dict], dict]:
    results = []
    for case in EVAL_QUERIES:
        result = evaluate_case(case, limit=limit, use_hybrid=use_hybrid, rerank_weight=rerank_weight)
        results.append(result)
        if verbose:
            print(
                f"{result['query']}  "
                f"top1={result['top1_intent'] or '(none)'}  "
                f"first_relevant_rank={result['first_relevant_rank'] or 'miss'}"
            )
    return results, summarize(results, limit)


def print_summary(summary: dict, mode: str) -> None:
    print("=" * 60)
    print(f"检索指标汇总（mode={mode}）")
    print(f"评测问题数：{summary['total_queries']}")
    print(f"候选截断 limit：{summary['limit']}")
    for k, value in summary["recall"].items():
        print(f"Recall{k}: {value:.4f}")
    print(f"MRR: {summary['mrr']:.4f}")
    for k, value in summary["ndcg"].items():
        print(f"NDCG{k}: {value:.4f}")


def save_report(
    results: list[dict],
    summary: dict,
    mode: str,
    limit: int,
    output_dir: str | Path = DEFAULT_REPORT_DIR,
) -> Path:
    created_at = datetime.now().astimezone()
    run_id = created_at.strftime("%Y-%m-%d_%H-%M-%S")
    payload = {
        "run_id": run_id,
        "created_at": created_at.isoformat(timespec="seconds"),
        "script": "scripts/evaluate_retrieval_metrics.py",
        "mode": mode,
        "limit": limit,
        "rag_config": get_rag_config_dict(),
        "summary": summary,
        "cases": results,
    }
    output_path = Path(output_dir) / f"{run_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    counter = 1
    while output_path.exists():
        output_path = Path(output_dir) / f"{run_id}-{counter}.json"
        counter += 1
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute Recall@K / MRR / NDCG for the retrieval eval set.")
    parser.add_argument("--limit", type=int, default=10, help="How many candidates to retrieve per query.")
    parser.add_argument("--rerank-weight", type=float, default=DEFAULT_MODEL_RERANK_WEIGHT)
    parser.add_argument(
        "--mode",
        choices=("hybrid", "vector"),
        default="hybrid",
        help="hybrid: vector + keyword bonus - direction penalty; vector: embedding only.",
    )
    parser.add_argument("--compare-modes", action="store_true", help="Run hybrid and vector-only side by side.")
    parser.add_argument("--save-report", action="store_true", help="Save JSON report under reports/retrieval_metrics/.")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    if args.compare_modes:
        for mode in ("hybrid", "vector"):
            _, summary = run_evaluation(
                limit=args.limit,
                use_hybrid=(mode == "hybrid"),
                rerank_weight=args.rerank_weight,
                verbose=False,
            )
            print_summary(summary, mode)
        return

    results, summary = run_evaluation(
        limit=args.limit,
        use_hybrid=(args.mode == "hybrid"),
        rerank_weight=args.rerank_weight,
    )
    print_summary(summary, args.mode)
    if args.save_report:
        path = save_report(results, summary, args.mode, args.limit)
        print()
        print(f"Saved report: {path}")


if __name__ == "__main__":
    main()
