import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze retrieval evaluation reports.")
    parser.add_argument("report_path", help="Path to a saved retrieval evaluation JSON file.")
    parser.add_argument(
        "--compare-to",
        help="Path to another retrieval evaluation JSON file for comparison.",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all cases instead of only bad or changed cases.",
    )
    return parser.parse_args()


def load_report(report_path: str | Path) -> dict:
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def format_score(value: object) -> str:
    if isinstance(value, int | float):
        return f"{value:.4f}"
    return ""


def is_bad_case(case: dict) -> bool:
    return not str(case.get("judgement", "")).startswith("Top1")


def classify_bad_case(case: dict) -> str:
    judgement = str(case.get("judgement", ""))
    if judgement.startswith("Top1"):
        return "top1"
    if judgement.startswith("Top3"):
        return "ranking_error"
    return "miss"


def extract_score_breakdown_lines(case: dict) -> list[str]:
    lines = []
    for item in case.get("results", []):
        score_parts = [
            f"score={format_score(item.get('score'))}",
            f"rerank={format_score(item.get('rerank_score'))}",
            f"vector={format_score(item.get('vector_score'))}",
            f"model={format_score(item.get('model_rerank_score'))}",
            f"keyword={format_score(item.get('keyword_bonus'))}",
            f"penalty={format_score(item.get('direction_penalty'))}",
        ]
        scores = ", ".join(part for part in score_parts if not part.endswith("="))
        lines.append(
            f"  #{item.get('rank', '')} {item.get('category', '')}/{item.get('intent', '')} "
            f"| {scores} | question={item.get('question', '')}"
        )
    return lines


def count_bad_case_types(cases: list[dict]) -> dict[str, int]:
    ranking_error_count = sum(
        1 for case in cases
        if is_bad_case(case) and classify_bad_case(case) == "ranking_error"
    )
    miss_count = sum(
        1 for case in cases
        if is_bad_case(case) and classify_bad_case(case) == "miss"
    )
    return {
        "ranking_error_count": ranking_error_count,
        "miss_count": miss_count,
    }


def print_case(index: int, case: dict) -> None:
    print("=" * 80)
    print(f"Case {index}: {case.get('query', '')}")
    print(f"judgement: {case.get('judgement', '')}")
    if is_bad_case(case):
        print(f"bad_case_type: {classify_bad_case(case)}")
    print(f"top_intents: {', '.join(case.get('top_intents', []))}")
    if case.get("error_type"):
        print(f"error_type: {case.get('error_type', '')}")
    if case.get("notes"):
        print(f"notes: {case.get('notes', '')}")
    print("topk_results:")
    for line in extract_score_breakdown_lines(case):
        print(line)


def print_report_header(payload: dict, shown_cases: int) -> dict[str, int]:
    cases = payload.get("cases", [])
    summary = payload.get("summary", {})
    rag_config = payload.get("rag_config", {})
    bad_case_counts = count_bad_case_types(cases)

    print(f"run_id: {payload.get('run_id', '')}")
    print(f"rerank_weight: {payload.get('rerank_weight', '')}")
    print(f"rag_config: {rag_config}")
    print(
        "summary: "
        f"Top1={summary.get('top1', 0)}, "
        f"Top3={summary.get('top3_error', 0)}, "
        f"miss={summary.get('miss', 0)}"
    )
    print(f"total_cases: {len(cases)}")
    print(f"shown_cases: {shown_cases}")
    print(f"ranking_error_count: {bad_case_counts['ranking_error_count']}")
    print(f"miss_count: {bad_case_counts['miss_count']}")
    return bad_case_counts


def analyze_report(report_path: str | Path, show_all: bool = False) -> dict:
    payload = load_report(report_path)
    cases = payload.get("cases", [])
    selected_cases = [case for case in cases if show_all or is_bad_case(case)]
    bad_case_counts = print_report_header(payload, shown_cases=len(selected_cases))

    for index, case in enumerate(selected_cases, start=1):
        print_case(index, case)

    return {
        "run_id": payload.get("run_id", ""),
        "total_cases": len(cases),
        "shown_cases": len(selected_cases),
        "ranking_error_count": bad_case_counts["ranking_error_count"],
        "miss_count": bad_case_counts["miss_count"],
    }


