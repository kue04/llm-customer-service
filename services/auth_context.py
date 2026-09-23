"""身份上下文与 JWT 校验（阶段 1.2 的核心）。

这一层是**纯 Python**，不依赖 FastAPI，因此可以脱离 HTTP 单独测试。

三条不可动摇的规则
------------------
1. **身份只能来自 `Authorization: Bearer <JWT>`。** 请求头里的
   `X-User-Role` / `X-Operator-Id` 不再参与任何判定，兼容期只记日志。
2. **角色由 token 声明，权限由服务端推导。** token 里的 `roles` claim 被信任
   （毕竟是我们自己签发的），但 token 里如果带了 `scopes` / `permissions` 之类的
   claim，**一律忽略** —— 否则持有合法 token 的人就能自报权限提权。
3. **密钥只从环境变量读，缺失时失败而不是降级。** 见 ``load_auth_config``。

scope 词汇表
------------
两个维度并存：

* **操作维度**（第一期沿用，保持既有接口的授权行为逐条不变）：
  ``read:`` / ``write:`` / ``review:`` 前缀 + 既有 operation 名，
  身份来源已从 header 换成 JWT。
* **资源维度**（阶段 4.1 落地，B7）：``knowledge_base:read``、``document:upload``、
  ``document:publish``、``index:rebuild``、``index:rollback``、``audit:read`` 等十个权限，
  见 :data:`RESOURCE_SCOPE_ROLES`。

两者都倒排进 :data:`ROLE_SCOPES`，调用方统一用 ``AuthContext.require_scope(...)``
或 ``services.auth_service`` 里的 ``require_*`` 辅助函数判定，
**不要在路由里写死角色名**。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from typing import Any

import jwt


# ---------------------------------------------------------------- 异常


class AuthConfigError(Exception):
    """服务端鉴权配置有误（例如缺少密钥）。

    与 ``AuthError`` 区分开：这是**服务端**的问题，应该返回 5xx 并告警，
    而不是把用户的请求判成 401。
    """


class AuthError(Exception):
    """身份校验失败。``code`` 供日志与测试断言，``message`` 面向客户端且不含令牌内容。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScopeDenied(AuthError):
    """身份合法但权限不足。调用方应返回 403。"""

    def __init__(self, scope: str) -> None:
        super().__init__("scope_denied", f"缺少所需权限 {scope}")
        self.scope = scope


# ---------------------------------------------------------------- 授权策略表
#
# 单一事实来源：以下三张表的 inversion 就是「角色 -> scope」。
# 新增接口时只加表项，不要在路由里写死角色判断。

#: 只读操作 -> 允许的角色
READ_OPERATION_ROLES: dict[str, frozenset[str]] = {
    "chat_generate": frozenset({"agent", "supervisor", "qa", "admin"}),
    "chat_history": frozenset({"agent", "supervisor", "qa", "admin"}),
    "knowledge_read": frozenset({"supervisor", "knowledge_ops", "qa", "admin"}),
    "feedback_read": frozenset({"supervisor", "qa", "knowledge_ops", "admin"}),
    "ops_metrics_read": frozenset({"supervisor", "qa", "admin"}),
    "order_state_read": frozenset({"agent", "supervisor", "admin"}),
    "prompt_read": frozenset({"supervisor", "qa", "admin"}),
    "audit_read": frozenset({"supervisor", "qa", "admin"}),
    "release_read": frozenset({"supervisor", "qa", "admin"}),
    "retrieval_read": frozenset({"agent", "supervisor", "qa", "admin"}),
    "model_info_read": frozenset({"supervisor", "qa", "admin"}),
    "example_read": frozenset({"supervisor", "knowledge_ops", "qa", "admin"}),
}

#: 写操作 -> 允许的角色
WRITE_OPERATION_ROLES: dict[str, frozenset[str]] = {
    "knowledge_create": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "knowledge_update": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "knowledge_archive": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "knowledge_review": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "knowledge_publish": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "knowledge_rollback": frozenset({"supervisor", "admin"}),
    "feedback_create": frozenset({"agent", "supervisor", "qa", "knowledge_ops", "admin"}),
    "feedback_export_eval_case": frozenset({"agent", "supervisor", "qa", "knowledge_ops", "admin"}),
    "order_state_upsert": frozenset({"agent", "supervisor", "admin"}),
    "prompt_write": frozenset({"admin"}),
}

