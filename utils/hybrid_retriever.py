from utils.lexical_retriever import LexicalHit


DEFAULT_DENSE_TOP_K = 30
DEFAULT_LEXICAL_TOP_K = 30
DEFAULT_FUSION_TOP_K = 20
DEFAULT_RRF_K = 60
DEFAULT_RERANKER_TOP_N = 20


def reciprocal_rank_fusion(
    dense_hits: list[tuple[int, float]],
    lexical_hits: list[LexicalHit],
    top_k: int = DEFAULT_FUSION_TOP_K,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[dict]:
    fused: dict[int, dict] = {}
    for rank, (doc_index, score) in enumerate(dense_hits, start=1):
        fused[int(doc_index)] = {
            "doc_index": int(doc_index),
            "dense_rank": rank,
            "lexical_rank": None,
            "dense_score": float(score),
            "lexical_score": 0.0,
        }

    for hit in lexical_hits:
        item = fused.setdefault(
            hit.doc_index,
            {
                "doc_index": hit.doc_index,
                "dense_rank": None,
                "lexical_rank": None,
                "dense_score": 0.0,
                "lexical_score": 0.0,
            },
        )
        item["lexical_rank"] = hit.rank
        item["lexical_score"] = hit.score

    results = []
    for item in fused.values():
        rrf_score = 0.0
        if item["dense_rank"] is not None:
            rrf_score += 1.0 / (rrf_k + item["dense_rank"])
        if item["lexical_rank"] is not None:
            rrf_score += 1.0 / (rrf_k + item["lexical_rank"])
        if item["dense_rank"] is not None and item["lexical_rank"] is not None:
            origin = "dense+lexical"
        elif item["lexical_rank"] is not None:
            origin = "lexical"
        else:
            origin = "dense"
        results.append({**item, "rrf_score": rrf_score, "retrieval_origin": origin})

    results.sort(key=lambda item: item["rrf_score"], reverse=True)
    return results[:top_k]
