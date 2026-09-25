from __future__ import annotations

from dataclasses import dataclass


RISK_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

# 兜底意图：无任何规则命中时使用。它不属于 INTENT_RULES，
# 因此也不会被「上下文继承」当成合法的继承来源（见 _inherited_intent）。
FALLBACK_INTENT_NAME = "通用客服咨询"
FALLBACK_CONFIDENCE = 0.5

# 上下文继承的置信度：必须显著低于任何直接命中的规则置信度（最低一档是 0.76），
# 否则「猜出来的意图」会被当成「真命中的意图」——本项目明令禁止。
INHERITED_CONFIDENCE = 0.6

# 低于该置信度走 clarify 路由（让链路有机会先澄清而不是硬猜）。
CLARIFY_CONFIDENCE_THRESHOLD = 0.6

# 指代词白名单：本句零命中、但含这些词时，才允许继承上文主意图。
COREFERENCE_HINTS = ("那", "这", "它", "他", "呢", "还要", "多久", "怎么办", "然后")

# 修饰词归一化白名单（B6 第一步）：删掉插入语 / 程度副词 / 口语冗余再匹配，
# 解决「骑手一直联系不上」匹配不到「骑手联系不上」这类精确子串漏判。
FILLER_WORDS = ("一直", "老是", "总是", "真的", "非常", "特别", "有点", "一下", "还是", "都")


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


KNOWN_INTENT_NAMES = frozenset(rule.name for rule in INTENT_RULES)


def _normalize(query: str) -> str:
    normalized = query
    for word in FILLER_WORDS:
        normalized = normalized.replace(word, "")
    return normalized


def _matched_keywords(query: str, keywords: tuple[str, ...]) -> list[str]:
    """原句与归一化句任一命中即算命中；证据片段固定取规则表里的原词。

    保留「原句命中」这一路，是为了不缩小既有召回面（归一化只做加法）。
    """
    normalized = _normalize(query)
    return [keyword for keyword in keywords if keyword in query or keyword in normalized]


def _looks_like_coreference(query: str) -> bool:
    return any(hint in query for hint in COREFERENCE_HINTS)


def _inherited_intent(query: str, facts: dict) -> str:
    """本句零命中时的指代消解：只在「上文主意图是真实规则意图」且「本句含指代词」时继承。

    两道守卫都是必要的：
    - 上一轮本身就走兜底（last_primary_intent = 通用客服咨询）时继承毫无信息量；
    - 脏 facts（历史数据 / 改过规则表）里的未知意图名不许进入结果。
    """
    last_intent = str(facts.get("last_primary_intent") or "")
    if last_intent not in KNOWN_INTENT_NAMES:
        return ""
    if not _looks_like_coreference(query):
        return ""
    return last_intent


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
    context = conversation_context or {}
    facts = context.get("facts") or {}

    matched_intents = []
    for rule in INTENT_RULES:
        evidence = _matched_keywords(query, rule.keywords)
        if evidence:
            matched_intents.append(_build_intent(rule, evidence))

    inherited_from = ""
    if not matched_intents:
        inherited_from = _inherited_intent(query, facts)
        if inherited_from:
            inherited_risk = str(facts.get("last_risk_level") or "low")
            matched_intents.append(
                {
                    "name": inherited_from,
                    "confidence": INHERITED_CONFIDENCE,  # 继承来的，必须低于直接命中
                    "risk_level": inherited_risk if inherited_risk in RISK_RANK else "low",
                    "priority": 50,  # 非直接命中，排在所有真实规则之后
                    "evidence": [],  # 本句没有命中证据，留空（前端有专门文案）
                    "inherited_from_context": True,
                }
            )

    if not matched_intents:
        matched_intents.append(
            {
                "name": FALLBACK_INTENT_NAME,
                "confidence": FALLBACK_CONFIDENCE,
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

    if primary_intent["confidence"] < CLARIFY_CONFIDENCE_THRESHOLD:
        routing = "clarify"
    elif RISK_RANK.get(risk_level, 0) >= RISK_RANK["high"]:
        routing = "high_risk_rag"
    else:
        routing = "rag"

    result = {
        "primary_intent": primary_intent["name"],
        "secondary_intents": secondary_intents,
        "risk_level": risk_level,
        "routing": routing,
        "intents": sorted(
            matched_intents,
            key=lambda item: (item["priority"], -item["confidence"]),
        ),
        "requires_safety_prefix": RISK_RANK.get(risk_level, 0) >= RISK_RANK["high"],
    }
    if inherited_from:
        # 让前端 / 日志能看出这是继承来的，而不是真命中的
        result["inherited_from_context"] = inherited_from
    return result
