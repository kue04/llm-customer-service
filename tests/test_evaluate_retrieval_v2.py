import json
from collections import Counter

import pytest

from scripts.evaluate_retrieval_v2 import build_evaluation_summary, calculate_metrics
from services.release_check_service import build_retrieval_v2_status


def test_calculate_metrics_reports_recall_mrr_ndcg_and_latency():
    metrics = calculate_metrics(
        [
            {"rank": 1, "latency_ms": 10.0},
            {"rank": 2, "latency_ms": 20.0},
            {"rank": None, "latency_ms": 30.0},
        ]
    )

    assert metrics["recall_at_1"] == pytest.approx(1 / 3)
    assert metrics["recall_at_3"] == pytest.approx(2 / 3)
    assert metrics["mrr"] == pytest.approx(0.5)
    assert metrics["ndcg_at_5"] > 0
    assert metrics["p50_ms"] == 20.0
    assert metrics["p95_ms"] == 29.0


def test_summary_groups_cases_and_lists_misses():
    summary = build_evaluation_summary(
        [
            {"case_id": "a", "case_type": "baseline", "risk_level": "low", "rank": 1, "latency_ms": 10.0},
            {"case_id": "b", "case_type": "baseline", "risk_level": "high", "rank": None, "latency_ms": 20.0},
        ]
    )
    assert summary["case_type"]["baseline"]["case_count"] == 2
    assert summary["risk_level"]["high"]["recall_at_3"] == 0.0
    assert summary["missed_case_ids"] == ["b"]


def test_dataset_contains_exact_v2_quota():
    rows = [
        json.loads(line)
        for line in open("data/retrieval_eval_v2.jsonl", encoding="utf-8")
        if line.strip()
    ]
    assert len(rows) == 120
    assert Counter(row["case_type"] for row in rows) == {
        "baseline": 20,
        "paraphrase": 20,
        "typo_colloquial": 20,
        "direction_conflict": 20,
        "multi_intent": 20,
        "high_risk": 20,
    }


def test_release_gate_surfaces_failed_retrieval_check():
    status = build_retrieval_v2_status(
        {
            "results": {
                "dense_only": {"overall": {"mrr": 0.8, "p95_ms": 5.0}},
                "hybrid_rerank": {"overall": {"recall_at_3": 0.96, "mrr": 0.9, "p95_ms": 100.0}},
            },
            "release_gate": {"passed": False, "checks": {"p95_latency": False}},
        }
    )
    assert status["status"] == "fail"
    assert "failed=p95_latency" in status["evidence"]
