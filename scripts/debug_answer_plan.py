import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.chat_service import generate_answer_plan
from services.chat_service import generate_reply
from utils.vector_retriever import retrieve_rag_items


REQUIRED_PLAN_FIELDS = [
    "user_intent",
    "answer_type",
    "direct_answer_brief",
    "key_evidence",
    "action_suggestion",
    "needs_caution",
    "caution_reason",
]


DEFAULT_CAUTION_REASON = "具体处理结果可能受支付渠道、订单状态或平台审核影响"
MAX_BRIEF_LENGTH = 32


def extract_json_object_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Answer plan output is empty")

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Answer plan output does not contain a JSON object")
    return stripped[start : end + 1]


def build_answer_plan_prompt(query: str, documents: list[str]) -> str:
    lines = [
        "你是外卖平台客服回答规划助手。",
        "请只根据用户问题和参考资料，先输出一个更结构化的回答计划 JSON。",
        "不要输出最终客服话术，不要输出 Markdown，不要补充额外解释。",
        "direct_answer_brief 必须简短，只写核心结论，尽量不超过 25 个字，不要写成长段客服回复。",
        "action_suggestion 必须简短，只写下一步动作，尽量不超过 18 个字，不要展开解释，不要写'相关结果以页面展示为准'这类补充说明。",
        "",
        "用户问题：",
        query,
        "",
        "参考资料：",
    ]

    for index, document in enumerate(documents, start=1):
        lines.append(f"{index}. {document}")

    lines.extend(
        [
            "",
            "请输出 JSON，字段要求：",
            '1. "user_intent": 用一句话概括用户真正想解决的问题',
            '2. "answer_type": 用简短标签概括回答类型，例如 refund_progress / aftersale / safety_warning',
            '3. "direct_answer_brief": 只写一句简短核心答案，不要展开，尽量不超过 25 个字',
            '4. "key_evidence": 从参考资料提取 1-3 条关键依据，必须是字符串列表',
            '5. "action_suggestion": 只写一句简短下一步动作，尽量不超过 18 个字',
            '6. "needs_caution": true 或 false',
            '7. "caution_reason": 如果 needs_caution=true，说明为什么要保守表达；否则写空字符串',
            "",
            "只输出这样的 JSON：",
            "{",
            '  "user_intent": "...",',
            '  "answer_type": "...",',
            '  "direct_answer_brief": "...",',
            '  "key_evidence": ["...", "..."],',
            '  "action_suggestion": "...",',
            '  "needs_caution": true,',
            '  "caution_reason": "..."',
            "}",
        ]
    )
    return "\n".join(lines)


