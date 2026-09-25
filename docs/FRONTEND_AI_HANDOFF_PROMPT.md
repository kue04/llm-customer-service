# 前端 AI 交接提示词（2026-09-23）

> **这份文件怎么用**：整份复制，粘贴给前端窗口的 AI（或前端开发者）即可开工。
> 它不是「背景资料」，是**作业规程**：谁看到它，谁就知道现在是什么状态、先读什么、
> 动手顺序是什么、什么不许做、做完了怎么算过关。
>
> **重写规则**：每次前端交付批次结束，本文件必须重写（进度快照 + 下一步 + 完成情况）。
> 与后端 `docs/RAG_NEXT_WINDOW_PROMPT.md` 同一范式。

---

## 0. 一句话任务

后端的问答/检索/知识库 API 已经重构完成（阶段 1–6），**前端还是旧的对接方式**，
现在 100% 打不通。你的任务是：**把 `D:\llm\front` 接到真实后端契约上，先恢复「能演示」，
再按规范补齐。** 不要碰后端仓库的代码。

---

## 1. 坐标（先确认你站在哪儿）

| 项 | 值 |
| --- | --- |
| 后端仓库（**只读参考，不改代码**） | `D:\llm\llm-customer-service` |
| 后端分支 | `optimize/interview-ready` |
| 前端仓库（**你干活的地方**） | `D:\llm\front`（独立 git 仓库） |
| 后端服务地址 | `http://127.0.0.1:8000` |
| 前端 dev server | `http://localhost:5173` |
| 交接日期 | 2026-09-23 |

**后端进程当前没有在跑。** 起服务的命令见 §6。

---

## 2. 必读文件（**按顺序读，不许跳**）

| 顺序 | 文件 | 你要从里面拿到什么 |
| --- | --- | --- |
| 1 | `D:\llm\llm-customer-service\docs\FRONTEND_HANDOFF_AND_ALIGNMENT.md` | **主文档（848 行）**。契约真相、破坏性变更、P0–P2 建议、后端缺口、联调步骤、验收判据。**这是本次交接的权威。** |
| 2 | `D:\llm\llm-customer-service\docs\frontend\FRONTEND_API_CONTRACT.md` | 逐端点契约：鉴权 scope、请求/响应字段、约束与默认值、状态码，附 60 个模型全量展开。**字段以这份为准，不要凭记忆写。** |
| 3 | `D:\llm\llm-customer-service\docs\frontend\backend_contract_types.ts` | 可直接复制进前端的 TS 类型（645 行）。 |
| 4 | `D:\llm\llm-customer-service\docs\RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` | 验收规范 v2.1（十功能区分批 B–F、M01–M28 指标、§17 验收判据）。**注意：该文档缺 §17 正文**，见 §5 已知缺陷。 |
| 5 | `D:\llm\front\docs\FULLSTACK_RAG_FRONTEND_SYNC_PLAN.md` | 前端自己的旧计划。**已部分作废**，见 §3.3。只用来对照，不要照做。 |

读完后，先向老霸汇报「我读到了什么 + 我打算先做哪四项」，**不要读完就闷头改 500 行代码**。

---

## 3. 三件「一定会坏」的事（动手前必须知道）

### 3.1 前端现在 100% 会 401 —— 身份头作废了

阶段 1 起，身份**只来自** `Authorization: Bearer <JWT>`。
前端 **11 个 `src/api/*.ts` 里有 32 处**硬编码：

```ts
const xxxHeaders = { "X-Operator-Id": "...", "X-User-Role": "..." };  // ← 全部失效
```

实测（2026-09-23）：

| 请求 | 结果 |
| --- | --- |
| 无令牌 `GET /model/info` | **401** `缺少 Authorization 头` |
| 只发 `X-User-Role: admin`（不带令牌） | **401**（不是放行！） |
| `Authorization: Bearer <合法令牌>` | 200 |

→ 见 §4 的 **P0-1**。

### 3.2 `/retrieval/search` 换了语义 —— 检索面板会**静默空白**

