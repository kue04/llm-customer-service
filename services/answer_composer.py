from __future__ import annotations

from dataclasses import dataclass
import re


BAD_GENERIC_REPLIES = (
    "很抱歉，这个问题我来帮您分析一下",
    "我会先帮您按订单问题来判断",
    "请您先根据订单问题判断",
)

GENERIC_TAILS = (
    "您可以先根据订单页面提示操作，相关结果以页面展示和平台处理为准。",
    "我理解您现在比较着急，这类问题建议优先在订单内处理，方便平台核实。",
)

CONCLUSION_TERMS = (
    "不可以",
    "不建议",
    "可以",
    "建议",
    "通常",
    "一般",
    "取决于",
    "需要",
    "无法",
    "可能",
    "是否",
)

ACTION_TERMS = (
    "订单详情页",
    "订单页面",
    "退款详情页",
    "结算页",
    "优惠券详情",
    "联系",
    "提交",
    "申请",
    "查看",
    "上传",
    "保留",
    "拍照",
    "反馈",
    "投诉",
)

CAVEAT_TERMS = (
    "以页面",
    "以订单",
    "以平台",
    "以商家",
    "以核实",
    "具体结果",
    "最终",
    "可能",
    "需要商家确认",
    "支付渠道",
    "订单状态",
)

QUERY_NEED_KEYWORDS = {
    "yes_no_request": ("能不能", "可以吗", "行吗", "合理吗", "能退", "能直接", "发我", "给我"),
    "reason_request": ("为什么", "凭什么"),
    "action_request": ("怎么办", "怎么处理", "现在咋办"),
    "safety_or_privacy_boundary": ("手机号", "联系电话", "验证码", "私下转账", "私下"),
    "refund_dispute": ("没吃上", "没收到", "没拿到", "扣钱", "扣我钱", "扣了钱", "配送费", "没全退", "只退", "少退"),
}

BOUNDARY_TERMS = (
    "不能",
    "不可以",
    "不建议",
    "无法",
    "不要",
    "未开放",
    "页面没有展示",
)

REQUIRED_ACTIONS_BY_INTENT = {
    "取消订单": "如果商家一直未接单，您可以在订单详情页发起取消申请；页面无法取消时再联系平台客服处理。",
    "接单后取消": "建议您先在订单详情页查看是否仍有取消入口；如果商家已开始制作，可能需要商家确认，也可以按页面提示提交售后或联系平台客服核实。",
    "配送异常追问": "建议您先在订单详情页查看配送状态，并通过平台内联系功能联系骑手；仍联系不上时提交配送异常反馈。",
    "催单": "建议您先刷新订单详情页并尝试联系骑手；如果位置长时间不更新、联系不上或仍无进展，可以在订单详情页反馈配送异常。",
    "地址修改追问": "建议您先确认骑手是否仍在原地址或是否已继续配送，并立即联系骑手说明新地址；如超出配送范围或无法改送，请联系平台客服协商取消、重新下单或售后处理。",
    "备注未满足": "提交售后时建议您上传餐品照片和订单备注截图，反馈未按备注制作。",
    "未收到餐": "建议您先确认门口、前台、取餐柜或指定收餐点是否有餐品，并尝试联系骑手；如果仍未找到，请在订单详情页提交未收到餐反馈。",
    "餐品撒漏": "请在订单详情页申请售后，并上传包装破损、撒漏情况、餐品照片等凭证。",
    "餐品撒漏售后": "请在订单详情页申请售后，并上传包装破损、撒漏情况、餐品照片等凭证。",
    "隐私保护咨询": "建议您通过平台内联系功能沟通，不要在聊天中主动发送完整手机号、地址或验证码。",
    "商家电话咨询": "建议您优先通过平台内电话或在线联系功能沟通。",
    "联系商家咨询": "平台客服可根据问题类型协助联系商家核实，建议您优先通过订单详情页的联系商家入口沟通并保留平台内聊天记录。",
    "优惠券不可用": "请先点开优惠券详情或结算页查看不可用原因；如果确认满足条件仍不可用，可以截图后通过订单页或官方客服反馈核实。",
}

