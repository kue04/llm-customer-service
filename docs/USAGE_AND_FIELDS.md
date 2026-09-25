# 使用与字段说明

## 快速启动

```powershell
$env:RAG_JWT_SECRET = "dev-only-secret-change-me-please-32-bytes"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

正式聊天接口：`POST /chat/prompt`。正式检索接口：`POST /retrieval/search`。两者都使用 `chunk-index`，并根据 JWT 中的 tenant 和角色执行权限过滤。

## 正式评测

```powershell
python scripts/run_formal_rag_evaluation.py --tenant-id tenant-dev --top-k 10
```

性能测量：

```powershell
python scripts/audit_manifest_load_cost.py --label perf_20260925 --repeat 15 --query-limit 30
```

`/retrieval/search-demo` 和 `scripts/evaluate_chat_grounding.py --legacy-seed` 只用于 seed FAQ 演示或兼容调试。

## 聊天响应关键字段

| 字段 | 含义 |
| --- | --- |
| `answer_mode` | 回答模式：`complete` 正常回答、`partial` 部分证据、`clarify` 需要用户补充、`human_review` 需要人工处理、`retrieval_error` 检索失败 |
| `conversation_status` | 会话状态，例如 `awaiting_clarification`、`human_handoff`、`pending_agent_review` |
| `retrieval_path` | 实际检索路径；正式值为 `chunk-index` |
| `data_source` | 数据来源；正式值为 `document_chunks` |
| `index_name` | 当前使用的索引名称 |
| `index_version` | active manifest 的索引版本号 |
| `embedding_model` | 把文本转换成向量的模型名称 |
| `sparse_available` | 当前索引是否有 FTS5 稀疏检索文件 |
| `index_load_ms` | 本次请求读取索引元数据的耗时，单位毫秒 |
| `citations` | 面向客户端的引用摘要 |
| `evidence_citations` | 调试和 grounding 使用的详细证据引用 |
| `citation_quality` | 引用完整性检查结果，包含空引用和来源不一致统计 |
| `retrieved_items` | 原始检索命中及分数明细 |
| `trace` | 请求级诊断信息，包括延迟、失败阶段和降级原因 |
| `failure_stage` | 失败发生阶段：`retrieval`、`generation`、`reply_rules`、`safety_guard` 等 |
| `fallback_reason` | 触发降级的原因，例如 `retrieval_failed:chunk_index_unavailable` |
| `answer_source` | 回答来源：`rag`、`rag_partial` 或 `fallback` |

## citation 字段

每条正式 chunk 引用应包含：`evidence_id`、`chunk_id`、`document_id`、`source`、`heading_path`、`page_start`、`page_end` 和 `quote`。其中 `chunk_id` 标识切片，`document_id` 标识原文档，`heading_path` 是章节路径，`page_start/page_end` 是页码范围。

## 常见问题

- `retrieval_error`：先检查 `index_version`、`sparse_available` 和 `failure_stage`。
- `clarify`：证据不足或查询缺少必要信息，不代表服务崩溃。
- `human_handoff`：高风险、工具失败或安全规则要求人工处理。
- `index_version` 变化：说明 active manifest 已切换，需结合发布或回滚记录排查。
