# app/main.py
from fastapi import FastAPI
from routers import chat


app = FastAPI()

# 注册路由
app.include_router(chat.router, prefix="/chat", tags=["chat"])

@app.get("/")
def read_root():
    return {"message": "Welcome to the customer service API"}
