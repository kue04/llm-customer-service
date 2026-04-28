# app/main.py
from fastapi import FastAPI
from routers import chat, example, info

app = FastAPI()

# 注册路由
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(example.router, prefix="/examples", tags=["examples"])
app.include_router(info.router, prefix="/model", tags=["info"])


@app.get("/")
def read_root():
    return {"message": "Welcome to the customer service API"}


