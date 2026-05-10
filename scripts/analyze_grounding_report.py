import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze grounding report bad cases.")
    parser.add_argument("report_path", help="Path to a saved grounding report JSON file.")
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all cases instead of only failed or partial judgments.",
    )
    return parser.parse_args()


def extract_judge_reason(report: dict) -> str:
    notes = report.get("manual_judgment", {}).get("notes", "")
    if "判断理由：" in notes:
        return notes.split("判断理由：", 1)[1].strip()
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
        intents.append("/".join(matched) if matched else "未识别")
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
            category = item.get("category") or "未分类"
            intent = item.get("intent") or "未标注"
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


def print_case(index: int, report: dict) -> None:
    judgment = report.get("manual_judgment", {})
    print("=" * 80)
    print(f"Case {index}: {report.get('query', '')}")
    print(
        "scores: "
        f"direct_answer={judgment.get('direct_answer', '')}, "
        f"grounded={judgment.get('grounded', '')}, "
        f"useful={judgment.get('useful', '')}"
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

    print(f"run_id: {payload.get('run_id', '')}")
    print(f"total_cases: {len(reports)}")
    print(f"shown_cases: {len(selected_reports)}")
    print(f"summary: {payload.get('summary', {})}")

    for index, report in enumerate(selected_reports, start=1):
        print_case(index, report)

    return {
        "run_id": payload.get("run_id", ""),
        "total_cases": len(reports),
        "shown_cases": len(selected_reports),
    }


def main() -> None:
    args = parse_args()
    analyze_report(args.report_path, show_all=args.show_all)


if __name__ == "__main__":
    main()
