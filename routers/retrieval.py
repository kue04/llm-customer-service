from fastapi import APIRouter, Depends
from config.rag_config import get_rag_config_dict
from models.prompt import create_prompt
from schemas.retrieval_schema import (
    PromptPreviewResponse,
    RagConfigResponse,
    RetrievalResultItem,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from services.auth_service import get_operator_context, require_read_operation_role
from utils.rag_context import build_prompt_context_items
from utils.vector_retriever import get_vector_store_status, retrieve_by_real_vector


router = APIRouter()


@router.get("/config", response_model=RagConfigResponse)
def get_retrieval_config(operator_context: dict = Depends(get_operator_context)) -> dict:
    require_read_operation_role("retrieval_read", operator_context)
    return {**get_rag_config_dict(), **get_vector_store_status()}


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
        dense_rank=item.get("dense_rank"),
        lexical_rank=item.get("lexical_rank"),
        dense_score=item.get("dense_score", item.get("vector_score", 0.0)),
        lexical_score=item.get("lexical_score", 0.0),
        rrf_score=item.get("rrf_score", 0.0),
        retrieval_origin=item.get("retrieval_origin", item.get("_retrieval_origin", "dense")),
        reranker_degraded=bool(item.get("reranker_degraded", False)),
        reranker_error=str(item.get("reranker_error", "")),
        category=source.get("category", ""),
        intent=source.get("intent", ""),
        question=source.get("question", ""),
        answer=item["answer"],
    )

@router.post("/search", response_model=RetrievalSearchResponse)
def search_retrieval(
    request: RetrievalSearchRequest,
    operator_context: dict = Depends(get_operator_context),
) -> RetrievalSearchResponse:
    require_read_operation_role("retrieval_read", operator_context)
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
def preview_prompt(
    request: RetrievalSearchRequest,
    operator_context: dict = Depends(get_operator_context),
) -> PromptPreviewResponse:
    require_read_operation_role("retrieval_read", operator_context)
    candidates = retrieve_by_real_vector(
        request.query,
        limit=request.limit,
        min_score=request.min_score,
        use_hybrid=request.mode == "hybrid",
    )
    results = []
    for rank, item in enumerate(candidates, start=1):
        results.append(build_retrieval_result_item(rank, item))

    prompt_context_items = build_prompt_context_items(
        [result.model_dump() for result in results],
        max_items=request.limit,
    )
    prompt_context_results = [
        RetrievalResultItem(
            role=item.role,
            evidence_strength=item.evidence_strength,
            display_title=item.display_title,
            evidence_summary=item.evidence_summary,
            prompt_instruction=item.prompt_instruction,
            source_question=item.source_question,
            source_answer=item.source_answer,
            rank=item.rank,
            score=item.score,
            rerank_score=item.rerank_score,
            model_rerank_score=0.0,
            vector_score=0.0,
            keyword_bonus=0.0,
            direction_penalty=0.0,
            category=item.category,
            intent=item.intent,
            question=item.question,
            answer=item.answer,
        )
        for item in prompt_context_items
    ]

    prompt = create_prompt(request.query, prompt_context_items)

    return PromptPreviewResponse(
        query=request.query,
        mode=request.mode,
        count=len(results),
        prompt=prompt,
        prompt_context_items=prompt_context_results,
        results=results,
    )
