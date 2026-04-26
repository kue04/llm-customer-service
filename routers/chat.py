# app/routers/chat.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.chat_service import get_answer_from_rag  # 用于从 RAG 获取答案

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str
    confidence_score: float

@router.post("/prompt", response_model=ChatResponse)
async def generate_answer(request: ChatRequest):
    # 从 RAG 获取生成的答案
    response = get_answer_from_rag(request.message)
    if not response:
        raise HTTPException(status_code=500, detail="Error while generating response.")
    return response