REQUIRED_CAVEATS_BY_INTENT = {
    "取消订单": "取消结果和退款处理以订单状态、订单详情页展示和平台处理结果为准。",
    "接单后取消": "取消结果和退款金额以订单状态、是否制作、商家确认和订单页面展示为准。",
    "配送异常追问": "平台会结合骑手位置、配送进度和订单状态核实处理。",
    "催单": "骑手位置长时间未更新可能与网络延迟、等待出餐或路况有关，处理结果以订单页面和平台核实为准。",
    "地址修改追问": "新地址能否改送取决于配送范围、骑手位置和订单状态。",
    "备注未满足": "平台会结合订单备注、商家制作情况和凭证核实处理。",
    "未收到餐": "平台会核实骑手送达情况和订单状态，退款或售后结果以平台核实处理为准。",
    "退款金额咨询": "平台会结合订单状态、配送进度、优惠抵扣和凭证核实处理。",
    "餐品撒漏": "平台会结合配送过程和凭证核实处理。",
    "餐品撒漏售后": "平台会结合配送过程和凭证核实处理。",
    "隐私保护咨询": "平台通常会通过隐私号等方式保护用户和骑手手机号，具体展示以平台规则和订单页面为准。",
    "商家电话咨询": "如果页面没有展示电话，说明商家可能未开放电话联系或使用平台虚拟号。",
    "联系商家咨询": "具体能否协助联系和处理结果以订单页面展示及平台核实为准。",
}

REQUIRED_CONCLUSIONS_BY_INTENT = {
    "退款金额咨询": "订单取消后只退一部分，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。",
    "催单": "骑手位置一直停在一个地方，通常可能与等待出餐、网络延迟或路况有关。",
    "配送异常追问": "配送状态、骑手定位或送达情况不一致时，建议优先按配送异常处理。",
    "地址修改追问": "地址写错且骑手已经到原地址时，需要尽快确认骑手当前位置并说明新地址。",
    "备注未满足": "如果备注了不要辣但餐品仍然很辣，可以按未按备注制作申请售后。",
    "未收到餐": "骑手取餐后仍未收到餐时，需要先按未收到餐或配送异常核实处理。",
    "餐品撒漏": "餐品撒漏、汤汁漏出或包装破损时，可以按餐品撒漏售后处理。",
    "餐品撒漏售后": "餐品撒漏、汤汁漏出或包装破损时，可以按餐品撒漏售后处理。",
    "联系商家咨询": "部分问题平台客服可以协助联系商家核实。",
    "接单后取消": "商家已接单或已开始制作后，能否取消及是否全额退款需要以订单状态、是否制作和页面展示为准。",
    "商家电话咨询": "商家电话一般在订单详情页或商家主页的“联系商家”入口查看，有电话时会在该入口展示。",
    "优惠券不可用": "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。",
}


@dataclass(frozen=True)
class AnswerParts:
    conclusion: str
    action: str
    caveat: str


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def strip_terminal_punctuation(text: str) -> str:
    return normalize_text(text).strip("。！？!? ，,；;：:")


def split_sentences(text: str) -> list[str]:
    normalized_text = normalize_text(text)
    if not normalized_text:
        return []

    raw_sentences = re.split(r"[。；;]+", normalized_text)
    sentences: list[str] = []
    for sentence in raw_sentences:
        for part in re.split(r"，(?=(?:建议|请|您可以|如果|若|具体|最终|平台|商家|骑手|退款|到账))", sentence):
            cleaned = strip_terminal_punctuation(part)
            if cleaned:
                sentences.append(cleaned)
    return sentences


def ensure_period(sentence: str) -> str:
    cleaned = strip_terminal_punctuation(sentence)
    if not cleaned:
        return ""
    return f"{cleaned}。"


def normalize_sentence_key(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalize_text(text))


def sentences_overlap(left: str, right: str) -> bool:
    left_key = normalize_sentence_key(left)
    right_key = normalize_sentence_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    overlap_ratio = len(shorter) / len(longer)
    return len(shorter) >= 8 and overlap_ratio >= 0.72 and shorter in longer


def sentence_has_any(sentence: str, terms: tuple[str, ...]) -> bool:
    return any(term in sentence for term in terms)


def detect_query_needs(query: str) -> set[str]:
    return {
        need
        for need, keywords in QUERY_NEED_KEYWORDS.items()
        if any(keyword in query for keyword in keywords)
    }


def pick_sentence(sentences: list[str], terms: tuple[str, ...], fallback_index: int = 0) -> str:
    for sentence in sentences:
        if sentence_has_any(sentence, terms):
            return sentence
    if not sentences:
        return ""
    return sentences[min(fallback_index, len(sentences) - 1)]


