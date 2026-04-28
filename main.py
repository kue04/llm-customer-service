# app/main.py
from fastapi import FastAPI
from routers import chat, example
from schemas.info_schema import ModelInfoResponse
from services.chat_service import get_model_info

app = FastAPI()

# 注册路由
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(example.router, prefix="/examples", tags=["examples"])

@app.get("/")
def read_root():
    return {"message": "Welcome to the customer service API"}



@app.get("/model/info", response_model=ModelInfoResponse, tags=["info"])
def model_info():
    return get_model_info()