响应从「种子 FAQ 形状」换成了「chunk 级形状」：

| 前端现在用的（旧） | 真实响应（新） |
| --- | --- |
| `question` / `answer` / `category` / `intent` / `rerank_score` | `chunk_id` / `text` / `heading_path` / `page_start` / `acl` / `retrieval_origin` + 顶层 `index` |

`RetrievalPanel` 里那些 `?? "未标注意图"`、`?? "未分类"` 的兜底，
会把「字段不存在」**伪装成正常文案**：标题写「未标注意图」、正文一片空白。

**这不是报错，是空白 —— 比 401 难查十倍。** → 见 §4 的 **P0-2**。

### 3.3 前端 sync plan 里 14 个字段，后端只有 1 个

`FULLSTACK_RAG_FRONTEND_SYNC_PLAN.md` 期望的
`query_rewrite_applied` / `answer_strategy` / `original_query` / `rewritten_query` /
`dense_rank` / `lexical_rank` / `rrf_score` / `reranker_degraded` / `answer_claims` /
`citation_validation` / `agent_enabled` / `agent_retry_count` / `tool_plan` /
`evidence_assessment` —— **13 个在后端代码里不存在**（唯一存在的是 `retrieval_origin`）。

**后果**：任何依赖这些字段的卡片/展开视图，**永远不会有数字**。
不要写代码等后端补，也不要用假数据填 —— 见 §5 反向清单。

---

## 4. 开工顺序：四个 P0（不改就必然坏）

### P0-1 · `src/api/client.ts` → Bearer 认证 + 三种错误形状

1. 令牌来源：开发期从 `import.meta.env.VITE_DEV_TOKEN` 读，统一注入
   `Authorization: Bearer ${token}`；
2. **错误体有三种形状，必须分别解析**：

```ts
// 422 → detail 是数组；业务错误 → detail 是 {error_code, message}；其他 → detail 是字符串
const detail = parsed?.detail;
if (typeof detail === "string") return { message: detail };
if (Array.isArray(detail)) return { message: "请求参数不合法", issues: detail };
if (detail && typeof detail === "object") return { message: detail.message, errorCode: detail.error_code };
```

3. `ApiError` 增加 `code`（= `error_code`）与 `retryable`（503 → true，500 → false）；
4. **删掉 11 个 `src/api/*.ts` 里全部 32 处 `X-*` 头常量**。

### P0-2 · 检索面板用对接口（**建议方案 A**）

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| **A（推荐）** | 正常聊天流程改调 `/retrieval/search-demo`（保持现有面板语义）；`/retrieval/search` 留给新的「检索实验台」 | 小，面板不动 |
| B | 改造 `RetrievalPanel` 支持 chunk 级字段 | 中，且丢掉「分数拆解」视图 |

**无论 A 还是 B，这两件事都必须做**：

1. 处理 **503 `chunk_index_unavailable`** → 显示「检索尚未接入」+ 建索引入口（**不要显示成错误弹窗**）；
2. **把 `retrieval_path` 显示出来** —— 用户必须知道这次看的是正式路径还是演示路径。
   这是后端拆两条路径的**目的**，不显示等于白拆。

### P0-3 · 正常聊天只打**一个**聊天请求

现状：`App.tsx:322-330` 用 `Promise.allSettled` 并发三个请求；
`buildOrderContextMessage()` 把订单号/金额/店铺**拼进了 `message`**。

1. `message` **只发用户原问题**（规范 §5.2：不许偷偷替换用户原文）；
   订单上下文走 `order_id` 字段（后端已支持）；
2. 正常聊天**只调 `/chat/prompt`**；`/retrieval/*` 只由「检索实验台」主动触发；
3. 证据区从本次 `ChatResponse.retrieved_items` / `prompt_context_items` 取，
   **不要用另一个请求的检索结果去补** —— 那不是同一次检索。

### P0-4 · 用生成文件替换手抄类型

