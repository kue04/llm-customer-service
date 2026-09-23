# app/main.py
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from routers import audit, chat, documents, example, feedback, info, knowledge, ops, order, prompt, release, retrieval

from services.auth_context import AuthConfigError, load_auth_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger(__name__)


def _check_auth_configuration() -> None:
    """启动时检查鉴权配置，把「生产环境忘配 JWT 密钥」暴露在启动日志里。

    这里只告警不中断：真正的阻断点在 ``get_auth_context`` ——
    缺少密钥时每个受保护请求都会返回 500，属于 fail closed，
    不会出现「静默放行」或「静默拒绝所有人」这两种更糟的情况。
    """

    try:
        load_auth_config()
    except AuthConfigError as error:
        logger.error(
            "authentication is not configured (%s); all protected endpoints will return 500 "
            "until RAG_JWT_SECRET is provided",
            error,
        )
    else:
        logger.info("authentication configuration loaded")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _check_auth_configuration()
    yield


app = FastAPI(title="LLM Customer Service API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(example.router, prefix="/examples", tags=["examples"])
app.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
app.include_router(info.router, prefix="/model", tags=["info"])
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
# 文档上传与任务查询的路径天然带两个前缀（/knowledge-bases/... 与 /documents/...），
# 因此在 router 内部写全路径，这里不再加 prefix。
app.include_router(documents.router, tags=["documents"])
app.include_router(ops.router, prefix="/ops", tags=["ops"])
app.include_router(order.router, prefix="/orders", tags=["orders"])
app.include_router(prompt.router, prefix="/prompt", tags=["prompt"])
app.include_router(release.router, prefix="/release", tags=["release"])
app.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])


def _custom_openapi() -> dict:
    """在 OpenAPI 里声明 Bearer 认证方案。

    目的是让 /docs 能直接粘贴 JWT 调试，同时把「身份只来自 Authorization JWT」
    这条约定写进接口文档 —— 避免联调方继续按旧的 X-User-Role 习惯调用。
    """

    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "身份只来自 Authorization: Bearer <JWT>。"
            "X-User-Role / X-Operator-Id 已失效，仅在日志中记录，不参与任何授权判定。"
        ),
    }
    schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


@app.get("/")
def read_root():
    return {"message": "Welcome to the customer service API"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
