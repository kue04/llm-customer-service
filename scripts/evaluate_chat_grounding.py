import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "chat_grounding"

RISKY_PROMISE_TERMS = [
    "一定",
    "补偿",
    "保证",
    "必赔",
    "百分百",
]

REVIEW_QUESTIONS = [
    "回复是否直接回答了用户问题？",
    "回复中的关键说法是否能被参考资料支撑？",
    "回复是否给出了安全、可执行的下一步？",
]

JUDGE_SCORE_VALUES = {"yes", "partial", "no"}
JUDGE_REQUIRED_FIELDS = [
    "direct_answer",
    "grounded",
    "useful",
    "risk_notes",
    "reason",
]

EVALUATION_QUERIES = [
    "退款多久到账",
    "外卖超时了怎么办",
    "餐品有异物可以赔吗",
    "骑手让我私下转配送费可以吗",
    "商家拒绝退款怎么办",
    "优惠券为什么不能用",
    "骑手联系不上怎么办",
    "订单取消后钱多久退回来",
    "平台客服可以看我的手机号吗",
    "餐洒了怎么申请售后",
]

EVALUATION_CASES = [
    {
        "query": "退款多久到账",
        "retrieved_documents": [
            "退款到账时间取决于支付渠道，平台审核通过后通常会原路退回。",
        ],
        "reply": "退款通常会原路退回，具体到账时间建议以支付渠道和订单页面展示为准。",
    },
    {
        "query": "外卖超时了怎么办",
        "retrieved_documents": [
            "配送超时时，用户可以在订单详情页查看骑手位置或联系平台客服协助处理。",
        ],
        "reply": "建议您先在订单详情页查看配送进度，也可以联系平台客服协助催促。",
    },
    {
        "query": "餐品有异物可以赔吗",
        "retrieved_documents": [
            "餐品异常需要用户保留照片、包装和订单信息，平台会根据核实结果处理。",
        ],
        "reply": "平台一定会补偿您，并保证百分百赔付。",
    },
    {
        "query": "骑手让我私下转配送费可以吗",
        "retrieved_documents": [
            "平台不建议用户与骑手私下转账或脱离平台支付。如遇额外收费要求，应保留证据并联系平台客服处理。",
        ],
        "reply": "不建议您私下转账，请保留聊天记录，并通过订单页面联系平台客服处理。",
    },
]


def find_risky_promises(reply: str) -> list[str]:
    return [term for term in RISKY_PROMISE_TERMS if term in reply]


def build_empty_manual_judgment() -> dict:
    return {
        "direct_answer": "",
        "grounded": "",
        "useful": "",
        "notes": "",
    }


def build_grounding_report(
    query: str,
    retrieved_documents: list[str],
    reply: str,
    retrieved_items: list[dict] | None = None,
) -> dict:
    risky_promises = find_risky_promises(reply)

    return {
        "query": query,
        "retrieved_documents": retrieved_documents,
        "retrieved_items": retrieved_items or [],
        "retrieved_document_count": len(retrieved_documents),
        "reply": reply,
        "risky_promises": risky_promises,
        "needs_manual_review": bool(risky_promises),
        "review_questions": REVIEW_QUESTIONS,
        "manual_judgment": build_empty_manual_judgment(),
    }


def build_grounding_reports(cases: list[dict]) -> list[dict]:
    return [
        build_grounding_report(
            query=case["query"],
            retrieved_documents=case["retrieved_documents"],
            reply=case["reply"],
            retrieved_items=case.get("retrieved_items", []),
        )
        for case in cases
    ]


def build_grounding_reports_from_rag(
    queries: list[str],
    answer_provider,
) -> list[dict]:
    reports = []
    for query in queries:
        answer = answer_provider(query)
        reports.append(
            build_grounding_report(
                query=query,
                retrieved_documents=answer.get("retrieved_documents", []),
                reply=answer["reply"],
                retrieved_items=answer.get("retrieved_items", []),
            )
        )
    return reports


