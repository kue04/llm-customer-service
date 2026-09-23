"""测试用的 JWT 签发与 AuthContext 构造辅助。

为什么测试自带密钥
------------------
生产代码里**不存在**可用于签发的密钥；只有显式打开 ``RAG_ALLOW_TEST_JWT_SECRET``
时才会回退到 ``services.auth_context.TEST_JWT_SECRET``。测试选择更彻底的做法：
自己生成一个进程内随机密钥写进 ``RAG_JWT_SECRET``，签发与校验用同一个值 ——
既走的是和真实鉴权完全相同的代码路径，也不会让任何测试密钥固化进产品代码。

用法
----
HTTP 层（TestClient）：:

    client.get("/knowledge/items", headers=auth_headers(roles=["knowledge_ops"], user_id="ops_1"))

直接调用 handler（绕过 HTTP）：:

    search_retrieval(request, auth=make_auth_context(roles=["agent"]))
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
import secrets
from typing import Any

import jwt

from services.auth_context import (
    DEFAULT_ALGORITHM,
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    AuthContext,
    resolve_scopes,
)


#: 每个测试进程独立的随机密钥，由 tests/conftest.py 写进 RAG_JWT_SECRET
TEST_SECRET = f"unit-test-secret-{secrets.token_hex(8)}"


def issue_token(
    *,
    user_id: str = "user-1",
    tenant_id: str = "tenant-1",
    roles: Iterable[str] = ("admin",),
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    expires_in_seconds: int = 3600,
    secret: str = TEST_SECRET,
    algorithm: str = DEFAULT_ALGORITHM,
    omit_claims: Sequence[str] = (),
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """签发一个测试用访问令牌。

    参数设计围绕「构造异常令牌」的需求：
    * ``expires_in_seconds`` 传负数即可得到过期令牌；
    * ``omit_claims=("tenant_id",)`` 可构造缺少必要 claim 的令牌；
    * ``issuer`` / ``audience`` / ``secret`` / ``algorithm`` 可构造不匹配或签名错误的令牌；
    * ``extra_claims`` 可注入伪造的 ``scopes`` / ``permissions``，用于验证提权无效。
    """

    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    for name in omit_claims:
        claims.pop(name, None)
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, secret, algorithm=algorithm)


def auth_headers(**kwargs: Any) -> dict[str, str]:
    """返回可直接传给 TestClient 的 Authorization 头（参数同 ``issue_token``）。"""

    return {"Authorization": f"Bearer {issue_token(**kwargs)}"}


def make_auth_context(
    *,
    user_id: str = "user-1",
    tenant_id: str = "tenant-1",
    roles: Iterable[str] = ("admin",),
    request_id: str = "req-test",
) -> AuthContext:
    """构造一个已校验身份，供直接调用 handler 的测试使用。"""

    role_set = frozenset(str(role) for role in roles)
    return AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=role_set,
        scopes=resolve_scopes(role_set),
        request_id=request_id,
    )
