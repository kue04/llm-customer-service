from fastapi import APIRouter, HTTPException, Query

from schemas.knowledge_schema import (
    KnowledgeExportResponse,
    KnowledgeItem,
    KnowledgeItemPayload,
    KnowledgeListResponse,
    KnowledgePublishHistoryResponse,
    KnowledgePublishResponse,
    KnowledgeReviewRequest,
)
from services.knowledge_service import (
    archive_knowledge_item,
    create_knowledge_item,
    export_approved_jsonl,
    list_publish_history,
    list_knowledge_items,
    publish_approved_knowledge,
    review_knowledge_item,
    rollback_latest_publish,
    update_knowledge_item,
)

router = APIRouter()


@router.get("/items", response_model=KnowledgeListResponse)
def knowledge_items(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str = "",
    intent: str = "",
    status: str = "",
    keyword: str = "",
):
    return list_knowledge_items(
        limit=limit,
        offset=offset,
        category=category,
        intent=intent,
        status=status,
        keyword=keyword,
    )


@router.post("/items", response_model=KnowledgeItem)
def create_item(request: KnowledgeItemPayload):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    return create_knowledge_item(payload)


@router.put("/items/{item_id}", response_model=KnowledgeItem)
def update_item(item_id: int, request: KnowledgeItemPayload):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    try:
        return update_knowledge_item(item_id, payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/items/{item_id}/archive", response_model=KnowledgeItem)
def archive_item(item_id: int):
    try:
        return archive_knowledge_item(item_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/items/{item_id}/review", response_model=KnowledgeItem)
def review_item(item_id: int, request: KnowledgeReviewRequest):
    try:
        return review_knowledge_item(item_id, request.status, request.review_note)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/export-approved", response_model=KnowledgeExportResponse)
def export_approved():
    return export_approved_jsonl()


@router.post("/publish-approved", response_model=KnowledgePublishResponse)
def publish_approved():
    return publish_approved_knowledge()


@router.get("/publish-history", response_model=KnowledgePublishHistoryResponse)
def publish_history(limit: int = Query(default=20, ge=1, le=100)):
    return list_publish_history(limit=limit)


@router.post("/rollback-latest", response_model=KnowledgePublishResponse)
def rollback_latest():
    try:
        return rollback_latest_publish()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
