from __future__ import annotations

import json

import faiss
import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer

from config.rag_config import get_rag_config
from utils.retriever import iter_knowledge_items, is_similar_answer


_TOY_VECTOR_INDEX: list[dict] | None = None
_RERANKER_MODEL: CrossEncoder | None = None
_EMBEDDING_MODEL: SentenceTransformer | None = None
_REAL_VECTOR_DOCS: list[dict] | None = None
_REAL_FAISS_INDEX: faiss.IndexFlatIP | None = None

DEFAULT_MODEL_RERANK_WEIGHT = get_rag_config().model_rerank_weight
DEFAULT_MIN_VECTOR_SCORE = get_rag_config().min_vector_score
VECTOR_STORE_DIR = get_rag_config().faiss_store_dir
FAISS_INDEX_PATH = VECTOR_STORE_DIR / "real_vector.index"
FAISS_DOCS_PATH = VECTOR_STORE_DIR / "real_vector_docs.json"
INTENT_HINT_BONUS = 0.10
INTENT_HINT_SUPPLEMENT_SCORE = 0.80


def get_reranker_model() -> CrossEncoder:
    global _RERANKER_MODEL

    if _RERANKER_MODEL is None:
        _RERANKER_MODEL = CrossEncoder(get_rag_config().reranker_model_name)

    return _RERANKER_MODEL


def get_toy_vector_index() -> list[dict]:
    global _TOY_VECTOR_INDEX

    if _TOY_VECTOR_INDEX is None:
        _TOY_VECTOR_INDEX = build_toy_vector_index()

    return _TOY_VECTOR_INDEX


def get_embedding_model() -> SentenceTransformer:
    global _EMBEDDING_MODEL

    if _EMBEDDING_MODEL is None:
        _EMBEDDING_MODEL = SentenceTransformer(get_rag_config().embedding_model_name)

    return _EMBEDDING_MODEL


def build_embedding(text: str) -> list[float]:
    model = get_embedding_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def build_document_text(item: dict) -> str:
    parts = [
        f"分类：{item.get('category', '')}",
        f"意图：{item.get('intent', '')}",
        f"问题：{item.get('question', '')}",
        f"答案：{item.get('answer', '')}",
    ]
    return "\n".join(parts)


def load_vector_documents() -> list[dict]:
    documents = []

    for index, item in enumerate(iter_knowledge_items()):
        documents.append(
            {
                "id": index,
                "text": build_document_text(item),
                "answer": item["answer"],
                "source": item,
            }
        )

    return documents


def current_vector_document_signature() -> list[dict]:
    return [
        {
            "answer": document["answer"],
            "source": {
                "question": document["source"].get("question", ""),
                "category": document["source"].get("category", ""),
                "intent": document["source"].get("intent", ""),
            },
        }
        for document in load_vector_documents()
    ]


def stored_vector_document_signature() -> list[dict]:
    return [
        {
            "answer": document["answer"],
            "source": {
                "question": document["source"].get("question", ""),
                "category": document["source"].get("category", ""),
                "intent": document["source"].get("intent", ""),
            },
        }
        for document in _REAL_VECTOR_DOCS or []
    ]


def get_real_vector_documents() -> list[dict]:
    global _REAL_VECTOR_DOCS

    if _REAL_VECTOR_DOCS is None:
        _REAL_VECTOR_DOCS = load_vector_documents()

    return _REAL_VECTOR_DOCS


def build_real_vector_index() -> faiss.IndexFlatIP:
    documents = get_real_vector_documents()
    vectors = np.array(
        [build_embedding(document["text"]) for document in documents],
        dtype="float32",
    )
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


def get_real_vector_index() -> faiss.IndexFlatIP:
    global _REAL_FAISS_INDEX

    if _REAL_FAISS_INDEX is None:
        _REAL_FAISS_INDEX = build_real_vector_index()

    return _REAL_FAISS_INDEX