def compare_configs(base_payload: dict, target_payload: dict) -> dict[str, dict[str, object]]:
    base_config = base_payload.get("rag_config", {})
    target_config = target_payload.get("rag_config", {})
    changed_keys = sorted(set(base_config) | set(target_config))
    return {
        key: {
            "base": base_config.get(key),
            "target": target_config.get(key),
        }
        for key in changed_keys
        if base_config.get(key) != target_config.get(key)
    }


def build_case_map(payload: dict) -> dict[str, dict]:
    return {
        case.get("query", ""): case
        for case in payload.get("cases", [])
    }


def classify_transition(base_case: dict, target_case: dict) -> str:
    base_type = classify_bad_case(base_case)
    target_type = classify_bad_case(target_case)
    order = {"top1": 0, "ranking_error": 1, "miss": 2}
    if order[target_type] < order[base_type]:
        return "improved"
    if order[target_type] > order[base_type]:
        return "worsened"
    return "unchanged"


def build_case_diff(query: str, base_case: dict, target_case: dict) -> dict:
    return {
        "query": query,
        "base_judgement": base_case.get("judgement", ""),
        "target_judgement": target_case.get("judgement", ""),
        "base_type": classify_bad_case(base_case),
        "target_type": classify_bad_case(target_case),
        "transition": classify_transition(base_case, target_case),
        "base_case": base_case,
        "target_case": target_case,
    }


def compare_reports(
    base_report_path: str | Path,
    target_report_path: str | Path,
    show_all: bool = False,
) -> dict:
    base_payload = load_report(base_report_path)
    target_payload = load_report(target_report_path)
    base_case_map = build_case_map(base_payload)
    target_case_map = build_case_map(target_payload)
    shared_queries = sorted(set(base_case_map) & set(target_case_map))
    diffs = [
        build_case_diff(query, base_case_map[query], target_case_map[query])
        for query in shared_queries
    ]
    selected_diffs = [
        diff for diff in diffs
        if show_all or diff["transition"] != "unchanged"
    ]
    config_diff = compare_configs(base_payload, target_payload)
    improved = [diff for diff in diffs if diff["transition"] == "improved"]
    worsened = [diff for diff in diffs if diff["transition"] == "worsened"]
    unchanged = [diff for diff in diffs if diff["transition"] == "unchanged"]
    base_bad_case_counts = count_bad_case_types(base_payload.get("cases", []))
    target_bad_case_counts = count_bad_case_types(target_payload.get("cases", []))

    print("BASE REPORT")
    print("-" * 80)
    print_report_header(base_payload, shown_cases=0)
    print()
    print("TARGET REPORT")
    print("-" * 80)
    print_report_header(target_payload, shown_cases=0)
    print()
    print("CONFIG DIFF")
    print("-" * 80)
    if config_diff:
        for key, values in config_diff.items():
            print(f"{key}: base={values['base']} | target={values['target']}")
    else:
        print("No config changes.")
    print()
    print("COMPARISON SUMMARY")
    print("-" * 80)
    print(f"shared_cases: {len(shared_queries)}")
    print(f"improved_count: {len(improved)}")
    print(f"worsened_count: {len(worsened)}")
    print(f"unchanged_count: {len(unchanged)}")
    print(
        "bad_case_counts: "
        f"base(ranking_error={base_bad_case_counts['ranking_error_count']}, miss={base_bad_case_counts['miss_count']}) "
        f"-> target(ranking_error={target_bad_case_counts['ranking_error_count']}, miss={target_bad_case_counts['miss_count']})"
    )

    for index, diff in enumerate(selected_diffs, start=1):
        print("=" * 80)
        print(f"Changed Case {index}: {diff['query']}")
        print(f"transition: {diff['transition']}")
        print(
            f"status: {diff['base_type']} ({diff['base_judgement']}) "
            f"-> {diff['target_type']} ({diff['target_judgement']})"
        )
        print("BASE topk_results:")
        for line in extract_score_breakdown_lines(diff["base_case"]):
            print(line)
        print("TARGET topk_results:")
        for line in extract_score_breakdown_lines(diff["target_case"]):
            print(line)

    return {
        "base_run_id": base_payload.get("run_id", ""),
        "target_run_id": target_payload.get("run_id", ""),
        "shared_cases": len(shared_queries),
        "shown_cases": len(selected_diffs),
        "improved_count": len(improved),
        "worsened_count": len(worsened),
        "unchanged_count": len(unchanged),
        "config_diff": config_diff,
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
