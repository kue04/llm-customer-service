"""给本地开发库种最小可用的租户 / 用户 / 知识库（2026-09-23 新增，前端联调用）。

为什么需要它
------------
阶段 0~4 只交付了**上传所需的身份侧数据模型**，没有任何「创建知识库」的接口：

* ``POST /knowledge-bases/{id}/documents`` 要求调用者是该知识库的**写成员**
  （``knowledge_base_members``），并会校验知识库存在；
* 写路径的审计事件有 ``tenant_id`` 外键（SQLite 已开 ``PRAGMA foreign_keys=ON``）。

于是「迁移完的开发库」是**空租户库**：直接上传会 404「知识库不存在」，
直接 ``POST /ingestion/indexes/rebuild`` 会 500（审计写入撞外键）。
本脚本补的就是这一步，让前端能跑通「上传 → 处理 → 重建索引 → 检索」。

它**不动任何已有数据**（幂等：存在就复用），也不创建文档与 chunk。

用法
----
    export RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+
    ./venv/Scripts/python.exe scripts/seed_dev_tenant.py
    ./venv/Scripts/python.exe scripts/seed_dev_tenant.py --tenant-id tenant-dev --user-id dev_user

输出里会打印 ``knowledge_base_id``，它是上传接口路径参数要用的值。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.auth_context import VALID_ROLES  # noqa: E402
from services.ingestion import db, repository  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="给开发库种租户 / 用户 / 知识库（幂等）")
    parser.add_argument("--tenant-id", default="tenant-dev", help="租户 id，需与 JWT 的 tenant_id 一致")
    parser.add_argument("--tenant-name", default="开发租户", help="租户显示名")
    parser.add_argument("--user-id", default="dev_user", help="用户 external_id，需与 JWT 的 sub 一致")
    parser.add_argument("--user-name", default="开发用户", help="用户显示名")
    parser.add_argument("--kb-slug", default="takeout-policy", help="知识库 slug（租户内唯一）")
    parser.add_argument("--kb-name", default="外卖政策库", help="知识库显示名")
    parser.add_argument(
        "--skip-roles",
        action="store_true",
        help="不写 user_roles（默认写全部已知角色，便于本地一把过）",
    )
    return parser


def main_cli() -> int:
    args = build_parser().parse_args()

    with db.session_scope() as session:
        tenant = repository.get_tenant(session, args.tenant_id)
        if tenant is None:
            tenant = repository.create_tenant(session, name=args.tenant_name, tenant_id=args.tenant_id)
            print(f"[+] 新建租户      {tenant.id}")
        else:
            print(f"[=] 租户已存在    {tenant.id}")

        user = repository.get_user_by_external_id(session, args.tenant_id, args.user_id)
        if user is None:
            user = repository.create_user(
                session,
                tenant_id=args.tenant_id,
                external_id=args.user_id,
                display_name=args.user_name,
            )
            print(f"[+] 新建用户      {user.id}（external_id={args.user_id}）")
        else:
            print(f"[=] 用户已存在    {user.id}（external_id={args.user_id}）")

        if not args.skip_roles:
            assigned = set(repository.list_user_role_codes(session, args.tenant_id, user.id))
            for code in sorted(VALID_ROLES):
                if code in assigned:
                    continue
                role = repository.get_role_by_code(session, args.tenant_id, code)
                if role is None:
                    role = repository.create_role(session, tenant_id=args.tenant_id, code=code)
                repository.assign_role(
                    session, tenant_id=args.tenant_id, user_id=user.id, role_id=role.id
                )
                print(f"[+] 授予角色      {code}")
            if assigned:
                print(f"[=] 已有角色      {', '.join(sorted(assigned))}")

        knowledge_base = repository.get_knowledge_base_by_slug(session, args.tenant_id, args.kb_slug)
        if knowledge_base is None:
            knowledge_base = repository.create_knowledge_base(
                session,
                tenant_id=args.tenant_id,
                slug=args.kb_slug,
                name=args.kb_name,
                description="本地开发用知识库（由 scripts/seed_dev_tenant.py 创建）",
                created_by=user.id,
            )
            print(f"[+] 新建知识库    {knowledge_base.id}")
        else:
            print(f"[=] 知识库已存在  {knowledge_base.id}")

        member = repository.get_knowledge_base_member(
            session, args.tenant_id, knowledge_base.id, user.id
        )
        if member is None:
            repository.add_knowledge_base_member(
                session,
                tenant_id=args.tenant_id,
                knowledge_base_id=knowledge_base.id,
                user_id=user.id,
                member_role="owner",
            )
            print("[+] 加入知识库    role=owner")
        else:
            print(f"[=] 已是成员      role={member.member_role}")

        tenant_id = args.tenant_id
        user_external = args.user_id
        kb_id = knowledge_base.id

    print()
    print("=" * 72)
    print("本地开发数据已就绪")
    print("=" * 72)
    print(f"tenant_id          : {tenant_id}")
    print(f"JWT sub(user_id)   : {user_external}")
    print(f"knowledge_base_id  : {kb_id}")
    print()
    print("下一步（三条命令，端口按需改）：")
    print()
    print("  1) 签令牌（拿到 -format token 的输出）")
    print(
        f"     ./venv/Scripts/python.exe scripts/mint_dev_token.py "
        f"--role admin --tenant-id {tenant_id} --user-id {user_external} --format token"
    )
    print()
    print("  2) 上传一份文档（multipart，字段名必须是 file）")
    print(
        f"     curl -H \"Authorization: Bearer <token>\" -F \"file=@tmp/demo_upload.md\" "
        f"http://127.0.0.1:8000/knowledge-bases/{kb_id}/documents"
    )
    print()
    print("  3) 重建 chunk 索引后检索")
    print(
        "     curl -X POST -H \"Authorization: Bearer <token>\" "
        "http://127.0.0.1:8000/ingestion/indexes/rebuild"
    )
    print(
        "     curl -H \"Authorization: Bearer <token>\" -H \"Content-Type: application/json\" "
        "-d '{\"query\":\"...\"}' http://127.0.0.1:8000/retrieval/search"
    )
    print()
    print("⚠️ 注意：本机无 Redis 时队列是**进程内**的（services/ingestion/queue.py:105），")
    print("   API 进程投递的任务不会被独立的 worker 进程消费 —— 上传后任务会停在 pending。")
    print("   联调「上传→可检索」闭环需要 Redis（RAG_REDIS_STREAM_URL），")
    print("   或在同一进程内直接调用 services.ingestion.pipeline.process_job。")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