#: 会话复核动作 -> 允许的角色
REVIEW_ACTION_ROLES: dict[str, frozenset[str]] = {
    "accepted": frozenset({"agent", "supervisor", "admin"}),
    "edited_and_sent": frozenset({"agent", "supervisor", "admin"}),
    "human_handoff": frozenset({"agent", "supervisor", "admin"}),
    "marked_bad_case": frozenset({"agent", "supervisor", "qa", "knowledge_ops", "admin"}),
}

#: 资源级权限 -> 允许的角色（阶段 4.1 的计划枚举，B7 落地）。
#:
#: 与上面三张表的区别：上面三张是**操作**维度（`read:chat_generate` 这类，
#: 沿用既有接口的授权表），这张是**资源**维度（计划原文点名的九个权限，
#: 加上 B8 新增的 ``index:rollback``，共十个）。
#: 键名就是完整的 scope 字符串（自带冒号），因此倒排时前缀为空。
#:
#: 三个刻意的授权划分：
#: * ``document:publish`` 与 ``index:rebuild`` **分开授权**（计划 4.2 明文要求）——
#:   发布一条审核过的知识 ≠ 允许重建全量索引；后者影响所有租户的检索结果。
#: * ``index:rollback`` 与 ``index:rebuild`` **再分一层**（B8 新增）——
#:   「能重建索引」≠「能把线上检索切回旧版本」。两者当前角色集合相同
#:   （supervisor / admin），**但独立成键**：将来要单独收窄（例如只给 admin）、
#:   或单独授予某个角色时，不必改动已有授权。授权的单元是**动作**，不是资源类别。
#: * ``audit:read`` 只给安全/质量角色，``document:delete`` 不给 knowledge_ops。
RESOURCE_SCOPE_ROLES: dict[str, frozenset[str]] = {
    "knowledge_base:read": frozenset({"agent", "supervisor", "knowledge_ops", "qa", "admin"}),
    "knowledge_base:write": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "document:upload": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "document:read": frozenset({"agent", "supervisor", "knowledge_ops", "qa", "admin"}),
    "document:review": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "document:publish": frozenset({"supervisor", "knowledge_ops", "admin"}),
    "document:delete": frozenset({"supervisor", "admin"}),
    "index:rebuild": frozenset({"supervisor", "admin"}),
    "index:rollback": frozenset({"supervisor", "admin"}),
    "audit:read": frozenset({"supervisor", "qa", "admin"}),
}

#: 全部已知角色（供校验与日志使用）
VALID_ROLES: frozenset[str] = frozenset(
    role
    for table in (
        READ_OPERATION_ROLES,
        WRITE_OPERATION_ROLES,
        REVIEW_ACTION_ROLES,
        RESOURCE_SCOPE_ROLES,
    )
    for roles in table.values()
    for role in roles
)

READ_SCOPE_PREFIX = "read:"
WRITE_SCOPE_PREFIX = "write:"
REVIEW_SCOPE_PREFIX = "review:"
#: 资源级 scope 自带完整命名空间（``document:read``），倒排时不需要再加前缀
RESOURCE_SCOPE_PREFIX = ""


def read_scope(operation: str) -> str:
    return f"{READ_SCOPE_PREFIX}{operation}"


def write_scope(operation: str) -> str:
    return f"{WRITE_SCOPE_PREFIX}{operation}"


def review_scope(action: str) -> str:
    return f"{REVIEW_SCOPE_PREFIX}{action}"


def resource_scope(permission: str) -> str:
    """资源级权限的规范名（``document:read`` 这类，本身即完整 scope）。"""

    return f"{RESOURCE_SCOPE_PREFIX}{permission}"


def _invert_grants() -> dict[str, frozenset[str]]:
    """把「scope -> 允许的角色」倒成「角色 -> 拥有的 scope」。"""

    inverted: dict[str, set[str]] = {}
    tables: tuple[tuple[str, Mapping[str, frozenset[str]]], ...] = (
        (READ_SCOPE_PREFIX, READ_OPERATION_ROLES),
        (WRITE_SCOPE_PREFIX, WRITE_OPERATION_ROLES),
        (REVIEW_SCOPE_PREFIX, REVIEW_ACTION_ROLES),
        (RESOURCE_SCOPE_PREFIX, RESOURCE_SCOPE_ROLES),
    )
    for prefix, table in tables:
        for name, roles in table.items():
            for role in roles:
                inverted.setdefault(role, set()).add(f"{prefix}{name}")
    return {role: frozenset(scopes) for role, scopes in inverted.items()}


