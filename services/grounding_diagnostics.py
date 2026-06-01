from scripts.analyze_grounding_report import (
    classify_grounding_issue,
    summarize_evaluation_metrics,
    suggest_fix_layer,
)
from scripts.evaluate_chat_grounding import (
    EVALUATION_CASE_METADATA,
    EVALUATION_QUERIES,
    build_grounding_report,
)


def _build_case_metadata_by_query() -> dict[str, dict]:
    return {
        query: EVALUATION_CASE_METADATA[index]
        for index, query in enumerate(EVALUATION_QUERIES)
        if index < len(EVALUATION_CASE_METADATA)
    }


CASE_METADATA_BY_QUERY = _build_case_metadata_by_query()


def _build_fallback_metadata(retrieved_items: list[dict]) -> dict:
    if not retrieved_items:
        return {}
    return {
        "expected_intent": retrieved_items[0].get("intent", ""),
        "expected_evidence_keywords": [],
        "forbidden_keywords": [],
    }


def build_chat_grounding_diagnostics(
    query: str,
    reply: str,
    retrieved_documents: list[str],
    retrieved_items: list[dict],
    prompt_context_items: list[dict],
    final_prompt: str,
) -> dict:
    metadata = CASE_METADATA_BY_QUERY.get(query) or _build_fallback_metadata(retrieved_items)
    report = build_grounding_report(
        query=query,
        retrieved_documents=retrieved_documents,
        reply=reply,
        retrieved_items=retrieved_items,
        final_prompt=final_prompt,
        prompt_context_items=prompt_context_items,
        expected_intent=metadata.get("expected_intent", ""),
        expected_evidence_keywords=metadata.get("expected_evidence_keywords", []),
        forbidden_keywords=metadata.get("forbidden_keywords", []),
    )
    report["issue_type"] = classify_grounding_issue(report)
    report["suggested_layer"] = suggest_fix_layer(report)
    report["evaluation_metrics"] = summarize_evaluation_metrics([report])
    report["evaluation_metrics"]["judge_pass_count"] = None
    report["evaluation_metrics"]["judge_pass_rate"] = None
    report["evaluation_metrics"]["judge_status"] = "not_run"
    return report
