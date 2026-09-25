"""FastAPI 鉴权依赖层。

分层说明
--------
* ``services/auth_context.py`` 放纯逻辑：JWT 校验、AuthContext、角色→scope 策略表。
* 本文件只做「HTTP 世界」的适配：从请求头取令牌、把 ``AuthError`` 翻译成
  401/403/500、把 ``Request`` 里的 ip / user-agent 取出来给审计用。

**身份来源只有一个**：``Authorization: Bearer <JWT>``。
``X-User-Role`` / ``X-Operator-Id`` 在兼容期内会被读取并记录日志，
但**不参与任何授权判定** —— 保留读取只是为了让「仍有旧客户端在发这些头」这件事可观测。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid

from fastapi import Header, HTTPException, Request

from services.auth_context import (
    READ_OPERATION_ROLES,
    RESOURCE_SCOPE_ROLES,
    REVIEW_ACTION_ROLES,
    VALID_ROLES,
    WRITE_OPERATION_ROLES,
    AuthConfigError,
    AuthContext,
    AuthError,
    ScopeDenied,
    build_auth_context,
    decode_access_token,
    load_auth_config,
    read_scope,
    resolve_scopes,
    resource_scope,
    review_scope,
    write_scope,
)

__all__ = [
    "READ_OPERATION_ROLES",
    "RESOURCE_SCOPE_ROLES",
    "REVIEW_ACTION_ROLES",
    "VALID_ROLES",
    "WRITE_OPERATION_ROLES",
    "AuthContext",
    "RequestMeta",
    "get_auth_context",
    "get_request_meta",
    "require_read_operation_role",
    "require_resource_scope",
    "require_review_action_role",
    "require_write_operation_role",
    "resolve_scopes",
]

logger = logging.getLogger(__name__)

BEARER_PREFIX = "bearer "
LEGACY_ROLE_HEADER = "X-User-Role"
LEGACY_OPERATOR_HEADER = "X-Operator-Id"
REQUEST_ID_HEADER = "X-Request-Id"


@dataclass(frozen=True, slots=True)
class RequestMeta:
    """请求元数据（来源信息），与身份无关。

    和 ``AuthContext`` 分开，是为了让「身份」与「来源」职责清晰：
    AuthContext 只装已校验的身份，ip / user-agent 属于审计信息。
    """

    ip: str = ""
    user_agent: str = ""


def get_request_meta(request: Request) -> RequestMeta:
    """给审计记录提供 ip 与 user-agent。"""

    return RequestMeta(
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.strip():
        raise AuthError("missing_authorization", "缺少 Authorization 头")
    value = authorization.strip()
    if not value.lower().startswith(BEARER_PREFIX):
        raise AuthError("unsupported_scheme", "仅支持 Bearer 认证方案")
    token = value[len(BEARER_PREFIX):].strip()
    if not token:
        raise AuthError("missing_token", "Bearer 令牌为空")
    return token


def _warn_on_legacy_headers(x_user_role: str | None, x_operator_id: str | None) -> None:
    """兼容期观测：旧客户端仍在发送信任头时留下痕迹，但绝不据此授权。"""

    observed = []
    if (x_user_role or "").strip():
        observed.append(f"{LEGACY_ROLE_HEADER}={x_user_role.strip()!r}")
    if (x_operator_id or "").strip():
        observed.append(f"{LEGACY_OPERATOR_HEADER}={x_operator_id.strip()!r}")
    if observed:
        logger.warning(
            "ignoring legacy identity headers (%s); identity must come from the Authorization JWT",
            ", ".join(observed),
        )


def get_auth_context(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_request_id: str | None = Header(default=None, alias=REQUEST_ID_HEADER),
    x_user_role: str | None = Header(default=None, alias=LEGACY_ROLE_HEADER),
    x_operator_id: str | None = Header(default=None, alias=LEGACY_OPERATOR_HEADER),
) -> AuthContext:
    """解析并校验身份，返回不可变 ``AuthContext``。

    状态码约定：
    * 缺少 / 过期 / 签名不合法 / iss、aud 不匹配的令牌 -> **401**
    * 服务端未配置签名密钥等配置问题 -> **500**（服务端故障，不该算用户 401）
    * 身份合法但权限不足 -> 由 ``require_*`` 辅助函数抛 **403**
    """

    _warn_on_legacy_headers(x_user_role, x_operator_id)

    try:
        config = load_auth_config()
        token = _extract_bearer_token(authorization)
        claims = decode_access_token(token, config)
    except AuthConfigError as error:
        logger.error("authentication is misconfigured: %s", error)
        raise HTTPException(status_code=500, detail="authentication is misconfigured") from error
    except AuthError as error:
        raise HTTPException(
            status_code=401,
            detail=error.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    request_id = (x_request_id or "").strip() or uuid.uuid4().hex

    try:
        return build_auth_context(claims, request_id=request_id)
    except AuthError as error:
        # sub / tenant_id 缺失属于令牌内容不合法，同样是 401
        raise HTTPException(
            status_code=401,
            detail=error.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def _require_scope(scope: str, context: AuthContext) -> None:
    try:
        context.require_scope(scope)
    except ScopeDenied as error:
        raise HTTPException(status_code=403, detail=error.message) from error


def require_read_operation_role(operation: str, context: AuthContext) -> None:
    """只读操作鉴权。未登记的 operation 不授予任何人（fail closed）。"""

    _require_scope(read_scope(operation), context)


def require_write_operation_role(operation: str, context: AuthContext) -> None:
    """写操作鉴权。未登记的 operation 不授予任何人（fail closed）。"""

    _require_scope(write_scope(operation), context)


def require_review_action_role(action: str, context: AuthContext) -> None:
    """会话复核动作鉴权。未登记的动作不授予任何人（fail closed）。"""

    _require_scope(review_scope(action), context)


def require_resource_scope(permission: str, context: AuthContext) -> None:
    """资源级权限鉴权（阶段 4.1 的枚举，如 ``document:read`` / ``index:rebuild``）。

    与 ``require_*_operation_role`` 的区别只在命名空间：那三个是操作维度
    （``read:`` / ``write:`` / ``review:`` 前缀 + 既有 operation 名），
    本函数是资源维度（键名即完整 scope）。未登记的权限同样不授予任何人。
    """

    _require_scope(resource_scope(permission), context)