#: 角色 -> scope 集合。**由上面的策略表推导，不要手工维护。**
ROLE_SCOPES: dict[str, frozenset[str]] = _invert_grants()


def resolve_scopes(roles: Sequence[str] | set[str] | frozenset[str]) -> frozenset[str]:
    """把角色集合解析成 scope 集合。未知角色贡献 0 个 scope（fail closed）。"""

    scopes: set[str] = set()
    for role in roles:
        scopes |= ROLE_SCOPES.get(str(role), frozenset())
    return frozenset(scopes)


# ---------------------------------------------------------------- AuthContext


@dataclass(frozen=True, slots=True)
class AuthContext:
    """**已校验**的身份上下文。本身不含请求元数据（ip / user-agent）。

    设计成不可变，是为了让「谁在操作」在请求生命周期内无法被业务代码修改 ——
    一个可变对象很容易被下游改写，最终导致审计记录与真实身份不符。
    """

    user_id: str
    tenant_id: str
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    request_id: str = ""

    def __post_init__(self) -> None:
        # 空 user_id / tenant_id 是无效身份，必须在构造时就拦下
        if not str(self.user_id).strip():
            raise AuthError("subject_missing", "身份缺少 user_id")
        if not str(self.tenant_id).strip():
            raise AuthError("tenant_claim_missing", "身份缺少 tenant_id")

    @property
    def primary_role(self) -> str:
        """用于审计记录的单一角色。

        多角色时取字典序最小者，保证同一身份每次记录的 role 都一致
        （若取集合里任意一个，审计记录会在不同请求间抖动）。
        """

        return sorted(self.roles)[0] if self.roles else ""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise ScopeDenied(scope)


# ---------------------------------------------------------------- 配置


JWT_SECRET_ENV = "RAG_JWT_SECRET"
JWT_ALGORITHM_ENV = "RAG_JWT_ALGORITHM"
JWT_ISSUER_ENV = "RAG_JWT_ISSUER"
JWT_AUDIENCE_ENV = "RAG_JWT_AUDIENCE"
JWT_LEEWAY_ENV = "RAG_JWT_LEEWAY_SECONDS"
#: 只有显式打开这个开关，才允许在缺少 RAG_JWT_SECRET 时回退到测试密钥。
ALLOW_TEST_SECRET_ENV = "RAG_ALLOW_TEST_JWT_SECRET"

DEFAULT_ALGORITHM = "HS256"
DEFAULT_ISSUER = "llm-customer-service"
DEFAULT_AUDIENCE = "customer-service-api"
DEFAULT_LEEWAY_SECONDS = 30

#: 允许的签名算法白名单。显式列白名单是为了挡住 `alg: none` 与
#: RS256→HS256 之类的算法混淆攻击。
ALLOWED_ALGORITHMS: frozenset[str] = frozenset({"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256"})

#: 测试专用密钥。**只有 RAG_ALLOW_TEST_JWT_SECRET 打开时才会被使用**，
#: 生产环境不设该开关，缺失 RAG_JWT_SECRET 会直接抛 AuthConfigError（fail closed）。
TEST_JWT_SECRET = "test-only-secret-not-for-production"

TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class AuthConfig:
    secret: str
    algorithm: str = DEFAULT_ALGORITHM
    issuer: str = DEFAULT_ISSUER
    audience: str = DEFAULT_AUDIENCE
    leeway_seconds: int = DEFAULT_LEEWAY_SECONDS


def _env(source: Mapping[str, str], name: str, default: str) -> str:
    value = (source.get(name) or "").strip()
    return value or default


