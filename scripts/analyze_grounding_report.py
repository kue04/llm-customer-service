import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze grounding report bad cases.")
    parser.add_argument("report_path", help="Path to a saved grounding report JSON file.")
    parser.add_argument(
        "--compare-to",
        help="Path to another grounding report JSON file for comparison.",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all cases instead of only failed or partial judgments.",
    )
    return parser.parse_args()


def extract_judge_reason(report: dict) -> str:
    notes = report.get("manual_judgment", {}).get("notes", "")
    marker = "判断理由："
    if marker in notes:
        return notes.split(marker, 1)[1].strip()
    return notes.strip()


def extract_retrieved_intents(report: dict) -> list[str]:
    intents = []
    for document in report.get("retrieved_documents", []):
        matched = []
        for keyword in [
            "退款",
            "超时",
            "食品安全",
            "异物",
            "私下转账",
            "优惠券",
            "骑手",
            "手机号",
            "撒漏",
            "商家拒绝",
        ]:
            if keyword in document:
                matched.append(keyword)
        intents.append("/".join(matched) if matched else "unrecognized")
    return intents


def format_score(value: object) -> str:
    if isinstance(value, int | float):
        return f"{value:.4f}"
    return ""


def extract_retrieved_metadata_lines(report: dict) -> list[str]:
    retrieved_items = report.get("retrieved_items") or []
    if retrieved_items:
        lines = []
        for index, item in enumerate(retrieved_items, start=1):
            rank = item.get("rank") or index
            category = item.get("category") or "unknown_category"
            intent = item.get("intent") or "unknown_intent"
            question = item.get("question") or ""
            score_parts = [
                f"score={format_score(item.get('score'))}",
                f"vector={format_score(item.get('vector_score'))}",
                f"rerank={format_score(item.get('rerank_score'))}",
                f"keyword={format_score(item.get('keyword_bonus'))}",
                f"penalty={format_score(item.get('direction_penalty'))}",
            ]
            scores = ", ".join(part for part in score_parts if not part.endswith("="))
            lines.append(
                f"  #{rank} {category}/{intent} | {scores} | question={question}"
            )
        return lines

    return [
        f"  #{index} guessed_intent={intent}"
        for index, intent in enumerate(extract_retrieved_intents(report), start=1)
    ]


def is_bad_case(report: dict) -> bool:
    judgment = report.get("manual_judgment", {})
    return any(
        judgment.get(field) != "yes"
        for field in ["direct_answer", "grounded", "useful"]
    )


def classify_grounding_issue(report: dict) -> str:
    judgment = report.get("manual_judgment", {})
    retrieved_items = report.get("retrieved_items") or []
    risky_promises = report.get("risky_promises") or []

    if not retrieved_items:
        return "retrieval_bad"
    if risky_promises:
        return "safety_overclaim"
    if judgment.get("grounded") == "no":
        return "generation_not_grounded"
    if judgment.get("direct_answer") in {"no", "partial"}:
        return "generation_not_direct"
    if judgment.get("grounded") == "partial":
        return "generation_not_grounded"
    return "judge_or_other"


def suggest_fix_layer(report: dict) -> str:
    judgment = report.get("manual_judgment", {})
    retrieved_items = report.get("retrieved_items") or []

    if report.get("judge_status") == "failed":
        return "judge"
    if not is_bad_case(report):
        return "pass"
    if not retrieved_items:
        return "retrieval"
    if not report.get("final_prompt"):
        return "context_builder"
    if report.get("mixed_supporting_intent"):
        return "context_builder_or_reply_rules"
    if report.get("risky_promises"):
        return "reply_rules"
    if report.get("used_primary_evidence") and judgment.get("grounded") == "no":
        return "judge"
    if not report.get("used_primary_evidence"):
        return "generation_or_reply_rules"
    if judgment.get("direct_answer") in {"no", "partial"}:
        return "generation_or_reply_rules"
    return "judge"


