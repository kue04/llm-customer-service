# app/utils/retriever.py
from typing import List
import json
from pathlib import Path
import string


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "takeout_customer_service_seed.jsonl"


def iter_knowledge_items():
    with DATA_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            yield json.loads(line)


STOP_WORDS = {"a", "an", "and", "are", "can", "how", "i", "is", "of", "the", "to", "what", "your"}

DOMAIN_KEYWORDS = {
    "外卖",
    "订单",
    "配送",
    "骑手",
    "商家",
    "退款",
    "售后",
    "会员",
    "红包",
    "优惠券",
    "支付",
    "取消",
    "超时",
    "投诉",
    "评价",
    "食品安全",
    "隐私",
    "账号",
}

QUERY_EXPANSIONS = {
    "异物": {"食品", "食品安全", "餐品", "售后", "投诉"},
    "食品": {"食品安全", "餐品", "售后", "投诉"},
    "联系不上": {"骑手", "配送", "订单", "客服", "投诉", "无法联系", "打不通", "配送异常"},
    "无法联系": {"骑手", "配送", "订单", "客服", "投诉", "联系不上", "打不通", "配送异常"},
    "打不通": {"骑手", "配送", "订单", "无法联系", "联系不上", "配送异常"},
    "多久到账": {"到账", "退款", "支付", "进度"},
    "多久到": {"到账", "退款", "支付", "进度"},
}

FIELD_WEIGHTS = {
    "intent": 5,
    "category": 3,
    "question": 2,
    "answer": 1,
}


def normalize_terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = {term.strip("?.!,;:，。！？；：、").lower() for term in lowered.split()}
    terms = {term for term in terms if term and term not in STOP_WORDS}

    for keyword in DOMAIN_KEYWORDS:
        if keyword in text:
            terms.add(keyword)

    for trigger, expanded_terms in QUERY_EXPANSIONS.items():
        if trigger in text:
            terms.add(trigger)
            terms.update(expanded_terms)


    return terms


def score_knowledge_item(query: str, query_terms: set[str], item: dict) -> int:
    explanation = explain_knowledge_item_score(query, query_terms, item)
    return explanation["score"]


def explain_knowledge_item_score(query: str, query_terms: set[str], item: dict) -> dict:
    query_lower = query.lower()
    score = 0
    details = []

    for field_name, weight in FIELD_WEIGHTS.items():
        field_text = item.get(field_name, "")
        if not field_text:
            continue

        field_text = field_text.lower()
        field_terms = normalize_terms(field_text)
        matched_terms = query_terms.intersection(field_terms)

        if matched_terms:
            points = len(matched_terms) * weight
            score += points
            details.append(
                {
                    "field": field_name,
                    "reason": "matched_terms",
                    "terms": sorted(matched_terms),
                    "points": points,
                }
            )

        if query_lower in field_text:
            points = weight + 2
            score += points
            details.append(
                {
                    "field": field_name,
                    "reason": "full_query_match",
                    "terms": [query],
                    "points": points,
                }
            )
    return {
        "score": score,
        "details": details,
    }
    

def normalize_answer_text(text: str) -> str:
    punctuation = string.punctuation + "，。！？；：、（）《》“”‘’"
    return "".join(char for char in text.lower() if char.strip() and char not in punctuation)


def text_bigrams(text: str) -> set[str]:
    normalized = normalize_answer_text(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()

    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def is_similar_answer(answer: str, seen_answer: str, threshold: float = 0.72) -> bool:
    normalized_answer = normalize_answer_text(answer)
    normalized_seen_answer = normalize_answer_text(seen_answer)

    if not normalized_answer or not normalized_seen_answer:
        return False

    shorter, longer = sorted([normalized_answer, normalized_seen_answer], key=len)
    if shorter in longer:
        return len(shorter) / len(longer) >= 0.6

    answer_bigrams = text_bigrams(answer)
    seen_bigrams = text_bigrams(seen_answer)
    if not answer_bigrams or not seen_bigrams:
        return False

    overlap = answer_bigrams.intersection(seen_bigrams)
    union = answer_bigrams.union(seen_bigrams)
    return len(overlap) / len(union) >= threshold


def retrieve_document_candidates(query: str, limit: int = 3) -> list[dict]:
    query_terms = normalize_terms(query)
    candidates = []
    seen_answers = set()

    for item in iter_knowledge_items():
        explanation = explain_knowledge_item_score(query, query_terms, item)
        score = explanation["score"]
        answer = item["answer"]
        matched_term_count = sum(
            len(detail["terms"])
            for detail in explanation["details"]
            if detail["reason"] == "matched_terms"
        )
        if score > 0 and answer not in seen_answers:
            if any(is_similar_answer(answer, seen_answer) for seen_answer in seen_answers):
                continue

            seen_answers.add(answer)
            candidates.append(
                {
                    "score": score,
                    "answer": answer,
                    "details": explanation["details"],
                    "matched_term_count": matched_term_count,
                }
            )

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["matched_term_count"],
            -len(item["answer"]),
        ),
        reverse=True,
    )

    return candidates[:limit]


def retrieve_documents(query: str, limit: int = 3) -> List[str]:
    candidates = retrieve_document_candidates(query, limit=limit)
    return [item["answer"] for item in candidates]
