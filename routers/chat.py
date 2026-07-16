# app/routers/chat.py
from fastapi import APIRouter, Depends, HTTPException

from schemas.chat_schema import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    ChatReviewActionRequest,
    ChatReviewActionResponse,
)
from services.auth_service import get_operator_context, require_read_operation_role, require_review_action_role
from services.audit_service import record_audit_log

router = APIRouter()


@router.post("/prompt", response_model=ChatResponse)
async def generate_answer(
    request: ChatRequest,
    operator_context: dict = Depends(get_operator_context),
):
    from services.chat_service import get_answer_from_rag

    require_read_operation_role("chat_generate", operator_context)
    response = get_answer_from_rag(request)
    if not response:
        raise HTTPException(status_code=500, detail="Error while generating response.")
    return response


@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    user_id: str = "demo_user",
    order_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    operator_context: dict = Depends(get_operator_context),
):
    from services import conversation_store

    require_read_operation_role("chat_history", operator_context)
    conversation = conversation_store.find_conversation(
        user_id=user_id,
        order_id=order_id,
        session_id=session_id,
    )
    if not conversation:
        return {
            "user_id": user_id,
            "session_id": session_id or "",
            "order_id": order_id,
            "messages": [],
            "latest_response": {},
        }

    resolved_session_id = conversation["session_id"]
    return {
        "user_id": conversation["user_id"],
        "session_id": resolved_session_id,
        "order_id": conversation.get("order_id"),
        "messages": conversation_store.list_messages(resolved_session_id, limit=limit),
        "latest_response": conversation_store.get_latest_turn_response(resolved_session_id),
    }


@router.post("/review-action", response_model=ChatReviewActionResponse)
async def review_chat_action(
    request: ChatReviewActionRequest,
    operator_context: dict = Depends(get_operator_context),
):
    from services import conversation_store

    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    require_review_action_role(payload["action"], operator_context)
    payload["operator_id"] = operator_context["operator_id"]
    payload["operator_role"] = operator_context["role"]
    try:
        result = conversation_store.save_review_action(payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result["audit_id"] = record_audit_log(
        operator_id=operator_context["operator_id"],
        operator_role=operator_context["role"],
        action_type=f"chat_review_{payload['action']}",
        object_type="conversation_turn",
        object_id=payload["request_id"],
        request_id=payload["request_id"],
        before_summary="pending_agent_review",
        after_summary=result["status"],
        ip=operator_context.get("ip", ""),
        device_info=operator_context.get("user_agent", ""),
    )
    return result
