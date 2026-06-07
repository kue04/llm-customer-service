# app/main.py
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import chat, example, feedback, info, knowledge, ops, retrieval

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(example.router, prefix="/examples", tags=["examples"])
app.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
app.include_router(info.router, prefix="/model", tags=["info"])
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
app.include_router(ops.router, prefix="/ops", tags=["ops"])
app.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])


@app.get("/")
def read_root():
    return {"message": "Welcome to the customer service API"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
