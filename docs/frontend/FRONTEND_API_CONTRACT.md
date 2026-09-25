# 后端接口与字段契约（前端对齐用）

> 生成时间：2026-09-23 21:59　|　来源：`main.app.openapi()`　|　生成器：`scripts/export_frontend_contract.py`
>
> **本文件是机器生成的，不要手改。** 后端接口变了，重跑一次脚本即可：
>
> ```bash
> ./venv/Scripts/python.exe scripts/export_openapi.py --output docs/frontend/openapi.json
> ./venv/Scripts/python.exe scripts/export_frontend_contract.py
> ```

配套产物：

| 文件 | 用途 |
| --- | --- |
| `docs/frontend/openapi.json` | 机器可读契约，前端可用它生成类型 |
| `docs/frontend/backend_contract_types.ts` | 已生成好的 TypeScript 类型，可直接复制 |
| `docs/FRONTEND_HANDOFF_AND_ALIGNMENT.md` | 交接与对齐主文档（含修改建议与缺口清单） |

## 1. 通用约定

| 项目 | 约定 |
| --- | --- |
| 本地地址 | `http://127.0.0.1:8000`（前端 dev server 需与其 CORS 匹配，见下） |
| 交互文档 | `http://127.0.0.1:8000/docs`（已声明 `bearerAuth`，可粘贴令牌调试） |
| **鉴权** | **只认** `Authorization: Bearer <JWT>`。`X-User-Role` / `X-Operator-Id` **已彻底失效**（只记日志，不参与判定）；缺失令牌 = 401，令牌合法但权限不足 = 403，服务端未配 `RAG_JWT_SECRET` = 500（fail closed，不会静默放行） |
| 令牌 claim | `sub`（用户 id）、`tenant_id`、`roles`（数组或字符串）、`iss`、`aud`、`exp`；**token 里自带的 `scopes` / `permissions` 一律被忽略**（防自报提权） |
| CORS | 只允许 `http://127.0.0.1:<port>` 与 `http://localhost:<port>`，`allow_credentials=True`，方法与头全放开 |
| 错误响应 | FastAPI 风格 `{"detail": ...}`；本项目的业务错误把 `detail` 做成**对象**：`{"detail": {"error_code": "chunk_index_unavailable", "message": "..."}}` —— 前端不能只取字符串，要同时读 `error_code` |
| 422 | 请求体校验失败（Pydantic），`detail` 是数组 |
| 分页 | 均为 `limit` + `offset` 查询参数，响应里带 `total` |

## 2. 角色与 scope

后端有两套并存的授权表（都定义在 `services/auth_context.py`，是唯一事实来源）：**操作维度**（`read:` / `write:` / `review:` 前缀）与**资源维度**（`document:upload` 这类）。接口往往同时要求两者。**前端不要在前端复刻这套表做权限判断**——它只用于「按钮显示/隐藏」，真正的判定始终在后端。

| 角色 | 主要能力 | 典型页面 |
| --- | --- | --- |
| `agent` | 问答生成、会话历史、订单状态读写、反馈提交、检索读 | 问答工作台 |
| `supervisor` | agent 的全部 + 质量概览、审计、发布检查、索引重建 | 运行概览 / 配置与发布 |
| `knowledge_ops` | 知识条目增删改审、文档上传、示例浏览、反馈读 | 知识库运营 |
| `qa` | 审计、指标、发布检查、反馈、提示词读（**无写权限**） | 质量与审计 |
| `admin` | 全部，含提示词写、索引重建、知识回滚 | 配置与发布 |

四个刻意的收窄（不要当成 bug）：

1. `index:rebuild` 只给 `supervisor` / `admin` —— 索引是**全局一份**，重建影响所有租户的检索结果；
2. `document:publish` 与 `index:rebuild` **分开授权** —— 发布一条审核过的知识 ≠ 允许重建全量索引；
3. `audit:read` 只给 `supervisor` / `qa` / `admin`，`document:delete` 不给 `knowledge_ops`；
4. 详情 / 版本 / 任务查询要**同时**满足 `read:knowledge_read` 与 `document:read`。

## 3. 端点总表

共 **39** 条路径。鉴权列中的 scope 是**必须全部满足**的意思。

| 方法 | 路径 | 鉴权（scope） | 角色 | 摘要 |
| --- | --- | --- | --- | --- |
| `GET` | `/` | **无需鉴权** | - | Read Root |
| `GET` | `/audit/logs` | `read:audit_read` | `supervisor` / `qa` / `admin` | Audit Logs |
| `GET` | `/chat/history` | `read:chat_history` | `agent` / `supervisor` / `qa` / `admin` | Get Chat History |
| `POST` | `/chat/prompt` | `read:chat_generate` | `agent` / `supervisor` / `qa` / `admin` | Generate Answer |
| `POST` | `/chat/review-action` | `review:<action>` | `agent` / `supervisor` / `qa` / `knowledge_ops` / `admin` | Review Chat Action |
| `GET` | `/documents/{document_id}` | `read:knowledge_read` + `document:read` | `agent` / `supervisor` / `knowledge_ops` / `qa` / `admin` | 查询文档详情 |
| `POST` | `/documents/{document_id}/reprocess` | `write:knowledge_rollback` | `supervisor` / `admin` | 重新处理文档（新建任务） |
| `GET` | `/documents/{document_id}/versions` | `read:knowledge_read` + `document:read` | `agent` / `supervisor` / `knowledge_ops` / `qa` / `admin` | 查询文档版本列表 |
| `GET` | `/examples/by-category` | `read:example_read` | `supervisor` / `knowledge_ops` / `qa` / `admin` | Examples |
| `GET` | `/examples/categories` | `read:example_read` | `supervisor` / `knowledge_ops` / `qa` / `admin` | Categories |
| `POST` | `/examples/search` | `read:example_read` | `supervisor` / `knowledge_ops` / `qa` / `admin` | Search Examples Api |
| `POST` | `/feedback` | `write:feedback_create` | `agent` / `supervisor` / `qa` / `knowledge_ops` / `admin` | Create Feedback |
| `POST` | `/feedback/export-eval-case` | `write:feedback_export_eval_case` | `agent` / `supervisor` / `qa` / `knowledge_ops` / `admin` | Export Eval Case |
| `GET` | `/feedback/recent` | `read:feedback_read` | `supervisor` / `qa` / `knowledge_ops` / `admin` | Recent Feedback |
| `GET` | `/health` | **无需鉴权** | - | Health Check |
| `GET` | `/ingestion-jobs/{job_id}` | `read:knowledge_read` + `document:read` | `agent` / `supervisor` / `knowledge_ops` / `qa` / `admin` | 查询接入任务 |
| `POST` | `/ingestion/indexes/rebuild` | `index:rebuild` | `supervisor` / `admin` | 重建 chunk 索引（需要 index:rebuild） |
| `POST` | `/knowledge-bases/{knowledge_base_id}/documents` | `write:knowledge_create` + `document:upload` | `supervisor` / `knowledge_ops` / `admin` | 上传文档并创建异步解析任务 |
| `GET` | `/knowledge/export-approved` | `read:knowledge_read` | `supervisor` / `knowledge_ops` / `qa` / `admin` | Export Approved |
| `GET` | `/knowledge/items` | `read:knowledge_read` | `supervisor` / `knowledge_ops` / `qa` / `admin` | Knowledge Items |
| `POST` | `/knowledge/items` | `write:knowledge_create` | `supervisor` / `knowledge_ops` / `admin` | Create Item |
| `PUT` | `/knowledge/items/{item_id}` | `write:knowledge_update` | `supervisor` / `knowledge_ops` / `admin` | Update Item |
| `POST` | `/knowledge/items/{item_id}/archive` | `write:knowledge_archive` | `supervisor` / `knowledge_ops` / `admin` | Archive Item |
| `POST` | `/knowledge/items/{item_id}/review` | `write:knowledge_review` | `supervisor` / `knowledge_ops` / `admin` | Review Item |
| `POST` | `/knowledge/publish-approved` | `write:knowledge_publish` + `document:publish` | `supervisor` / `knowledge_ops` / `admin` | Publish Approved |
| `GET` | `/knowledge/publish-history` | `read:knowledge_read` | `supervisor` / `knowledge_ops` / `qa` / `admin` | Publish History |
| `POST` | `/knowledge/rollback-latest` | `write:knowledge_rollback` | `supervisor` / `admin` | Rollback Latest |
| `GET` | `/model/info` | `read:model_info_read` | `supervisor` / `qa` / `admin` | Model Info |
| `GET` | `/ops/metrics` | `read:ops_metrics_read` | `supervisor` / `qa` / `admin` | Ops Metrics |
| `GET` | `/orders/{order_id}/state` | `read:order_state_read` | `agent` / `supervisor` / `admin` | Read Order State |
| `PUT` | `/orders/{order_id}/state` | `write:order_state_upsert` | `agent` / `supervisor` / `admin` | Save Order State |
| `GET` | `/prompt/active` | `read:prompt_read` | `supervisor` / `qa` / `admin` | Active Prompt |
| `POST` | `/prompt/rollback-latest` | `write:prompt_write` | `admin` | Rollback Latest |
| `GET` | `/prompt/versions` | `read:prompt_read` | `supervisor` / `qa` / `admin` | Prompt Versions |
| `POST` | `/prompt/versions` | `write:prompt_write` | `admin` | Create Version |
| `POST` | `/prompt/versions/{version_id}/activate` | `write:prompt_write` | `admin` | Activate Version |
| `POST` | `/prompt/versions/{version_id}/status` | `write:prompt_write` | `admin` | Update Status |
| `GET` | `/release/checklist` | `read:release_read` | `supervisor` / `qa` / `admin` | Release Checklist |
| `GET` | `/retrieval/config` | `read:retrieval_read` | `agent` / `supervisor` / `qa` / `admin` | Get Retrieval Config |
| `POST` | `/retrieval/prompt-preview` | `read:retrieval_read` | `agent` / `supervisor` / `qa` / `admin` | 【演示 / 兼容】拼装 RAG prompt（基于 A 轨种子 FAQ，无权限过滤） |
| `POST` | `/retrieval/search` | `read:retrieval_read` | `agent` / `supervisor` / `qa` / `admin` | 检索文档 chunk（正式路径，按租户与 document ACL 过滤） |
| `POST` | `/retrieval/search-demo` | `read:retrieval_read` | `agent` / `supervisor` / `qa` / `admin` | 【演示 / 兼容】检索种子 FAQ（A 轨，无权限过滤） |

