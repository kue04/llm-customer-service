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
from services.auth_service import (
    AuthContext,
    RequestMeta,
    get_auth_context,
    get_request_meta,
    require_read_operation_role,
    require_resource_scope,
    require_write_operation_role,
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


def _audit_knowledge_action(
    auth: AuthContext,
    meta: RequestMeta,
    action_type: str,
    object_id: str,
    before_summary: str = "",
    after_summary: str = "",
) -> None:
    record_audit_log(
        operator_id=auth.user_id,
        operator_role=auth.primary_role,
        action_type=action_type,
        object_type="knowledge",
        object_id=object_id,
        request_id=auth.request_id,
        before_summary=before_summary,
        after_summary=after_summary,
        ip=meta.ip,
        device_info=meta.user_agent,
    )


@router.get("/items", response_model=KnowledgeListResponse)
def knowledge_items(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str = "",
    intent: str = "",
    status: str = "",
    keyword: str = "",
    auth: AuthContext = Depends(get_auth_context),
):
    require_read_operation_role("knowledge_read", auth)
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
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("knowledge_create", auth)
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    item = create_knowledge_item(payload)
    _audit_knowledge_action(
        auth,
        meta,
        "knowledge_create",
        str(item["id"]),
        after_summary=f"{item['status']}:{item['question']}",
    )
    return item


@router.put("/items/{item_id}", response_model=KnowledgeItem)
def update_item(
    item_id: int,
    request: KnowledgeItemPayload,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("knowledge_update", auth)
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    try:
        item = update_knowledge_item(item_id, payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _audit_knowledge_action(
        auth,
        meta,
        "knowledge_update",
        str(item["id"]),
        before_summary=f"source_item={item_id}",
        after_summary=f"{item['status']}:v{item['version']}:{item['question']}",
    )
    return item


@router.post("/items/{item_id}/archive", response_model=KnowledgeItem)
def archive_item(
    item_id: int,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("knowledge_archive", auth)
    try:
        item = archive_knowledge_item(item_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _audit_knowledge_action(
        auth,
        meta,
        "knowledge_archive",
        str(item["id"]),
        after_summary=f"{item['status']}:{item['question']}",
    )
    return item


@router.post("/items/{item_id}/review", response_model=KnowledgeItem)
def review_item(
    item_id: int,
    request: KnowledgeReviewRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("knowledge_review", auth)
    try:
        item = review_knowledge_item(item_id, request.status, request.review_note)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit_knowledge_action(
        auth,
        meta,
        "knowledge_review",
        str(item["id"]),
        after_summary=f"{item['status']}:{item['review_note']}",
    )
    return item


@router.get("/export-approved", response_model=KnowledgeExportResponse)
def export_approved(auth: AuthContext = Depends(get_auth_context)):
    require_read_operation_role("knowledge_read", auth)
    return export_approved_jsonl()


@router.post("/publish-approved", response_model=KnowledgePublishResponse)
def publish_approved(
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    # 两层判定：操作维度（既有 ``knowledge_publish``，保证既有调用方行为不变）+
    # 资源维度（阶段 4.1 的 ``document:publish``）。
    # 与 ``POST /ingestion/indexes/rebuild`` 的 ``index:rebuild`` **分开授权**（计划 4.2）：
    # 发布一条审核过的知识 ≠ 允许重建全量索引，后者影响所有租户的检索结果。
    require_write_operation_role("knowledge_publish", auth)
    require_resource_scope("document:publish", auth)
    result = publish_approved_knowledge()
    _audit_knowledge_action(
        auth,
        meta,
        "knowledge_publish",
        result["publish_id"],
        after_summary=f"{result['status']}:merged={result['merged_count']}",
    )
    return result


@router.get("/publish-history", response_model=KnowledgePublishHistoryResponse)
def publish_history(
    limit: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
):
    require_read_operation_role("knowledge_read", auth)
    return list_publish_history(limit=limit)


@router.post("/rollback-latest", response_model=KnowledgePublishResponse)
def rollback_latest(
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("knowledge_rollback", auth)
    try:
        result = rollback_latest_publish()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    _audit_knowledge_action(
        auth,
        meta,
        "knowledge_rollback",
        result["publish_id"],
        after_summary=f"{result['status']}:merged={result['merged_count']}",
    )
    return result
