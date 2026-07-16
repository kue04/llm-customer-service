from __future__ import annotations

from dataclasses import dataclass


RISK_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


@dataclass(frozen=True)
class IntentRule:
    name: str
    risk_level: str
    priority: int
    confidence: float
    keywords: tuple[str, ...]


INTENT_RULES = (
    IntentRule(
        name="验证码诈骗提醒",
        risk_level="critical",
        priority=1,
        confidence=0.96,
        keywords=("验证码", "校验码", "验正码", "短信码", "银行卡号", "银行卡", "支付密码"),
    ),
    IntentRule(
        name="食品安全投诉",
        risk_level="high",
        priority=2,
        confidence=0.93,
        keywords=("食品安全", "异物", "变质", "酸了", "发霉", "吃坏", "拉肚子", "不敢吃", "过敏", "发麻"),
    ),
    IntentRule(
        name="站外交易风险",
        risk_level="high",
        priority=3,
        confidence=0.95,
        keywords=("商家微信", "店家微信", "平台外", "别走平台", "不走平台", "绕开平台", "私下退款", "私下退钱"),
    ),
    IntentRule(
        name="私下收费风险",
        risk_level="high",
        priority=4,
        confidence=0.94,
        keywords=("加微信", "私下", "转账", "转运费", "转配送费", "线下付款"),
    ),
    IntentRule(
        name="隐私保护咨询",
        risk_level="high",
        priority=5,
        confidence=0.9,
        keywords=("手机号", "真实手机号", "真实号码", "完整号码", "完整手机号", "隐私", "地址泄露", "身份证", "身份证信息"),
    ),
    IntentRule(
        name="退款进度",
        risk_level="medium",
        priority=20,
        confidence=0.84,
        keywords=("退款", "退钱", "多久到账", "退回来", "退款进度"),
    ),
    IntentRule(
        name="退款金额咨询",
        risk_level="medium",
        priority=21,
        confidence=0.82,
        keywords=("全额", "全款", "只退", "少退", "扣钱", "扣费", "配送费"),
    ),
    IntentRule(
        name="未收到餐",
        risk_level="medium",
        priority=22,
        confidence=0.84,
        keywords=("没收到餐", "没拿到餐", "没有收到", "门口没有", "显示送达"),
    ),
    IntentRule(
        name="配送异常追问",
        risk_level="medium",
        priority=23,
        confidence=0.8,
        keywords=("骑手联系不上", "配送异常", "定位", "位置", "超时", "送达时间"),
    ),
    IntentRule(
        name="骑手态度投诉",
        risk_level="medium",
        priority=24,
        confidence=0.78,
        keywords=("骑手态度", "态度差", "态度不好", "说话太冲", "发脾气"),
    ),
    IntentRule(
        name="优惠券不可用",
        risk_level="low",
        priority=40,
        confidence=0.76,
        keywords=("优惠券", "红包", "满减", "不能用", "用不了", "没抵扣"),
    ),
)


def _matched_keywords(query: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in query]


def _build_intent(rule: IntentRule, evidence: list[str]) -> dict:
    confidence = min(0.99, rule.confidence + max(0, len(evidence) - 1) * 0.02)
    return {
        "name": rule.name,
        "confidence": round(confidence, 2),
        "risk_level": rule.risk_level,
        "priority": rule.priority,
        "evidence": evidence,
    }


def _is_refund_explicitly_primary(query: str) -> bool:
    has_focus_word = any(word in query for word in ("主要", "重点", "只想问", "先问"))
    has_refund_word = any(word in query for word in ("退款", "退钱", "全额", "全款", "赔"))
    return has_focus_word and has_refund_word


def _choose_primary_intent(query: str, intents: list[dict]) -> dict:
    if _is_refund_explicitly_primary(query):
        refund_intents = [
            intent
            for intent in intents
            if intent["name"] in {"退款进度", "退款金额咨询"}
        ]
        if refund_intents:
            return sorted(refund_intents, key=lambda item: item["confidence"], reverse=True)[0]

    return sorted(
        intents,
        key=lambda item: (item["priority"], -item["confidence"]),
    )[0]


def analyze_intents(query: str, conversation_context: dict | None = None) -> dict:
    del conversation_context
    matched_intents = []
    for rule in INTENT_RULES:
        evidence = _matched_keywords(query, rule.keywords)
        if evidence:
            matched_intents.append(_build_intent(rule, evidence))

    if not matched_intents:
        matched_intents.append(
            {
                "name": "通用客服咨询",
                "confidence": 0.5,
                "risk_level": "low",
                "priority": 99,
                "evidence": [],
            }
        )

    primary_intent = _choose_primary_intent(query, matched_intents)
    secondary_intents = [
        intent["name"]
        for intent in matched_intents
        if intent["name"] != primary_intent["name"]
    ]
    max_risk = max(matched_intents, key=lambda item: RISK_RANK.get(item["risk_level"], 0))
    risk_level = max_risk["risk_level"]

    return {
        "primary_intent": primary_intent["name"],
        "secondary_intents": secondary_intents,
        "risk_level": risk_level,
        "routing": "high_risk_rag" if RISK_RANK.get(risk_level, 0) >= RISK_RANK["high"] else "rag",
        "intents": sorted(
            matched_intents,
            key=lambda item: (item["priority"], -item["confidence"]),
        ),
        "requires_safety_prefix": RISK_RANK.get(risk_level, 0) >= RISK_RANK["high"],
    }
