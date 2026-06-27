# app/routers/chat.py
from fastapi import APIRouter, HTTPException

from schemas.chat_schema import ChatHistoryResponse, ChatRequest, ChatResponse

router = APIRouter()


@router.post("/prompt", response_model=ChatResponse)
async def generate_answer(request: ChatRequest):
    from services.chat_service import get_answer_from_rag

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
):
    from services import conversation_store

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
