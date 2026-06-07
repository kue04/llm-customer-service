# app/routers/chat.py
from fastapi import APIRouter, HTTPException

from schemas.chat_schema import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/prompt", response_model=ChatResponse)
async def generate_answer(request: ChatRequest):
    from services.chat_service import get_answer_from_rag

    response = get_answer_from_rag(request.message)
    if not response:
        raise HTTPException(status_code=500, detail="Error while generating response.")
    return response