def build_judge_prompt(report: dict) -> str:
    documents_text = "\n".join(
        f"{index}. {document}"
        for index, document in enumerate(report["retrieved_documents"], start=1)
    )

    return f"""你是一个 RAG 客服回答评估员。
请只根据用户问题、参考资料和客服回复进行判断，不要引入外部知识。

评估标准：
1. direct_answer：客服回复是否直接回答了用户问题。
   如果用户问题包含“可以吗”或“能不能”，回复必须明确回答“可以”“不建议”“不可以”或“需要平台核实”，否则 direct_answer 应为 partial 或 no。
   如果用户问题包含“赔付”“索赔”或“可以赔吗”，回复必须明确说明“可以申请售后/赔付核实，但是否赔付需平台根据证据和规则判断”。
   如果用户问题是赔付类问题，回复只说“提交投诉”或“提交售后”，但没有说明可以申请或需要平台核实赔付，direct_answer 应为 partial 或 no。
   如果用户问题包含“怎么办”“可以吗”“能不能”或“多久到账”，但回复只给泛泛建议，没有回答核心问题，direct_answer 应为 partial。
2. grounded：客服回复中的关键说法是否能被参考资料支撑。
3. useful：客服回复是否给出了安全、可执行的下一步。
4. risk_notes：指出未被资料支撑、过度承诺或可能误导用户的内容；没有则写空字符串。
   risk_notes 只能指出客服回复中存在的问题；不要把参考资料中未被客服回复提到的内容写成风险。
5. reason：用一句话解释你的判断。reason 必须填写，不能为空，不能写空字符串。
   即使三个评分都是 yes，也必须说明“回复直接回答了问题，关键说法能被参考资料支撑，并给出了可执行下一步”。

每个评分字段只能使用 yes | partial | no。

用户问题：{report["query"]}

参考资料：
{documents_text}

客服回复：{report["reply"]}

请只输出 JSON，不要输出 Markdown 或额外解释：
{{
  "direct_answer": "yes | partial | no",
  "grounded": "yes | partial | no",
  "useful": "yes | partial | no",
  "risk_notes": "没有风险则写空字符串；有风险则用一句话说明",
  "reason": "必须用一句中文解释评分理由，不能留空"
}}"""


def parse_judge_response(text: str) -> dict:
    result = json.loads(text)

    for field in JUDGE_REQUIRED_FIELDS:
        if field not in result:
            raise ValueError(f"Judge response missing field: {field}")

    for field in ["direct_answer", "grounded", "useful"]:
        if result[field] not in JUDGE_SCORE_VALUES:
            raise ValueError(f"Invalid judge score for {field}: {result[field]}")

    if not str(result["reason"]).strip():
        raise ValueError("Judge response reason cannot be empty")

    return {
        "direct_answer": result["direct_answer"],
        "grounded": result["grounded"],
        "useful": result["useful"],
        "risk_notes": result["risk_notes"],
        "reason": result["reason"],
    }


def apply_judge_result(report: dict, judge_result: dict) -> dict:
    report["manual_judgment"] = {
        "direct_answer": judge_result["direct_answer"],
        "grounded": judge_result["grounded"],
        "useful": judge_result["useful"],
        "notes": (
            f"风险提示：{judge_result['risk_notes']} "
            f"判断理由：{judge_result['reason']}"
        ),
    }
    return report


def local_judge_provider(prompt: str) -> str:
    from services.chat_service import generate_reply

    return generate_reply(prompt)


def judge_grounding_report(report: dict, judge_provider) -> dict:
    prompt = build_judge_prompt(report)
    judge_text = judge_provider(prompt)
    report["raw_judge_response"] = judge_text
    try:
        judge_result = parse_judge_response(judge_text)
    except (ValueError, TypeError) as error:
        report["judge_status"] = "failed"
        report["judge_error"] = str(error)
        return report

    report["judge_status"] = "succeeded"
    report["judge_error"] = ""
    return apply_judge_result(report, judge_result)


def judge_grounding_reports(reports: list[dict], judge_provider) -> list[dict]:
    return [
        judge_grounding_report(report, judge_provider)
        for report in reports
    ]


def build_empty_score_counts() -> dict:
    return {
        "yes": 0,
        "partial": 0,
        "no": 0,
        "empty": 0,
    }


def summarize_grounding_reports(reports: list[dict]) -> dict:
    judge_status_counts = {
        "succeeded": 0,
        "failed": 0,
        "not_run": 0,
    }
    judgment_counts = {
        "direct_answer": build_empty_score_counts(),
        "grounded": build_empty_score_counts(),
        "useful": build_empty_score_counts(),
    }

    for report in reports:
        judge_status = report.get("judge_status") or "not_run"
        if judge_status not in judge_status_counts:
            judge_status = "not_run"
        judge_status_counts[judge_status] += 1

        manual_judgment = report.get("manual_judgment", {})
        for field, counts in judgment_counts.items():
            score = manual_judgment.get(field) or "empty"
            if score not in counts:
                score = "empty"
            counts[score] += 1

    return {
        "total": len(reports),
        "manual_review_count": sum(
            1 for report in reports if report.get("needs_manual_review")
        ),
        "judge_status_counts": judge_status_counts,
        "judgment_counts": judgment_counts,
    }