## 4. 逐端点详情

### 问答工作台

#### `GET /chat/history`

**Get Chat History**

- 鉴权：`read:chat_history`　角色：`agent` / `supervisor` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `user_id` | query | string | 否 | - | - |
| `order_id` | query | string \| null | 否 | - | - |
| `session_id` | query | string \| null | 否 | - | - |
| `limit` | query | integer | 否 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ChatHistoryResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `latest_response` | object | 否 | - | - |
| `messages` | ChatHistoryMessage[] | 否 | - | - |
| `order_id` | string \| null | 否 | - | - |
| `session_id` | string | 否 | "" | - |
| `user_id` | string | 否 | "demo_user" | - |

#### `POST /chat/prompt`

**Generate Answer**

- 鉴权：`read:chat_generate`　角色：`agent` / `supervisor` / `qa` / `admin`

**请求体**（`application/json`）

模型：`ChatRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `channel` | string | 否 | "test" | - |
| `message` | string | 是 | - | - |
| `order_id` | string \| null | 否 | - | - |
| `session_id` | string \| null | 否 | - | - |
| `user_id` | string | 否 | "demo_user" | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ChatResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer_basis` | string | 否 | "" | - |
| `citations` | object[] | 否 | - | - |
| `confidence_level` | string | 否 | "medium" | - |
| `confidence_score` | number | 是 | - | - |
| `context_used` | object | 否 | - | - |
| `conversation_status` | string | 否 | "pending_agent_review" | - |
| `decision_trace` | object | 否 | - | - |
| `evaluation_metrics` | object | 否 | - | - |
| `evidence_citations` | object[] | 否 | - | - |
| `expected_evidence_keywords` | string[] | 否 | - | - |
| `expected_intent` | string | 否 | "" | - |
| `final_prompt` | string | 是 | - | - |
| `forbidden_keyword_hits` | string[] | 否 | - | - |
| `forbidden_keywords` | string[] | 否 | - | - |
| `full_trace` | object[] | 否 | - | - |
| `handoff_ticket` | object \| null | 否 | - | - |
| `human_review_reason` | string | 否 | "v1 默认客服确认后发送" | - |
| `intent_analysis` | object | 否 | - | - |
| `issue_type` | string | 否 | "" | - |
| `manual_judgment` | object | 否 | - | - |
| `matched_evidence_keywords` | string[] | 否 | - | - |
| `memory_snapshot` | object | 否 | - | - |
| `missing_evidence_keywords` | string[] | 否 | - | - |
| `mixed_supporting_intent` | boolean | 否 | false | - |
| `need_human_review` | boolean | 否 | true | - |
| `needs_manual_review` | boolean | 否 | false | - |
| `order_id` | string \| null | 否 | - | - |
| `prompt_context_items` | PromptContextItemResponse[] | 否 | - | - |
| `prompt_version` | string | 否 | "" | - |
| `reply` | string | 是 | - | - |
| `request_id` | string | 否 | "" | - |
| `retrieved_documents` | string[] | 是 | - | - |
| `retrieved_items` | object[] | 否 | - | - |
| `risk_level` | string | 否 | "low" | - |
| `risky_promises` | string[] | 否 | - | - |
| `safety_status` | object | 否 | - | - |
| `session_id` | string | 否 | "" | - |
| `suggested_layer` | string | 否 | "" | - |
| `token_usage` | object | 否 | - | - |
| `tool_results` | object[] | 否 | - | - |
| `trace` | ChatTrace | 是 | - | 模型：ChatTrace |
| `used_primary_evidence` | boolean | 否 | false | - |
| `user_id` | string | 否 | "demo_user" | - |

#### `POST /chat/review-action`

**Review Chat Action**

- 鉴权：`review:<action>`　角色：`agent` / `supervisor` / `qa` / `knowledge_ops` / `admin`
- 说明：action 是请求体字段，按值判定：accepted / edited_and_sent / human_handoff 只给 agent/supervisor/admin；marked_bad_case 额外允许 qa、knowledge_ops。

**请求体**（`application/json`）

模型：`ChatReviewActionRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | `accepted` \\| `edited_and_sent` \\| `human_handoff` \\| `marked_bad_case` | 是 | - | 可选值：`accepted` / `edited_and_sent` / `human_handoff` / `marked_bad_case` |
| `final_reply` | string | 否 | "" | - |
| `operator_id` | string | 否 | "demo_agent" | - |
| `operator_role` | string | 否 | "agent" | - |
| `reason` | string | 否 | "" | - |
| `request_id` | string | 是 | - | 长度>= 1 |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ChatReviewActionResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | `accepted` \\| `edited_and_sent` \\| `human_handoff` \\| `marked_bad_case` | 是 | - | 可选值：`accepted` / `edited_and_sent` / `human_handoff` / `marked_bad_case` |
| `audit_id` | integer \| null | 否 | - | - |
| `created_at` | string | 是 | - | - |
| `final_reply` | string | 否 | "" | - |
| `handoff_ticket` | object \| null | 否 | - | - |
| `order_id` | string \| null | 否 | - | - |
| `reason` | string | 否 | "" | - |
| `request_id` | string | 是 | - | - |
| `saved` | boolean | 否 | true | - |
| `session_id` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `user_id` | string | 是 | - | - |

### 检索（正式路径 · chunk 级）

#### `POST /retrieval/search`

**检索文档 chunk（正式路径，按租户与 document ACL 过滤）**

> 正式的检索接口：**先授权，再检索**。  三个必须按顺序理解的点：  1. ``access`` 过滤器在**服务端**构造（``build_chunk_access_filter``），    输入只有已校验的 ``AuthContext`` 与库里的 ACL / 版本状态； 2. 过滤发生在**候选暴露之前**（FAISS ``IDSelectorBatch`` 预过滤），    不是"先全局 top-k 再筛" —— 后者在长尾租户上会静默返回 0 条； 3. 零命中是**正常结果**（无权限 = 零命中，不返回 403），    因为返回 403 会泄漏"这条文档存在但你没权限"。

- 鉴权：`read:retrieval_read`　角色：`agent` / `supervisor` / `qa` / `admin`

**请求体**（`application/json`）

模型：`ChunkRetrievalRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |
| `min_score` | number \| null | 否 | - | - |
| `query` | string | 是 | - | 长度>= 1 |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ChunkRetrievalResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `index` | ChunkIndexInfo | 是 | - | 当前生效索引的自我描述（便于排障"为什么零命中"）。；模型：ChunkIndexInfo |
| `query` | string | 是 | - | - |
| `results` | ChunkRetrievalItem[] | 是 | - | - |
| `retrieval_path` | `chunk-index` \\| `seed-faq-demo` | 否 | "chunk-index" | 可选值：`chunk-index` / `seed-faq-demo` |

