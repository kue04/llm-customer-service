from fastapi import APIRouter

from schemas.retrieval_schema import (
    RetrievalResultItem,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from utils.vector_retriever import retrieve_by_real_vector


router = APIRouter()


@router.post("/search", response_model=RetrievalSearchResponse)
def search_retrieval(request: RetrievalSearchRequest) -> RetrievalSearchResponse:
    candidates = retrieve_by_real_vector(
        request.query,
        limit=request.limit,
        min_score=request.min_score,
        use_hybrid=request.mode == "hybrid",
    )

    results = []
    for rank, item in enumerate(candidates, start=1):
        source = item["source"]
        results.append(
            RetrievalResultItem(
                rank=rank,
                score=item["score"],
                rerank_score=item["rerank_score"],
                model_rerank_score=item["model_rerank_score"],
                vector_score=item["vector_score"],
                keyword_bonus=item["keyword_bonus"],
                direction_penalty=item.get("direction_penalty", 0.0),
                category=source.get("category", ""),
                intent=source.get("intent", ""),
                question=source.get("question", ""),
                answer=item["answer"],
            )
        )

    return RetrievalSearchResponse(
        query=request.query,
        mode=request.mode,
        count=len(results),
        results=results,
    )
