
from sentence_transformers import SentenceTransformer
from utils.retriever import iter_knowledge_items, is_similar_answer


_TOY_VECTOR_INDEX: list[dict] | None = None

_EMBEDDING_MODEL = None

_REAL_VECTOR_INDEX: list[dict] | None = None

def get_toy_vector_index() -> list[dict]:
    global _TOY_VECTOR_INDEX

    if _TOY_VECTOR_INDEX is None:
        _TOY_VECTOR_INDEX = build_toy_vector_index()

    return _TOY_VECTOR_INDEX

def get_embedding_model() -> SentenceTransformer:
    global _EMBEDDING_MODEL

    if _EMBEDDING_MODEL is None:
        _EMBEDDING_MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5")

    return _EMBEDDING_MODEL

def build_real_vector_index() -> list[dict]:
    index = []

    for document in load_vector_documents():
        index.append(
            {
                "id": document["id"],
                "vector": build_embedding(document["text"]),
                "answer": document["answer"],
                "text": document["text"],
                "source": document["source"],
            }
        )

    return index


def get_real_vector_index() -> list[dict]:
    global _REAL_VECTOR_INDEX

    if _REAL_VECTOR_INDEX is None:
        _REAL_VECTOR_INDEX = build_real_vector_index()

    return _REAL_VECTOR_INDEX


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


def retrieve_by_real_vector(
    query: str,
    limit: int = 3,
    min_score: float = 0.62,
    use_hybrid: bool = True,
) -> list[dict]:
    query_vector = build_embedding(query)
    index = get_real_vector_index()
    raw_candidates = []

    for item in index:
        similarity = cosine_similarity(query_vector, item["vector"])

        if similarity < min_score:
            continue

        bonus = calculate_keyword_bonus(query, item["source"]) if use_hybrid else 0.0
        penalty = calculate_direction_penalty(query, item["source"]) if use_hybrid else 0.0
        final_score = similarity + bonus - penalty

        raw_candidates.append(
            {
                "score": final_score,
                "vector_score": similarity,
                "keyword_bonus": bonus,
                "answer": item["answer"],
                "text": item["text"],
                "source": item["source"],
                "direction_penalty": penalty,
            }
        )

    raw_candidates.sort(key=lambda item: item["score"], reverse=True)

    candidates = []
    seen_answers = set()

    for item in raw_candidates:
        if item["answer"] in seen_answers:
            continue

        if any(is_similar_answer(item["answer"], seen_answer) for seen_answer in seen_answers):
            continue

        seen_answers.add(item["answer"])
        candidates.append(item)

        if len(candidates) >= limit:
            break

    return candidates


def calculate_direction_penalty(query: str, source: dict) -> float:
    question = source.get("question", "")

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
    }

    bonus = 0.0

    for field_name, field_weight in field_weights.items():
        field_text = source.get(field_name, "")

    for keyword, keyword_weight in keyword_weights.items():
        if keyword in query and keyword in field_text:
            bonus += field_weight * keyword_weight

    return bonus