### 检索（演示路径 · 种子 FAQ）

#### `POST /retrieval/prompt-preview`

**【演示 / 兼容】拼装 RAG prompt（基于 A 轨种子 FAQ，无权限过滤）**

> 与 `POST /retrieval/search-demo` 同一条链路（种子 FAQ，无权限过滤），只额外返回最终拼好的 prompt，供调试台观察证据如何进上下文。 正式检索路径是 `POST /retrieval/search`。

- 鉴权：`read:retrieval_read`　角色：`agent` / `supervisor` / `qa` / `admin`

**请求体**（`application/json`）

模型：`RetrievalSearchRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |
| `min_score` | number | 否 | 0.4 | >= 0.0；<= 1.0 |
| `mode` | `vector` \\| `hybrid` | 否 | "hybrid" | 可选值：`vector` / `hybrid` |
| `query` | string | 是 | - | 长度>= 1 |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptPreviewResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `mode` | `vector` \\| `hybrid` | 是 | - | 可选值：`vector` / `hybrid` |
| `prompt` | string | 是 | - | - |
| `prompt_context_items` | RetrievalResultItem[] | 是 | - | - |
| `query` | string | 是 | - | - |
| `results` | RetrievalResultItem[] | 是 | - | - |

#### `POST /retrieval/search-demo`

**【演示 / 兼容】检索种子 FAQ（A 轨，无权限过滤）**

> **这不是正式检索路径。** 它读的是手工整理的 781 条种子 FAQ（`data/takeout_customer_service_seed.jsonl`），语料本身是单租户的，**没有 tenant / ACL 过滤**（`retrieve_*` 系列签名里就没有这两个参数）。  正式路径是 `POST /retrieval/search`（chunk 级 + 服务端权限过滤）。 保留本端点只是为了演示检索分数构成与兼容旧调试台。

- 鉴权：`read:retrieval_read`　角色：`agent` / `supervisor` / `qa` / `admin`

**请求体**（`application/json`）

模型：`RetrievalSearchRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |
| `min_score` | number | 否 | 0.4 | >= 0.0；<= 1.0 |
| `mode` | `vector` \\| `hybrid` | 否 | "hybrid" | 可选值：`vector` / `hybrid` |
| `query` | string | 是 | - | 长度>= 1 |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `RetrievalSearchResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `mode` | `vector` \\| `hybrid` | 是 | - | 可选值：`vector` / `hybrid` |
| `query` | string | 是 | - | - |
| `results` | RetrievalResultItem[] | 是 | - | - |
| `retrieval_path` | `chunk-index` \\| `seed-faq-demo` | 否 | "seed-faq-demo" | 可选值：`chunk-index` / `seed-faq-demo` |

### 检索

#### `GET /retrieval/config`

**Get Retrieval Config**

- 鉴权：`read:retrieval_read`　角色：`agent` / `supervisor` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `RagConfigResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `embedding_model_name` | string | 是 | - | - |
| `faiss_docs_path` | string | 是 | - | - |
| `faiss_index_path` | string | 是 | - | - |
| `faiss_store_dir` | string | 是 | - | - |
| `min_vector_score` | number | 是 | - | - |
| `model_rerank_weight` | number | 是 | - | - |
| `reply_rules_enabled` | boolean | 是 | - | - |
| `reranker_model_name` | string | 是 | - | - |

### 知识库 · 接入

#### `GET /documents/{document_id}`

**查询文档详情**

- 鉴权：`read:knowledge_read` + `document:read`　角色：`agent` / `supervisor` / `knowledge_ops` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `document_id` | path | string | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `DocumentDetail` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `created_at` | string | 是 | - | format=date-time |
| `created_by` | string \| null | 否 | - | - |
| `id` | string | 是 | - | - |
| `knowledge_base_id` | string | 是 | - | - |
| `latest_version` | DocumentVersionSummary \| null | 否 | - | - |
| `source_type` | string | 是 | - | - |
| `source_uri` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `tenant_id` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | format=date-time |

#### `POST /documents/{document_id}/reprocess`

**重新处理文档（新建任务）**

> 重跑消费索引资源，因此用比上传更严的授权与独立的审计动作名。  **新建 job，不复用旧 job 改状态**：``retry_count`` 的语义是「同一个 job 的失败重试 次数」，与「用户又点了一次重处理」不是一回事，混用会让重试次数失去意义。 流水线侧的「从失败阶段幂等重试」属于 3.3，这里只把新 job 的起点设为 ``received``。

- 鉴权：`write:knowledge_rollback`　角色：`supervisor` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `document_id` | path | string | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `202` | Successful Response | `DocumentReprocessResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `document_id` | string | 是 | - | - |
| `job_id` | string | 是 | - | - |
| `job_stage` | string | 是 | - | - |
| `job_status` | string | 是 | - | - |
| `status` | `accepted` | 否 | "accepted" | - |

#### `GET /documents/{document_id}/versions`

**查询文档版本列表**

- 鉴权：`read:knowledge_read` + `document:read`　角色：`agent` / `supervisor` / `knowledge_ops` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `document_id` | path | string | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `DocumentVersionListResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `document_id` | string | 是 | - | - |
| `items` | DocumentVersionSummary[] | 是 | - | - |
| `total` | integer | 是 | - | >= 0.0 |

#### `GET /ingestion-jobs/{job_id}`

**查询接入任务**

- 鉴权：`read:knowledge_read` + `document:read`　角色：`agent` / `supervisor` / `knowledge_ops` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `job_id` | path | string | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `IngestionJobDetail` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `created_at` | string | 是 | - | format=date-time |
| `document_id` | string | 是 | - | - |
| `document_version_id` | string \| null | 否 | - | - |
| `error_code` | string \| null | 否 | - | - |
| `error_message` | string \| null | 否 | - | - |
| `id` | string | 是 | - | - |
| `retry_count` | integer | 是 | - | >= 0.0 |
| `stage` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `tenant_id` | string | 是 | - | - |
| `updated_at` | string | 是 | - | format=date-time |
| `warnings` | ParseWarningItem[] | 否 | - | - |

#### `POST /ingestion/indexes/rebuild`

**重建 chunk 索引（需要 index:rebuild）**

> 整份重建全局 chunk 索引并原子切换（构建到临时目录 → 校验 → 改指针）。  与**发布知识**分开授权（计划 4.2）：本端点要求资源级权限 `index:rebuild`（supervisor / admin），因为一次重建会影响**所有租户**的检索结果； 发布一条审核过的知识用 `POST /knowledge/publish-approved`（`document:publish`，knowledge_ops 也有）。

- 鉴权：`index:rebuild`　角色：`supervisor` / `admin`
- 说明：只有资源维度授权，没有操作维度授权 —— 索引是全局一份，影响所有租户。

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `IndexRebuildResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `chunk_count` | integer | 是 | - | >= 0.0 |
| `embedding_dimension` | integer | 否 | 0 | >= 0.0 |
| `embedding_model` | string | 否 | "" | - |
| `index_name` | string | 是 | - | - |
| `index_version` | integer | 是 | - | >= 0.0 |
| `manifest_uri` | string | 否 | "" | - |
| `skip_reason` | string | 否 | "" | - |
| `skipped` | boolean | 否 | false | - |
| `switched` | boolean | 否 | false | - |
| `tenant_count` | integer | 否 | 0 | >= 0.0 |

#### `POST /knowledge-bases/{knowledge_base_id}/documents`

**上传文档并创建异步解析任务**

> 上传只做「鉴权 → 类型/大小检查 → 对象存储 → 建任务」，202 即刻返回。  解析、切分、内容 hash 重复判定都在 worker 与流水线（3.3）里异步完成。

- 鉴权：`write:knowledge_create` + `document:upload`　角色：`supervisor` / `knowledge_ops` / `admin`
- 说明：额外要求：调用者必须是该知识库的写成员（knowledge_base_members）。

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `knowledge_base_id` | path | string | 是 | - | - |

**请求体**（`multipart/form-data`）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `file` | string | 是 | - | 待接入的文档：PDF / DOCX / HTML / MD / TXT |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `202` | Successful Response | `DocumentUploadResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `document_id` | string | 是 | - | - |
| `filename` | string | 是 | - | - |
| `job_id` | string | 是 | - | - |
| `job_stage` | string | 是 | - | 任务初始阶段，上传期为 received |
| `job_status` | string | 是 | - | 任务初始状态，上传期为 pending |
| `knowledge_base_id` | string | 是 | - | - |
| `reused_document` | boolean | 是 | - | - |
| `size_bytes` | integer | 是 | - | >= 0.0 |
| `source_type` | string | 是 | - | - |
| `source_uri` | string | 是 | - | - |
| `status` | `accepted` | 否 | "accepted" | - |