`src/types/api.ts`（531 行）是手抄的，已经漂移
（它把 `/retrieval/search` 定义成 `RetrievalSearchResponse`，真实响应是 `ChunkRetrievalResponse`）。

→ 把 `docs/frontend/backend_contract_types.ts` 复制为 `src/types/backendContract.ts`；
页面 ViewModel 可以留，但**接口返回值的形状以生成文件为准**。
后端一改，重跑生成器 + diff，不手改。

---

## 5. 反向清单：**现在不要做**（做了就是自欺）

| 不要做 | 原因 |
| --- | --- |
| SSE / 打字机 / 断线续传 / `Last-Event-ID` | 后端**没有**流式端点，且验收规范 §9.1 有意选择「缓冲完整答案后核验」 |
| 「停止生成」按钮 | 无取消接口；浏览器断开 ≠ 模型停止（规范 §11.4 明写） |
| 「首安全正文延迟」指标卡 | 非流式架构下**该量不存在** |
| Dense / BM25 / RRF 分数卡 | 13/14 字段不存在 |
| Query Rewrite 展开视图 | 后端无改写字段 |
| Agent 节点 / attempt=2 视图 | 后端无 Agent 编排 |
| 「AI 可信度 98%」总圆环 | 规范 §4.3 **明文禁止** |
| 引用点击 → 原件高亮定位 | 后端无 `/evidence/{id}`、无坐标、无签名资源 |
| 评测中心（数据集 / 批量评测 / 门禁） | 后端无评测执行接口、无指标引擎 |
| 把 `/ops/metrics` 当作 D01–D24 的实现 | 口径完全不同（A 轨 chat 维度，非按阶段/配置切片） |
| 假数据 / mock 兜底填字段 | 会掩盖真实缺口，下一批排查成本翻倍 |

**另有三条纪律**：

- **不确定的标「待验证」**，不许把没验证的说成已完成；
- 后端没 `GET /capabilities`，能力边界看 §6.3 那张表，**不要假设它存在**；
- 展示「缺失」时**必须有原因**（规范 §4.2），不能只放空或 `--`。

---

## 6. 联调前置

### 6.1 后端：四步（★ 已在本机执行过迁移与种子，数据在 `data/rag_metadata.db`）

```bash
cd /d/llm/llm-customer-service

# 1) 迁移（跳过 → 所有涉及 chunk/任务/审计的接口 500 "no such table"）
./venv/Scripts/python.exe -m alembic upgrade head

# 2) 种租户/用户/知识库（跳过 → 上传 404「知识库不存在」，重建索引 500 外键失败）
./venv/Scripts/python.exe scripts/seed_dev_tenant.py      # 记下打印的 knowledge_base_id

# 3) 设密钥 + 起服务（密钥须与下一步一致，HS256 ≥ 32 字节）
export RAG_JWT_SECRET='dev-secret-please-change-me-32bytes+'
./venv/Scripts/python.exe -m uvicorn main:app --reload

# 4) 另开终端签令牌
./venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --format env
#    --format decoded → 人读；--format token → 纯令牌；--format curl → 带令牌的 curl
```

角色可选：`agent` / `supervisor` / `knowledge_ops` / `qa` / `admin`。
**权限行为要按角色验**（例：`agent` 令牌打 `/knowledge/items` 应 403）。

### 6.2 前端：起 dev server

```bash
cd /d/llm/front
# .env.local（不要提交）
# VITE_API_BASE_URL=http://127.0.0.1:8000
# VITE_DEV_TOKEN=<mint_dev_token.py 的输出>
npm run dev
```

⚠️ **必须用 `http://localhost:5173` / `http://127.0.0.1:5173` 访问**。
用局域网 IP（如 `192.168.x.x`）会因后端 CORS 白名单被拒。

### 6.3 能力协商表（**这是当前唯一的能力来源**）

