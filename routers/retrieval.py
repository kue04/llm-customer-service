from fastapi import APIRouter
from models.prompt import create_prompt
from schemas.retrieval_schema import (
    PromptPreviewResponse,
    RetrievalResultItem,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from utils.vector_retriever import retrieve_by_real_vector


router = APIRouter()


def build_retrieval_result_item(rank: int, item: dict) -> RetrievalResultItem:
    source = item["source"]
    return RetrievalResultItem(
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
        results.append(build_retrieval_result_item(rank, item))


    return RetrievalSearchResponse(
        query=request.query,
        mode=request.mode,
        count=len(results),
        results=results,
    )

@router.post("/prompt-preview", response_model=PromptPreviewResponse)
def preview_prompt(request: RetrievalSearchRequest) -> PromptPreviewResponse:
    candidates = retrieve_by_real_vector(
        request.query,
        limit=request.limit,
        min_score=request.min_score,
        use_hybrid=request.mode == "hybrid",
    )
    results = []
    documents = []
    for rank, item in enumerate(candidates, start=1):
        documents.append(item["answer"])
        results.append(build_retrieval_result_item(rank, item))


    prompt = create_prompt(request.query, documents)

    return PromptPreviewResponse(
        query=request.query,
        mode=request.mode,
        count=len(results),
        prompt=prompt,
        results=results,
    )