def save_reports_to_file(
    reports: list[dict],
    output_dir: str | Path = DEFAULT_REPORT_DIR,
    use_local_judge: bool = False,
) -> Path:
    created_at = datetime.now().astimezone()
    run_id = created_at.strftime("%Y-%m-%d_%H-%M-%S")
    complete_reports = []
    for report in reports:
        complete_report = dict(report)
        complete_report.setdefault("judge_status", "not_run")
        complete_report.setdefault("raw_judge_response", "")
        complete_report.setdefault("judge_error", "")
        complete_report.setdefault("retrieved_items", [])
        complete_reports.append(complete_report)

    payload = {
        "run_id": run_id,
        "created_at": created_at.isoformat(timespec="seconds"),
        "script": "scripts/evaluate_chat_grounding.py",
        "use_local_judge": use_local_judge,
        "report_count": len(complete_reports),
        "summary": summarize_grounding_reports(complete_reports),
        "reports": complete_reports,
    }

    output_path = Path(output_dir) / f"{run_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    counter = 1
    while output_path.exists():
        output_path = Path(output_dir) / f"{run_id}-{counter}.json"
        counter += 1
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG chat grounding.")
    parser.add_argument(
        "--show-judge-prompt",
        action="store_true",
        help="Print the LLM-as-judge prompt for each report.",
    )
    parser.add_argument(
        "--use-local-judge",
        action="store_true",
        help="Use the local Qwen model to judge each report.",
    )
    parser.add_argument(
        "--show-judge-response",
        action="store_true",
        help="Print the raw judge model response for each report.",
    )
    parser.add_argument(
        "--save-report",
        action="store_true",
        help="Save the complete grounding report to a timestamped JSON file.",
    )
    return parser.parse_args(argv)


def print_grounding_report(report: dict) -> None:
    print("=" * 60)
    print(f"用户问题：{report['query']}")
    print()
    print("参考资料：")
    for index, document in enumerate(report["retrieved_documents"], start=1):
        print(f"{index}. {document}")
    print()
    print(f"客服回复：{report['reply']}")
    print()
    print(f"高风险承诺词：{'、'.join(report['risky_promises']) or '无'}")
    print(f"需要人工复核：{'是' if report['needs_manual_review'] else '否'}")
    print()
    print("人工观察问题：")
    for index, question in enumerate(report["review_questions"], start=1):
        print(f"{index}. {question}")
    print()
    print("人工评估记录：")
    print(f"direct_answer: {report['manual_judgment']['direct_answer']}")
    print(f"grounded: {report['manual_judgment']['grounded']}")
    print(f"useful: {report['manual_judgment']['useful']}")
    print(f"notes: {report['manual_judgment']['notes']}")
    if report.get("judge_status"):
        print(f"judge_status: {report['judge_status']}")
    if report.get("judge_error"):
        print(f"judge_error: {report['judge_error']}")


def print_judge_prompt(report: dict) -> None:
    print()
    print("Judge prompt:")
    print("-" * 60)
    print(build_judge_prompt(report))


def print_judge_response(report: dict) -> None:
    print()
    print("Raw judge response:")
    print("-" * 60)
    print(report.get("raw_judge_response", ""))


def print_summary(summary: dict) -> None:
    print()
    print("=" * 60)
    print("Evaluation summary")
    print(f"total: {summary['total']}")
    print(f"manual_review_count: {summary['manual_review_count']}")
    print(f"judge_status_counts: {summary['judge_status_counts']}")
    print("judgment_counts:")
    for field, counts in summary["judgment_counts"].items():
        print(f"  {field}: {counts}")


def main() -> None:
    args = parse_args()

    from services.chat_service import get_answer_from_rag

    reports = build_grounding_reports_from_rag(
        queries=EVALUATION_QUERIES,
        answer_provider=get_answer_from_rag,
    )
    if args.use_local_judge:
        reports = judge_grounding_reports(reports, local_judge_provider)

    for report in reports:
        print_grounding_report(report)
        if args.show_judge_prompt:
            print_judge_prompt(report)
        if args.show_judge_response:
            print_judge_response(report)

    summary = summarize_grounding_reports(reports)
    print_summary(summary)

    if args.save_report:
        output_path = save_reports_to_file(
            reports=reports,
            use_local_judge=args.use_local_judge,
        )
        print()
        print(f"Saved report: {output_path}")
   

if __name__ == "__main__":
    main()
