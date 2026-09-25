"""从 OpenAPI schema 生成前端要用的两份产物（2026-09-23 新增）。

产出
----
1. ``docs/frontend/FRONTEND_API_CONTRACT.md`` —— 人读的接口与字段字典：
   逐端点的鉴权要求、请求/响应字段、类型、必填、约束、默认值，以及数据模型全量展开。
2. ``docs/frontend/backend_contract_types.ts`` —— 机读的 TypeScript 类型：
   直接对应 ``components.schemas``，前端复制过去即可，避免手抄字段抄错。

为什么要有这个脚本
------------------
``docs/API_INTEGRATION.md`` 是**手写散文**，它记录过 ``X-User-Role`` 时代、
也记录过 ``/retrieval/search`` 返回种子 FAQ 的旧形状 —— 两者都已经变了。
手写文档无法保证与代码同步，所以这里改成**从代码生成**：
接口改了，重跑本脚本，文档就是新的。

鉴权表为什么是手写的
--------------------
FastAPI 的 ``Depends(get_auth_context)`` 在请求体里**不出现**
（身份来自 Authorization 头），依赖函数体里的 ``require_*`` 调用更是
OpenAPI 看不见的。所以 :data:`ENDPOINT_AUTH` 只能人工登记；
新增端点时**必须同步加一行**，否则文档会漏标鉴权。

用法
----
    python scripts/export_frontend_contract.py
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OPENAPI = Path("docs/frontend/openapi.json")
DEFAULT_MD = Path("docs/frontend/FRONTEND_API_CONTRACT.md")
DEFAULT_TS = Path("docs/frontend/backend_contract_types.ts")

#: 端点 -> 鉴权要求。键是 ``"METHOD /path"``，值见 :data:`AuthSpec` 的字段说明。
#: **新增端点必须在这里加一行**（见模块 docstring 的说明）。
ENDPOINT_AUTH: dict[str, dict[str, Any]] = {
    "GET /": {"public": True, "roles": []},
    "GET /health": {"public": True, "roles": []},
    "POST /chat/prompt": {"scopes": ["read:chat_generate"], "roles": ["agent", "supervisor", "qa", "admin"]},
    "GET /chat/history": {"scopes": ["read:chat_history"], "roles": ["agent", "supervisor", "qa", "admin"]},
    "POST /chat/review-action": {
        "scopes": ["review:<action>"],
        "roles": ["agent", "supervisor", "qa", "knowledge_ops", "admin"],
        "note": "action 是请求体字段，按值判定：accepted / edited_and_sent / human_handoff 只给 agent/supervisor/admin；marked_bad_case 额外允许 qa、knowledge_ops。",
    },
    "GET /audit/logs": {"scopes": ["read:audit_read"], "roles": ["supervisor", "qa", "admin"]},
    "GET /examples/categories": {"scopes": ["read:example_read"], "roles": ["supervisor", "knowledge_ops", "qa", "admin"]},
    "GET /examples/by-category": {"scopes": ["read:example_read"], "roles": ["supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /examples/search": {"scopes": ["read:example_read"], "roles": ["supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /feedback": {"scopes": ["write:feedback_create"], "roles": ["agent", "supervisor", "qa", "knowledge_ops", "admin"]},
    "GET /feedback/recent": {"scopes": ["read:feedback_read"], "roles": ["supervisor", "qa", "knowledge_ops", "admin"]},
    "POST /feedback/export-eval-case": {"scopes": ["write:feedback_export_eval_case"], "roles": ["agent", "supervisor", "qa", "knowledge_ops", "admin"]},
    "GET /model/info": {"scopes": ["read:model_info_read"], "roles": ["supervisor", "qa", "admin"]},
    "GET /knowledge/items": {"scopes": ["read:knowledge_read"], "roles": ["supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /knowledge/items": {"scopes": ["write:knowledge_create"], "roles": ["supervisor", "knowledge_ops", "admin"]},
    "PUT /knowledge/items/{item_id}": {"scopes": ["write:knowledge_update"], "roles": ["supervisor", "knowledge_ops", "admin"]},
    "POST /knowledge/items/{item_id}/archive": {"scopes": ["write:knowledge_archive"], "roles": ["supervisor", "knowledge_ops", "admin"]},
    "POST /knowledge/items/{item_id}/review": {"scopes": ["write:knowledge_review"], "roles": ["supervisor", "knowledge_ops", "admin"]},
    "GET /knowledge/export-approved": {"scopes": ["read:knowledge_read"], "roles": ["supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /knowledge/publish-approved": {"scopes": ["write:knowledge_publish", "document:publish"], "roles": ["supervisor", "knowledge_ops", "admin"]},
    "GET /knowledge/publish-history": {"scopes": ["read:knowledge_read"], "roles": ["supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /knowledge/rollback-latest": {"scopes": ["write:knowledge_rollback"], "roles": ["supervisor", "admin"]},
    "POST /knowledge-bases/{knowledge_base_id}/documents": {
        "scopes": ["write:knowledge_create", "document:upload"],
        "roles": ["supervisor", "knowledge_ops", "admin"],
        "note": "额外要求：调用者必须是该知识库的写成员（knowledge_base_members）。",
    },
    "GET /documents/{document_id}": {"scopes": ["read:knowledge_read", "document:read"], "roles": ["agent", "supervisor", "knowledge_ops", "qa", "admin"]},
    "GET /documents/{document_id}/versions": {"scopes": ["read:knowledge_read", "document:read"], "roles": ["agent", "supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /documents/{document_id}/reprocess": {"scopes": ["write:knowledge_rollback"], "roles": ["supervisor", "admin"]},
    "GET /ingestion-jobs/{job_id}": {"scopes": ["read:knowledge_read", "document:read"], "roles": ["agent", "supervisor", "knowledge_ops", "qa", "admin"]},
    "POST /ingestion/indexes/rebuild": {
        "scopes": ["index:rebuild"],
        "roles": ["supervisor", "admin"],
        "note": "只有资源维度授权，没有操作维度授权 —— 索引是全局一份，影响所有租户。",
    },
    "PUT /orders/{order_id}/state": {"scopes": ["write:order_state_upsert"], "roles": ["agent", "supervisor", "admin"]},
    "GET /orders/{order_id}/state": {"scopes": ["read:order_state_read"], "roles": ["agent", "supervisor", "admin"]},
    "GET /prompt/versions": {"scopes": ["read:prompt_read"], "roles": ["supervisor", "qa", "admin"]},
    "GET /prompt/active": {"scopes": ["read:prompt_read"], "roles": ["supervisor", "qa", "admin"]},
    "POST /prompt/versions": {"scopes": ["write:prompt_write"], "roles": ["admin"]},
    "POST /prompt/versions/{version_id}/status": {"scopes": ["write:prompt_write"], "roles": ["admin"]},
    "POST /prompt/versions/{version_id}/activate": {"scopes": ["write:prompt_write"], "roles": ["admin"]},
    "POST /prompt/rollback-latest": {"scopes": ["write:prompt_write"], "roles": ["admin"]},
    "GET /ops/metrics": {"scopes": ["read:ops_metrics_read"], "roles": ["supervisor", "qa", "admin"]},
    "GET /release/checklist": {"scopes": ["read:release_read"], "roles": ["supervisor", "qa", "admin"]},
    "GET /retrieval/config": {"scopes": ["read:retrieval_read"], "roles": ["agent", "supervisor", "qa", "admin"]},
    "POST /retrieval/search": {"scopes": ["read:retrieval_read"], "roles": ["agent", "supervisor", "qa", "admin"]},
    "POST /retrieval/search-demo": {"scopes": ["read:retrieval_read"], "roles": ["agent", "supervisor", "qa", "admin"]},
    "POST /retrieval/prompt-preview": {"scopes": ["read:retrieval_read"], "roles": ["agent", "supervisor", "qa", "admin"]},
}

#: 端点分组（按前端页面归属，不是按 OpenAPI tag）
ENDPOINT_FRONTEND_GROUP: dict[str, str] = {
    "POST /chat/prompt": "问答工作台",
    "GET /chat/history": "问答工作台",
    "POST /chat/review-action": "问答工作台",
    "POST /feedback": "反馈队列",
    "GET /feedback/recent": "反馈队列",
    "POST /feedback/export-eval-case": "反馈队列",
    "GET /retrieval/search": "检索",
    "GET /retrieval/config": "检索",
    "POST /retrieval/search": "检索（正式路径 · chunk 级）",
    "POST /retrieval/search-demo": "检索（演示路径 · 种子 FAQ）",
    "POST /retrieval/prompt-preview": "检索（演示路径 · 种子 FAQ）",
    "POST /knowledge-bases/{knowledge_base_id}/documents": "知识库 · 接入",
    "GET /documents/{document_id}": "知识库 · 接入",
    "GET /documents/{document_id}/versions": "知识库 · 接入",
    "POST /documents/{document_id}/reprocess": "知识库 · 接入",
    "GET /ingestion-jobs/{job_id}": "知识库 · 接入",
    "POST /ingestion/indexes/rebuild": "知识库 · 接入",
    "GET /knowledge/items": "知识库 · 运营",
    "POST /knowledge/items": "知识库 · 运营",
    "PUT /knowledge/items/{item_id}": "知识库 · 运营",
    "POST /knowledge/items/{item_id}/archive": "知识库 · 运营",
    "POST /knowledge/items/{item_id}/review": "知识库 · 运营",
    "GET /knowledge/export-approved": "知识库 · 运营",
    "POST /knowledge/publish-approved": "知识库 · 运营",
    "GET /knowledge/publish-history": "知识库 · 运营",
    "POST /knowledge/rollback-latest": "知识库 · 运营",
    "GET /prompt/versions": "配置与发布",
    "GET /prompt/active": "配置与发布",
    "POST /prompt/versions": "配置与发布",
    "POST /prompt/versions/{version_id}/status": "配置与发布",
    "POST /prompt/versions/{version_id}/activate": "配置与发布",
    "POST /prompt/rollback-latest": "配置与发布",
    "GET /release/checklist": "配置与发布",
    "GET /audit/logs": "安全与治理",
    "GET /model/info": "运行概览",
    "GET /ops/metrics": "运行概览",
    "GET /examples/categories": "知识库浏览（种子 FAQ）",
    "GET /examples/by-category": "知识库浏览（种子 FAQ）",
    "POST /examples/search": "知识库浏览（种子 FAQ）",
    "PUT /orders/{order_id}/state": "订单状态",
    "GET /orders/{order_id}/state": "订单状态",
    "GET /": "其他",
    "GET /health": "其他",
}

GROUP_ORDER = [
    "问答工作台",
    "检索（正式路径 · chunk 级）",
    "检索（演示路径 · 种子 FAQ）",
    "检索",
    "知识库 · 接入",
    "知识库 · 运营",
    "知识库浏览（种子 FAQ）",
    "反馈队列",
    "配置与发布",
    "安全与治理",
    "运行概览",
    "订单状态",
    "其他",
]

HTTP_METHODS = ("get", "post", "put", "delete", "patch")


# ---------------------------------------------------------------- 通用小工具


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """把 ``$ref`` 展开成实际 schema（只展开一层，够用且不会无限递归）。"""

    seen = 0
    current = schema
    while isinstance(current, dict) and "$ref" in current and seen < 8:
        ref = current["$ref"]
        name = ref.rsplit("/", 1)[-1]
        current = root.get("components", {}).get("schemas", {}).get(name, {}) or {}
        seen += 1
    return current if isinstance(current, dict) else {}


def _ref_name(schema: Any) -> str | None:
    if isinstance(schema, dict) and "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    return None


def _type_label(schema: Any, root: dict[str, Any]) -> str:
    """把 schema 压成一行可读的类型标签。"""

    if not isinstance(schema, dict) or not schema:
        return "any"

    name = _ref_name(schema)
    if name:
        return name

    if "anyOf" in schema or "oneOf" in schema:
        parts = schema.get("anyOf") or schema.get("oneOf") or []
        labels: list[str] = []
        has_null = False
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "null":
                has_null = True
                continue
            labels.append(_type_label(part, root))
        base = " | ".join(dict.fromkeys(labels)) or "any"
        return f"{base} | null" if has_null else base

    if "allOf" in schema:
        labels = [_type_label(part, root) for part in schema["allOf"]]
        return " & ".join(dict.fromkeys(labels))

    if "enum" in schema:
        return " \\| ".join(f"`{value}`" for value in schema["enum"])

    if "const" in schema:
        return f"`{schema['const']}`"

    kind = schema.get("type")
    if kind == "array":
        return f"{_type_label(schema.get('items', {}), root)}[]"
    if kind == "object" or "properties" in schema:
        if isinstance(schema.get("additionalProperties"), dict):
            return f"Record<string, {_type_label(schema['additionalProperties'], root)}>"
        return "object"
    if isinstance(kind, list):
        return " | ".join(str(item) for item in kind)
    return str(kind or "any")


def _constraints(schema: dict[str, Any]) -> str:
    """把常见的数值 / 长度约束压成一行。"""

    if not isinstance(schema, dict):
        return ""
    bits: list[str] = []
    for key, label in (
        ("minimum", ">="),
        ("exclusiveMinimum", ">"),
        ("maximum", "<="),
        ("exclusiveMaximum", "<"),
        ("minLength", "长度>="),
        ("maxLength", "长度<="),
        ("minItems", "项数>="),
        ("maxItems", "项数<="),
        ("multipleOf", "倍数"),
    ):
        if key in schema:
            bits.append(f"{label} {schema[key]}")
    if schema.get("format"):
        bits.append(f"format={schema['format']}")
    if "pattern" in schema:
        bits.append(f"pattern={schema['pattern']}")
    return "；".join(bits)


def _cell(text: Any) -> str:
    """转义 Markdown 表格里的管道符与换行。"""

    value = "" if text is None else str(text)
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _fields_table(schema: Any, root: dict[str, Any], required_only: bool = False) -> list[str]:
    resolved = _resolve(schema, root)
    properties = resolved.get("properties")
    if not isinstance(properties, dict):
        return []

    required = set(resolved.get("required") or [])
    rows: list[str] = []
    for field_name, field_schema in properties.items():
        is_required = field_name in required
        if required_only and not is_required:
            continue
        field = _resolve(field_schema, root)
        default = field.get("default", None)
        has_default = "default" in field
        default_text = ""
        if has_default:
            default_text = json.dumps(default, ensure_ascii=False)
            if len(default_text) > 40:
                default_text = default_text[:37] + "..."
        note_parts = []
        if field.get("description"):
            note_parts.append(str(field["description"]))
        if _constraints(field):
            note_parts.append(_constraints(field))
        if field.get("enum"):
            note_parts.append("可选值：" + " / ".join(f"`{v}`" for v in field["enum"]))
        if _ref_name(field_schema):
            note_parts.append(f"模型：{_ref_name(field_schema)}")
        rows.append(
            "| `{name}` | {type} | {required} | {default} | {note} |".format(
                name=field_name,
                type=_cell(_type_label(field_schema, root)),
                required="是" if is_required else "否",
                default=_cell(default_text or ("-" if not has_default else "")),
                note=_cell("；".join(note_parts) or "-"),
            )
        )
    return rows


# ---------------------------------------------------------------- Markdown


def build_markdown(schema: dict[str, Any], generated_at: str) -> str:
    paths = schema.get("paths", {})
    components = schema.get("components", {}).get("schemas", {})

    lines: list[str] = []
    lines.append("# 后端接口与字段契约（前端对齐用）")
    lines.append("")
    lines.append(
        f"> 生成时间：{generated_at}　|　来源：`main.app.openapi()`　|　"
        "生成器：`scripts/export_frontend_contract.py`"
    )
    lines.append(">")
    lines.append(
        "> **本文件是机器生成的，不要手改。** 后端接口变了，重跑一次脚本即可："
    )
    lines.append(">")
    lines.append("> ```bash")
    lines.append("> ./venv/Scripts/python.exe scripts/export_openapi.py --output docs/frontend/openapi.json")
    lines.append("> ./venv/Scripts/python.exe scripts/export_frontend_contract.py")
    lines.append("> ```")
    lines.append("")
    lines.append("配套产物：")
    lines.append("")
    lines.append("| 文件 | 用途 |")
    lines.append("| --- | --- |")
    lines.append("| `docs/frontend/openapi.json` | 机器可读契约，前端可用它生成类型 |")
    lines.append("| `docs/frontend/backend_contract_types.ts` | 已生成好的 TypeScript 类型，可直接复制 |")
    lines.append("| `docs/FRONTEND_HANDOFF_AND_ALIGNMENT.md` | 交接与对齐主文档（含修改建议与缺口清单） |")
    lines.append("")

    # ---------------- 1. 通用约定
    lines.append("## 1. 通用约定")
    lines.append("")
    lines.append("| 项目 | 约定 |")
    lines.append("| --- | --- |")
    lines.append("| 本地地址 | `http://127.0.0.1:8000`（前端 dev server 需与其 CORS 匹配，见下） |")
    lines.append("| 交互文档 | `http://127.0.0.1:8000/docs`（已声明 `bearerAuth`，可粘贴令牌调试） |")
    lines.append(
        "| **鉴权** | **只认** `Authorization: Bearer <JWT>`。`X-User-Role` / `X-Operator-Id` "
        "**已彻底失效**（只记日志，不参与判定）；缺失令牌 = 401，令牌合法但权限不足 = 403，"
        "服务端未配 `RAG_JWT_SECRET` = 500（fail closed，不会静默放行） |"
    )
    lines.append(
        "| 令牌 claim | `sub`（用户 id）、`tenant_id`、`roles`（数组或字符串）、`iss`、`aud`、`exp`；"
        "**token 里自带的 `scopes` / `permissions` 一律被忽略**（防自报提权） |"
    )
    lines.append(
        "| CORS | 只允许 `http://127.0.0.1:<port>` 与 `http://localhost:<port>`，"
        "`allow_credentials=True`，方法与头全放开 |"
    )
    lines.append(
        "| 错误响应 | FastAPI 风格 `{\"detail\": ...}`；本项目的业务错误把 `detail` 做成**对象**："
        "`{\"detail\": {\"error_code\": \"chunk_index_unavailable\", \"message\": \"...\"}}` "
        "—— 前端不能只取字符串，要同时读 `error_code` |"
    )
    lines.append("| 422 | 请求体校验失败（Pydantic），`detail` 是数组 |")
    lines.append("| 分页 | 均为 `limit` + `offset` 查询参数，响应里带 `total` |")
    lines.append("")

    # ---------------- 2. 角色 → scope
    lines.append("## 2. 角色与 scope")
    lines.append("")
    lines.append(
        "后端有两套并存的授权表（都定义在 `services/auth_context.py`，是唯一事实来源）："
        "**操作维度**（`read:` / `write:` / `review:` 前缀）与**资源维度**（`document:upload` 这类）。"
        "接口往往同时要求两者。**前端不要在前端复刻这套表做权限判断**——"
        "它只用于「按钮显示/隐藏」，真正的判定始终在后端。"
    )
    lines.append("")
    lines.append("| 角色 | 主要能力 | 典型页面 |")
    lines.append("| --- | --- | --- |")
    lines.append("| `agent` | 问答生成、会话历史、订单状态读写、反馈提交、检索读 | 问答工作台 |")
    lines.append("| `supervisor` | agent 的全部 + 质量概览、审计、发布检查、索引重建 | 运行概览 / 配置与发布 |")
    lines.append("| `knowledge_ops` | 知识条目增删改审、文档上传、示例浏览、反馈读 | 知识库运营 |")
    lines.append("| `qa` | 审计、指标、发布检查、反馈、提示词读（**无写权限**） | 质量与审计 |")
    lines.append("| `admin` | 全部，含提示词写、索引重建、知识回滚 | 配置与发布 |")
    lines.append("")
    lines.append("四个刻意的收窄（不要当成 bug）：")
    lines.append("")
    lines.append(
        "1. `index:rebuild` 只给 `supervisor` / `admin` —— 索引是**全局一份**，"
        "重建影响所有租户的检索结果；"
    )
    lines.append("2. `document:publish` 与 `index:rebuild` **分开授权** —— 发布一条审核过的知识 ≠ 允许重建全量索引；")
    lines.append("3. `audit:read` 只给 `supervisor` / `qa` / `admin`，`document:delete` 不给 `knowledge_ops`；")
    lines.append("4. 详情 / 版本 / 任务查询要**同时**满足 `read:knowledge_read` 与 `document:read`。")
    lines.append("")

    # ---------------- 3. 端点总表
    lines.append("## 3. 端点总表")
    lines.append("")
    lines.append(f"共 **{sum(1 for _ in paths)}** 条路径。鉴权列中的 scope 是**必须全部满足**的意思。")
    lines.append("")
    lines.append("| 方法 | 路径 | 鉴权（scope） | 角色 | 摘要 |")
    lines.append("| --- | --- | --- | --- | --- |")

    endpoint_index: list[tuple[str, str, dict[str, Any]]] = []
    for path in sorted(paths):
        for method in HTTP_METHODS:
            operation = paths[path].get(method)
            if not operation:
                continue
            key = f"{method.upper()} {path}"
            endpoint_index.append((key, path, operation))
            auth_spec = ENDPOINT_AUTH.get(key, {})
            if auth_spec.get("public"):
                scope_text = "**无需鉴权**"
                roles_text = "-"
            elif auth_spec:
                scope_text = " + ".join(f"`{s}`" for s in auth_spec.get("scopes", [])) or "-"
                roles_text = " / ".join(f"`{r}`" for r in auth_spec.get("roles", [])) or "-"
            else:
                scope_text = "**未登记**"
                roles_text = "**未登记**"
            lines.append(
                "| `{method}` | `{path}` | {scope} | {roles} | {summary} |".format(
                    method=method.upper(),
                    path=path,
                    scope=scope_text,
                    roles=roles_text,
                    summary=_cell(operation.get("summary") or "-"),
                )
            )
    lines.append("")
    missing = [key for key, _, _ in endpoint_index if key not in ENDPOINT_AUTH]
    if missing:
        lines.append("> ⚠️ 以下端点**未在生成器的 `ENDPOINT_AUTH` 里登记鉴权**，说明文档有缺口：")
        lines.append(">")
        for key in missing:
            lines.append(f"> - `{key}`")
        lines.append("")

    # ---------------- 4. 逐端点详情
    lines.append("## 4. 逐端点详情")
    lines.append("")

    grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for key, path, operation in endpoint_index:
        group = ENDPOINT_FRONTEND_GROUP.get(key, "其他")
        grouped.setdefault(group, []).append((key, path, operation))
    order = [g for g in GROUP_ORDER if g in grouped] + [g for g in grouped if g not in GROUP_ORDER]

    for group in order:
        lines.append(f"### {group}")
        lines.append("")
        for key, path, operation in grouped[group]:
            method = key.split(" ", 1)[0]
            lines.append(f"#### `{method} {path}`")
            lines.append("")
            if operation.get("summary"):
                lines.append(f"**{operation['summary']}**")
                lines.append("")
            if operation.get("description"):
                description = str(operation["description"]).replace("\n", " ").strip()
                lines.append(f"> {description}")
                lines.append("")

            auth_spec = ENDPOINT_AUTH.get(key, {})
            if auth_spec.get("public"):
                lines.append("- 鉴权：**无需令牌**")
            elif auth_spec:
                lines.append(
                    "- 鉴权："
                    + " + ".join(f"`{s}`" for s in auth_spec.get("scopes", []))
                    + "　角色："
                    + " / ".join(f"`{r}`" for r in auth_spec.get("roles", []))
                )
            else:
                lines.append("- 鉴权：**未登记（文档缺口）**")
            if auth_spec.get("note"):
                lines.append(f"- 说明：{auth_spec['note']}")

            # 路径 / 查询参数
            params = [p for p in operation.get("parameters", []) if p.get("in") in ("path", "query")]
            if params:
                lines.append("")
                lines.append("**路径 / 查询参数**")
                lines.append("")
                lines.append("| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |")
                lines.append("| --- | --- | --- | --- | --- | --- |")
                for param in params:
                    lines.append(
                        "| `{name}` | {where} | {type} | {required} | {constraints} | {desc} |".format(
                            name=param.get("name"),
                            where="path" if param.get("in") == "path" else "query",
                            type=_cell(_type_label(param.get("schema", {}), schema)),
                            required="是" if param.get("required") else "否",
                            constraints=_cell(_constraints(_resolve(param.get("schema", {}), schema)) or "-"),
                            desc=_cell(param.get("description") or "-"),
                        )
                    )

            # 请求体
            body = operation.get("requestBody")
            if body:
                content = body.get("content", {})
                lines.append("")
                if "application/json" in content:
                    body_schema = content["application/json"].get("schema", {})
                    lines.append("**请求体**（`application/json`）")
                    lines.append("")
                    ref = _ref_name(body_schema)
                    if ref:
                        lines.append(f"模型：`{ref}`（字段见第 5 节）")
                        lines.append("")
                    rows = _fields_table(body_schema, schema)
                    if rows:
                        lines.append("| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |")
                        lines.append("| --- | --- | --- | --- | --- |")
                        lines.extend(rows)
                    elif ref:
                        lines.append("（字段见第 5 节同名模型）")
                for media_type, media in content.items():
                    if media_type == "application/json":
                        continue
                    media_schema = media.get("schema", {})
                    lines.append(f"**请求体**（`{media_type}`）")
                    lines.append("")
                    rows = _fields_table(media_schema, schema)
                    if rows:
                        lines.append("| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |")
                        lines.append("| --- | --- | --- | --- | --- |")
                        lines.extend(rows)
                    else:
                        lines.append(f"schema：`{_type_label(media_schema, schema)}`")

            # 响应
            responses = operation.get("responses", {})
            lines.append("")
            lines.append("**响应**")
            lines.append("")
            lines.append("| 状态码 | 说明 | 模型 |")
            lines.append("| --- | --- | --- |")
            for status_code in sorted(responses):
                response = responses[status_code]
                ref = _ref_name(response.get("content", {}).get("application/json", {}).get("schema"))
                lines.append(
                    "| `{code}` | {desc} | {model} |".format(
                        code=status_code,
                        desc=_cell(response.get("description") or "-"),
                        model=f"`{ref}`" if ref else "-",
                    )
                )

            success = responses.get("200") or responses.get("202")
            if success:
                success_schema = success.get("content", {}).get("application/json", {}).get("schema")
                rows = _fields_table(success_schema, schema)
                if rows:
                    lines.append("")
                    lines.append("成功响应字段：")
                    lines.append("")
                    lines.append("| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |")
                    lines.append("| --- | --- | --- | --- | --- |")
                    lines.extend(rows)
            lines.append("")

    # ---------------- 5. 数据模型
    lines.append("## 5. 数据模型全量展开")
    lines.append("")
    lines.append(f"共 {len(components)} 个模型，全部来自 `components.schemas`。")
    lines.append("")
    for name in sorted(components):
        model_schema = components[name]
        lines.append(f"### `{name}`")
        lines.append("")
        if model_schema.get("description"):
            lines.append(f"> {str(model_schema['description']).replace(chr(10), ' ').strip()}")
            lines.append("")
        if model_schema.get("enum"):
            lines.append(
                "枚举值：" + " / ".join(f"`{value}`" for value in model_schema["enum"])
            )
            lines.append("")
            continue
        rows = _fields_table(model_schema, schema)
        if rows:
            lines.append("| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |")
            lines.append("| --- | --- | --- | --- | --- |")
            lines.extend(rows)
        else:
            lines.append(f"类型：`{_type_label(model_schema, schema)}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- TypeScript


def _ts_type(schema: Any, root: dict[str, Any], indent: int = 0) -> str:
    if not isinstance(schema, dict) or not schema:
        return "unknown"

    name = _ref_name(schema)
    if name:
        return _ts_name(name)

    if "anyOf" in schema or "oneOf" in schema:
        parts = schema.get("anyOf") or schema.get("oneOf") or []
        labels: list[str] = []
        for part in parts:
            labels.append(_ts_type(part, root, indent))
        unique = list(dict.fromkeys(labels))
        return " | ".join(unique)

    if "allOf" in schema:
        return " & ".join(dict.fromkeys(_ts_type(part, root, indent) for part in schema["allOf"]))

    if "enum" in schema:
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in schema["enum"])

    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)

    kind = schema.get("type")
    if kind == "array":
        item = _ts_type(schema.get("items", {}), root, indent)
        if "|" in item:
            return f"Array<{item}>"
        return f"{item}[]"
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties")
        if isinstance(properties, dict) and properties:
            pad = "  " * (indent + 1)
            required = set(schema.get("required") or [])
            body_lines = []
            for field_name, field_schema in properties.items():
                optional = "" if field_name in required else "?"
                safe_name = field_name if field_name.isidentifier() else json.dumps(field_name)
                body_lines.append(
                    f"{pad}{safe_name}{optional}: {_ts_type(field_schema, root, indent + 1)};"
                )
            return "{\n" + "\n".join(body_lines) + "\n" + "  " * indent + "}"
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {_ts_type(additional, root, indent)}>"
        if additional is True:
            return "Record<string, unknown>"
        return "Record<string, unknown>"
    if isinstance(kind, list):
        return " | ".join(
            "null" if item == "null" else _ts_type({"type": item}, root, indent) for item in kind
        )
    if kind == "integer" or kind == "number":
        return "number"
    if kind == "string":
        return "string"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    return "unknown"


def _ts_name(name: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in name)
    safe = safe.replace("-", "_")
    if safe and safe[0].isdigit():
        safe = f"Model_{safe}"
    return safe


def build_typescript(schema: dict[str, Any], generated_at: str) -> str:
    components = schema.get("components", {}).get("schemas", {})
    lines: list[str] = []
    lines.append("// 后端接口类型（由 scripts/export_frontend_contract.py 自动生成，请勿手改）")
    lines.append("//")
    lines.append(f"// 生成时间：{generated_at}")
    lines.append("// 来源：main.app.openapi() → docs/frontend/openapi.json")
    lines.append("//")
    lines.append("// 用法：整体复制到前端 src/types/backendContract.ts，")
    lines.append("// 页面内部仍可保留自己的 ViewModel，但**接口返回值的形状以本文件为准**。")
    lines.append("//")
    lines.append("// 注意两点：")
    lines.append("// 1. 标记为可选（?）的字段是**后端允许缺失**的，前端必须处理 undefined，")
    lines.append("//    不允许 `value || 0` 这类兜底（缺数据和真实 0 是两件事）。")
    lines.append("// 2. 后端新增可选字段不会破坏本文件，但**删除或改名字段是破坏性变更**，")
    lines.append("//    升级时先 diff docs/frontend/openapi.json。")
    lines.append("")
    lines.append("/* eslint-disable */")
    lines.append("")

    for name in sorted(components):
        model_schema = components[name]
        ts_name = _ts_name(name)
        if model_schema.get("enum"):
            union = " | ".join(json.dumps(value, ensure_ascii=False) for value in model_schema["enum"])
            lines.append(f"export type {ts_name} = {union};")
            lines.append("")
            continue
        if model_schema.get("type") == "string" and not model_schema.get("properties"):
            lines.append(f"export type {ts_name} = string;")
            lines.append("")
            continue
        if model_schema.get("type") == "object" and not model_schema.get("properties"):
            additional = model_schema.get("additionalProperties")
            if isinstance(additional, dict):
                lines.append(f"export type {ts_name} = Record<string, {_ts_type(additional, schema)}>;")
            else:
                lines.append(f"export type {ts_name} = Record<string, unknown>;")
            lines.append("")
            continue
        body = _ts_type(model_schema, schema, 0)
        if body.startswith("{"):
            lines.append(f"export interface {ts_name} {body}")
        else:
            lines.append(f"export type {ts_name} = {body};")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the frontend-facing API contract from OpenAPI.")
    parser.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI, help="OpenAPI JSON 路径")
    parser.add_argument("--md", type=Path, default=DEFAULT_MD, help="Markdown 输出路径")
    parser.add_argument("--ts", type=Path, default=DEFAULT_TS, help="TypeScript 输出路径")
    return parser


def main_cli() -> int:
    args = build_parser().parse_args()
    openapi_path = args.openapi if args.openapi.is_absolute() else PROJECT_ROOT / args.openapi
    if not openapi_path.exists():
        print(f"找不到 {openapi_path}，请先运行 scripts/export_openapi.py", file=sys.stderr)
        return 2

    schema = json.loads(openapi_path.read_text(encoding="utf-8"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    md_path = args.md if args.md.is_absolute() else PROJECT_ROOT / args.md
    ts_path = args.ts if args.ts.is_absolute() else PROJECT_ROOT / args.ts
    md_path.parent.mkdir(parents=True, exist_ok=True)

    md_path.write_text(build_markdown(schema, generated_at), encoding="utf-8")
    ts_path.write_text(build_typescript(schema, generated_at), encoding="utf-8")

    print(f"已生成：{md_path.relative_to(PROJECT_ROOT).as_posix()}")
    print(f"已生成：{ts_path.relative_to(PROJECT_ROOT).as_posix()}")
    print(f"端点总数：{sum(len([m for m in v if m in HTTP_METHODS]) for v in schema.get('paths', {}).values())}")
    print(f"模型总数：{len(schema.get('components', {}).get('schemas', {}))}")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
