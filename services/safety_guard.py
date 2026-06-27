from __future__ import annotations

import re


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

RISKY_PROMISE_TERMS = (
    "一定退款",
    "保证退款",
    "一定退",
    "保证退",
    "一定赔付",
    "保证赔付",
    "一定赔偿",
    "保证赔偿",
    "马上到账",
    "肯定到账",
    "平台一定",
)

PRIVATE_TRANSACTION_TERMS = (
    "可以加微信",
    "建议加微信",
    "可以私下转账",
    "建议私下转账",
    "直接转给骑手",
    "线下付款",
)

VERIFY_CODE_TERMS = (
    "发送验证码",
    "提供验证码",
    "告诉验证码",
    "把验证码发给",
)


def _safe_fallback(query: str, intent_analysis: dict) -> str:
    primary_intent = intent_analysis.get("primary_intent", "")
    intents = {intent.get("name", "") for intent in intent_analysis.get("intents", [])}

    if primary_intent == "验证码诈骗提醒" or "验证码诈骗提醒" in intents:
        return (
            "不需要也不应该提供验证码。验证码、密码等属于敏感信息，请不要发给骑手、商家或任何陌生人；"
            "涉及退款、配送或订单处理，请通过订单页面或官方客服渠道操作。"
        )
    if primary_intent == "私下收费风险" or "私下收费风险" in intents:
        return (
            "不建议私下转账或加微信支付额外费用。配送费和订单费用应以平台订单页面为准；"
            "如果对方要求站外付款，请保留聊天记录并通过订单页或官方客服反馈。"
        )
    if primary_intent == "食品安全投诉" or "食品安全投诉" in intents:
        return (
            "食品安全问题请先停止食用，并保留餐品、包装、异物照片和订单信息等凭证。"
            "建议您通过订单售后或官方客服提交食品安全反馈；是否退款或赔付以平台核实结果为准。"
        )
    if "手机号" in query or primary_intent == "隐私保护咨询":
        return (
            "请不要在聊天中发送完整手机号、验证码或详细地址等敏感信息。"
            "建议优先通过平台内联系功能或官方客服渠道处理，具体联系方式以订单页面展示为准。"
        )
    return (
        "这个问题需要以订单页面和平台核实结果为准。建议您先查看订单详情页的当前状态，"
        "必要时通过官方客服或订单售后入口提交反馈；我不能在缺少核实结果时承诺退款、赔付或处理成功。"
    )


def validate_reply(
    query: str,
    reply: str,
    intent_analysis: dict | None = None,
    retrieved_items: list[dict] | None = None,
) -> tuple[str, dict]:
    intent_analysis = intent_analysis or {}
    retrieved_items = retrieved_items or []
    issues = []

    if any(term in reply for term in RISKY_PROMISE_TERMS):
        issues.append("risky_promise")
    if any(term in reply for term in PRIVATE_TRANSACTION_TERMS):
        issues.append("private_transaction")
    if any(term in reply for term in VERIFY_CODE_TERMS):
        issues.append("verification_code_leak")
    if PHONE_RE.search(reply):
        issues.append("phone_number_leak")

    high_risk = intent_analysis.get("risk_level") in {"high", "critical"}
    no_evidence = not retrieved_items
    if high_risk and no_evidence and any(term in reply for term in ("可以赔", "可以退", "会赔", "会退")):
        issues.append("unsupported_high_risk_claim")

    if not issues:
        return reply, {
            "passed": True,
            "blocked": False,
            "issues": [],
            "fallback_applied": False,
        }

    return _safe_fallback(query, intent_analysis), {
        "passed": False,
        "blocked": True,
        "issues": issues,
        "fallback_applied": True,
    }
