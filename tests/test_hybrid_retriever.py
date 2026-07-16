import pytest

from utils.hybrid_retriever import reciprocal_rank_fusion
from utils.lexical_retriever import LexicalHit


def test_rrf_prefers_document_hit_by_both_routes():
    results = reciprocal_rank_fusion(
        dense_hits=[(0, 0.90), (1, 0.80)],
        lexical_hits=[
            LexicalHit(doc_index=1, score=8.0, rank=1),
            LexicalHit(doc_index=2, score=7.0, rank=2),
        ],
        top_k=3,
        rrf_k=60,
    )

    assert results[0]["doc_index"] == 1
    assert results[0]["retrieval_origin"] == "dense+lexical"
    assert results[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_keeps_lexical_only_document():
    results = reciprocal_rank_fusion(
        dense_hits=[(0, 0.90)],
        lexical_hits=[LexicalHit(doc_index=2, score=7.0, rank=1)],
        top_k=2,
    )

    lexical = next(item for item in results if item["doc_index"] == 2)
    assert lexical["dense_rank"] is None
    assert lexical["lexical_rank"] == 1
    assert lexical["retrieval_origin"] == "lexical"