def load_auth_config(env: Mapping[str, str] | None = None) -> AuthConfig:
    """从环境变量读取鉴权配置。

    每次调用都重新读环境变量，不做缓存 —— 这样测试里改环境变量立即生效，
    也不会出现「进程启动后改配置不生效」的隐蔽问题。开销是几次字典查找，
    相比 JWT 签名校验可以忽略。

    密钥缺失时的行为：
    * 打开了 ``RAG_ALLOW_TEST_JWT_SECRET`` -> 用测试密钥（仅限测试环境）；
    * 否则 -> 抛 ``AuthConfigError``，让服务在第一次鉴权时以 5xx 暴露配置问题，
      而不是静默放行或静默拒绝所有人。
    """

    source = os.environ if env is None else env

    secret = (source.get(JWT_SECRET_ENV) or "").strip()
    if not secret:
        if (source.get(ALLOW_TEST_SECRET_ENV) or "").strip().lower() in TRUTHY:
            secret = TEST_JWT_SECRET
        else:
            raise AuthConfigError(f"{JWT_SECRET_ENV} is not configured; refusing to start without a signing key")

    algorithm = _env(source, JWT_ALGORITHM_ENV, DEFAULT_ALGORITHM).upper()
    if algorithm not in ALLOWED_ALGORITHMS:
        raise AuthConfigError(f"unsupported JWT algorithm {algorithm!r}")

    raw_leeway = (source.get(JWT_LEEWAY_ENV) or "").strip()
    if raw_leeway:
        try:
            leeway = int(raw_leeway)
        except ValueError as error:
            raise AuthConfigError(f"{JWT_LEEWAY_ENV} must be an integer, got {raw_leeway!r}") from error
        if leeway < 0:
            raise AuthConfigError(f"{JWT_LEEWAY_ENV} must not be negative")
    else:
        leeway = DEFAULT_LEEWAY_SECONDS

    return AuthConfig(
        secret=secret,
        algorithm=algorithm,
        issuer=_env(source, JWT_ISSUER_ENV, DEFAULT_ISSUER),
        audience=_env(source, JWT_AUDIENCE_ENV, DEFAULT_AUDIENCE),
        leeway_seconds=leeway,
    )


# ---------------------------------------------------------------- 令牌校验


def decode_access_token(token: str, config: AuthConfig) -> dict[str, Any]:
    """校验并解码访问令牌。

    校验项：签名、算法（白名单）、过期时间、发行方、受众、必需 claim。
    失败一律抛 ``AuthError``，**错误消息里绝不回显令牌内容**。
    """

    if not token or not token.strip():
        raise AuthError("missing_token", "缺少访问令牌")

    try:
        claims = jwt.decode(
            token,
            config.secret,
            algorithms=[config.algorithm],
            issuer=config.issuer,
            audience=config.audience,
            leeway=config.leeway_seconds,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as error:
        raise AuthError("token_expired", "访问令牌已过期") from error
    except jwt.InvalidAudienceError as error:
        raise AuthError("token_audience_mismatch", "访问令牌的受众不匹配") from error
    except jwt.InvalidIssuerError as error:
        raise AuthError("token_issuer_mismatch", "访问令牌的发行方不匹配") from error
    except jwt.MissingRequiredClaimError as error:
        raise AuthError("token_claim_missing", "访问令牌缺少必要声明") from error
    except jwt.InvalidTokenError as error:
        # 签名错误、结构错误、alg 不在白名单等都归到这里，不细分以免泄漏信息
        raise AuthError("token_invalid", "访问令牌无效") from error

    if not isinstance(claims, dict):
        raise AuthError("token_invalid", "访问令牌载荷格式不正确")
    return claims


def _claim_roles(claims: Mapping[str, Any]) -> frozenset[str]:
    raw = claims.get("roles")
    if isinstance(raw, str):
        return frozenset({raw.strip()}) if raw.strip() else frozenset()
    if isinstance(raw, (list, tuple, set, frozenset)):
        cleaned = {str(item).strip() for item in raw if str(item).strip()}
        return frozenset(cleaned)
    return frozenset()


def build_auth_context(claims: Mapping[str, Any], *, request_id: str = "") -> AuthContext:
    """由**已验证**的 claims 构造 AuthContext。

    注意：``scopes`` 一律由服务端按角色推导，token 里同名 claim 被忽略，
    防止持有合法 token 的人自报权限提权。
    """

    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise AuthError("subject_missing", "访问令牌缺少 sub 声明")

    tenant_id = str(claims.get("tenant_id") or "").strip()
    if not tenant_id:
        raise AuthError("tenant_claim_missing", "访问令牌缺少 tenant_id 声明")

    roles = _claim_roles(claims)
    return AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles,
        scopes=resolve_scopes(roles),
        request_id=request_id,
    )