### 知识库 · 运营

#### `GET /knowledge/export-approved`

**Export Approved**

- 鉴权：`read:knowledge_read`　角色：`supervisor` / `knowledge_ops` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgeExportResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `jsonl` | string | 是 | - | - |

#### `GET /knowledge/items`

**Knowledge Items**

- 鉴权：`read:knowledge_read`　角色：`supervisor` / `knowledge_ops` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `limit` | query | integer | 否 | >= 1；<= 100 | - |
| `offset` | query | integer | 否 | >= 0 | - |
| `category` | query | string | 否 | - | - |
| `intent` | query | string | 否 | - | - |
| `status` | query | string | 否 | - | - |
| `keyword` | query | string | 否 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgeListResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `items` | KnowledgeItem[] | 是 | - | - |
| `limit` | integer | 是 | - | - |
| `offset` | integer | 是 | - | - |
| `total` | integer | 是 | - | - |

#### `POST /knowledge/items`

**Create Item**

- 鉴权：`write:knowledge_create`　角色：`supervisor` / `knowledge_ops` / `admin`

**请求体**（`application/json`）

模型：`KnowledgeItemPayload`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | 长度>= 1 |
| `category` | string | 是 | - | 长度>= 1 |
| `effective_at` | string | 否 | "" | - |
| `expired_at` | string | 否 | "" | - |
| `intent` | string | 是 | - | 长度>= 1 |
| `owner` | string | 否 | "knowledge_ops" | - |
| `question` | string | 是 | - | 长度>= 1 |
| `source` | string | 否 | "knowledge_ops" | - |
| `title` | string | 否 | "" | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgeItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `base_id` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `expired_at` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `intent` | string | 是 | - | - |
| `owner` | string | 是 | - | - |
| `question` | string | 是 | - | - |
| `review_note` | string | 是 | - | - |
| `reviewed_at` | string | 是 | - | - |
| `source` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | - |
| `version` | integer | 是 | - | - |

#### `PUT /knowledge/items/{item_id}`

**Update Item**

- 鉴权：`write:knowledge_update`　角色：`supervisor` / `knowledge_ops` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `item_id` | path | integer | 是 | - | - |

**请求体**（`application/json`）

模型：`KnowledgeItemPayload`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | 长度>= 1 |
| `category` | string | 是 | - | 长度>= 1 |
| `effective_at` | string | 否 | "" | - |
| `expired_at` | string | 否 | "" | - |
| `intent` | string | 是 | - | 长度>= 1 |
| `owner` | string | 否 | "knowledge_ops" | - |
| `question` | string | 是 | - | 长度>= 1 |
| `source` | string | 否 | "knowledge_ops" | - |
| `title` | string | 否 | "" | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgeItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `base_id` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `expired_at` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `intent` | string | 是 | - | - |
| `owner` | string | 是 | - | - |
| `question` | string | 是 | - | - |
| `review_note` | string | 是 | - | - |
| `reviewed_at` | string | 是 | - | - |
| `source` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | - |
| `version` | integer | 是 | - | - |

#### `POST /knowledge/items/{item_id}/archive`

**Archive Item**

- 鉴权：`write:knowledge_archive`　角色：`supervisor` / `knowledge_ops` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `item_id` | path | integer | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgeItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `base_id` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `expired_at` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `intent` | string | 是 | - | - |
| `owner` | string | 是 | - | - |
| `question` | string | 是 | - | - |
| `review_note` | string | 是 | - | - |
| `reviewed_at` | string | 是 | - | - |
| `source` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | - |
| `version` | integer | 是 | - | - |

#### `POST /knowledge/items/{item_id}/review`

**Review Item**

- 鉴权：`write:knowledge_review`　角色：`supervisor` / `knowledge_ops` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `item_id` | path | integer | 是 | - | - |

**请求体**（`application/json`）

模型：`KnowledgeReviewRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `review_note` | string | 否 | "" | - |
| `status` | string | 是 | - | pattern=^(pending_review\|approved\|rejected)$ |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgeItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `base_id` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `expired_at` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `intent` | string | 是 | - | - |
| `owner` | string | 是 | - | - |
| `question` | string | 是 | - | - |
| `review_note` | string | 是 | - | - |
| `reviewed_at` | string | 是 | - | - |
| `source` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | - |
| `version` | integer | 是 | - | - |

#### `POST /knowledge/publish-approved`

**Publish Approved**

- 鉴权：`write:knowledge_publish` + `document:publish`　角色：`supervisor` / `knowledge_ops` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgePublishResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | - | - |
| `backup_path` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `faiss_index_path` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `item_ids` | integer[] | 是 | - | - |
| `knowledge_path` | string | 是 | - | - |
| `merged_count` | integer | 是 | - | - |
| `note` | string | 是 | - | - |
| `publish_id` | string | 是 | - | - |
| `status` | string | 是 | - | - |

#### `GET /knowledge/publish-history`

**Publish History**

- 鉴权：`read:knowledge_read`　角色：`supervisor` / `knowledge_ops` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `limit` | query | integer | 否 | >= 1；<= 100 | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgePublishHistoryResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | KnowledgePublishHistoryItem[] | 是 | - | - |

#### `POST /knowledge/rollback-latest`

**Rollback Latest**

- 鉴权：`write:knowledge_rollback`　角色：`supervisor` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `KnowledgePublishResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | - | - |
| `backup_path` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `faiss_index_path` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `item_ids` | integer[] | 是 | - | - |
| `knowledge_path` | string | 是 | - | - |
| `merged_count` | integer | 是 | - | - |
| `note` | string | 是 | - | - |
| `publish_id` | string | 是 | - | - |
| `status` | string | 是 | - | - |

### 知识库浏览（种子 FAQ）

#### `GET /examples/by-category`

**Examples**

- 鉴权：`read:example_read`　角色：`supervisor` / `knowledge_ops` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `category` | query | string | 是 | - | - |
| `limit` | query | integer | 否 | >= 1；<= 20 | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ExamplesByCategoryResponse` |
| `404` | Category not found | - |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `category` | string | 是 | - | - |
| `count` | integer | 是 | - | - |
| `examples` | ExampleItem[] | 是 | - | - |

#### `GET /examples/categories`

**Categories**

- 鉴权：`read:example_read`　角色：`supervisor` / `knowledge_ops` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `CategoriesResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `categories` | string[] | 是 | - | - |
| `count` | integer | 是 | - | - |

#### `POST /examples/search`

**Search Examples Api**

- 鉴权：`read:example_read`　角色：`supervisor` / `knowledge_ops` / `qa` / `admin`

**请求体**（`application/json`）

模型：`SearchExamplesRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `keyword` | string | 是 | - | 长度>= 1 |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `SearchExamplesResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `keyword` | string | 是 | - | - |
| `results` | SearchResultItem[] | 是 | - | - |

### 反馈队列

#### `POST /feedback`

**Create Feedback**

- 鉴权：`write:feedback_create`　角色：`agent` / `supervisor` / `qa` / `knowledge_ops` / `admin`

**请求体**（`application/json`）

模型：`FeedbackRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `expected_reply` | string | 否 | "" | - |
| `helpful` | boolean | 是 | - | - |
| `query` | string | 是 | - | 长度>= 1 |
| `reason` | string | 否 | "" | - |
| `reply` | string | 是 | - | 长度>= 1 |
| `request_id` | string | 是 | - | 长度>= 1 |
| `trace` | object | 否 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `FeedbackResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `feedback_id` | integer | 是 | - | - |
| `saved` | boolean | 是 | - | - |

#### `POST /feedback/export-eval-case`

**Export Eval Case**

- 鉴权：`write:feedback_export_eval_case`　角色：`agent` / `supervisor` / `qa` / `knowledge_ops` / `admin`

**请求体**（`application/json`）

模型：`ExportEvalCaseRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `feedback_id` | integer | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ExportEvalCaseResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `eval_case` | object | 是 | - | - |
| `feedback_id` | integer | 是 | - | - |

#### `GET /feedback/recent`

**Recent Feedback**

- 鉴权：`read:feedback_read`　角色：`supervisor` / `qa` / `knowledge_ops` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `limit` | query | integer | 否 | >= 1；<= 100 | - |
| `helpful` | query | boolean \| null | 否 | - | - |
| `intent` | query | string | 否 | - | - |
| `failure_stage` | query | string | 否 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `RecentFeedbackResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | FeedbackItem[] | 是 | - | - |

### 配置与发布

