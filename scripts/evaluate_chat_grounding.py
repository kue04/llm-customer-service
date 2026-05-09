import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

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
) -> dict:
    risky_promises = find_risky_promises(reply)

    return {
        "query": query,
        "retrieved_documents": retrieved_documents,
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
   如果用户问题包含“可以吗”或“能不能”，回复必须明确回答“可以”“不建议”或“需要平台核实”，否则 direct_answer 应为 partial 或 no。
   如果用户问题包含“怎么办”“可以吗”“能不能”或“多久到账”，但回复只给泛泛建议，没有回答核心问题，direct_answer 应为 partial。
2. grounded：客服回复中的关键说法是否能被参考资料支撑。
3. useful：客服回复是否给出了安全、可执行的下一步。
4. risk_notes：指出未被资料支撑、过度承诺或可能误导用户的内容；没有则写空字符串。
   risk_notes 只能指出客服回复中存在的问题；不要把参考资料中未被客服回复提到的内容写成风险。
5. reason：用一句话解释你的判断。reason 必须填写，不能为空。

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
  "risk_notes": "",
  "reason": ""
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
    judge_result = parse_judge_response(judge_text)
    return apply_judge_result(report, judge_result)


def judge_grounding_reports(reports: list[dict], judge_provider) -> list[dict]:
    return [
        judge_grounding_report(report, judge_provider)
        for report in reports
    ]


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
   

if __name__ == "__main__":
    main()
