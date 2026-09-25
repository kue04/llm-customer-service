from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path

try:
    import faiss
except ModuleNotFoundError:
    faiss = None
import numpy as np
try:
    from sentence_transformers import CrossEncoder, SentenceTransformer
except ModuleNotFoundError:
    CrossEncoder = None
    SentenceTransformer = None

from config.rag_config import get_rag_config
from utils.retrieval_dedup import (  # noqa: F401  (B17：Top-K 内容级去重)
    candidates_from_hits,
    oversampled_top_k,
    select_diverse_evidence,
)
from utils.retriever import iter_knowledge_items, is_similar_answer

# 阶段 3.4：chunk 级索引的 manifest 由 ingestion 子系统定义（单一真源）。
# 方向说明：这里出现 services → utils 的反向依赖是**刻意**的 ——
# 索引产物的格式必须只有一份定义（构建方与检索方共用），
# 否则「构建时这样写、检索时那样读」就是下一次静默错位的来源。
# 该模块只依赖标准库，不会把 SQLAlchemy / 数据库会话拉进检索层。
from services.ingestion.index_manifest import (
    DEFAULT_INDEX_NAME,
    IndexManifest,
    IndexManifestError,
    ManifestEntry,
    load_active_manifest,
)


_TOY_VECTOR_INDEX: list[dict] | None = None
_RERANKER_MODEL: CrossEncoder | None = None
_EMBEDDING_MODEL: SentenceTransformer | None = None
_REAL_VECTOR_DOCS: list[dict] | None = None
_REAL_FAISS_INDEX: faiss.IndexFlatIP | None = None

DEFAULT_MODEL_RERANK_WEIGHT = get_rag_config().model_rerank_weight
DEFAULT_MIN_VECTOR_SCORE = get_rag_config().min_vector_score


def _require_faiss():
    if faiss is None:
        raise RuntimeError("faiss is not installed; install faiss-cpu to use the real vector store")
    return faiss


def _require_sentence_transformers():
    if CrossEncoder is None or SentenceTransformer is None:
        raise RuntimeError("sentence-transformers is not installed; install it to use model reranking and embeddings")
    return CrossEncoder, SentenceTransformer


def _knowledge_id_from_source(source: dict, rank: int) -> str:
    if source.get("id"):
        return str(source["id"])
    raw_key = "|".join(
        str(source.get(field, ""))
        for field in ("question", "answer", "category", "intent")
    ).strip("|")
    if not raw_key:
        return f"kb_rank_{rank}"
    return f"kb_{hashlib.md5(raw_key.encode('utf-8')).hexdigest()[:12]}"


def _knowledge_version_from_source(source: dict) -> str:
    if source.get("version"):
        return str(source["version"])
    knowledge_id = str(source.get("id", ""))
    if "_v" in knowledge_id:
        return f"v{knowledge_id.rsplit('_v', 1)[-1]}"
    if source.get("quality") == "reviewed":
        return "reviewed"
    return "seed"
VECTOR_STORE_DIR = get_rag_config().faiss_store_dir
FAISS_INDEX_PATH = VECTOR_STORE_DIR / "real_vector.index"
FAISS_DOCS_PATH = VECTOR_STORE_DIR / "real_vector_docs.json"
INTENT_HINT_BONUS = 0.10
INTENT_HINT_SUPPLEMENT_SCORE = 0.80


def get_reranker_model() -> CrossEncoder:
    global _RERANKER_MODEL

    if _RERANKER_MODEL is None:
        cross_encoder_cls, _ = _require_sentence_transformers()
        _RERANKER_MODEL = cross_encoder_cls(get_rag_config().reranker_model_name)

    return _RERANKER_MODEL


def get_toy_vector_index() -> list[dict]:
    global _TOY_VECTOR_INDEX

    if _TOY_VECTOR_INDEX is None:
        _TOY_VECTOR_INDEX = build_toy_vector_index()

    return _TOY_VECTOR_INDEX