def parse_answer_plan(text: str) -> dict:
    result = json.loads(extract_json_object_text(text))

    for field in REQUIRED_PLAN_FIELDS:
        if field not in result:
            raise ValueError(f"Answer plan missing field: {field}")

    for field in ["user_intent", "answer_type", "direct_answer_brief", "action_suggestion", "caution_reason"]:
        if not isinstance(result[field], str):
            raise ValueError(f"{field} must be a string")

    if not result["user_intent"].strip():
        raise ValueError("user_intent must be a non-empty string")
    if not result["answer_type"].strip():
        raise ValueError("answer_type must be a non-empty string")
    if not result["direct_answer_brief"].strip():
        raise ValueError("direct_answer_brief must be a non-empty string")
    if not result["action_suggestion"].strip():
        raise ValueError("action_suggestion must be a non-empty string")
    if not isinstance(result["key_evidence"], list) or not result["key_evidence"]:
        raise ValueError("key_evidence must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in result["key_evidence"]):
        raise ValueError("key_evidence items must be non-empty strings")
    if not isinstance(result["needs_caution"], bool):
        raise ValueError("needs_caution must be a boolean")
    if result["needs_caution"] and not result["caution_reason"].strip():
        raise ValueError("caution_reason must be non-empty when needs_caution is true")

    return result


def normalize_answer_plan(plan: dict) -> dict:
    normalized = dict(plan)
    normalized["user_intent"] = normalized["user_intent"].strip()
    normalized["answer_type"] = normalized["answer_type"].strip()
    normalized["direct_answer_brief"] = normalized["direct_answer_brief"].strip()
    normalized["action_suggestion"] = normalized["action_suggestion"].strip().rstrip("。")
    normalized["caution_reason"] = normalized["caution_reason"].strip()
    normalized["key_evidence"] = [
        item.strip().rstrip("。")
        for item in normalized["key_evidence"]
        if isinstance(item, str) and item.strip()
    ]

    if normalized["needs_caution"] and not normalized["caution_reason"]:
        normalized["caution_reason"] = DEFAULT_CAUTION_REASON

    normalized["direct_answer_brief"] = shorten_direct_answer_brief(
        normalized["direct_answer_brief"]
    )
    normalized["action_suggestion"] = normalize_action_suggestion(
        normalized["action_suggestion"]
    )

    return normalized


def shorten_direct_answer_brief(text: str) -> str:
    stripped = text.strip()
    for separator in ["，", "；", "。"]:
        index = stripped.find(separator)
        if index != -1 and index > 0:
            candidate = stripped[:index].strip()
            if candidate:
                return candidate

    if len(stripped) <= MAX_BRIEF_LENGTH:
        return stripped

    return stripped[:MAX_BRIEF_LENGTH].rstrip("，；。 ")


def normalize_action_suggestion(text: str) -> str:
    stripped = text.strip().rstrip("。")
    replacements = [
        "相关结果以页面展示和平台处理为准",
        "相关结果以页面展示为准",
        "具体结果以页面展示为准",
        "请您",
        "您可以",
    ]
    for replacement in replacements:
        stripped = stripped.replace(replacement, "")

    stripped = stripped.strip("，,；;。 ")
    if not stripped:
        return "查看订单页退款进度"
    return stripped


def build_evidence_summary(plan: dict) -> str:
    return "；".join(plan["key_evidence"][:2])


def render_plan_debug(plan: dict) -> str:
    parts = [plan["direct_answer_brief"].strip()]

    evidence_summary = build_evidence_summary(plan)
    if evidence_summary:
        parts.append(f"参考依据：{evidence_summary}。")

    action_suggestion = plan["action_suggestion"].strip()
    if action_suggestion:
        parts.append(f"建议您{action_suggestion}。")

    if plan["needs_caution"]:
        caution_reason = plan["caution_reason"].strip()
        if caution_reason:
            parts.append(f"这类问题需要保守处理，原因是：{caution_reason}。")
        parts.append("具体结果请以订单页面展示或平台核实结果为准。")

    return "\n".join(parts)


def render_user_reply(plan: dict) -> str:
    parts = [plan["direct_answer_brief"].strip()]

    action_suggestion = plan["action_suggestion"].strip()
    if action_suggestion:
        if not action_suggestion.startswith(("请", "建议")):
            action_suggestion = f"建议您{action_suggestion}"
        parts.append(f"{action_suggestion}。")

    if plan["needs_caution"]:
        parts.append("具体结果请以订单页面展示或平台核实结果为准。")

    return "\n".join(parts)


def debug_answer_plan(query: str, limit: int = 3) -> dict:
    retrieved_items = retrieve_rag_items(query)[:limit]
    documents = [item["answer"] for item in retrieved_items]
    prompt = build_answer_plan_prompt(query, documents)
    raw_output = generate_answer_plan(prompt)
    reply_prompt = (
        "请根据以下回答计划，输出给用户看的最终客服回复。"
        "要求语气自然、简洁，不要输出 JSON。\n\n"
        f"{raw_output}"
    )
    model_reply_style_output = generate_reply(reply_prompt)
    parse_error = ""
    plan = None
    debug_rendered_reply = ""
    user_rendered_reply = ""
    try:
        plan = normalize_answer_plan(parse_answer_plan(raw_output))
        debug_rendered_reply = render_plan_debug(plan)
        user_rendered_reply = render_user_reply(plan)
    except ValueError as error:
        if "caution_reason must be non-empty" in str(error):
            repaired_plan = json.loads(extract_json_object_text(raw_output))
            repaired_plan = normalize_answer_plan(repaired_plan)
            plan = repaired_plan
            debug_rendered_reply = render_plan_debug(plan)
            user_rendered_reply = render_user_reply(plan)
        else:
            parse_error = str(error)
    except json.JSONDecodeError as error:
        parse_error = str(error)
    return {
        "query": query,
        "retrieved_items": retrieved_items,
        "documents": documents,
        "prompt": prompt,
        "raw_output": raw_output,
        "model_reply_style_output": model_reply_style_output,
        "parse_error": parse_error,
        "plan": plan,
        "debug_rendered_reply": debug_rendered_reply,
        "user_rendered_reply": user_rendered_reply,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug structured answer plan generation.")
    parser.add_argument("query", help="User query to analyze.")
    parser.add_argument("--limit", type=int, default=3, help="Number of retrieved documents to include.")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = debug_answer_plan(args.query, limit=args.limit)

    print("Retrieved items:")
    for index, item in enumerate(result["retrieved_items"], start=1):
        print(f"{index}. {item.get('intent', '')} | {item.get('question', '')}")

    print()
    print("Answer plan prompt:")
    print("-" * 60)
    print(result["prompt"])

    print()
    print("Raw model output:")
    print("-" * 60)
    print(result["raw_output"])

    print()
    if result["plan"] is not None:
        print("Parsed answer plan:")
        print("-" * 60)
        print(json.dumps(result["plan"], ensure_ascii=False, indent=2))
        print()
        print("model reply style output:")
        print("-" * 60)
        print(result["model_reply_style_output"])
        print()
        print("debug rendered from plan output:")
        print("-" * 60)
        print(result["debug_rendered_reply"])
        print()
        print("user rendered from plan output:")
        print("-" * 60)
        print(result["user_rendered_reply"])
    else:
        print("Parse error:")
        print("-" * 60)
        print(result["parse_error"])


if __name__ == "__main__":
    main()