#### `GET /prompt/active`

**Active Prompt**

- 鉴权：`read:prompt_read`　角色：`supervisor` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptVersionItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `activated_at` | string | 是 | - | - |
| `author` | string | 是 | - | - |
| `change_reason` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `developer_prompt` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `evaluation_result` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `rolled_back_from` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `system_prompt` | string | 是 | - | - |
| `version` | string | 是 | - | - |

#### `POST /prompt/rollback-latest`

**Rollback Latest**

- 鉴权：`write:prompt_write`　角色：`admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptVersionItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `activated_at` | string | 是 | - | - |
| `author` | string | 是 | - | - |
| `change_reason` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `developer_prompt` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `evaluation_result` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `rolled_back_from` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `system_prompt` | string | 是 | - | - |
| `version` | string | 是 | - | - |

#### `GET /prompt/versions`

**Prompt Versions**

- 鉴权：`read:prompt_read`　角色：`supervisor` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `limit` | query | integer | 否 | >= 1；<= 100 | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptVersionListResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | PromptVersionItem[] | 是 | - | - |

#### `POST /prompt/versions`

**Create Version**

- 鉴权：`write:prompt_write`　角色：`admin`

**请求体**（`application/json`）

模型：`PromptVersionPayload`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `change_reason` | string | 否 | "" | - |
| `developer_prompt` | string | 否 | "" | - |
| `effective_at` | string | 否 | "" | - |
| `evaluation_result` | string | 否 | "" | - |
| `system_prompt` | string | 是 | - | 长度>= 1 |
| `version` | string | 否 | "" | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptVersionItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `activated_at` | string | 是 | - | - |
| `author` | string | 是 | - | - |
| `change_reason` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `developer_prompt` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `evaluation_result` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `rolled_back_from` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `system_prompt` | string | 是 | - | - |
| `version` | string | 是 | - | - |

#### `POST /prompt/versions/{version_id}/activate`

**Activate Version**

- 鉴权：`write:prompt_write`　角色：`admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `version_id` | path | integer | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptVersionItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `activated_at` | string | 是 | - | - |
| `author` | string | 是 | - | - |
| `change_reason` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `developer_prompt` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `evaluation_result` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `rolled_back_from` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `system_prompt` | string | 是 | - | - |
| `version` | string | 是 | - | - |

#### `POST /prompt/versions/{version_id}/status`

**Update Status**

- 鉴权：`write:prompt_write`　角色：`admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `version_id` | path | integer | 是 | - | - |

**请求体**（`application/json`）

模型：`PromptVersionStatusRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `evaluation_result` | string | 否 | "" | - |
| `status` | string | 是 | - | pattern=^(evaluation\|approved\|canary)$ |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `PromptVersionItem` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `activated_at` | string | 是 | - | - |
| `author` | string | 是 | - | - |
| `change_reason` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `developer_prompt` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `evaluation_result` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `rolled_back_from` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `system_prompt` | string | 是 | - | - |
| `version` | string | 是 | - | - |

#### `GET /release/checklist`

**Release Checklist**

- 鉴权：`read:release_read`　角色：`supervisor` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ReleaseChecklistResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `failed_count` | integer | 是 | - | - |
| `items` | ReleaseChecklistItem[] | 是 | - | - |
| `ready` | boolean | 是 | - | - |
| `warning_count` | integer | 是 | - | - |

### 安全与治理

#### `GET /audit/logs`

**Audit Logs**

- 鉴权：`read:audit_read`　角色：`supervisor` / `qa` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `limit` | query | integer | 否 | >= 1；<= 200 | - |
| `action_type` | query | string | 否 | - | - |
| `object_type` | query | string | 否 | - | - |
| `operator_role` | query | string | 否 | - | - |
| `request_id` | query | string | 否 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `AuditLogListResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | AuditLogItem[] | 是 | - | - |

### 运行概览

#### `GET /model/info`

**Model Info**

- 鉴权：`read:model_info_read`　角色：`supervisor` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `ModelInfoResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `adapter_enabled` | boolean | 是 | - | - |
| `adapter_name` | string \| null | 是 | - | - |
| `base_model` | string | 是 | - | - |
| `generation_provider` | string | 否 | "" | - |
| `online_api_base_url_configured` | boolean | 否 | false | - |
| `online_api_key_env` | string | 否 | "" | - |
| `online_model_name` | string | 否 | "" | - |

#### `GET /ops/metrics`

**Ops Metrics**

- 鉴权：`read:ops_metrics_read`　角色：`supervisor` / `qa` / `admin`

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `OpsMetricsResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `accepted_count` | integer | 是 | - | - |
| `accepted_rate` | number | 是 | - | - |
| `average_latency_ms` | number | 是 | - | - |
| `average_tokens_per_request` | number | 是 | - | - |
| `bad_case_count` | integer | 是 | - | - |
| `bad_case_rate` | number | 是 | - | - |
| `edited_sent_count` | integer | 是 | - | - |
| `edited_sent_rate` | number | 是 | - | - |
| `empty_retrieval_count` | integer | 是 | - | - |
| `failure_count` | integer | 是 | - | - |
| `fallback_count` | integer | 是 | - | - |
| `human_handoff_count` | integer | 是 | - | - |
| `human_handoff_rate` | number | 是 | - | - |
| `p95_latency_ms` | number | 是 | - | - |
| `reply_rules_hit_count` | integer | 是 | - | - |
| `request_count` | integer | 是 | - | - |
| `reviewed_count` | integer | 是 | - | - |
| `source` | string | 否 | "" | - |
| `token_record_rate` | number | 是 | - | - |
| `token_recorded_count` | integer | 是 | - | - |
| `total_completion_tokens` | integer | 是 | - | - |
| `total_prompt_tokens` | integer | 是 | - | - |
| `total_tokens` | integer | 是 | - | - |

### 订单状态

#### `GET /orders/{order_id}/state`

**Read Order State**

- 鉴权：`read:order_state_read`　角色：`agent` / `supervisor` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `order_id` | path | string | 是 | - | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `OrderStateResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `delivery_status` | string | 否 | "" | - |
| `items` | object[] | 否 | - | - |
| `order_id` | string | 是 | - | - |
| `refund_status` | string | 否 | "none" | - |
| `status` | string | 是 | - | - |
| `status_label` | string | 否 | "" | - |
| `store_name` | string | 否 | "" | - |
| `summary` | string | 否 | "" | - |
| `total` | number | 否 | 0.0 | - |
| `updated_at` | string | 否 | "" | - |
| `user_id` | string | 否 | "demo_user" | - |

#### `PUT /orders/{order_id}/state`

**Save Order State**

- 鉴权：`write:order_state_upsert`　角色：`agent` / `supervisor` / `admin`

**路径 / 查询参数**

| 名称 | 位置 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `order_id` | path | string | 是 | - | - |

**请求体**（`application/json`）

模型：`OrderStateRequest`（字段见第 5 节）

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `delivery_status` | string | 否 | "" | - |
| `items` | object[] | 否 | - | - |
| `order_id` | string | 是 | - | - |
| `refund_status` | string | 否 | "none" | - |
| `status` | string | 是 | - | - |
| `status_label` | string | 否 | "" | - |
| `store_name` | string | 否 | "" | - |
| `summary` | string | 否 | "" | - |
| `total` | number | 否 | 0.0 | - |
| `user_id` | string | 否 | "demo_user" | - |

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | `OrderStateResponse` |
| `422` | Validation Error | `HTTPValidationError` |

成功响应字段：

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `delivery_status` | string | 否 | "" | - |
| `items` | object[] | 否 | - | - |
| `order_id` | string | 是 | - | - |
| `refund_status` | string | 否 | "none" | - |
| `status` | string | 是 | - | - |
| `status_label` | string | 否 | "" | - |
| `store_name` | string | 否 | "" | - |
| `summary` | string | 否 | "" | - |
| `total` | number | 否 | 0.0 | - |
| `updated_at` | string | 否 | "" | - |
| `user_id` | string | 否 | "demo_user" | - |

### 其他

#### `GET /`

**Read Root**

- 鉴权：**无需令牌**

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | - |

#### `GET /health`

**Health Check**

- 鉴权：**无需令牌**

**响应**

| 状态码 | 说明 | 模型 |
| --- | --- | --- |
| `200` | Successful Response | - |

## 5. 数据模型全量展开

共 60 个模型，全部来自 `components.schemas`。