def pick_boundary_sentence(sentences: list[str]) -> str:
    return pick_sentence(sentences, BOUNDARY_TERMS)


def pick_conclusion(query: str, sentences: list[str]) -> str:
    query_needs = detect_query_needs(query)
    if "safety_or_privacy_boundary" in query_needs and "yes_no_request" in query_needs:
        return pick_boundary_sentence(sentences)

    if any(term in query for term in ("可以吗", "能不能", "可以不", "还能取消吗")):
        return pick_sentence(sentences, ("不可以", "不建议", "可以", "取决于", "需要", "是否"))
    if any(term in query for term in ("多久到账", "多久退", "什么时候到账", "钱多久")):
        return pick_sentence(sentences, ("到账时间", "支付渠道", "原路退回", "退款通常", "退款进度"))
    if any(term in query for term in ("在哪里", "哪里看", "在哪看")):
        return pick_sentence(sentences, ("订单详情页", "商家主页", "入口", "查看", "联系商家"))
    return pick_sentence(sentences, CONCLUSION_TERMS)


def pick_action(sentences: list[str], conclusion: str) -> str:
    for sentence in sentences:
        if sentence != conclusion and sentence_has_any(sentence, ACTION_TERMS):
            return sentence
    return ""


def pick_caveat(sentences: list[str], conclusion: str, action: str) -> str:
    for sentence in sentences:
        if sentence not in {conclusion, action} and sentence_has_any(sentence, CAVEAT_TERMS):
            return sentence
    return ""


def extract_answer_parts(query: str, primary_answer: str) -> AnswerParts:
    sentences = split_sentences(primary_answer)
    if not sentences:
        return AnswerParts(
            conclusion="建议您先在订单详情页查看当前状态",
            action="如果页面无法处理，可以联系平台客服核实",
            caveat="具体结果以订单页面和平台处理结果为准",
        )

    conclusion = pick_conclusion(query, sentences)
    action = pick_action(sentences, conclusion)
    caveat = pick_caveat(sentences, conclusion, action)

    return AnswerParts(
        conclusion=conclusion,
        action=action,
        caveat=caveat,
    )


def query_requests_unsupported_guarantee(query: str) -> bool:
    return any(term in query for term in ["一定", "保证", "承诺", "直接说"])