def get_embedding_model() -> SentenceTransformer:
    global _EMBEDDING_MODEL

    if _EMBEDDING_MODEL is None:
        _, sentence_transformer_cls = _require_sentence_transformers()
        _EMBEDDING_MODEL = sentence_transformer_cls(get_rag_config().embedding_model_name)

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
    faiss_module = _require_faiss()
    documents = get_real_vector_documents()
    vectors = np.array(
        [build_embedding(document["text"]) for document in documents],
        dtype="float32",
    )
    index = faiss_module.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


def get_real_vector_index() -> faiss.IndexFlatIP:
    global _REAL_FAISS_INDEX

    if _REAL_FAISS_INDEX is None:
        _REAL_FAISS_INDEX = build_real_vector_index()

    return _REAL_FAISS_INDEX


def save_real_vector_store() -> None:
    global _REAL_FAISS_INDEX

    faiss_module = _require_faiss()
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    index = build_real_vector_index()
    faiss_module.write_index(index, str(FAISS_INDEX_PATH))
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

    faiss_module = _require_faiss()
    _REAL_FAISS_INDEX = faiss_module.read_index(str(FAISS_INDEX_PATH))
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

    asks_food_safety = any(
        word in query
        for word in ["食品安全", "异物", "变质", "发霉", "吃坏", "拉肚子", "过敏", "发麻"]
    )
    if asks_food_safety:
        return "食品安全投诉"

    asks_off_platform_refund = (
        any(word in query for word in ["商家微信", "店家微信", "平台外", "别走平台", "不走平台", "绕开平台", "私下退款", "私下退钱"])
        or ("私下" in query and any(word in query for word in ["退款", "退钱", "让他退", "商家退"]))
    )
    if asks_off_platform_refund:
        return "站外交易风险"

    asks_sensitive_identity = any(
        word in query
        for word in ["身份证", "身份证信息", "真实手机号", "完整手机号", "真实号码", "完整号码"]
    )
    if asks_sensitive_identity:
        return "隐私保护咨询"

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
        knowledge_id = _knowledge_id_from_source(source, rank)
        items.append(
            {
                "rank": rank,
                "knowledge_id": knowledge_id,
                "title": source.get("title", "") or source.get("question", "") or knowledge_id,
                "version": _knowledge_version_from_source(source),
                "updated_at": source.get("updated_at", "") or source.get("effective_at", ""),
                "source": source.get("source", ""),
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


# ============================================================================
# 阶段 3.4：chunk 级索引检索（manifest + 强制 tenant / ACL 过滤）
# ============================================================================
#
# 与上面那套「种子 FAQ 索引」的关系
# ---------------------------------
# 上面 `retrieve_*` 系列读的是手工整理的 FAQ（A 轨），**签名里没有 tenant / acl** ——
# 它不是"忘了加"，而是它的数据源本来就是单租户的种子语料。B6 不做"给它加过滤"，
# 而是**另开一条 chunk 级检索路径**：数据来自 document_chunks → manifest，
# 天然带租户与 ACL。B7 的 4.3 会把 API 层接到这条路径上（并补检索层的隔离测试）。
#
# 为什么过滤必须"前置"而不是"检索后再筛"
# --------------------------------------
# 后过滤（先全局 top-k 再按租户/ACL 筛）在本项目实测过：当目标租户只占语料 0.1% 时，
# `top-50` 筛完**返回 0 条** —— 答案被其他租户挤出了全局 top-k，系统会对用户说
# "没找到资料"，而证据其实就在库里。这类失败没有任何报错，只是"查不到"，
# 因此这里用 FAISS 原生预过滤（`SearchParameters(sel=IDSelectorBatch(...))`），
# 只在允许的 row_id 集合内检索。
#
# 为什么缺 filter 必须**报错**而不是默认全库
# -----------------------------------------
# 默认全库 = "少传一个参数就把所有租户的数据都检索出来"，而调用方在写代码时
# 根本不会注意到 —— 这正是最容易被漏掉、后果又最严重的一种默认值。
# 因此 `access` 是必填关键字参数，显式传 None 也会抛错（有负向测试）。


logger = logging.getLogger(__name__)

#: 默认索引名（与 index_builder 一致）
CHUNK_INDEX_NAME = DEFAULT_INDEX_NAME

ERROR_FILTER_REQUIRED = "chunk_filter_required"
ERROR_FILTER_TENANT_MISSING = "chunk_filter_tenant_missing"
ERROR_INDEX_UNAVAILABLE = "chunk_index_unavailable"
ERROR_EMBEDDING_MODEL_MISMATCH = "embedding_model_mismatch"


class ChunkRetrievalError(RuntimeError):
    """chunk 级检索的错误（``error_code`` 稳定，供 API 层翻译成状态码）。"""

    def __init__(self, error_code: str, message: str, *, detail: dict | None = None) -> None:
        self.error_code = error_code
        self.message = message
        self.detail = dict(detail or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ChunkAccessFilter:
    """**服务端构造**的授权过滤器（不由客户端传入）。

    ``allowed_chunk_ids`` 的含义：

    * ``None`` —— 不额外限制（仍强制按 ``tenant_id`` 过滤）；
      适用于"本租户公开知识库"这类没有资源级 ACL 的场景；
    * 集合 —— 只允许这些 chunk 可见（B7 从 AuthContext + document ACL + 发布状态
      算出来）。**空集合等价于"什么都看不到"**，而不是"不限" ——
      这个方向的默认值选错一次就是全量泄漏。
    """

    tenant_id: str
    allowed_chunk_ids: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ChunkRetrievalError(
                ERROR_FILTER_TENANT_MISSING,
                "access filter 必须带非空 tenant_id（租户是过滤的第一判据，不允许缺省）",
            )
        if self.allowed_chunk_ids is not None:
            object.__setattr__(
                self, "allowed_chunk_ids", frozenset(str(item) for item in self.allowed_chunk_ids)
            )


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """一条命中：chunk + document + version + tenant + ACL + 来源元数据（计划 3.4 要求）。"""

    score: float
    row_id: int
    chunk_id: str
    text: str
    tenant_id: str
    document_id: str
    document_version: int
    document_version_id: str
    chunk_type: str
    ordinal: int
    heading_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None
    acl: tuple[dict, ...] = ()
    content_hash: str = ""
    token_count: int = 0
    source_uri: str = ""
    filename: str = ""
    document_title: str = ""
    source_type: str = ""
    oversize: bool = False

    @property
    def heading_text(self) -> str:
        return self.heading_path[-1] if self.heading_path else ""

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "row_id": self.row_id,
            "chunk_id": self.chunk_id,
            "text": self.text,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "document_version_id": self.document_version_id,
            "chunk_type": self.chunk_type,
            "ordinal": self.ordinal,
            "heading_path": list(self.heading_path),
            "heading_text": self.heading_text,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "acl": [dict(item) for item in self.acl],
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "source_uri": self.source_uri,
            "filename": self.filename,
            "document_title": self.document_title,
            "source_type": self.source_type,
            "oversize": self.oversize,
        }


def chunk_index_root() -> Path:
    """chunk 索引根目录（与 ``index_builder`` 的默认根一致）。"""

    return VECTOR_STORE_DIR


def load_chunk_index(
    *,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> tuple[IndexManifest, faiss.IndexFlatIP]:
    """按指针加载当前生效的 chunk 索引；没有生效索引时抛 ``chunk_index_unavailable``。"""

    faiss_module = _require_faiss()
    root_path = Path(root) if root is not None else chunk_index_root()
    try:
        manifest, _pointer, index_file = load_active_manifest(root_path, index_name)
    except IndexManifestError as error:
        raise ChunkRetrievalError(
            ERROR_INDEX_UNAVAILABLE, f"没有可用的 chunk 索引：{error}", detail=error.detail
        ) from error
    if not index_file.exists():
        raise ChunkRetrievalError(
            ERROR_INDEX_UNAVAILABLE,
            f"索引文件不存在：{index_file}",
            detail={"path": str(index_file)},
        )
    return manifest, faiss_module.read_index(str(index_file))


def search_chunk_index(
    query: str,
    *,
    access: ChunkAccessFilter,
    top_k: int = 10,
    min_score: float | None = None,
    embedder: Callable[[str], list[float]] | None = None,
    embedding_model: str = "",
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> list[ChunkHit]:
    """在**授权范围内**检索 chunk。

    ``access`` 必填且必须是非空的 :class:`ChunkAccessFilter`；
    显式传 ``None`` 会抛 :class:`ChunkRetrievalError`（有负向测试锁定）。
    ``min_score`` 默认为 ``None``：分数阈值是**质量**取舍，
    而权限过滤是**安全**要求，两者不应该共用一个"默认值" ——
    给安全相关的分支设默认值，等于给未来的自己埋一个静默放宽。
    """

    if access is None:
        raise ChunkRetrievalError(
            ERROR_FILTER_REQUIRED,
            "检索必须传入服务端构造的 access filter；缺失 filter 一律拒绝（不默认全库检索）",
        )
    if not isinstance(access, ChunkAccessFilter):
        raise ChunkRetrievalError(
            ERROR_FILTER_REQUIRED,
            f"access filter 类型不对：{type(access).__name__}；必须由服务端构造 ChunkAccessFilter",
        )

    manifest, index = load_chunk_index(root=root, index_name=index_name)

    if embedding_model and embedding_model != manifest.embedding_model:
        # 模型不同 → 向量空间不可比 → 分数无意义（不报错的话会表现为"检索质量突然变差"）
        raise ChunkRetrievalError(
            ERROR_EMBEDDING_MODEL_MISMATCH,
            "查询用的 embedding 模型与索引构建时不一致",
            detail={"query_model": embedding_model, "index_model": manifest.embedding_model},
        )
    if not embedding_model:
        logger.warning(
            "search_chunk_index 未传 embedding_model，跳过模型一致性校验（索引用的是 %s）",
            manifest.embedding_model,
        )

    allowed_entries = [
        entry
        for entry in manifest.entries
        if entry.tenant_id == access.tenant_id
        and (access.allowed_chunk_ids is None or entry.chunk_id in access.allowed_chunk_ids)
    ]
    if not allowed_entries:
        # **fail closed**：授权范围内没有任何候选时直接返回空，
        # 绝不"退化成不限租户"再检索一次。
        return []

    allowed_row_ids = np.asarray([entry.row_id for entry in allowed_entries], dtype="int64")
    selector = faiss.IDSelectorBatch(allowed_row_ids)
    params = faiss.SearchParameters(sel=selector)

    embed = embedder or build_embedding
    query_vector = np.array([embed(query)], dtype="float32")
    scores, indices = index.search(query_vector, max(int(top_k), 1), params=params)

    allowed_set = {int(row_id) for row_id in allowed_row_ids}
    hits: list[ChunkHit] = []
    for row_id, score in zip(indices[0], scores[0]):
        position = int(row_id)
        if position < 0:  # FAISS 用 -1 表示"没有更多候选"
            continue
        if position not in allowed_set:  # 双保险：预过滤之外的兜底断言
            continue
        if min_score is not None and float(score) < min_score:
            continue
        entry: ManifestEntry = manifest.entries[position]
        hits.append(
            ChunkHit(
                score=float(score),
                row_id=entry.row_id,
                chunk_id=entry.chunk_id,
                text=entry.text,
                tenant_id=entry.tenant_id,
                document_id=entry.document_id,
                document_version=entry.document_version,
                document_version_id=entry.document_version_id,
                chunk_type=entry.chunk_type,
                ordinal=entry.ordinal,
                heading_path=entry.heading_path,
                page_start=entry.page_start,
                page_end=entry.page_end,
                acl=entry.acl,
                content_hash=entry.content_hash,
                token_count=entry.token_count,
                source_uri=entry.source_uri,
                filename=entry.filename,
                document_title=entry.document_title,
                source_type=entry.source_type,
                oversize=entry.oversize,
            )
        )
    return hits


def retrieve_chunk_items(
    query: str,
    *,
    access: ChunkAccessFilter,
    limit: int = 3,
    min_score: float | None = None,
    embedder: Callable[[str], list[float]] | None = None,
    embedding_model: str = "",
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> list[dict]:
    """chunk 级检索的「条目」形态（形状对齐 ``retrieve_rag_items``，便于下游替换）。

    字段里同时给出 ``chunk_id`` / ``document_id`` / ``document_version`` / ``tenant_id``
    / ``acl`` / ``heading_path`` / 页码 / 来源，命中即自证来源（3.4 的要求）。

    **B17**：候选按 ``limit × 5``（下限 20）超额召回 → **内容级去重** → 再截断到 ``limit``。
    去重在 :func:`~utils.vector_retriever.search_chunk_index` **之后**做，
    因此它看到的所有命中都已通过服务端权限预过滤——"留下的那条一定是他有权看的"
    （顺序颠倒会让无权副本挤掉有权副本，且**静默**）。详见 ``utils/retrieval_dedup.py``。
    """

    top_k = oversampled_top_k(limit)
    hits = search_chunk_index(
        query,
        access=access,
        top_k=top_k,
        min_score=min_score,
        embedder=embedder,
        embedding_model=embedding_model,
        root=root,
        index_name=index_name,
    )
    selected = select_diverse_evidence(
        candidates_from_hits(hits), limit=max(int(limit), 0)
    )

    items: list[dict] = []
    for rank, candidate in enumerate(selected, start=1):
        hit = candidate.payload
        items.append(
            {
                "rank": rank,
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "document_version": hit.document_version,
                "document_version_id": hit.document_version_id,
                "tenant_id": hit.tenant_id,
                "title": hit.heading_text or hit.document_title or hit.chunk_id,
                "document_title": hit.document_title,
                "chunk_type": hit.chunk_type,
                "heading_path": list(hit.heading_path),
                "page_start": hit.page_start,
                "page_end": hit.page_end,
                "acl": [dict(item) for item in hit.acl],
                "source_uri": hit.source_uri,
                "filename": hit.filename,
                "source_type": hit.source_type,
                "content_hash": hit.content_hash,
                "token_count": hit.token_count,
                "text": hit.text,
                "score": hit.score,
                "retrieval_origin": "chunk-index",
            }
        )
    return items


def describe_chunk_index(
    *,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> dict:
    """当前生效 chunk 索引的自我描述（**不加载向量**，B7 新增）。

    API 层需要告诉调用方"你查的是哪一版索引"——尤其是**零命中**的时候：
    零命中可能是"没权限"，也可能是"索引不存在/太旧"，两者对排障的意义完全不同。
    这里只读 manifest（几 KB），不 ``read_index``，避免为了回一个元信息
    把整个向量文件读进内存。

    没有生效索引时抛 ``chunk_index_unavailable``（与检索路径同一个错误码）。
    """

    root_path = Path(root) if root is not None else chunk_index_root()
    try:
        manifest, pointer, _index_file = load_active_manifest(root_path, index_name)
    except IndexManifestError as error:
        raise ChunkRetrievalError(
            ERROR_INDEX_UNAVAILABLE, f"没有可用的 chunk 索引：{error}", detail=error.detail
        ) from error
    return {
        "index_name": manifest.index_name,
        "index_version": int(pointer.index_version),
        "embedding_model": manifest.embedding_model,
        "embedding_dimension": int(manifest.embedding_dimension),
        "built_at": manifest.built_at,
        "chunk_count": int(manifest.chunk_count),
        "tokenizer_id": manifest.tokenizer_id,
        "scope": str(manifest.extra.get("scope", "")),
        "tenants": [str(item) for item in (manifest.extra.get("tenants") or ())],
        # B8：稀疏路可用性由 manifest 的 ``extra["sparse"]`` 声明决定。
        # 这里**不需要**去碰文件系统 —— 声明与文件是否一致由
        # ``verify_sparse_index`` 在构建/回滚时保证；查询期再 stat 一次
        # 只会引入"声明有、文件没了"的第三种状态，而那种情况由
        # ``utils.sparse_retriever.load_sparse_index`` 显式报错更合适。
        "sparse_available": bool(manifest.extra.get("sparse")),
        "sparse_index_file": str((manifest.extra.get("sparse") or {}).get("file", "")),
        "sparse_gram_algorithm": str(
            (manifest.extra.get("sparse") or {}).get("gram_algorithm", "")
        ),
    }