def save_real_vector_store() -> None:
    global _REAL_FAISS_INDEX

    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    index = build_real_vector_index()
    faiss.write_index(index, str(FAISS_INDEX_PATH))
    FAISS_DOCS_PATH.write_text(
        json.dumps(get_real_vector_documents(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _REAL_FAISS_INDEX = index


def reset_vector_store_cache() -> None:
    global _TOY_VECTOR_INDEX, _REAL_VECTOR_DOCS, _REAL_FAISS_INDEX

    _TOY_VECTOR_INDEX = None
    _REAL_VECTOR_DOCS = None
    _REAL_FAISS_INDEX = None


def load_real_vector_store() -> bool:
    global _REAL_FAISS_INDEX, _REAL_VECTOR_DOCS

    if not FAISS_INDEX_PATH.exists() or not FAISS_DOCS_PATH.exists():
        return False

    _REAL_FAISS_INDEX = faiss.read_index(str(FAISS_INDEX_PATH))
    _REAL_VECTOR_DOCS = json.loads(FAISS_DOCS_PATH.read_text(encoding="utf-8"))
    return True


def _stored_vector_store_is_compatible() -> bool:
    if _REAL_FAISS_INDEX is None or _REAL_VECTOR_DOCS is None:
        return False

    if _REAL_FAISS_INDEX.ntotal != len(_REAL_VECTOR_DOCS):
        return False

    if stored_vector_document_signature() != current_vector_document_signature():
        return False

    sample_vector = build_embedding(_REAL_VECTOR_DOCS[0]["text"])
    return _REAL_FAISS_INDEX.d == len(sample_vector)


def ensure_real_vector_store() -> faiss.IndexFlatIP:
    if load_real_vector_store() and _stored_vector_store_is_compatible():
        return get_real_vector_index()

    global _REAL_FAISS_INDEX, _REAL_VECTOR_DOCS
    _REAL_FAISS_INDEX = None
    _REAL_VECTOR_DOCS = None
    save_real_vector_store()
    return get_real_vector_index()


def build_toy_embedding(text: str) -> list[float]:
    dimensions = [
        ["退款", "退钱", "到账", "取消"],
        ["配送", "超时", "骑手", "外卖"],
        ["优惠券", "红包", "会员"],
        ["食品", "异物", "安全", "变质"],
    ]

    return [
        float(sum(1 for keyword in keywords if keyword in text))
        for keywords in dimensions
    ]


def build_toy_vector_index() -> list[dict]:
    index = []

    for document in load_vector_documents():
        index.append(
            {
                "id": document["id"],
                "vector": build_toy_embedding(document["text"]),
                "answer": document["answer"],
                "text": document["text"],
            }
        )

    return index


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = sum(a * a for a in vector_a) ** 0.5
    norm_b = sum(b * b for b in vector_b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def retrieve_by_toy_vector(query: str, limit: int = 3) -> list[dict]:
    query_vector = build_toy_embedding(query)
    candidates = []

    for document in load_vector_documents():
        document_vector = build_toy_embedding(document["text"])
        similarity = cosine_similarity(query_vector, document_vector)
        if similarity <= 0:
            continue

        candidates.append(
            {
                "score": similarity,
                "answer": document["answer"],
                "text": document["text"],
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:limit]


def retrieve_by_toy_index(query: str, limit: int = 3) -> list[dict]:
    query_vector = build_toy_embedding(query)
    index = get_toy_vector_index()
    candidates = []

    for item in index:
        similarity = cosine_similarity(query_vector, item["vector"])

        if similarity <= 0:
            continue

        candidates.append(
            {
                "score": similarity,
                "answer": item["answer"],
                "text": item["text"],
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:limit]


def build_rerank_text(candidate: dict) -> str:
    source = candidate.get("source", {})
    parts = [
        f"分类：{source.get('category', '')}",
        f"意图：{source.get('intent', '')}",
        f"问题：{source.get('question', '')}",
        f"答案：{source.get('answer', '')}",
    ]
    return "\n".join(parts)


def calculate_model_rerank_score(query: str, candidate: dict) -> float:
    return 0.0


def calculate_model_rerank_scores(
    query: str,
    candidates: list[dict],
) -> list[float]:
    if not candidates:
        return []

    pairs = [[query, build_rerank_text(candidate)] for candidate in candidates]
    model = get_reranker_model()
    scores = model.predict(pairs)
    return [float(score) for score in scores]


def detect_intent_hint(query: str) -> str:
    wrong_item_delivered = any(
        word in query
        for word in ["送错", "送错了", "给错餐", "不是我点的", "收到的是别", "收到别的"]
    )
    if wrong_item_delivered:
        return "错送餐品"

    asks_verification_code = any(word in query for word in ["验证码", "验正码", "校验码"])
    if asks_verification_code:
        return "验证码诈骗提醒"

    unaccepted_order_cancel = (
        any(word in query for word in ["商家", "店家"])
        and any(word in query for word in ["不接单", "未接单", "没接单", "没接", "半小时没接单"])
        and any(word in query for word in ["取消", "不想要"])
    )
    if unaccepted_order_cancel:
        return "取消订单"

    asks_private_fee = (
        any(word in query for word in ["骑手", "骑首", "配送员"])
        and any(word in query for word in ["加微信", "微信", "私下", "转钱", "转运费", "转配送费", "转费用"])
    )
    if asks_private_fee:
        return "私下收费风险"

    asks_refund_progress_with_pressure = (
        any(word in query for word in ["退我", "退款", "退钱", "把钱退", "钱退"])
        and any(word in query for word in ["保证", "现在", "马上", "投诉", "多久到账", "进度"])
    )
    if asks_refund_progress_with_pressure:
        return "退款进度"

    asks_spill_after_sales = (
        any(word in query for word in ["洒", "撒", "漏", "洒烂", "撒漏"])
        and (
            any(word in query for word in ["赔", "售后", "举证", "凭证", "照片"])
            or any(word in query for word in ["汤", "餐", "饭", "包装"])
        )
    )
    if asks_spill_after_sales:
        return "餐品撒漏售后"

    delivered_but_not_received = (
        (
            any(word in query for word in ["显示送达", "显示送到了", "已送达", "送到了"])
            and any(word in query for word in ["没收到", "没拿到", "没有餐", "真没拿到", "门口没有"])
        )
        or (
            any(word in query for word in ["放前台", "放门口", "放取餐柜"])
            and any(word in query for word in ["没看到", "没找到", "没有", "找不到"])
        )
    )
    if delivered_but_not_received:
        return "未收到餐"

    delivery_eta_keeps_slipping = (
        any(word in query for word in ["预计时间", "送达时间", "时间"])
        and any(word in query for word in ["往后跳", "一直变", "一直往后", "越来越晚"])
    )
    if delivery_eta_keeps_slipping:
        return "配送异常追问"

    payment_failed_but_charged = (
        any(word in query for word in ["付款失败", "支付失败", "没支付成功"])
        and any(word in query for word in ["扣", "扣款", "被扣", "扣了一次", "去哪看进度"])
    )
    if payment_failed_but_charged:
        return "退款失败"

    merchant_unresponsive_contact = (
        any(word in query for word in ["商家", "店家"])
        and any(word in query for word in ["不回复", "不回", "没回复", "一直不回复"])
        and any(word in query for word in ["平台介入", "催一下", "联系", "协助"])
    )
    if merchant_unresponsive_contact:
        return "联系商家咨询"

    address_change_follow_up = (
        any(word in query for word in ["地址写错", "地址填错", "地址写搓", "地址写搓了", "写成公司", "旧地址", "原地址", "老地址"])
        and any(word in query for word in ["骑手", "配送员"])
        and any(word in query for word in ["改送", "改地址", "到家", "回家", "快到", "已经到", "不在那"])
    )
    if address_change_follow_up:
        return "地址修改追问"

    asks_delay_compensation = (
        any(word in query for word in ["超时", "迟到", "晚了", "承诺时间"])
        and any(word in query for word in ["赔", "赔钱", "赔偿", "补偿"])
    )
    if asks_delay_compensation:
        return "延误补偿"

    asks_missing_item = (
        any(word in query for word in ["少送", "漏送", "少了一份", "少送了一份", "缺少", "没送到"])
        and not any(word in query for word in ["发票", "优惠券", "红包"])
    )
    if asks_missing_item:
        return "少送漏送"

    asks_food_safety_without_evidence = (
        any(word in query for word in ["没拍照", "没有拍照", "没包装", "没有包装", "没留证据"])
        and any(word in query for word in ["赔", "赔偿", "能赔", "一定能赔"])
    )
    if asks_food_safety_without_evidence:
        return "食品安全投诉"

    asks_rider_attitude_complaint = (
        any(word in query for word in ["骑手", "配送员"])
        and any(word in query for word in ["态度差", "态度不好", "说话太冲", "发脾气"])
    )
    if asks_rider_attitude_complaint:
        return "骑手态度投诉"

    asks_missing_food_refund_guarantee = (
        any(word in query for word in ["没收到餐", "没有收到餐", "没拿到餐", "没拿到"])
        and any(word in query for word in ["退全款", "全额退款", "全款", "退款"])
    )
    if asks_missing_food_refund_guarantee:
        return "未收到餐"

    asks_refund_progress_after_merchant_cancel = (
        any(word in query for word in ["商家取消", "店家取消", "商家取消了", "店家取消了"])
        and any(word in query for word in ["退款", "退钱", "退回", "全额退款", "承诺"])
    )
    if asks_refund_progress_after_merchant_cancel:
        return "退款进度"

    accepted_or_started_order_cancel = (
        any(word in query for word in ["商家", "店家"])
        and any(word in query for word in ["接单", "开始做", "已经做", "制作", "还没送"])
        and any(word in query for word in ["不想要", "取消", "退全款", "全款", "全额退", "强制全额", "不扣钱", "强制取消"])
        and not any(word in query for word in ["不接单", "未接单", "没接单"])
    )
    if accepted_or_started_order_cancel:
        return "接单后取消"

    asks_refund_amount_guarantee = (
        any(word in query for word in ["全款", "全额", "扣除", "扣钱", "只退", "少退"])
        and any(word in query for word in ["一定", "保证", "承诺", "直接说", "截图"])
    )
    if asks_refund_amount_guarantee:
        return "退款金额咨询"

    rider_unreachable = (
        any(word in query for word in ["骑手", "配送员"])
        and any(word in query for word in ["联系不上", "联系不到", "打不通", "电话不接", "无法联系"])
    )
    if rider_unreachable:
        return "配送异常追问"

    coupon_unavailable = (
        any(word in query for word in ["优惠券", "红包", "券"])
        and any(word in query for word in ["不能用", "用不了", "没用上", "不可用", "无法使用", "结算时不能用", "不能抵", "没抵", "不抵", "抵不了"])
    )
    if coupon_unavailable:
        return "优惠券不可用"

    asks_refund_amount_review = (
        (
            any(word in query for word in ["不是问骑手", "不是问商家", "我想问", "重点是"])
            and any(word in query for word in ["扣费", "扣钱", "配送费", "没全退", "只退", "少退"])
        )
        or (
            any(word in query for word in ["没收到", "没拿到", "没吃上", "没用餐"])
            and any(word in query for word in ["扣费", "扣钱", "配送费", "没全退", "只退", "少退"])
        )
    )
    if asks_refund_amount_review:
        return "退款金额咨询"

    asks_refund_time_priority = (
        any(word in query for word in ["钱什么时候退", "钱啥时候退", "钱多久退", "多久到账", "退回来"])
        and any(word in query for word in ["重点", "主要", "想问", "问的是", "取消", "超时"])
    )
    if asks_refund_time_priority:
        return "退款进度"

    has_real_phone_privacy = any(
        word in query
        for word in ["真实手机号", "看到我的手机号", "看到我手机号", "知道我的手机号", "完整手机号"]
    )
    if has_real_phone_privacy:
        return "隐私保护咨询"

    if "平台客服" in query and "手机号" in query:
        return "隐私保护咨询"

    asks_platform_contact_merchant = (
        any(word in query for word in ["帮我打给", "直接帮我打", "直接私下打给", "私下打给", "私下替我打给", "帮我联系", "客服联系"])
        and any(word in query for word in ["商家", "店家"])
    )
    if asks_platform_contact_merchant:
        return "联系商家咨询"

    rider_arrival_location_mismatch = (
        "骑手" in query
        and any(word in query for word in ["说到了", "显示到了", "说已到"])
        and any(word in query for word in ["定位", "位置"])
    )
    if rider_arrival_location_mismatch:
        return "配送异常追问"

    has_merchant = any(word in query for word in ["商家", "店家"])
    has_phone = any(word in query for word in ["手机号", "联系电话", "电话"])
    if has_merchant and has_phone:
        return "商家电话咨询"

    has_refund_context = any(
        word in query
        for word in ["没吃上", "没收到", "没用餐", "取消", "只退", "少退"]
    )
    has_money_dispute = any(
        word in query
        for word in ["扣钱", "扣我钱", "扣了钱", "没全退", "只退", "少退"]
    )
    if has_refund_context and has_money_dispute:
        return "退款金额咨询"

    return ""


def supplement_candidates_by_intent_hint(
    candidates: list[dict],
    intent_hint: str,
    limit: int = 3,
) -> list[dict]:
    if not intent_hint:
        return candidates

    existing_questions = {
        candidate.get("source", {}).get("question", "")
        for candidate in candidates
    }
    supplemented = list(candidates)

    for document in get_real_vector_documents():
        source = document.get("source", {})
        if source.get("intent") != intent_hint:
            continue
        if source.get("question", "") in existing_questions:
            continue

        supplemented.append(
            {
                "score": INTENT_HINT_SUPPLEMENT_SCORE,
                "vector_score": 0.0,
                "keyword_bonus": 0.0,
                "answer": document["answer"],
                "text": document["text"],
                "source": source,
                "direction_penalty": 0.0,
                "_retrieval_origin": "intent_hint_supplement",
            }
        )
        existing_questions.add(source.get("question", ""))
        if len(supplemented) - len(candidates) >= limit:
            break

    return supplemented


def rerank_candidates(
    query: str,
    candidates: list[dict],
    model_rerank_weight: float = DEFAULT_MODEL_RERANK_WEIGHT,
) -> list[dict]:
    reranked_candidates = []
    intent_hint = detect_intent_hint(query)
    model_rerank_scores = calculate_model_rerank_scores(query, candidates)
    if len(model_rerank_scores) != len(candidates):
        raise ValueError("Model rerank scores count must match candidates count.")

    for index, candidate in enumerate(candidates):
        reranked_candidate = candidate.copy()
        model_rerank_score = model_rerank_scores[index]
        rerank_score = candidate["score"] + model_rerank_score * model_rerank_weight
        source = candidate.get("source", {})
        intent = source.get("intent", "")
        question = source.get("question", "")

        if "怎么办" in query and ("追问" in intent or "追问" in question):
            rerank_score += 0.02

        if intent_hint and intent == intent_hint:
            rerank_score += INTENT_HINT_BONUS
            if (
                intent_hint == "优惠券不可用"
                and "优惠券" in query
                and "满减" in query
            ):
                rerank_score += 0.02

        reranked_candidate["rerank_score"] = rerank_score
        reranked_candidate["model_rerank_score"] = model_rerank_score
        reranked_candidates.append(reranked_candidate)

    reranked_candidates.sort(
        key=lambda candidate: candidate["rerank_score"],
        reverse=True,
    )
    return reranked_candidates


def _search_real_faiss(query: str, top_k: int) -> list[tuple[int, float]]:
    query_vector = np.array([build_embedding(query)], dtype="float32")
    index = ensure_real_vector_store()
    scores, indices = index.search(query_vector, top_k)
    results = []

    for doc_index, score in zip(indices[0], scores[0]):
        if doc_index < 0:
            continue
        results.append((int(doc_index), float(score)))

    return results


def _build_raw_candidate(query: str, document: dict, similarity: float, use_hybrid: bool) -> dict:
    bonus = calculate_keyword_bonus(query, document["source"]) if use_hybrid else 0.0
    penalty = calculate_direction_penalty(query, document["source"]) if use_hybrid else 0.0
    final_score = similarity + bonus - penalty

    return {
        "score": final_score,
        "vector_score": similarity,
        "keyword_bonus": bonus,
        "answer": document["answer"],
        "text": document["text"],
        "source": document["source"],
        "direction_penalty": penalty,
    }


def _dedupe_candidates(candidates: list[dict], limit: int) -> list[dict]:
    deduped = []
    seen_answers = set()

    for item in candidates:
        if item["answer"] in seen_answers:
            continue

        if any(is_similar_answer(item["answer"], seen_answer) for seen_answer in seen_answers):
            continue

        seen_answers.add(item["answer"])
        deduped.append(item)

        if len(deduped) >= limit:
            break

    return deduped


def retrieve_by_real_vector(
    query: str,
    limit: int = 3,
    min_score: float = DEFAULT_MIN_VECTOR_SCORE,
    use_hybrid: bool = True,
    rerank_weight: float = DEFAULT_MODEL_RERANK_WEIGHT,
) -> list[dict]:
    top_k = max(limit * 5, 20)
    faiss_hits = _search_real_faiss(query, top_k=top_k)
    documents = get_real_vector_documents()
    raw_candidates = []

    for doc_index, similarity in faiss_hits:
        if similarity < min_score:
            continue

        document = documents[doc_index]
        candidate = _build_raw_candidate(query, document, similarity, use_hybrid)
        candidate["_retrieval_origin"] = "faiss"
        raw_candidates.append(candidate)

    raw_candidates = supplement_candidates_by_intent_hint(
        raw_candidates,
        detect_intent_hint(query),
    )
    raw_candidates = rerank_candidates(
        query,
        raw_candidates,
        model_rerank_weight=rerank_weight,
    )

    return _dedupe_candidates(raw_candidates, limit)


def retrieve_rag_documents(
    query: str,
    limit: int = 3,
    min_score: float = DEFAULT_MIN_VECTOR_SCORE,
) -> list[str]:
    return [candidate["answer"] for candidate in retrieve_rag_items(query, limit, min_score)]


def retrieve_rag_items(
    query: str,
    limit: int = 3,
    min_score: float = DEFAULT_MIN_VECTOR_SCORE,
) -> list[dict]:
    candidates = retrieve_by_real_vector(
        query,
        limit=limit,
        min_score=min_score,
        use_hybrid=True,
    )
    items = []
    for rank, candidate in enumerate(candidates, start=1):
        source = candidate.get("source", {})
        items.append(
            {
                "rank": rank,
                "answer": candidate["answer"],
                "category": source.get("category", ""),
                "intent": source.get("intent", ""),
                "question": source.get("question", ""),
                "score": candidate.get("score", 0.0),
                "rerank_score": candidate.get("rerank_score", 0.0),
                "model_rerank_score": candidate.get("model_rerank_score", 0.0),
                "vector_score": candidate.get("vector_score", 0.0),
                "keyword_bonus": candidate.get("keyword_bonus", 0.0),
                "direction_penalty": candidate.get("direction_penalty", 0.0),
                "retrieval_origin": candidate.get("_retrieval_origin", "faiss"),
            }
        )
    return items


def calculate_direction_penalty(query: str, source: dict) -> float:
    category = source.get("category", "")
    intent = source.get("intent", "")
    question = source.get("question", "")

    food_safety_query = any(
        word in query
        for word in ["餐品", "异物", "食品", "变质", "吃坏", "赔"]
    )
    clearly_unrelated_food_intents = [
        "发票",
        "优惠",
        "赠品",
        "会员",
        "金额",
    ]
    if food_safety_query and any(
        word in f"{category}{intent}{question}"
        for word in clearly_unrelated_food_intents
    ):
        return 0.15

    has_timeout = "超时" in query
    has_cancel_intent = any(
        word in query
        for word in ["取消", "不想要", "不要了", "退款", "退单"]
    )

    if has_timeout and not has_cancel_intent and intent == "超时取消":
        return 0.08

    user_contact_rider = "联系不上" in query and (
        "骑手" in query or "配送员" in query
    )

    rider_contact_user = (
        ("骑手" in question or "配送员" in question)
        and ("联系不到我" in question or "联系不上我" in question)
    )

    if user_contact_rider and rider_contact_user:
        return 0.08

    return 0.0


def calculate_keyword_bonus(query: str, source: dict) -> float:
    field_weights = {
        "intent": 0.04,
        "category": 0.03,
        "question": 0.02,
        "answer": 0.01,
    }
    keyword_weights = {
        "退款": 1.0,
        "到账": 1.0,
        "进度": 0.8,
        "会员": 0.3,
        "退回来": 1.0,
        "退钱": 1.0,
        "钱": 0.6,
        "多久": 0.5,
        "餐品": 1.0,
        "异物": 1.2,
        "食品": 1.0,
        "安全": 1.0,
        "变质": 1.0,
        "售后": 0.8,
        "投诉": 0.8,
        "赔": 0.6,
    }

    bonus = 0.0

    for field_name, field_weight in field_weights.items():
        field_text = source.get(field_name, "")

        for keyword, keyword_weight in keyword_weights.items():
            if keyword in query and keyword in field_text:
                bonus += field_weight * keyword_weight

    return bonus
