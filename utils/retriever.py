# app/utils/retriever.py
from typing import List
import json
from pathlib import Path


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

    return terms


def score_knowledge_item(query: str, query_terms: set[str], item: dict) -> int:
    query_lower = query.lower()
    score = 0

    for field_name, weight in FIELD_WEIGHTS.items():
        field_text = item.get(field_name, "")
        if not field_text:
            continue

        field_lower = field_text.lower()
        field_terms = normalize_terms(field_text)
        matched_terms = query_terms.intersection(field_terms)
        score += len(matched_terms) * weight

        if query_lower in field_lower:
            score += weight + 2

    return score


def retrieve_documents(query: str, limit: int = 3) -> List[str]:
    query_terms = normalize_terms(query)
    candidates = []
    seen_answers = set()

    for item in iter_knowledge_items():
        score = score_knowledge_item(query, query_terms, item)
        answer = item["answer"]
        if score > 0 and answer not in seen_answers:
            seen_answers.add(answer)
            candidates.append(
                {
                    "score": score,
                    "answer": answer,
                }
            )

    candidates.sort(key=lambda item: item["score"], reverse=True)

    return [item["answer"] for item in candidates[:limit]]