### `AuditLogItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action_type` | string | 是 | - | - |
| `after_summary` | string | 否 | "" | - |
| `before_summary` | string | 否 | "" | - |
| `created_at` | string | 是 | - | - |
| `device_info` | string | 否 | "" | - |
| `id` | integer | 是 | - | - |
| `ip` | string | 否 | "" | - |
| `object_id` | string | 是 | - | - |
| `object_type` | string | 是 | - | - |
| `operator_id` | string | 是 | - | - |
| `operator_role` | string | 是 | - | - |
| `request_id` | string | 否 | "" | - |

### `AuditLogListResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | AuditLogItem[] | 是 | - | - |

### `Body_upload_document_knowledge_bases__knowledge_base_id__documents_post`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `file` | string | 是 | - | 待接入的文档：PDF / DOCX / HTML / MD / TXT |

### `CategoriesResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `categories` | string[] | 是 | - | - |
| `count` | integer | 是 | - | - |

### `ChatHistoryMessage`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `content` | string | 是 | - | - |
| `created_at` | string | 否 | "" | - |
| `intent` | object | 否 | - | - |
| `risk_level` | string | 否 | "low" | - |
| `role` | string | 是 | - | - |

### `ChatHistoryResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `latest_response` | object | 否 | - | - |
| `messages` | ChatHistoryMessage[] | 否 | - | - |
| `order_id` | string \| null | 否 | - | - |
| `session_id` | string | 否 | "" | - |
| `user_id` | string | 否 | "demo_user" | - |

### `ChatRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `channel` | string | 否 | "test" | - |
| `message` | string | 是 | - | - |
| `order_id` | string \| null | 否 | - | - |
| `session_id` | string \| null | 否 | - | - |
| `user_id` | string | 否 | "demo_user" | - |

### `ChatResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer_basis` | string | 否 | "" | - |
| `citations` | object[] | 否 | - | - |
| `confidence_level` | string | 否 | "medium" | - |
| `confidence_score` | number | 是 | - | - |
| `context_used` | object | 否 | - | - |
| `conversation_status` | string | 否 | "pending_agent_review" | - |
| `decision_trace` | object | 否 | - | - |
| `evaluation_metrics` | object | 否 | - | - |
| `evidence_citations` | object[] | 否 | - | - |
| `expected_evidence_keywords` | string[] | 否 | - | - |
| `expected_intent` | string | 否 | "" | - |
| `final_prompt` | string | 是 | - | - |
| `forbidden_keyword_hits` | string[] | 否 | - | - |
| `forbidden_keywords` | string[] | 否 | - | - |
| `full_trace` | object[] | 否 | - | - |
| `handoff_ticket` | object \| null | 否 | - | - |
| `human_review_reason` | string | 否 | "v1 默认客服确认后发送" | - |
| `intent_analysis` | object | 否 | - | - |
| `issue_type` | string | 否 | "" | - |
| `manual_judgment` | object | 否 | - | - |
| `matched_evidence_keywords` | string[] | 否 | - | - |
| `memory_snapshot` | object | 否 | - | - |
| `missing_evidence_keywords` | string[] | 否 | - | - |
| `mixed_supporting_intent` | boolean | 否 | false | - |
| `need_human_review` | boolean | 否 | true | - |
| `needs_manual_review` | boolean | 否 | false | - |
| `order_id` | string \| null | 否 | - | - |
| `prompt_context_items` | PromptContextItemResponse[] | 否 | - | - |
| `prompt_version` | string | 否 | "" | - |
| `reply` | string | 是 | - | - |
| `request_id` | string | 否 | "" | - |
| `retrieved_documents` | string[] | 是 | - | - |
| `retrieved_items` | object[] | 否 | - | - |
| `risk_level` | string | 否 | "low" | - |
| `risky_promises` | string[] | 否 | - | - |
| `safety_status` | object | 否 | - | - |
| `session_id` | string | 否 | "" | - |
| `suggested_layer` | string | 否 | "" | - |
| `token_usage` | object | 否 | - | - |
| `tool_results` | object[] | 否 | - | - |
| `trace` | ChatTrace | 是 | - | 模型：ChatTrace |
| `used_primary_evidence` | boolean | 否 | false | - |
| `user_id` | string | 否 | "demo_user" | - |

### `ChatReviewActionRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | `accepted` \\| `edited_and_sent` \\| `human_handoff` \\| `marked_bad_case` | 是 | - | 可选值：`accepted` / `edited_and_sent` / `human_handoff` / `marked_bad_case` |
| `final_reply` | string | 否 | "" | - |
| `operator_id` | string | 否 | "demo_agent" | - |
| `operator_role` | string | 否 | "agent" | - |
| `reason` | string | 否 | "" | - |
| `request_id` | string | 是 | - | 长度>= 1 |

### `ChatReviewActionResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | `accepted` \\| `edited_and_sent` \\| `human_handoff` \\| `marked_bad_case` | 是 | - | 可选值：`accepted` / `edited_and_sent` / `human_handoff` / `marked_bad_case` |
| `audit_id` | integer \| null | 否 | - | - |
| `created_at` | string | 是 | - | - |
| `final_reply` | string | 否 | "" | - |
| `handoff_ticket` | object \| null | 否 | - | - |
| `order_id` | string \| null | 否 | - | - |
| `reason` | string | 否 | "" | - |
| `request_id` | string | 是 | - | - |
| `saved` | boolean | 否 | true | - |
| `session_id` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `user_id` | string | 是 | - | - |

### `ChatTrace`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer_source` | string | 是 | - | rag or fallback |
| `degraded` | boolean | 是 | - | - |
| `failure_stage` | string | 是 | - | none, retrieval, generation, or reply_rules |
| `fallback_reason` | string | 是 | - | - |
| `latency_ms` | number | 否 | 0.0 | - |
| `order_id` | string \| null | 否 | - | - |
| `reply_rules_applied` | boolean | 是 | - | - |
| `request_id` | string | 否 | "" | - |
| `retrieval_count` | integer | 是 | - | - |
| `session_id` | string | 否 | "" | - |
| `top1_intent` | string | 否 | "" | - |
| `used_fallback_prompt` | boolean | 是 | - | - |
| `user_id` | string | 否 | "" | - |

### `ChunkIndexInfo`

> 当前生效索引的自我描述（便于排障"为什么零命中"）。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `built_at` | string | 是 | - | - |
| `chunk_count` | integer | 是 | - | - |
| `embedding_dimension` | integer | 是 | - | - |
| `embedding_model` | string | 是 | - | - |
| `index_name` | string | 是 | - | - |
| `index_version` | integer | 是 | - | - |
| `tokenizer_id` | string | 否 | "" | - |
| `visible_chunk_count` | integer | 否 | 0 | - |

### `ChunkRetrievalItem`

> 一条 chunk 命中：命中即自证来源（文档 / 版本 / 租户 / ACL / 页码 / 标题路径）。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `acl` | object[] | 否 | - | - |
| `chunk_id` | string | 是 | - | - |
| `chunk_type` | string | 否 | "" | - |
| `content_hash` | string | 否 | "" | - |
| `document_id` | string | 是 | - | - |
| `document_title` | string | 否 | "" | - |
| `document_version` | integer | 是 | - | - |
| `document_version_id` | string | 是 | - | - |
| `filename` | string | 否 | "" | - |
| `heading_path` | string[] | 否 | - | - |
| `page_end` | integer \| null | 否 | - | - |
| `page_start` | integer \| null | 否 | - | - |
| `rank` | integer | 是 | - | - |
| `retrieval_origin` | string | 否 | "chunk-index" | - |
| `score` | number | 否 | 0.0 | - |
| `source_type` | string | 否 | "" | - |
| `source_uri` | string | 否 | "" | - |
| `tenant_id` | string | 是 | - | - |
| `text` | string | 否 | "" | - |
| `title` | string | 否 | "" | - |
| `token_count` | integer | 否 | 0 | - |

### `ChunkRetrievalRequest`

> chunk 级检索请求（计划 4.3）。  **刻意没有任何可以影响授权范围的字段**：没有 ``tenant_id``、没有 ``acl``、 没有 filter 表达式、没有 FAISS row_id（计划 4.3 明文禁止）。 租户与可见 chunk 集合由服务端从 JWT 身份推导。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |
| `min_score` | number \| null | 否 | - | - |
| `query` | string | 是 | - | 长度>= 1 |

### `ChunkRetrievalResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `index` | ChunkIndexInfo | 是 | - | 当前生效索引的自我描述（便于排障"为什么零命中"）。；模型：ChunkIndexInfo |
| `query` | string | 是 | - | - |
| `results` | ChunkRetrievalItem[] | 是 | - | - |
| `retrieval_path` | `chunk-index` \\| `seed-faq-demo` | 否 | "chunk-index" | 可选值：`chunk-index` / `seed-faq-demo` |