def summarize_suggested_layers(reports: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for report in reports:
        layer = suggest_fix_layer(report)
        counts[layer] = counts.get(layer, 0) + 1
    return counts


def summarize_judge_failures(reports: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {
        "empty_response": 0,
        "not_json": 0,
        "missing_field": 0,
        "invalid_enum": 0,
        "empty_reason": 0,
        "other": 0,
    }
    for report in reports:
        if report.get("judge_status") != "failed":
            continue
        failure_type = report.get("judge_failure_type") or "other"
        if failure_type not in counts:
            failure_type = "other"
        counts[failure_type] += 1
    return counts


def summarize_issue_types(reports: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for report in reports:
        if not is_bad_case(report):
            continue
        issue_type = classify_grounding_issue(report)
        counts[issue_type] = counts.get(issue_type, 0) + 1
    return counts


def calculate_rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def get_top1_intent(report: dict) -> str:
    retrieved_items = report.get("retrieved_items") or []
    if not retrieved_items:
        return ""
    return str(retrieved_items[0].get("intent", ""))


def retrieved_documents_contain_keyword(report: dict, keyword: str) -> bool:
    documents = report.get("retrieved_documents") or []
    return any(keyword in document for document in documents)


def classify_failure_attribution(report: dict) -> str:
    if not is_bad_case(report):
        return "pass"
    if report.get("judge_status") == "failed":
        return "judge"

    expected_intent = report.get("expected_intent") or ""
    if not report.get("retrieved_items"):
        return "retrieval_failure"
    if expected_intent and get_top1_intent(report) != expected_intent:
        return "retrieval_failure"

    missing_keywords = report.get("missing_evidence_keywords") or []
    missing_from_evidence = [
        keyword
        for keyword in missing_keywords
        if not retrieved_documents_contain_keyword(report, keyword)
    ]
    if missing_from_evidence:
        return "evidence_insufficient"

    judgment = report.get("manual_judgment", {})
    if judgment.get("grounded") in {"no", "partial"}:
        return "generation_not_using_evidence"
    if judgment.get("direct_answer") in {"no", "partial"}:
        return "generation_not_using_evidence"
    if report.get("risky_promises"):
        return "generation_not_using_evidence"
    return "judge_or_other"


def classify_generation_sub_attribution(report: dict) -> str:
    if classify_failure_attribution(report) != "generation_not_using_evidence":
        return ""

    judgment = report.get("manual_judgment", {})
    judge_reason = extract_judge_reason(report)
    reply = report.get("reply") or ""
    missing_keywords = report.get("missing_evidence_keywords") or []

    if (
        judgment.get("direct_answer") == "yes"
        and judgment.get("useful") == "yes"
        and judgment.get("grounded") == "no"
    ):
        return "judge_too_strict"

    if judgment.get("direct_answer") in {"no", "partial"} and judgment.get(
        "grounded"
    ) in {"yes", "partial"}:
        return "reply_not_direct_enough"

    if missing_keywords and any(keyword in reply for keyword in missing_keywords):
        return "evidence_wording_mismatch"

    if "参考文档没有明确" in judge_reason or "参考文档未明确" in judge_reason:
        return "evidence_wording_mismatch"

    if "未明确" in judge_reason:
        return "reply_missing_required_step"

    return "reply_not_direct_enough"


def summarize_failure_attributions(reports: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for report in reports:
        attribution = classify_failure_attribution(report)
        counts[attribution] = counts.get(attribution, 0) + 1
    return counts


def summarize_generation_sub_attributions(reports: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for report in reports:
        attribution = classify_generation_sub_attribution(report)
        if not attribution:
            continue
        counts[attribution] = counts.get(attribution, 0) + 1
    return counts


def build_generation_sub_attribution_table(reports: list[dict]) -> list[dict]:
    table = []
    for attribution, action in [
        ("judge_too_strict", "先复核 judge 标尺或 judge prompt"),
        ("reply_not_direct_enough", "优化回答开头，让结论更直接"),
        ("reply_missing_required_step", "补 answer plan 的必要步骤约束"),
        ("evidence_wording_mismatch", "补证据表述或放宽 judge 对同义表达的要求"),
    ]:
        cases = [
            report.get("query", "")
            for report in reports
            if classify_generation_sub_attribution(report) == attribution
        ]
        if cases:
            table.append(
                {
                    "attribution": attribution,
                    "count": len(cases),
                    "action": action,
                    "cases": cases,
                }
            )
    return table


def build_failure_attribution_table(reports: list[dict]) -> list[dict]:
    table = []
    for attribution, action in [
        ("retrieval_failure", "改召回、重排、query rewrite 或意图规则"),
        ("evidence_insufficient", "补知识库证据，别先改 prompt"),
        ("generation_not_using_evidence", "改生成 prompt、answer plan 或 reply rules"),
        ("judge", "修 judge 输出或判分标准"),
        ("judge_or_other", "人工复核，先别盲改链路"),
    ]:
        cases = [
            report.get("query", "")
            for report in reports
            if classify_failure_attribution(report) == attribution
        ]
        if cases:
            table.append(
                {
                    "attribution": attribution,
                    "count": len(cases),
                    "action": action,
                    "cases": cases,
                }
            )
    return table


def summarize_evaluation_metrics(reports: list[dict]) -> dict:
    total_cases = len(reports)
    expected_intent_cases = [
        report for report in reports if report.get("expected_intent")
    ]
    top1_intent_hit_count = sum(
        1
        for report in expected_intent_cases
        if get_top1_intent(report) == report.get("expected_intent")
    )

    expected_keyword_count = sum(
        len(report.get("expected_evidence_keywords") or [])
        for report in reports
    )
    matched_keyword_count = sum(
        len(report.get("matched_evidence_keywords") or [])
        for report in reports
    )
    forbidden_hit_count = sum(
        len(report.get("forbidden_keyword_hits") or [])
        for report in reports
    )
    judge_pass_count = sum(
        1
        for report in reports
        if report.get("manual_judgment", {}).get("direct_answer") == "yes"
        and report.get("manual_judgment", {}).get("grounded") == "yes"
        and report.get("manual_judgment", {}).get("useful") == "yes"
    )

    return {
        "total_cases": total_cases,
        "expected_intent_case_count": len(expected_intent_cases),
        "top1_intent_hit_count": top1_intent_hit_count,
        "top1_intent_hit_rate": calculate_rate(
            top1_intent_hit_count,
            len(expected_intent_cases),
        ),
        "matched_evidence_keyword_count": matched_keyword_count,
        "expected_evidence_keyword_count": expected_keyword_count,
        "evidence_keyword_coverage": calculate_rate(
            matched_keyword_count,
            expected_keyword_count,
        ),
        "forbidden_hit_count": forbidden_hit_count,
        "judge_pass_count": judge_pass_count,
        "judge_pass_rate": calculate_rate(judge_pass_count, total_cases),
    }


def print_case(index: int, report: dict) -> None:
    judgment = report.get("manual_judgment", {})
    print("=" * 80)
    print(f"Case {index}: {report.get('query', '')}")
    print(f"issue_type: {classify_grounding_issue(report)}")
    print(f"suggested_layer: {suggest_fix_layer(report)}")
    if report.get("expected_intent"):
        print(f"expected_intent: {report.get('expected_intent', '')}")
    if report.get("missing_evidence_keywords"):
        print(f"missing_evidence_keywords: {report.get('missing_evidence_keywords', [])}")
    if report.get("forbidden_keyword_hits"):
        print(f"forbidden_keyword_hits: {report.get('forbidden_keyword_hits', [])}")
    print(
        "scores: "
        f"direct_answer={judgment.get('direct_answer', '')}, "
        f"grounded={judgment.get('grounded', '')}, "
        f"useful={judgment.get('useful', '')}"
    )
    trace = report.get("trace") or {}
    print(
        "trace: "
        f"answer_source={trace.get('answer_source', '')}, "
        f"reply_rules_applied={trace.get('reply_rules_applied', '')}, "
        f"failure_stage={trace.get('failure_stage', '')}"
    )
    retrieval_trace = trace.get("retrieval_trace") or {}
    if retrieval_trace:
        print(
            "retrieval_trace: "
            f"intent_hint={retrieval_trace.get('intent_hint', '')}, "
            f"intent_hint_applied={retrieval_trace.get('intent_hint_applied', '')}, "
            f"hint_supplement_added={retrieval_trace.get('hint_supplement_added', '')}, "
            f"hint_supplement_count={retrieval_trace.get('hint_supplement_count', '')}, "
            f"hint_match_count={retrieval_trace.get('hint_match_count', '')}"
        )
    reply_rules_trace = trace.get("reply_rules_trace") or {}
    print(
        "reply_rule: "
        f"matched={reply_rules_trace.get('matched', '')}, "
        f"rule_index={reply_rules_trace.get('rule_index', '')}, "
        f"matched_keyword={reply_rules_trace.get('matched_keyword', '')}, "
        f"primary_intent={reply_rules_trace.get('primary_intent', '')}"
    )
    print("retrieved_items:")
    for line in extract_retrieved_metadata_lines(report):
        print(line)
    print(f"reply: {report.get('reply', '')}")
    print(f"judge_reason: {extract_judge_reason(report)}")


def analyze_report(report_path: str | Path, show_all: bool = False) -> dict:
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    reports = payload.get("reports", [])
    selected_reports = [
        report
        for report in reports
        if show_all or is_bad_case(report)
    ]
    issue_counts = summarize_issue_types(reports)
    suggested_layer_counts = summarize_suggested_layers(reports)
    evaluation_metrics = summarize_evaluation_metrics(reports)

    print(f"run_id: {payload.get('run_id', '')}")
    print(f"total_cases: {len(reports)}")
    print(f"shown_cases: {len(selected_reports)}")
    print(f"summary: {payload.get('summary', {})}")
    print(f"evaluation_metrics: {evaluation_metrics}")
    print(f"issue_type_counts: {issue_counts}")
    print(f"suggested_layer_counts: {suggested_layer_counts}")
    print(f"failure_attribution_counts: {summarize_failure_attributions(reports)}")
    print(
        "generation_sub_attribution_counts: "
        f"{summarize_generation_sub_attributions(reports)}"
    )
    print("failure_attribution_table:")
    for row in build_failure_attribution_table(reports):
        preview_cases = " | ".join(row["cases"][:3])
        if len(row["cases"]) > 3:
            preview_cases = f"{preview_cases} | ..."
        print(
            f"  {row['attribution']}: count={row['count']} "
            f"action={row['action']} cases={preview_cases}"
        )
    print("generation_sub_attribution_table:")
    for row in build_generation_sub_attribution_table(reports):
        preview_cases = " | ".join(row["cases"][:3])
        if len(row["cases"]) > 3:
            preview_cases = f"{preview_cases} | ..."
        print(
            f"  {row['attribution']}: count={row['count']} "
            f"action={row['action']} cases={preview_cases}"
        )
    if payload.get("summary", {}).get("judge_failure_type_counts") is not None:
        print(f"judge_failure_type_counts: {payload['summary']['judge_failure_type_counts']}")
    else:
        print(f"judge_failure_type_counts: {summarize_judge_failures(reports)}")

    for index, report in enumerate(selected_reports, start=1):
        print_case(index, report)

    return {
        "run_id": payload.get("run_id", ""),
        "total_cases": len(reports),
        "shown_cases": len(selected_reports),
        "issue_type_counts": issue_counts,
        "failure_attribution_counts": summarize_failure_attributions(reports),
        "failure_attribution_table": build_failure_attribution_table(reports),
        "generation_sub_attribution_counts": summarize_generation_sub_attributions(
            reports
        ),
        "generation_sub_attribution_table": build_generation_sub_attribution_table(
            reports
        ),
        "evaluation_metrics": evaluation_metrics,
    }


def build_case_map(payload: dict) -> dict[str, dict]:
    return {
        report.get("query", ""): report
        for report in payload.get("reports", [])
    }


def classify_judgment_transition(base_report: dict, target_report: dict) -> str:
    base_judgment = base_report.get("manual_judgment", {})
    target_judgment = target_report.get("manual_judgment", {})
    base_pass = (
        base_judgment.get("direct_answer") == "yes"
        and base_judgment.get("grounded") == "yes"
        and base_judgment.get("useful") == "yes"
    )
    target_pass = (
        target_judgment.get("direct_answer") == "yes"
        and target_judgment.get("grounded") == "yes"
        and target_judgment.get("useful") == "yes"
    )
    if base_pass and not target_pass:
        return "worsened"
    if not base_pass and target_pass:
        return "improved"
    if base_judgment == target_judgment:
        return "unchanged"
    return "changed"


def compare_reports(
    base_report_path: str | Path,
    target_report_path: str | Path,
    show_all: bool = False,
) -> dict:
    base_payload = json.loads(Path(base_report_path).read_text(encoding="utf-8"))
    target_payload = json.loads(Path(target_report_path).read_text(encoding="utf-8"))
    base_reports = base_payload.get("reports", [])
    target_reports = target_payload.get("reports", [])
    base_case_map = build_case_map(base_payload)
    target_case_map = build_case_map(target_payload)
    shared_queries = sorted(set(base_case_map) & set(target_case_map))

    diffs = [
        {
            "query": query,
            "transition": classify_judgment_transition(
                base_case_map[query],
                target_case_map[query],
            ),
            "base_case": base_case_map[query],
            "target_case": target_case_map[query],
        }
        for query in shared_queries
    ]
    selected_diffs = [
        diff for diff in diffs
        if show_all or diff["transition"] != "unchanged"
    ]
    base_metrics = summarize_evaluation_metrics(base_reports)
    target_metrics = summarize_evaluation_metrics(target_reports)

    print("BASE REPORT")
    print("-" * 80)
    print(f"run_id: {base_payload.get('run_id', '')}")
    print(f"summary: {base_payload.get('summary', {})}")
    print(f"evaluation_metrics: {base_metrics}")
    print()
    print("TARGET REPORT")
    print("-" * 80)
    print(f"run_id: {target_payload.get('run_id', '')}")
    print(f"summary: {target_payload.get('summary', {})}")
    print(f"evaluation_metrics: {target_metrics}")
    print()
    print("COMPARISON SUMMARY")
    print("-" * 80)
    print(f"shared_cases: {len(shared_queries)}")
    print(
        "judge_pass_rate: "
        f"{base_metrics['judge_pass_rate']} -> {target_metrics['judge_pass_rate']}"
    )
    print(
        "forbidden_hit_count: "
        f"{base_metrics['forbidden_hit_count']} -> {target_metrics['forbidden_hit_count']}"
    )
    print(f"improved_count: {sum(1 for diff in diffs if diff['transition'] == 'improved')}")
    print(f"worsened_count: {sum(1 for diff in diffs if diff['transition'] == 'worsened')}")
    print(f"shown_cases: {len(selected_diffs)}")

    for index, diff in enumerate(selected_diffs, start=1):
        print("=" * 80)
        print(f"Changed Case {index}: {diff['query']}")
        print(f"transition: {diff['transition']}")
        print("BASE reply:")
        print(diff["base_case"].get("reply", ""))
        print("TARGET reply:")
        print(diff["target_case"].get("reply", ""))
        print("BASE judge_reason:")
        print(extract_judge_reason(diff["base_case"]))
        print("TARGET judge_reason:")
        print(extract_judge_reason(diff["target_case"]))

    return {
        "base_run_id": base_payload.get("run_id", ""),
        "target_run_id": target_payload.get("run_id", ""),
        "shared_cases": len(shared_queries),
        "shown_cases": len(selected_diffs),
        "improved_count": sum(1 for diff in diffs if diff["transition"] == "improved"),
        "worsened_count": sum(1 for diff in diffs if diff["transition"] == "worsened"),
        "base_metrics": base_metrics,
        "target_metrics": target_metrics,
    }


def main() -> None:
    args = parse_args()
    if args.compare_to:
        compare_reports(
            args.report_path,
            args.compare_to,
            show_all=args.show_all,
        )
        return
    analyze_report(args.report_path, show_all=args.show_all)


if __name__ == "__main__":
    main()
