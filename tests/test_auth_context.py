"""阶段 1.2 的身份上下文与鉴权测试。

分三层覆盖
----------
1. **纯逻辑层**：``load_auth_config`` 的配置解析与 fail closed 行为，
   ``decode_access_token`` 对签名/发行方/受众/过期/算法白名单的校验，
   ``build_auth_context`` 对 claims 的处理（含「忽略 token 自报 scopes」）；
2. **HTTP 层**：401 / 403 / 500 的状态码约定，以及
   **伪造 X-User-Role 不能提权**这条核心安全断言；
3. **策略一致性**：角色→scope 的推导结果与授权表保持同构，
   防止有人只改表不改推导逻辑导致权限静默漂移。

为了不依赖业务路由，测试自建了一组 /probe 探针接口，
它们只做鉴权与回显，把「鉴权行为」和「业务行为」解耦。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import jwt
import pytest

from auth_helpers import TEST_SECRET, auth_headers, issue_token, make_auth_context
from services.auth_context import (
    ALLOWED_ALGORITHMS,
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    READ_OPERATION_ROLES,
    RESOURCE_SCOPE_ROLES,
    REVIEW_ACTION_ROLES,
    ROLE_SCOPES,
    TEST_JWT_SECRET,
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
from services.auth_service import (
    get_auth_context,
    require_read_operation_role,
    require_write_operation_role,
)


# ---------------------------------------------------------------- 探针应用


def build_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe/whoami")
    def whoami(auth: AuthContext = Depends(get_auth_context)) -> dict:
        return {
            "user_id": auth.user_id,
            "tenant_id": auth.tenant_id,
            "roles": sorted(auth.roles),
            "scopes": sorted(auth.scopes),
            "primary_role": auth.primary_role,
            "request_id": auth.request_id,
        }

    @app.get("/probe/chat-read")
    def chat_read(auth: AuthContext = Depends(get_auth_context)) -> dict:
        require_read_operation_role("chat_generate", auth)
        return {"ok": True}

    @app.get("/probe/prompt-write")
    def prompt_write(auth: AuthContext = Depends(get_auth_context)) -> dict:
        require_write_operation_role("prompt_write", auth)
        return {"ok": True}

    @app.get("/probe/unregistered-operation")
    def unregistered(auth: AuthContext = Depends(get_auth_context)) -> dict:
        require_read_operation_role("no_such_operation", auth)
        return {"ok": True}

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(build_probe_app())


# ---------------------------------------------------------------- 1. 配置解析


def test_missing_secret_fails_closed() -> None:
    """没有密钥时必须报错，而不是悄悄用某个默认值放行。"""

    with pytest.raises(AuthConfigError):
        load_auth_config({})


def test_test_secret_only_used_with_explicit_opt_in() -> None:
    config = load_auth_config({"RAG_ALLOW_TEST_JWT_SECRET": "1"})
    assert config.secret == TEST_JWT_SECRET

    # 开关写别的值等于没开
    with pytest.raises(AuthConfigError):
        load_auth_config({"RAG_ALLOW_TEST_JWT_SECRET": "0"})


def test_config_reads_issuer_audience_algorithm_from_env() -> None:
    config = load_auth_config(
        {
            "RAG_JWT_SECRET": "s3cret",
            "RAG_JWT_ISSUER": "issuer-x",
            "RAG_JWT_AUDIENCE": "audience-y",
            "RAG_JWT_ALGORITHM": "hs512",
            "RAG_JWT_LEEWAY_SECONDS": "5",
        }
    )
    assert (config.secret, config.issuer, config.audience) == ("s3cret", "issuer-x", "audience-y")
    assert config.algorithm == "HS512"
    assert config.leeway_seconds == 5


@pytest.mark.parametrize(
    "env",
    [
        {"RAG_JWT_SECRET": "s", "RAG_JWT_ALGORITHM": "none"},
        {"RAG_JWT_SECRET": "s", "RAG_JWT_ALGORITHM": "HS256 ", "RAG_JWT_LEEWAY_SECONDS": "abc"},
        {"RAG_JWT_SECRET": "s", "RAG_JWT_LEEWAY_SECONDS": "-5"},
    ],
)
def test_invalid_config_is_rejected(env: dict) -> None:
    with pytest.raises(AuthConfigError):
        load_auth_config(env)


def test_algorithm_allowlist_has_no_none() -> None:
    assert "none" not in {item.lower() for item in ALLOWED_ALGORITHMS}


# ---------------------------------------------------------------- 2. 令牌校验


def _claims(**overrides: object) -> dict:
    now = datetime.now(timezone.utc)
    base: dict = {
        "sub": "u1",
        "tenant_id": "t1",
        "roles": ["admin"],
        "iss": DEFAULT_ISSUER,
        "aud": DEFAULT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    base.update(overrides)
    return base


def test_decode_accepts_valid_token() -> None:
    config = load_auth_config()
    claims = decode_access_token(issue_token(roles=["agent"], user_id="u9"), config)
    assert claims["sub"] == "u9"


@pytest.mark.parametrize(
    "token_factory, expected_code",
    [
        (lambda: issue_token(expires_in_seconds=-120), "token_expired"),
        (lambda: issue_token(issuer="someone-else"), "token_issuer_mismatch"),
        (lambda: issue_token(audience="other-api"), "token_audience_mismatch"),
        (lambda: issue_token(secret="wrong-secret-that-is-long-enough-for-hs256"), "token_invalid"),
        (lambda: issue_token(omit_claims=("exp",)), "token_claim_missing"),
        (lambda: issue_token(omit_claims=("aud",)), "token_claim_missing"),
        (lambda: jwt.encode(_claims(), "", algorithm="none"), "token_invalid"),
        (lambda: "", "missing_token"),
        (lambda: "not-a-jwt", "token_invalid"),
    ],
)
def test_decode_rejects_invalid_tokens(token_factory, expected_code: str) -> None:
    config = load_auth_config()
    with pytest.raises(AuthError) as error:
        decode_access_token(token_factory(), config)
    assert error.value.code == expected_code


def test_decode_error_message_never_echoes_token() -> None:
    config = load_auth_config()
    token = issue_token(secret="wrong-secret-that-is-long-enough-for-hs256")
    with pytest.raises(AuthError) as error:
        decode_access_token(token, config)
    assert token not in error.value.message


# ---------------------------------------------------------------- 3. AuthContext


def test_auth_context_is_immutable() -> None:
    context = make_auth_context(roles=["agent"])
    with pytest.raises(FrozenInstanceError):
        context.tenant_id = "other"  # type: ignore[misc]


def test_auth_context_requires_identity_fields() -> None:
    with pytest.raises(AuthError):
        AuthContext(user_id="", tenant_id="t1")
    with pytest.raises(AuthError):
        AuthContext(user_id="u1", tenant_id="   ")


def test_primary_role_is_deterministic_across_role_order() -> None:
    first = make_auth_context(roles=["supervisor", "agent"])
    second = make_auth_context(roles=["agent", "supervisor"])
    assert first.primary_role == second.primary_role == "agent"


def test_require_scope_raises_scope_denied() -> None:
    context = make_auth_context(roles=["agent"])
    context.require_scope(read_scope("chat_generate"))
    with pytest.raises(ScopeDenied):
        context.require_scope(write_scope("prompt_write"))


def test_build_auth_context_rejects_missing_tenant_and_subject() -> None:
    with pytest.raises(AuthError) as error:
        build_auth_context(_claims(tenant_id=""))
    assert error.value.code == "tenant_claim_missing"

    with pytest.raises(AuthError) as error:
        build_auth_context({key: value for key, value in _claims().items() if key != "sub"})
    assert error.value.code == "subject_missing"


def test_build_auth_context_accepts_roles_as_string() -> None:
    context = build_auth_context(_claims(roles="qa"))
    assert context.roles == frozenset({"qa"})


def test_token_supplied_scopes_are_ignored() -> None:
    """token 里自报 scopes / permissions 一律无效 —— 权限只能由服务端按角色推导。"""

    context = build_auth_context(
        _claims(
            roles=["agent"],
            scopes=["write:prompt_write", "read:audit_read"],
            permissions=["*"],
        )
    )
    assert write_scope("prompt_write") not in context.scopes
    assert read_scope("audit_read") not in context.scopes


def test_unknown_role_contributes_no_scope() -> None:
    assert resolve_scopes(["root", "superuser"]) == frozenset()
    assert make_auth_context(roles=["root"]).roles == frozenset({"root"})
    assert make_auth_context(roles=["root"]).primary_role == "root"


# ---------------------------------------------------------------- 4. 策略一致性


def test_role_scopes_is_exact_inversion_of_policy_tables() -> None:
    """ROLE_SCOPES 必须恰好是授权表的 inversion，防止推导逻辑与表脱节。

    阶段 4.1 起有四张表：三张操作维度 + 一张资源维度（``document:read`` 这类
    自带完整命名空间的 scope，因此前缀函数是恒等映射 ``resource_scope``）。
    """

    expected: dict[str, set[str]] = {role: set() for role in VALID_ROLES}
    for prefix, table in (
        (read_scope, READ_OPERATION_ROLES),
        (write_scope, WRITE_OPERATION_ROLES),
        (review_scope, REVIEW_ACTION_ROLES),
        (resource_scope, RESOURCE_SCOPE_ROLES),
    ):
        for name, roles in table.items():
            for role in roles:
                expected[role].add(prefix(name))

    assert {role: set(scopes) for role, scopes in ROLE_SCOPES.items()} == expected


def test_resource_scopes_follow_the_plan_enumeration() -> None:
    """计划 4.1 点名的九个资源级权限必须**逐个**存在，且键名即完整 scope。"""

    plan_permissions = {
        "knowledge_base:read",
        "knowledge_base:write",
        "document:upload",
        "document:read",
        "document:review",
        "document:publish",
        "document:delete",
        "index:rebuild",
        "audit:read",
    }
    assert set(RESOURCE_SCOPE_ROLES) == plan_permissions
    for permission in plan_permissions:
        assert resource_scope(permission) == permission


def test_publish_and_index_rebuild_are_granted_separately() -> None:
    """计划 4.2：发布与索引重建必须分开授权（两者是独立的两道门）。"""

    knowledge_ops = make_auth_context(roles=["knowledge_ops"])
    assert knowledge_ops.has_scope(resource_scope("document:publish"))
    assert not knowledge_ops.has_scope(resource_scope("index:rebuild"))
    assert not knowledge_ops.has_scope(resource_scope("document:delete"))

    supervisor = make_auth_context(roles=["supervisor"])
    assert supervisor.has_scope(resource_scope("index:rebuild"))
    assert supervisor.has_scope(resource_scope("document:publish"))

    agent = make_auth_context(roles=["agent"])
    assert not agent.has_scope(resource_scope("document:publish"))
    assert not agent.has_scope(resource_scope("index:rebuild"))


def test_admin_holds_every_scope_and_agent_does_not() -> None:
    admin = make_auth_context(roles=["admin"])
    for table, prefix in (
        (READ_OPERATION_ROLES, read_scope),
        (WRITE_OPERATION_ROLES, write_scope),
        (REVIEW_ACTION_ROLES, review_scope),
        (RESOURCE_SCOPE_ROLES, resource_scope),
    ):
        for name in table:
            assert admin.has_scope(prefix(name))

    agent = make_auth_context(roles=["agent"])
    assert not agent.has_scope(write_scope("knowledge_publish"))
    assert not agent.has_scope(read_scope("audit_read"))
    assert not agent.has_scope(resource_scope("document:publish"))
    assert not agent.has_scope(resource_scope("index:rebuild"))
    assert not agent.has_scope(resource_scope("audit:read"))


# ---------------------------------------------------------------- 5. HTTP 层


def test_valid_token_is_accepted(client: TestClient) -> None:
    response = client.get("/probe/whoami", headers=auth_headers(roles=["agent"], user_id="agent_7"))
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "agent_7"
    assert body["tenant_id"] == "tenant-1"
    assert body["roles"] == ["agent"]
    assert body["request_id"]


def test_request_id_header_is_propagated(client: TestClient) -> None:
    headers = auth_headers(roles=["agent"])
    headers["X-Request-Id"] = "trace-42"
    body = client.get("/probe/whoami", headers=headers).json()
    assert body["request_id"] == "trace-42"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic YWRtaW46YWRtaW4="},
        {"Authorization": "Token abc"},
    ],
)
def test_missing_or_malformed_authorization_returns_401(client: TestClient, headers: dict) -> None:
    assert client.get("/probe/whoami", headers=headers).status_code == 401


def test_expired_token_returns_401(client: TestClient) -> None:
    response = client.get("/probe/whoami", headers=auth_headers(expires_in_seconds=-60))
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_token_without_tenant_returns_401(client: TestClient) -> None:
    response = client.get("/probe/whoami", headers=auth_headers(omit_claims=("tenant_id",)))
    assert response.status_code == 401


def test_insufficient_role_returns_403(client: TestClient) -> None:
    """认证通过但授权不足 —— 必须是 403，不能混成 401。"""

    assert client.get("/probe/prompt-write", headers=auth_headers(roles=["agent"])).status_code == 403
    assert client.get("/probe/prompt-write", headers=auth_headers(roles=["admin"])).status_code == 200


def test_unregistered_operation_is_fail_closed(client: TestClient) -> None:
    """未登记的 operation 不授予任何人，连 admin 也拿不到。"""

    assert client.get("/probe/unregistered-operation", headers=auth_headers(roles=["admin"])).status_code == 403


def test_forged_role_header_cannot_elevate_privileges(client: TestClient) -> None:
    """核心安全断言：伪造 X-User-Role / X-Operator-Id 不能换来任何权限。"""

    forged = auth_headers(roles=["agent"], user_id="agent_1")
    forged.update({"X-User-Role": "admin", "X-Operator-Id": "admin_1"})

    assert client.get("/probe/prompt-write", headers=forged).status_code == 403
    # 身份仍以 JWT 为准，header 不参与
    assert client.get("/probe/whoami", headers=forged).json()["user_id"] == "agent_1"


def test_forged_role_header_cannot_authenticate_alone(client: TestClient) -> None:
    response = client.get("/probe/whoami", headers={"X-User-Role": "admin", "X-Operator-Id": "admin_1"})
    assert response.status_code == 401


def test_token_supplied_scopes_cannot_elevate_privileges(client: TestClient) -> None:
    token = issue_token(roles=["agent"], extra_claims={"scopes": ["write:prompt_write"], "permissions": ["*"]})
    response = client.get("/probe/prompt-write", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_missing_server_secret_returns_500(client: TestClient, monkeypatch) -> None:
    """服务端没配密钥属于服务端故障（5xx），不该判成用户的 401。"""

    monkeypatch.delenv("RAG_JWT_SECRET", raising=False)
    monkeypatch.delenv("RAG_ALLOW_TEST_JWT_SECRET", raising=False)

    response = client.get("/probe/whoami", headers=auth_headers(roles=["admin"]))
    assert response.status_code == 500


def test_secret_from_env_is_actually_used() -> None:
    """测试进程里生效的密钥必须来自环境变量，而不是内置常量。"""

    assert load_auth_config().secret == TEST_SECRET
    assert TEST_SECRET != TEST_JWT_SECRET