def apply_required_steps(parts: AnswerParts, query: str, primary_item: dict) -> AnswerParts:
    intent = str(primary_item.get("intent", ""))
    query_needs = detect_query_needs(query)
    conclusion = parts.conclusion
    action = parts.action
    caveat = parts.caveat

    required_conclusion = REQUIRED_CONCLUSIONS_BY_INTENT.get(intent, "")
    if required_conclusion:
        conclusion = required_conclusion

    if intent == "未收到餐" and query_requests_unsupported_guarantee(query):
        conclusion = "不能直接承诺一定全额退款，需要先由平台核实骑手送达情况和订单状态。"
        action = "如果您仍未找到餐品或无法立即取回餐品，请先确认门口、前台、取餐柜或指定收餐点，并在订单详情页提交未收到餐反馈。"
        caveat = "退款或售后结果以平台核实处理为准。"
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    if intent == "接单后取消" and query_requests_unsupported_guarantee(query):
        conclusion = "不能保证商家接单后取消一定不扣钱，能否取消和退款金额需要以订单状态、是否制作和页面显示为准。"
        action = "建议您先在订单详情页查看是否仍有取消入口；如果商家已开始制作，可以按页面提示提交售后或联系平台客服核实。"
        caveat = "取消结果和退款金额以订单状态、商家确认和订单页面显示为准。"
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    if (
        intent == "退款金额咨询"
        and "refund_dispute" in query_needs
        and any(term in query for term in ["没吃上", "没收到", "没用餐"])
    ):
        conclusion = "没吃上但仍被扣款或只退部分金额，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。"
        action = "建议您先在退款详情页查看扣除原因；如您认为扣款不合理，可以在订单内提交售后复核或申请平台介入。"
        caveat = "平台会结合订单状态和凭证核实处理。"

    if intent == "退款金额咨询" and "refund_dispute" in query_needs and "配送费" in query:
        conclusion = "餐没拿到但仍被扣配送费或只退部分金额时，需要以退款详情页的扣除原因和平台核实结果为准。"
        action = "建议您先查看退款详情页，如页面原因不清楚，可以在订单内提交售后复核。"
        caveat = "平台会结合配送进度、订单状态和凭证核实处理。"

    if intent == "备注未满足" and any(term in query for term in ["过敏", "花生"]):
        conclusion = "如果已备注过敏但餐品仍放入相关食材，建议先停止食用，并按未按备注制作或餐品问题提交售后。"
        action = "建议您上传餐品照片、订单备注截图和相关沟通记录，说明过敏备注未被满足。"
        caveat = "平台会结合订单备注、餐品情况和凭证核实处理。"

    elif intent == "备注未满足" and "香菜" in query:
        conclusion = "如果已备注不要香菜但餐品仍放了香菜，可以按未按备注制作提交售后。"
        action = "建议您上传餐品照片、订单备注截图和相关沟通记录，反馈商家未按备注制作。"
        caveat = "平台会结合订单备注、餐品情况和凭证核实处理。"

    elif intent == "备注未满足" and "忌口" in query:
        conclusion = "如果已写明忌口或过敏备注但商家未按备注制作，可以按未按备注制作或餐品问题提交售后。"
        action = "建议您上传餐品照片、订单备注截图和相关沟通记录。"
        caveat = "平台会结合订单备注、餐品情况和凭证核实处理。"

    if intent == "联系商家咨询" and any(term in query for term in ["帮我打", "直接打", "打给店家", "打给商家"]):
        conclusion = "平台客服不能直接代您私下打给店家，但可以根据问题类型协助联系商家核实。"
        action = "建议您优先通过订单详情页的联系商家入口沟通，并保留平台内聊天记录；需要客服协助时可在订单内提交问题。"
        caveat = "具体能否协助联系和处理结果以订单页面展示及平台核实为准。"
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    if intent == "配送异常追问" and any(term in query for term in ["联系不上", "联系不到", "打不通", "电话不接", "无法联系"]):
        conclusion = "骑手联系不上时，建议先按配送异常处理。"
        action = "请先在订单详情页查看配送状态，并通过平台内联系功能再次联系骑手；如果仍联系不上或订单已明显超时，请在订单详情页提交配送异常反馈。"
        caveat = "平台会结合骑手位置、配送进度和订单状态核实处理。"
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    if intent == "配送异常追问" and any(term in query for term in ["定位", "位置"]) and any(term in query for term in ["说到了", "显示到了", "说已到"]):
        conclusion = "骑手说已到但定位仍很远，属于配送状态和骑手定位不一致，建议先按配送异常处理。"
        action = "请先在订单详情页核对配送状态，并通过平台内联系功能联系骑手确认实际位置；如果仍无法确认或联系不上，立即提交配送异常或未收到餐反馈。"
        caveat = "平台会结合骑手位置、配送进度和订单状态核实处理。"

    if intent == "未收到餐" and "能退" in query:
        conclusion = "骑手取餐后一直没收到餐，可以提交未收到餐反馈；能否退款需要以平台核实结果为准。"
        action = "建议您先确认门口、前台、取餐柜或指定收餐点是否有餐品，并尝试联系骑手；如果仍未找到，请在订单详情页提交未收到餐反馈。"
        caveat = "平台会核实骑手送达情况和订单状态，退款或售后结果以平台核实处理为准。"

    if intent == "隐私保护咨询" and "商家" in query and "手机号" in query:
        conclusion = "商家一般不应直接看到您的真实手机号，平台通常会通过隐私号等方式保护联系方式。"
        action = "建议您通过订单页面或平台内联系功能沟通，不要在聊天中主动发送完整手机号、地址或验证码。"
        caveat = "具体号码展示方式以平台规则和订单页面为准。"
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    if intent == "商家电话咨询" and "safety_or_privacy_boundary" in query_needs and "yes_no_request" in query_needs:
        conclusion = "不能直接提供页面未展示的店家手机号。"
        action = "您可以在订单详情页或商家主页查看“联系商家”入口，并优先通过平台内电话或在线联系功能沟通。"
        caveat = "如果页面没有展示电话，说明商家可能未开放电话联系或使用平台虚拟号，具体以页面展示为准。"
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    required_action = REQUIRED_ACTIONS_BY_INTENT.get(intent, "")
    if required_action and required_action not in action:
        action = required_action

    required_caveat = REQUIRED_CAVEATS_BY_INTENT.get(intent, "")
    if required_caveat and required_caveat not in caveat:
        caveat = required_caveat

    if "平台客服" in query and "手机号" in query:
        conclusion = "平台客服处理订单时也应遵守平台隐私保护规则，您不需要主动提供完整手机号。"
        action = REQUIRED_ACTIONS_BY_INTENT["隐私保护咨询"]
        caveat = REQUIRED_CAVEATS_BY_INTENT["隐私保护咨询"]
        return AnswerParts(conclusion=conclusion, action=action, caveat=caveat)

    return AnswerParts(
        conclusion=conclusion,
        action=action,
        caveat=caveat,
    )