### `DocumentDetail`

> ``GET /documents/{document_id}`` 的响应：文档 + 最新版本摘要。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `created_at` | string | 是 | - | format=date-time |
| `created_by` | string \| null | 否 | - | - |
| `id` | string | 是 | - | - |
| `knowledge_base_id` | string | 是 | - | - |
| `latest_version` | DocumentVersionSummary \| null | 否 | - | - |
| `source_type` | string | 是 | - | - |
| `source_uri` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `tenant_id` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | format=date-time |

### `DocumentReprocessResponse`

> ``POST /documents/{document_id}/reprocess`` 的 202 响应。  每次重处理都**新建一个 job**（不复用旧 job 改状态）， 因为 ``retry_count`` 的语义是「同一个 job 的失败重试次数」。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `document_id` | string | 是 | - | - |
| `job_id` | string | 是 | - | - |
| `job_stage` | string | 是 | - | - |
| `job_status` | string | 是 | - | - |
| `status` | `accepted` | 否 | "accepted" | - |

### `DocumentUploadResponse`

> ``POST /knowledge-bases/{kb_id}/documents`` 的 202 响应。  ``reused_document`` 说明这次上传是「给已有文档追加新版本」还是「新建文档」—— 对调用方是可观测信息（同名文件在租户内即同一份文档，见 [D-1]）。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `document_id` | string | 是 | - | - |
| `filename` | string | 是 | - | - |
| `job_id` | string | 是 | - | - |
| `job_stage` | string | 是 | - | 任务初始阶段，上传期为 received |
| `job_status` | string | 是 | - | 任务初始状态，上传期为 pending |
| `knowledge_base_id` | string | 是 | - | - |
| `reused_document` | boolean | 是 | - | - |
| `size_bytes` | integer | 是 | - | >= 0.0 |
| `source_type` | string | 是 | - | - |
| `source_uri` | string | 是 | - | - |
| `status` | `accepted` | 否 | "accepted" | - |

### `DocumentVersionListResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `document_id` | string | 是 | - | - |
| `items` | DocumentVersionSummary[] | 是 | - | - |
| `total` | integer | 是 | - | >= 0.0 |

### `DocumentVersionSummary`

> 版本摘要。**不含** ``metadata_json`` 全量内容。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `content_hash` | string | 是 | - | - |
| `created_at` | string | 是 | - | format=date-time |
| `id` | string | 是 | - | - |
| `parser_name` | string | 是 | - | - |
| `parser_version` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `version` | integer | 是 | - | >= 1.0 |

### `ExampleItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `question` | string | 是 | - | - |

### `ExamplesByCategoryResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `category` | string | 是 | - | - |
| `count` | integer | 是 | - | - |
| `examples` | ExampleItem[] | 是 | - | - |

### `ExportEvalCaseRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `feedback_id` | integer | 是 | - | - |

### `ExportEvalCaseResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `eval_case` | object | 是 | - | - |
| `feedback_id` | integer | 是 | - | - |

### `FeedbackItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer_source` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `expected_reply` | string | 是 | - | - |
| `exported` | boolean | 是 | - | - |
| `failure_stage` | string | 是 | - | - |
| `helpful` | boolean | 是 | - | - |
| `id` | integer | 是 | - | - |
| `latency_ms` | number | 是 | - | - |
| `query` | string | 是 | - | - |
| `reason` | string | 是 | - | - |
| `reply` | string | 是 | - | - |
| `request_id` | string | 是 | - | - |
| `top1_intent` | string | 是 | - | - |

### `FeedbackRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `expected_reply` | string | 否 | "" | - |
| `helpful` | boolean | 是 | - | - |
| `query` | string | 是 | - | 长度>= 1 |
| `reason` | string | 否 | "" | - |
| `reply` | string | 是 | - | 长度>= 1 |
| `request_id` | string | 是 | - | 长度>= 1 |
| `trace` | object | 否 | - | - |

### `FeedbackResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `feedback_id` | integer | 是 | - | - |
| `saved` | boolean | 是 | - | - |

### `HTTPValidationError`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `detail` | ValidationError[] | 否 | - | - |

### `IndexRebuildResponse`

> ``POST /ingestion/indexes/rebuild`` 的响应（阶段 4.2，B7 新增）。  索引是**全局一份**（所有租户共用、由检索层按租户与 ACL 前置过滤）， 因此这里返回的是"这份全局索引整体重建后"的状态，而不是某一个租户的子集。 ``skipped=True`` 表示库里没有任何可索引的 chunk（不是错误）。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `chunk_count` | integer | 是 | - | >= 0.0 |
| `embedding_dimension` | integer | 否 | 0 | >= 0.0 |
| `embedding_model` | string | 否 | "" | - |
| `index_name` | string | 是 | - | - |
| `index_version` | integer | 是 | - | >= 0.0 |
| `manifest_uri` | string | 否 | "" | - |
| `skip_reason` | string | 否 | "" | - |
| `skipped` | boolean | 否 | false | - |
| `switched` | boolean | 否 | false | - |
| `tenant_count` | integer | 否 | 0 | >= 0.0 |

### `IngestionJobDetail`

> ``GET /ingestion-jobs/{job_id}`` 的响应。  ``warnings`` 是「解析警告可由任务查询接口读取」这条要求的落点： 它来自 ``document_versions.metadata_json["warnings"]``（``ingestion_jobs`` 没有 warnings 列，也不打算为它加列），前端可以按 ``code`` 分类展示 （例如 ``no_text_layer`` -> 「疑似扫描件」）。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `created_at` | string | 是 | - | format=date-time |
| `document_id` | string | 是 | - | - |
| `document_version_id` | string \| null | 否 | - | - |
| `error_code` | string \| null | 否 | - | - |
| `error_message` | string \| null | 否 | - | - |
| `id` | string | 是 | - | - |
| `retry_count` | integer | 是 | - | >= 0.0 |
| `stage` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `tenant_id` | string | 是 | - | - |
| `updated_at` | string | 是 | - | format=date-time |
| `warnings` | ParseWarningItem[] | 否 | - | - |

### `KnowledgeExportResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `jsonl` | string | 是 | - | - |

### `KnowledgeItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `base_id` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `expired_at` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `intent` | string | 是 | - | - |
| `owner` | string | 是 | - | - |
| `question` | string | 是 | - | - |
| `review_note` | string | 是 | - | - |
| `reviewed_at` | string | 是 | - | - |
| `source` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `title` | string | 是 | - | - |
| `updated_at` | string | 是 | - | - |
| `version` | integer | 是 | - | - |

### `KnowledgeItemPayload`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | 长度>= 1 |
| `category` | string | 是 | - | 长度>= 1 |
| `effective_at` | string | 否 | "" | - |
| `expired_at` | string | 否 | "" | - |
| `intent` | string | 是 | - | 长度>= 1 |
| `owner` | string | 否 | "knowledge_ops" | - |
| `question` | string | 是 | - | 长度>= 1 |
| `source` | string | 否 | "knowledge_ops" | - |
| `title` | string | 否 | "" | - |

### `KnowledgeListResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `items` | KnowledgeItem[] | 是 | - | - |
| `limit` | integer | 是 | - | - |
| `offset` | integer | 是 | - | - |
| `total` | integer | 是 | - | - |

### `KnowledgePublishHistoryItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | - | - |
| `backup_path` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `faiss_index_path` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `item_ids` | integer[] | 是 | - | - |
| `knowledge_path` | string | 是 | - | - |
| `merged_count` | integer | 是 | - | - |
| `note` | string | 是 | - | - |
| `publish_id` | string | 是 | - | - |
| `status` | string | 是 | - | - |

### `KnowledgePublishHistoryResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | KnowledgePublishHistoryItem[] | 是 | - | - |

### `KnowledgePublishResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `action` | string | 是 | - | - |
| `backup_path` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `faiss_index_path` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `item_ids` | integer[] | 是 | - | - |
| `knowledge_path` | string | 是 | - | - |
| `merged_count` | integer | 是 | - | - |
| `note` | string | 是 | - | - |
| `publish_id` | string | 是 | - | - |
| `status` | string | 是 | - | - |

### `KnowledgeReviewRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `review_note` | string | 否 | "" | - |
| `status` | string | 是 | - | pattern=^(pending_review\|approved\|rejected)$ |

### `ModelInfoResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `adapter_enabled` | boolean | 是 | - | - |
| `adapter_name` | string \| null | 是 | - | - |
| `base_model` | string | 是 | - | - |
| `generation_provider` | string | 否 | "" | - |
| `online_api_base_url_configured` | boolean | 否 | false | - |
| `online_api_key_env` | string | 否 | "" | - |
| `online_model_name` | string | 否 | "" | - |

