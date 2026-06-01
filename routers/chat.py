# app/routers/chat.py
from fastapi import APIRouter, HTTPException

from schemas.chat_schema import ChatRequest, ChatResponse
from services.chat_service import get_answer_from_rag

router = APIRouter()


@router.post("/prompt", response_model=ChatResponse)
async def generate_answer(request: ChatRequest):
    response = get_answer_from_rag(request.message)
    if not response:
        raise HTTPException(status_code=500, detail="Error while generating response.")
    return response