| 能力 | 本机现状 | 说明 |
| --- | --- | --- |
| 鉴权 / 上传 / 任务查询 / 文档版本 / 重处理 / 索引重建 | ✅ 可用 | 迁移 + 种子之后 |
| 知识条目运营 / Prompt 版本 / 审计 / 发布检查 / 订单状态 / 反馈 | ✅ 可用 | 实测 200 |
| **正式 chunk 检索** | ⚠️ 空库 503；有 chunk 会 500 | venv 缺 `sentence-transformers` → embedder 加载失败 |
| **A 轨 `/retrieval/search-demo`、`/retrieval/prompt-preview`** | ❌ **500** | 同上 |
| **`/chat/prompt` 真实生成** | ⚠️ 200 但**走降级** | `trace.degraded=true, failure_stage="generation"` —— 这是**正确降级**，不是缺陷 |
| 上传 → 自动处理 → 可检索 | ❌ **不闭环** | 无 Redis 时队列是**进程内**的（`queue.py:105`），API 进程投递的 job 独立 worker 看不到 |
| 真实引擎（PostgreSQL / Redis / OCR） | ❌ 未复验 | 本机无 Docker / psql / Redis |

**前端要据此做「能力协商」**：能用的给真数据，不能用的显示「未接入 + 原因」，
**不是**显示 0、不是空白、不是假数据。

**想让检索真的有结果**（补模型依赖）：

```bash
./venv/Scripts/python.exe -m pip install --no-cache-dir \
  --trusted-host mirrors.aliyun.com -i https://mirrors.aliyun.com/pypi/simple/ \
  sentence-transformers
```

### 6.4 已知后端缺陷（前端要绕开，别当自己的 bug 查）

- 后端**没有** `GET /api/traces/{id}`：诊断数据只能从**当次** `/chat/prompt` 的
  `trace`/`full_trace` 拿；重新打开会话**拿不到完整诊断**（`/chat/history` 的
  `latest_response` 只回放部分）；
- 文档只有单一 `status`（9 值混装 processing + publication），**没有 `quality_status`**
  → 前端只能显示合并状态并注明；
- 只有版本级 `parser_name`，**没有页级/坐标** → 解析检查台、引用高亮做不了；
- `D:\llm\llm-customer-service\docs\RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` **缺 §17 正文**
  （v2.1 修订说明声称有 §17.5 映射表，实测只到 §16）→ 验收判据以主文档 §8.1 为准。

---

## 7. 验收：最小验证清单（12 条，2026-09-23 全部实测过）

改完 P0 后，**逐条打勾**，结果写进交付说明：

| # | 操作 | 期望 |
| --- | --- | --- |
| 1 | 无令牌 `GET /model/info` | 401 `缺少 Authorization 头` |
| 2 | 只发 `X-User-Role: admin` | **401**（不是放行） |
| 3 | 带 admin 令牌 `GET /model/info` | 200 |
| 4 | agent 令牌 `GET /knowledge/items` | 403 `缺少所需权限 read:knowledge_read` |
| 5 | `POST /retrieval/search`（未建索引） | 503 `chunk_index_unavailable` |
| 6 | `GET /ingestion-jobs/<不存在>` | 404 `任务不存在` |
| 7 | `GET /documents/<不存在>` | 404 `文档不存在` |
| 8 | 上传到不存在的知识库 | 404 `知识库不存在` |
| 9 | 种子后上传一份 md | 202，`job_status=pending` |
| 10 | `POST /ingestion/indexes/rebuild`（空库） | 200 `skipped=true, skip_reason="no_chunks"` |
| 11 | `POST /chat/prompt` | 200（本机降级，`trace.degraded=true`） |
| 12 | 13 个只读端点 | 全部 200 |

**另外更重要的两条前端侧验收**（对着屏幕看）：

- **A**：检索面板**不再出现**「未标注意图 / 未分类」这类**由缺失伪造出来的文案**；
- **B**：`retrieval_path` 在界面上**可见**，「正式路径 / 演示路径」不会混淆。

---

## 8. 交付要求（做完必须给出）

