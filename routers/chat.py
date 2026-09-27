from services.runtime_db import scoped_route
# app/routers/chat.py
from fastapi import APIRouter, Depends, HTTPException, Query

from schemas.chat_schema import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    ChatReviewActionRequest,
    ChatReviewActionResponse,
)
from services.auth_service import (
    AuthContext,
    RequestMeta,
    get_auth_context,
    get_request_meta,
    require_read_operation_role,
    require_review_action_role,
)
from services.audit_service import record_audit_log

router = APIRouter()


@router.post("/prompt", response_model=ChatResponse)
@scoped_route
async def generate_answer(
    request: ChatRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    from services.chat_service import get_answer_from_rag
    from services.conversation_store import ConversationAccessError

    require_read_operation_role("chat_generate", auth)
    # auth 一路传到检索层：B 轨（chunk 索引）的租户 / document ACL 过滤必须由
    # 服务端身份构造，传的是**已校验的 AuthContext**，不接受任何自报字段（D-2）。
    try:
        response = get_answer_from_rag(request, auth)
    except ConversationAccessError as error:
        raise HTTPException(status_code=404, detail="conversation not found") from error
    if not response:
        raise HTTPException(status_code=500, detail="Error while generating response.")
    return response


@router.get("/history", response_model=ChatHistoryResponse)
@scoped_route
async def get_chat_history(
    user_id: str | None = Query(default=None, deprecated=True, description="Ignored; identity comes from JWT."),
    order_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    auth: AuthContext = Depends(get_auth_context),
):
    from services import conversation_store

    require_read_operation_role("chat_history", auth)
    conversation = conversation_store.find_conversation(
        user_id=auth.user_id,
        tenant_id=auth.tenant_id,
        order_id=order_id,
        session_id=session_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="conversation not found")

    resolved_session_id = conversation["session_id"]
    return {
        "user_id": conversation["user_id"],
        "session_id": resolved_session_id,
        "order_id": conversation.get("order_id"),
        "messages": conversation_store.list_messages(resolved_session_id, limit=limit),
        "latest_response": conversation_store.get_latest_turn_response(resolved_session_id),
    }


@router.post("/review-action", response_model=ChatReviewActionResponse)
@scoped_route
async def review_chat_action(
    request: ChatReviewActionRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    from services import conversation_store

    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    require_review_action_role(payload["action"], auth)
    payload["operator_id"] = auth.user_id
    payload["operator_role"] = auth.primary_role
    try:
        result = conversation_store.save_review_action(
            payload, user_id=auth.user_id, tenant_id=auth.tenant_id,
        )
    except (KeyError, conversation_store.ConversationAccessError) as error:
        raise HTTPException(status_code=404, detail="conversation not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result["audit_id"] = record_audit_log(
        operator_id=auth.user_id,
        operator_role=auth.primary_role,
        action_type=f"chat_review_{payload['action']}",
        object_type="conversation_turn",
        # object_id 是被复核的那一轮会话（业务对象），request_id 是本次 HTTP 请求的追踪号，
        # 两者分开记录，既保留与会话记录的可关联性，又能按请求链路检索审计。
        object_id=payload["request_id"],
        request_id=auth.request_id,
        before_summary="pending_agent_review",
        after_summary=result["status"],
        ip=meta.ip,
        device_info=meta.user_agent,
    )
    return result
