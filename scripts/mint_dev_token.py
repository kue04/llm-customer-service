r"""签发本地开发用的 JWT（2026-09-23 新增，前端联调用）。

为什么需要它
------------
阶段 1 起**所有受保护接口只认 `Authorization: Bearer <JWT>`**，
`X-User-Role` / `X-Operator-Id` 已彻底失效。前端（`D:\llm\front`）里
每个 `src/api/*.ts` 都还在发旧的 X-* 头 —— 联调时会直接拿到 401。
但前端没法自己造令牌：**签名密钥只在服务端**，这正是这条边界存在的意义。

所以开发期的正确做法是：后端这边签一个**角色受限**的令牌给前端用，
而不是把 X-* 头恢复回去。

用法
----
先把密钥设进当前 shell（服务端和本脚本必须用同一个值；
HS256 的密钥**至少 32 字节**，短了 PyJWT 会告警）::

    export RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+
    ./venv/Scripts/python.exe -m uvicorn main:app --reload

再签令牌::

    # 默认签一个 admin（联调全接口时最省事）
    ./venv/Scripts/python.exe scripts/mint_dev_token.py

    # 按页面签对应角色
    ./venv/Scripts/python.exe scripts/mint_dev_token.py --role agent
    ./venv/Scripts/python.exe scripts/mint_dev_token.py --role knowledge_ops --ttl 43200

    # 直接输出可贴进 Swagger UI / 前端的形态
    ./venv/Scripts/python.exe scripts/mint_dev_token.py --role agent --format curl
    ./venv/Scripts/python.exe scripts/mint_dev_token.py --role agent --format env

只想临时跑通、不想设密钥时，服务端与本脚本都设
``RAG_ALLOW_TEST_JWT_SECRET=1``（走 ``services.auth_context.TEST_JWT_SECRET``）。
**这条开关只用于本地开发，绝不要在生产环境打开。**
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import jwt  # noqa: E402

from services.auth_context import (  # noqa: E402
    ALLOW_TEST_SECRET_ENV,
    DEFAULT_ALGORITHM,
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    JWT_SECRET_ENV,
    ROLE_SCOPES,
    TEST_JWT_SECRET,
    VALID_ROLES,
    load_auth_config,
)


#: 角色 -> 建议的演示身份（仅是默认值，可用 --role 覆盖）
ROLE_EXAMPLES: dict[str, str] = {
    "agent": "客服坐席，问答工作台 + 订单状态",
    "supervisor": "主管，含质量概览 / 审计 / 发布检查 / 索引重建",
    "knowledge_ops": "知识运营，知识条目与文档接入",
    "qa": "质检，只读审计与指标",
    "admin": "管理员，全部权限含提示词写",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="颁发本地开发用 JWT（角色受限，claim 与 services/auth_context.py 的授权表一致）"
    )
    parser.add_argument(
        "--role",
        default="admin",
        help="逗号分隔的角色，可选：" + "、".join(sorted(VALID_ROLES)) + "（默认 admin）",
    )
    parser.add_argument("--user-id", default="dev_user", help="sub claim，默认 dev_user")
    parser.add_argument("--tenant-id", default="tenant-dev", help="tenant_id claim，默认 tenant-dev")
    parser.add_argument("--ttl", type=int, default=28800, help="有效期（秒），默认 28800（8 小时）")
    parser.add_argument(
        "--format",
        choices=("token", "decoded", "curl", "env", "json"),
        default="decoded",
        help="输出形态：裸令牌 / 令牌+解码信息 / curl 示例 / 前端 env 片段 / JSON",
    )
    return parser


def _resolve_secret() -> tuple[str, str]:
    """返回 ``(密钥, 来源说明)``。缺失密钥时给出可操作的报错。"""

    try:
        config = load_auth_config()
    except Exception as error:  # AuthConfigError，但不去 import 具体类以免耦合
        raise SystemExit(
            f"无法读取签名密钥：{error}\n"
            f"请先设置 export {JWT_SECRET_ENV}=<你的密钥>（服务端必须用同一个值），\n"
            f"或本地临时开发设置 export {ALLOW_TEST_SECRET_ENV}=1。"
        ) from error

    import os

    if os.environ.get(JWT_SECRET_ENV, "").strip():
        return config.secret, f"环境变量 {JWT_SECRET_ENV}"
    return TEST_JWT_SECRET, f"{ALLOW_TEST_SECRET_ENV}=1 → 测试密钥（仅供本地开发）"


def issue(role: str, user_id: str, tenant_id: str, ttl: int, secret: str) -> tuple[str, dict]:
    roles = [item.strip() for item in role.split(",") if item.strip()]
    unknown = [item for item in roles if item not in VALID_ROLES]
    if unknown:
        raise SystemExit(
            f"未知角色：{', '.join(unknown)}\n可选角色：{', '.join(sorted(VALID_ROLES))}"
        )
    if not roles:
        raise SystemExit("至少要指定一个角色")

    now = datetime.now(timezone.utc)
    claims = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "iss": DEFAULT_ISSUER,
        "aud": DEFAULT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(claims, secret, algorithm=DEFAULT_ALGORITHM)
    return token, claims


def main_cli() -> int:
    args = build_parser().parse_args()
    secret, secret_source = _resolve_secret()
    token, claims = issue(args.role, args.user_id, args.tenant_id, args.ttl, secret)
    roles = claims["roles"]
    scopes = sorted({scope for role in roles for scope in ROLE_SCOPES.get(role, frozenset())})

    if args.format == "token":
        print(token)
        return 0

    if args.format == "json":
        print(json.dumps({"token": token, "claims": claims, "scopes": scopes}, ensure_ascii=False, indent=2))
        return 0

    expires_at = datetime.fromtimestamp(claims["exp"], tz=timezone.utc).astimezone()
    if args.format == "env":
        print("# 贴到 D:\\llm\\front\\.env.local（前端自行决定怎么注入，不要把令牌提交进仓库）")
        print("VITE_API_BASE_URL=http://127.0.0.1:8000")
        print(f"VITE_DEV_TOKEN={token}")
        return 0

    if args.format == "curl":
        print(f"curl -H \"Authorization: Bearer {token}\" http://127.0.0.1:8000/chat/history?user_id={claims['sub']}")
        return 0

    print("=" * 72)
    print("开发用 JWT 已签发")
    print("=" * 72)
    print(f"角色      ：{', '.join(roles)}")
    for role in roles:
        print(f"            - {role}：{ROLE_EXAMPLES.get(role, '—')}")
    print(f"user_id   ：{claims['sub']}")
    print(f"tenant_id ：{claims['tenant_id']}")
    print(f"有效期至  ：{expires_at:%Y-%m-%d %H:%M:%S %z}（{args.ttl} 秒）")
    print(f"签名密钥  ：来自 {secret_source}")
    print()
    print(f"该身份可用 scope（{len(scopes)} 个）：")
    for scope in scopes:
        print(f"  - {scope}")
    print()
    print("令牌：")
    print(token)
    print()
    print("用法：")
    print("  # Swagger UI 右上角 Authorize 里粘贴上面的令牌（不用加 Bearer 前缀）")
    print("  # 命令行：")
    print('  curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/model/info')
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
