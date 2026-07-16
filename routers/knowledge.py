from fastapi import APIRouter, Depends, HTTPException, Query

from schemas.knowledge_schema import (
    KnowledgeExportResponse,
    KnowledgeItem,
    KnowledgeItemPayload,
    KnowledgeListResponse,
    KnowledgePublishHistoryResponse,
    KnowledgePublishResponse,
    KnowledgeReviewRequest,
)
from services.audit_service import record_audit_log
from services.auth_service import get_operator_context, require_read_operation_role, require_write_operation_role
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


def _audit_knowledge_action(
    operator_context: dict,
    action_type: str,
    object_id: str,
    before_summary: str = "",
    after_summary: str = "",
) -> None:
    record_audit_log(
        operator_id=operator_context["operator_id"],
        operator_role=operator_context["role"],
        action_type=action_type,
        object_type="knowledge",
        object_id=object_id,
        before_summary=before_summary,
        after_summary=after_summary,
        ip=operator_context.get("ip", ""),
        device_info=operator_context.get("user_agent", ""),
    )


@router.get("/items", response_model=KnowledgeListResponse)
def knowledge_items(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str = "",
    intent: str = "",
    status: str = "",
    keyword: str = "",
    operator_context: dict = Depends(get_operator_context),
):
    require_read_operation_role("knowledge_read", operator_context)
    return list_knowledge_items(
        limit=limit,
        offset=offset,
        category=category,
        intent=intent,
        status=status,
        keyword=keyword,
    )


@router.post("/items", response_model=KnowledgeItem)
def create_item(
    request: KnowledgeItemPayload,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("knowledge_create", operator_context)
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    item = create_knowledge_item(payload)
    _audit_knowledge_action(
        operator_context,
        "knowledge_create",
        str(item["id"]),
        after_summary=f"{item['status']}:{item['question']}",
    )
    return item


@router.put("/items/{item_id}", response_model=KnowledgeItem)
def update_item(
    item_id: int,
    request: KnowledgeItemPayload,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("knowledge_update", operator_context)
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    try:
        item = update_knowledge_item(item_id, payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _audit_knowledge_action(
        operator_context,
        "knowledge_update",
        str(item["id"]),
        before_summary=f"source_item={item_id}",
        after_summary=f"{item['status']}:v{item['version']}:{item['question']}",
    )
    return item


@router.post("/items/{item_id}/archive", response_model=KnowledgeItem)
def archive_item(
    item_id: int,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("knowledge_archive", operator_context)
    try:
        item = archive_knowledge_item(item_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _audit_knowledge_action(
        operator_context,
        "knowledge_archive",
        str(item["id"]),
        after_summary=f"{item['status']}:{item['question']}",
    )
    return item


@router.post("/items/{item_id}/review", response_model=KnowledgeItem)
def review_item(
    item_id: int,
    request: KnowledgeReviewRequest,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("knowledge_review", operator_context)
    try:
        item = review_knowledge_item(item_id, request.status, request.review_note)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit_knowledge_action(
        operator_context,
        "knowledge_review",
        str(item["id"]),
        after_summary=f"{item['status']}:{item['review_note']}",
    )
    return item


@router.get("/export-approved", response_model=KnowledgeExportResponse)
def export_approved(operator_context: dict = Depends(get_operator_context)):
    require_read_operation_role("knowledge_read", operator_context)
    return export_approved_jsonl()


@router.post("/publish-approved", response_model=KnowledgePublishResponse)
def publish_approved(operator_context: dict = Depends(get_operator_context)):
    require_write_operation_role("knowledge_publish", operator_context)
    result = publish_approved_knowledge()
    _audit_knowledge_action(
        operator_context,
        "knowledge_publish",
        result["publish_id"],
        after_summary=f"{result['status']}:merged={result['merged_count']}",
    )
    return result


@router.get("/publish-history", response_model=KnowledgePublishHistoryResponse)
def publish_history(
    limit: int = Query(default=20, ge=1, le=100),
    operator_context: dict = Depends(get_operator_context),
):
    require_read_operation_role("knowledge_read", operator_context)
    return list_publish_history(limit=limit)


@router.post("/rollback-latest", response_model=KnowledgePublishResponse)
def rollback_latest(operator_context: dict = Depends(get_operator_context)):
    require_write_operation_role("knowledge_rollback", operator_context)
    try:
        result = rollback_latest_publish()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    _audit_knowledge_action(
        operator_context,
        "knowledge_rollback",
        result["publish_id"],
        after_summary=f"{result['status']}:merged={result['merged_count']}",
    )
    return result