1. **改动文件清单**（路径 + 每个文件改了什么 + 为什么）；
2. **§7 的 12 条 + 2 条前端验收**，逐条的实测结果（截图或 curl 输出）；
3. **未做项与原因**：P1/P2 里哪些没做、卡在哪个后端缺口上（照抄主文档 §6 的编号）；
4. **反向清单自查**：确认没有实现 §5 里的任何一条；
5. **待验证清单**：任何你没能亲自验证的结论，显式标出来。

**没有这五项，不算交付。**

---

## 9. 边界（别越线）

- **不改后端仓库代码**。发现后端缺口 → 记下来反馈，不要自己动手加接口；
- **不提交前端仓库的 git 历史**，提交由老霸自己来；
- **不删后端仓库的 `data/*.db`**（那是本机联调环境）；
- `.env.local` 不要提交，也不要把令牌写进任何会被提交的文件。

---

## 10. 下一步（交接进度快照 · 2026-09-23）

| # | 事项 | 状态 |
| --- | --- | --- |
| 1 | 读完 §2 的必读文件 | ⬜ 待做 |
| 2 | P0-1 Bearer 认证（`client.ts` + 11 个 api 文件） | ⬜ 待做 |
| 3 | P0-2 检索面板用对接口（方案 A） | ⬜ 待做 |
| 4 | P0-3 聊天单请求 + `message` 不拼订单号 | ⬜ 待做 |
| 5 | P0-4 换成生成类型 | ⬜ 待做 |
| 6 | §7 验收清单逐条实测 | ⬜ 待做 |
| 7 | P1（状态语义统一 / 降级提示 / 视图分离 / 文档接入页） | ⬜ 待做，依赖后端缺口 |
| 8 | 前端主文档回写：把实际改动与偏差记回交付说明 | ⬜ 待做 |

**后端进度快照**：阶段 1–6 已完成，阶段 7（B8）在另一窗口推进中
→ 所以「发布门禁 PASS」**目前还没有结论**，前端的 `/release/checklist` 展示不要预设它已通过。

---

## 附录 A · 短版粘贴块（对面 AI 能读盘时用这个）

> 前后端在**同一台机器**上，文件系统共享。所以不必贴全文 ——
> 贴下面这段，让它自己去读。**贴完盯一句「先把 §2 读完再动手」**，
> 否则它容易只读个标题就开始改代码。

```text
接手任务：把前端 D:\llm\front 接到重构后的后端 API 上。

必读（按顺序，读完再动手）：
1. D:\llm\llm-customer-service\docs\FRONTEND_AI_HANDOFF_PROMPT.md   ← 你的作业规程，全按它做
2. D:\llm\llm-customer-service\docs\FRONTEND_HANDOFF_AND_ALIGNMENT.md  ← 契约真相与修改建议
3. D:\llm\llm-customer-service\docs\frontend\FRONTEND_API_CONTRACT.md  ← 字段以此为准
4. D:\llm\llm-customer-service\docs\frontend\backend_contract_types.ts ← 直接复制的类型

三件事先知道，别踩：
- 前端现在 100% 401：11 个 src/api/*.ts 里 32 处 X-User-Role / X-Operator-Id 全部失效，
  身份只来自 Authorization: Bearer <JWT>。
- /retrieval/search 已换成 chunk 级响应，检索面板的 ?? 兜底会把字段缺失伪装成文案（静默空白）。
- 前端 sync plan 期望的 14 个字段里 13 个后端不存在，不要为它们写 UI。

动手顺序：P0-1 Bearer 认证 → P0-2 检索面板改用 /retrieval/search-demo → P0-3 聊天只打一个
请求且 message 不含订单号 → P0-4 换用生成的类型。

纪律：不改后端仓库代码；不提交 git；不确定的标「待验证」；禁止做 SSE/打字机/
Dense-BM25-RRF 分数卡/引用高亮/评测中心；缺失必须显示原因，不许假数据。

交付：改动文件清单 + 12 条最小验证清单实测结果 + 未做项与原因 + 反向清单自查 +
待验证清单。缺这五项不算交付。
```
