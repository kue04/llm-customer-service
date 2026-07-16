import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "retrieval_eval_v2.jsonl"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "reports" / "retrieval_v2.json"
ABLATIONS = {
    "dense_only": {"use_hybrid": False, "apply_rules": False, "apply_reranker": False},
    "dense_rules": {"use_hybrid": False, "apply_rules": True, "apply_reranker": False},
    "dense_rerank": {"use_hybrid": False, "apply_rules": False, "apply_reranker": True},
    "hybrid_no_rerank": {"use_hybrid": True, "apply_rules": True, "apply_reranker": False},
    "hybrid_rerank": {"use_hybrid": True, "apply_rules": True, "apply_reranker": True},
}


def calculate_metrics(records: list[dict]) -> dict:
    if not records:
        return {
            "case_count": 0,
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "mrr": 0.0,
            "ndcg_at_5": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
        }
    ranks = [record.get("rank") for record in records]
    latencies = [float(record.get("latency_ms") or 0.0) for record in records]
    count = len(records)
    return {
        "case_count": count,
        "recall_at_1": sum(rank == 1 for rank in ranks) / count,
        "recall_at_3": sum(rank is not None and rank <= 3 for rank in ranks) / count,
        "recall_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / count,
        "mrr": sum(1.0 / rank for rank in ranks if rank) / count,
        "ndcg_at_5": sum(
            1.0 / np.log2(rank + 1) for rank in ranks if rank is not None and rank <= 5
        ) / count,
        "p50_ms": float(np.percentile(latencies, 50)),
        "p95_ms": float(np.percentile(latencies, 95)),
    }


def build_evaluation_summary(records: list[dict]) -> dict:
    by_case_type: dict[str, list[dict]] = defaultdict(list)
    by_risk_level: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_case_type[str(record.get("case_type") or "unknown")].append(record)
        by_risk_level[str(record.get("risk_level") or "unknown")].append(record)
    return {
        "overall": calculate_metrics(records),
        "case_type": {name: calculate_metrics(items) for name, items in sorted(by_case_type.items())},
        "risk_level": {name: calculate_metrics(items) for name, items in sorted(by_risk_level.items())},
        "missed_case_ids": [record["case_id"] for record in records if record.get("rank") is None],
    }


def load_cases(path: Path = DEFAULT_DATASET_PATH) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def expected_rank(case: dict, results: list[dict]) -> int | None:
    expected = set(case.get("expected_intents") or [case.get("expected_intent")])
    for rank, result in enumerate(results, start=1):
        if result.get("source", {}).get("intent") in expected:
            return rank
    return None


def evaluate_configuration(cases: list[dict], config_name: str) -> dict:
    from utils.vector_retriever import retrieve_by_real_vector

    options = ABLATIONS[config_name]
    records = []
    for case in cases:
        started_at = time.perf_counter()
        results = retrieve_by_real_vector(case["query"], limit=5, **options)
        records.append(
            {
                "case_id": case["id"],
                "case_type": case["case_type"],
                "risk_level": case.get("risk_level", "low"),
                "rank": expected_rank(case, results),
                "latency_ms": (time.perf_counter() - started_at) * 1000,
            }
        )
    return build_evaluation_summary(records)


def build_release_gate(results: dict) -> dict:
    dense = results["dense_only"]["overall"]
    hybrid = results["hybrid_rerank"]["overall"]
    high_risk = results["hybrid_rerank"]["risk_level"].get("high", {})
    checks = {
        "recall_at_3": hybrid["recall_at_3"] >= 0.95,
        "mrr_not_worse": hybrid["mrr"] >= dense["mrr"],
        "high_risk_recall_at_3": high_risk.get("recall_at_3", 0.0) == 1.0,
        "p95_latency": hybrid["p95_ms"] <= dense["p95_ms"] * 1.5,
    }
    return {"passed": all(checks.values()), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--config", choices=["all", *ABLATIONS], default="all")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    config_names = list(ABLATIONS) if args.config == "all" else [args.config]
    results = {name: evaluate_configuration(cases, name) for name in config_names}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "results": results,
    }
    if args.config == "all":
        report["release_gate"] = build_release_gate(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
