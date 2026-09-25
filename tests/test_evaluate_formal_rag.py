from scripts.evaluate_formal_rag import aggregate_formal_report


def test_aggregate_formal_report_keeps_index_and_route_metrics():
    report = aggregate_formal_report(
        {"index": {"index_version": 3}, "datasets": {"title": {"metrics": {"hybrid": {"mrr": 0.8}}}}},
        {"run_id": "g1", "summary": {"route_counts": {"clarify": 2}, "clarify_count": 2, "human_handoff_count": 1}},
    )
    assert report["retrieval_path"] == "chunk-index"
    assert report["index"]["index_version"] == 3
    assert report["retrieval"]["metrics"]["title"]["hybrid"]["mrr"] == 0.8
    assert report["evidence_and_grounding"]["clarify_count"] == 2
    assert report["evidence_and_grounding"]["human_handoff_count"] == 1