def dedupe_answer_parts(parts: AnswerParts) -> AnswerParts:
    action = parts.action
    caveat = parts.caveat

    if sentences_overlap(parts.conclusion, action):
        action = ""
    if sentences_overlap(parts.conclusion, caveat) or sentences_overlap(action, caveat):
        caveat = ""

    return AnswerParts(
        conclusion=parts.conclusion,
        action=action,
        caveat=caveat,
    )


def render_answer_parts(parts: AnswerParts) -> str:
    parts = dedupe_answer_parts(parts)
    rendered_sentences = [
        ensure_period(parts.conclusion),
        ensure_period(parts.action),
        ensure_period(parts.caveat),
    ]
    return "".join(sentence for sentence in rendered_sentences if sentence)


def compose_from_primary_evidence(query: str, primary_item: dict) -> tuple[str, AnswerParts]:
    primary_answer = normalize_text(primary_item.get("answer", ""))
    parts = extract_answer_parts(query, primary_answer)
    parts = apply_required_steps(parts, query, primary_item)
    return render_answer_parts(parts), parts


def _contains_query_echo(query: str, reply: str) -> bool:
    normalized_query = strip_terminal_punctuation(query)
    normalized_reply = strip_terminal_punctuation(reply)
    if not normalized_query or not normalized_reply:
        return False

    if normalized_reply == normalized_query:
        return True

    return normalized_query in normalized_reply and len(normalized_reply) <= len(normalized_query) + 20


def _has_primary_evidence_overlap(reply: str, primary_answer: str) -> bool:
    if not reply or not primary_answer:
        return False

    evidence_terms = [
        term
        for term in re.split(r"[，。；、\s]+", primary_answer)
        if len(term) >= 3
    ]
    if not evidence_terms:
        return True

    matched_count = sum(1 for term in evidence_terms if term in reply)
    return matched_count >= 1


def remove_generic_tails(reply: str) -> str:
    cleaned_reply = normalize_text(reply)
    for tail in GENERIC_TAILS:
        for suffix in (f" {tail}", tail):
            if cleaned_reply.endswith(suffix):
                cleaned_reply = cleaned_reply[: -len(suffix)].rstrip()
    return cleaned_reply


def reply_needs_composer(query: str, reply: str, primary_item: dict) -> bool:
    normalized_reply = normalize_text(reply)
    if not normalized_reply:
        return True

    if len(normalized_reply) < 20:
        return True

    if _contains_query_echo(query, normalized_reply):
        return True

    if any(generic_reply in normalized_reply for generic_reply in BAD_GENERIC_REPLIES):
        return True

    primary_answer = normalize_text(primary_item.get("answer", ""))
    if primary_answer and not _has_primary_evidence_overlap(normalized_reply, primary_answer):
        return True

    return False


def compose_answer_if_needed(
    query: str,
    reply: str,
    retrieved_items: list[dict],
) -> tuple[str, dict]:
    primary_item = retrieved_items[0] if retrieved_items else {}
    if not primary_item:
        return reply, {"applied": False, "reason": "no_primary_item"}

    cleaned_reply = remove_generic_tails(reply)
    composed_reply, parts = compose_from_primary_evidence(query, primary_item)
    low_quality_reply = reply_needs_composer(query, cleaned_reply, primary_item)

    return composed_reply, {
        "applied": composed_reply != reply,
        "reason": "low_quality_model_reply" if low_quality_reply else "structured_from_primary_evidence",
        "primary_category": primary_item.get("category", ""),
        "primary_intent": primary_item.get("intent", ""),
        "answer_parts": {
            "conclusion": parts.conclusion,
            "action": parts.action,
            "caveat": parts.caveat,
        },
    }