### `OpsMetricsResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `accepted_count` | integer | 是 | - | - |
| `accepted_rate` | number | 是 | - | - |
| `average_latency_ms` | number | 是 | - | - |
| `average_tokens_per_request` | number | 是 | - | - |
| `bad_case_count` | integer | 是 | - | - |
| `bad_case_rate` | number | 是 | - | - |
| `edited_sent_count` | integer | 是 | - | - |
| `edited_sent_rate` | number | 是 | - | - |
| `empty_retrieval_count` | integer | 是 | - | - |
| `failure_count` | integer | 是 | - | - |
| `fallback_count` | integer | 是 | - | - |
| `human_handoff_count` | integer | 是 | - | - |
| `human_handoff_rate` | number | 是 | - | - |
| `p95_latency_ms` | number | 是 | - | - |
| `reply_rules_hit_count` | integer | 是 | - | - |
| `request_count` | integer | 是 | - | - |
| `reviewed_count` | integer | 是 | - | - |
| `source` | string | 否 | "" | - |
| `token_record_rate` | number | 是 | - | - |
| `token_recorded_count` | integer | 是 | - | - |
| `total_completion_tokens` | integer | 是 | - | - |
| `total_prompt_tokens` | integer | 是 | - | - |
| `total_tokens` | integer | 是 | - | - |

### `OrderStateRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `delivery_status` | string | 否 | "" | - |
| `items` | object[] | 否 | - | - |
| `order_id` | string | 是 | - | - |
| `refund_status` | string | 否 | "none" | - |
| `status` | string | 是 | - | - |
| `status_label` | string | 否 | "" | - |
| `store_name` | string | 否 | "" | - |
| `summary` | string | 否 | "" | - |
| `total` | number | 否 | 0.0 | - |
| `user_id` | string | 否 | "demo_user" | - |

### `OrderStateResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `delivery_status` | string | 否 | "" | - |
| `items` | object[] | 否 | - | - |
| `order_id` | string | 是 | - | - |
| `refund_status` | string | 否 | "none" | - |
| `status` | string | 是 | - | - |
| `status_label` | string | 否 | "" | - |
| `store_name` | string | 否 | "" | - |
| `summary` | string | 否 | "" | - |
| `total` | number | 否 | 0.0 | - |
| `updated_at` | string | 否 | "" | - |
| `user_id` | string | 否 | "demo_user" | - |

### `ParseWarningItem`

> 解析警告。形状与 ``services.ingestion.parsers.base.ParseWarning.to_dict()`` 一致。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `code` | string | 是 | - | - |
| `detail` | object \| null | 否 | - | - |
| `message` | string | 是 | - | - |

### `PromptContextItemResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `display_title` | string | 否 | "" | - |
| `evidence_strength` | string | 是 | - | - |
| `evidence_summary` | string | 否 | "" | - |
| `intent` | string | 是 | - | - |
| `knowledge_id` | string | 否 | "" | - |
| `prompt_instruction` | string | 否 | "" | - |
| `question` | string | 是 | - | - |
| `rank` | integer | 是 | - | - |
| `rerank_score` | number | 是 | - | - |
| `role` | string | 是 | - | - |
| `score` | number | 是 | - | - |
| `source` | string | 否 | "" | - |
| `source_answer` | string | 否 | "" | - |
| `source_question` | string | 否 | "" | - |
| `title` | string | 否 | "" | - |
| `updated_at` | string | 否 | "" | - |
| `version` | string | 否 | "" | - |

### `PromptPreviewResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `mode` | `vector` \\| `hybrid` | 是 | - | 可选值：`vector` / `hybrid` |
| `prompt` | string | 是 | - | - |
| `prompt_context_items` | RetrievalResultItem[] | 是 | - | - |
| `query` | string | 是 | - | - |
| `results` | RetrievalResultItem[] | 是 | - | - |

### `PromptVersionItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `activated_at` | string | 是 | - | - |
| `author` | string | 是 | - | - |
| `change_reason` | string | 是 | - | - |
| `created_at` | string | 是 | - | - |
| `developer_prompt` | string | 是 | - | - |
| `effective_at` | string | 是 | - | - |
| `evaluation_result` | string | 是 | - | - |
| `id` | integer | 是 | - | - |
| `rolled_back_from` | string | 是 | - | - |
| `status` | string | 是 | - | - |
| `system_prompt` | string | 是 | - | - |
| `version` | string | 是 | - | - |

### `PromptVersionListResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | PromptVersionItem[] | 是 | - | - |

### `PromptVersionPayload`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `change_reason` | string | 否 | "" | - |
| `developer_prompt` | string | 否 | "" | - |
| `effective_at` | string | 否 | "" | - |
| `evaluation_result` | string | 否 | "" | - |
| `system_prompt` | string | 是 | - | 长度>= 1 |
| `version` | string | 否 | "" | - |

### `PromptVersionStatusRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `evaluation_result` | string | 否 | "" | - |
| `status` | string | 是 | - | pattern=^(evaluation\|approved\|canary)$ |

### `RagConfigResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `embedding_model_name` | string | 是 | - | - |
| `faiss_docs_path` | string | 是 | - | - |
| `faiss_index_path` | string | 是 | - | - |
| `faiss_store_dir` | string | 是 | - | - |
| `min_vector_score` | number | 是 | - | - |
| `model_rerank_weight` | number | 是 | - | - |
| `reply_rules_enabled` | boolean | 是 | - | - |
| `reranker_model_name` | string | 是 | - | - |

### `RecentFeedbackResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `items` | FeedbackItem[] | 是 | - | - |

### `ReleaseChecklistItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `evidence` | string | 是 | - | - |
| `name` | string | 是 | - | - |
| `next_step` | string | 否 | "" | - |
| `status` | string | 是 | - | - |

### `ReleaseChecklistResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `failed_count` | integer | 是 | - | - |
| `items` | ReleaseChecklistItem[] | 是 | - | - |
| `ready` | boolean | 是 | - | - |
| `warning_count` | integer | 是 | - | - |

### `RetrievalResultItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `direction_penalty` | number | 是 | - | - |
| `display_title` | string | 否 | "" | - |
| `evidence_strength` | string | 否 | "normal" | - |
| `evidence_summary` | string | 否 | "" | - |
| `intent` | string | 是 | - | - |
| `keyword_bonus` | number | 是 | - | - |
| `model_rerank_score` | number | 是 | - | - |
| `prompt_instruction` | string | 否 | "" | - |
| `question` | string | 是 | - | - |
| `rank` | integer | 是 | - | - |
| `rerank_score` | number | 是 | - | - |
| `role` | string | 否 | "" | - |
| `score` | number | 是 | - | - |
| `source_answer` | string | 否 | "" | - |
| `source_question` | string | 否 | "" | - |
| `vector_score` | number | 是 | - | - |

### `RetrievalSearchRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |
| `min_score` | number | 否 | 0.4 | >= 0.0；<= 1.0 |
| `mode` | `vector` \\| `hybrid` | 否 | "hybrid" | 可选值：`vector` / `hybrid` |
| `query` | string | 是 | - | 长度>= 1 |

### `RetrievalSearchResponse`

> **演示 / 兼容路径**的响应（种子 FAQ，无权限过滤）。

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `mode` | `vector` \\| `hybrid` | 是 | - | 可选值：`vector` / `hybrid` |
| `query` | string | 是 | - | - |
| `results` | RetrievalResultItem[] | 是 | - | - |
| `retrieval_path` | `chunk-index` \\| `seed-faq-demo` | 否 | "seed-faq-demo" | 可选值：`chunk-index` / `seed-faq-demo` |

### `SearchExamplesRequest`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `keyword` | string | 是 | - | 长度>= 1 |
| `limit` | integer | 否 | 5 | >= 1.0；<= 20.0 |

### `SearchExamplesResponse`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `count` | integer | 是 | - | - |
| `keyword` | string | 是 | - | - |
| `results` | SearchResultItem[] | 是 | - | - |

### `SearchResultItem`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `answer` | string | 是 | - | - |
| `category` | string | 是 | - | - |
| `question` | string | 是 | - | - |

### `ValidationError`

| 字段 | 类型 | 必填 | 默认 | 约束 / 说明 |
| --- | --- | --- | --- | --- |
| `ctx` | object | 否 | - | - |
| `input` | any | 否 | - | - |
| `loc` | string \| integer[] | 是 | - | - |
| `msg` | string | 是 | - | - |
| `type` | string | 是 | - | - |
