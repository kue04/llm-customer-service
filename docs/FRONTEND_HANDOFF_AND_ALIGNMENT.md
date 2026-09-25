# 前端交接与后续对齐文档

版本：1.0｜日期：**2026-09-23**｜性质：**交接与作业规程 + 对齐基线**（不是前端代码审计报告）

> **交接时间戳：2026-09-23**。这个日期是判断本文是否过期的唯一依据。
> 若后端 `HEAD` 已不是 `624d4968366f095b6ffcb32d27dee41ffe08d382`、
> 或前端契约有变化，**先按第 7.1 节重新导出 OpenAPI 并 diff，再动手**。

## 0. 怎么用这份文档（30 秒版）

| 你是 | 先看 | 再按 |
| --- | --- | --- |
| 前端开发，要开始联调 | §1 交接状态 → §3 契约真相（**必须先看，有破坏性变更**） | §7 联调步骤（命令已验证） |
| 前端开发，要定页面结构 | §2 现状盘点 | §4 接口与字段 + §6 缺口清单 |
| 前端开发，要排改造顺序 | §5 修改建议（P0/P1/P2） | §8 验收判据 |
| 后端开发，要知道前端还缺什么 | §6 后端缺口清单 | §10 已知限制 |
| 评审 / 答辩要讲清楚这事 | §1.2 进度快照 + §8.4 演示纪律 | §10 声明 |

> **要交接给别人（或另一个 AI 窗口）？** 用 `docs/FRONTEND_AI_HANDOFF_PROMPT.md` ——
> 那是可直接粘贴的**作业规程**，本文是它的依据。本文自己太长，不适合直接贴。

**本文的三条取数纪律**（与本项目既有约定一致）：

1. **已实测**的结论都写了证据（命令、HTTP 状态码、源码行号）；
2. 未经实测的判断标为**待验证**，不允许当结论用；
3. 「后端没有」和「前端没做」是两件不同的事，本文分开写。

---

# 第一部分 · 交接状态

## 1.1 两个仓库的坐标（2026-09-23 实测）

| 项 | 后端 | 前端 |
| --- | --- | --- |
| 路径 | `D:\llm\llm-customer-service` | `D:\llm\front` |
| 分支 | `optimize/interview-ready` | `main` |
| HEAD | `624d4968366f095b6ffcb32d27dee41ffe08d382` | 见前端仓库自身记录；前端基线文档为 `docs/BASELINE_2026-07-16.md` |
| 规模 | 12 个 router、**39 条路径 / 42 个操作 / 60 个数据模型**、884 条测试 | 约 6100 行 TS/TSX；React 19 + TS 5.9 + Vite 7 + Tailwind 3（无测试框架） |
| 前端框架结论 | —— | **Vite + React + TypeScript + Tailwind + lucide-react**，无路由库（`App.tsx` 内 state 切换视图） |

> 前端不是「没有代码」的状态，而是**一套可运行的外卖下单 + 客服原型**（8 个视图：`home / store / checkout / orders / orderDetail / support / knowledge / showcase`），
> RAG 能力集中在 `support`（客服工作台 + 诊断侧栏）与 `knowledge`（知识运营）两个视图。

## 1.2 后端进度快照（可公开的完成度）

| 里程碑 | 状态 | 证据 |
| --- | --- | --- |
| 阶段 0 基线 | ✅ PASS | `reports/rag_ingestion_auth_review/stage0_review.txt` |
| 阶段 1 数据模型 + JWT 身份 | ✅ PASS | `stage1_review.txt`、`services/auth_context.py` |
| 阶段 2 解析契约 + 上传/任务 API | ✅ PASS | `stage2_review.txt`、`routers/documents.py` |
| 阶段 3 切分 + 索引 manifest + 原子切换 | ✅ PASS | `stage3_review.txt`、`services/ingestion/index_builder.py` |
| 阶段 4 权限判定 + 检索层隔离 | ✅ PASS | `stage4_review.txt`、`tests/test_retrieval_isolation.py` |
| **阶段 7 发布门禁（四格式端到端 / 索引回滚 / 总审查）** | ❌ **未做** | 台账 `docs/RAG_EXECUTION_PROGRESS.md` 第 4 节 |
| 全量测试 884 / 0 failures / 0 errors；ruff / compileall / 体积全绿；warning 5 | ✅ | `tmp/b8_baseline_junit.xml`（B8 开工前实测复核） |
| **指标体系（M01–M28）** | ❌ 不存在 | 见 §6.2 |
| **流式（SSE）事件通道** | ❌ 不存在（**且不打算做**） | 见 §3.6 |

**对前端最重要的一句话**：后端**已经**把「鉴权 → 上传 → 解析 → 切分 → 索引 → 权限过滤检索」这条链路接通了，
但**没有**任何「请求级诊断快照 / 事件流 / 指标聚合 / 评测执行 / 引用定位」接口。
前端规范里画的十个功能区，目前**只有三个有真实后端支撑**（见 §2.2）。

## 1.3 交接给你的时候，工作区是什么状态

**2026-09-23 实测**（`git status --porcelain`）：

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `docs/RAG_EXECUTION_PROGRESS.md` | 已修改未提交 | B8 开工前的基线复核记录（含 HEAD 校正） |
| `docs/RAG_NEXT_WINDOW_PROMPT.md` | 已修改未提交 | B8 交接规程 |
| `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` | **未跟踪** | 验收基准 v2.1（前端规范里的「配套文档」） |
| `docs/frontend/`、`scripts/export_frontend_contract.py`、`scripts/mint_dev_token.py`、`scripts/seed_dev_tenant.py` | **未跟踪** | 本文档这批新增 |

**提交/推送归用户**，接手方不做 git 写操作。

**⚠️ 一处文档内部矛盾，交接时必须知道**：`docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` 的 v2.1 修订说明写了
「新增 §17《验收作业规程》……映射表见 §17.5」，但**该文件当前只到 §16，没有 §17**
（2026-09-23 实测：549 行，末节为「## 16. 参考依据与适用范围」）。
引用 §17 之前先确认它是否已被补写 —— **不要凭修订说明假设它存在**。

## 1.4 本次交接交付了什么

| 产物 | 作用 | 是否机器生成 |
| --- | --- | --- |
| `docs/FRONTEND_HANDOFF_AND_ALIGNMENT.md`（本文） | 交接 + 对齐 + 修改建议 + 缺口 + 联调 + 验收 | 手写 |
| `docs/frontend/FRONTEND_API_CONTRACT.md` | 逐端点的鉴权、请求/响应字段、约束、默认值 + 60 个模型全量展开 | **是**（可重生成） |
| `docs/frontend/openapi.json` | 机器可读契约（39 路径 / 60 模型） | **是** |
| `docs/frontend/backend_contract_types.ts` | 已生成好的 TypeScript 类型，前端可直接复制 | **是** |
| `scripts/export_frontend_contract.py` | 上面两份的生成器 | 新增脚本 |
| `scripts/mint_dev_token.py` | 按角色签发本地开发 JWT（替代已失效的 `X-User-Role`） | 新增脚本 |
| `scripts/seed_dev_tenant.py` | 种租户 / 用户 / 知识库，让上传与索引重建能跑通 | 新增脚本 |

**重新生成契约的命令**（后端接口一改就重跑，不要手抄字段）：

```bash
./venv/Scripts/python.exe scripts/export_openapi.py --output docs/frontend/openapi.json
./venv/Scripts/python.exe scripts/export_frontend_contract.py
```

---

# 第二部分 · 对齐基线

## 2.1 现状盘点：按规范 §2 的四档口径

规范要求代码盘点结果只能填「已具备 / 需扩展 / 后端缺失 / 未核实」。逐项填：

| 盘点项 | 后端事实 | 前端事实 | 结论 |
| --- | --- | --- | --- |
| 框架、路由、构建 | 无关 | Vite + React 19 + TS，`App.tsx` 内 state 切视图，无路由库 | **已具备**（沿用，不重建） |
| 组件库、主题、布局 | 无关 | Tailwind + lucide-react；已有 Tabs（诊断侧栏 5 个）、卡片、表格、空态组件 | **已具备**；**无图表库**（规范 §6.3 允许先用可展开表格） |
| 聊天状态 | `/chat/prompt` 为**一次性非流式**返回；`/chat/history` 按 `user_id`+`order_id` 恢复会话（SQLite 持久化） | 有本地 state + 后端历史恢复（`supportSessions`） | **需扩展**：无 `query_id/attempt` 概念，无取消 |
| API 客户端 | 39 条路径全部要求 `Authorization: Bearer`；业务错误 `detail` 是**对象** | `src/api/client.ts` 只发 `X-Operator-Id` / `X-User-Role`，且只解析 `detail` 字符串 | **需扩展**（**P0，当前必然 401**） |
| 文档管理 | 上传/任务/版本/重处理/索引重建齐备；**处理、质量、发布三个维度没有分开** | 无文档管理页面（只有知识**条目**运营） | **需扩展**（后端字段级缺口见 §6.3） |
| 登录和权限 | 服务端 JWT + 双维授权表，逐接口独立授权（实测 403/401 正确） | 只有前端隐藏（`canViewInternalDiagnostics`），无令牌 | **需扩展**（令牌由服务端签发） |
| 可观测数据 | 有 `request_id`、`full_trace`（步骤级）、`trace`（请求级摘要）；**没有 span/瀑布/阶段耗时** | `DiagnosticsPanel` 5 个 tab（时间线/工具/证据/记忆/原始 JSON） | **需扩展**（trace 是步骤列表，不是 span 树） |
| 测试/监控 | 有 `/ops/metrics`（A 轨 chat 会话维度）；**无前端测试框架** | `package.json` 无 `test` 脚本 | **后端缺失**（前端测试需自行引入 Vitest） |

> 「页面有占位数据不算能力已具备」—— 前端 `App.tsx` 里的 `releaseChecklist / auditLogs / opsMetrics / promptVersions`
> **都是真实接口调用**，不是占位数据；但它们打的是**真实接口**，因此后端一改就会一起坏。

## 2.2 规范十大功能区 → 本项目实际支撑情况

| 规范功能区 | 前端现成资产 | 后端接口 | 结论 |
| --- | --- | --- | --- |
| 问答工作台 | `features/support/SupportView.tsx`（1324 行）+ `DiagnosticsPanel` | `/chat/prompt`、`/chat/history` | ✅ **可做**（无流式，见 §3.6） |
| 请求诊断 | 同上，第 1 个 tab（流程时间线） | 仅 `full_trace` 步骤列表 | ⚠️ **大幅缩水做**：无 span、无七面板数据源 |
| 知识库 | `features/knowledge/KnowledgeOpsView.tsx`（**运维的是知识条目，不是文档**） | `/knowledge/*`（知识条目）+ `/documents/*`（文档，**前端未接**） | ⚠️ **需区分两条线**，文档侧要从零接 |
| 解析检查台 | 无 | 仅 job `warnings` + 版本 `parser_name/parser_version`；**无页级、无坐标、无 blocks** | ❌ **后端缺失** |
| 检索实验台 | `components/RetrievalPanel.tsx` + `prompt-preview` | `/retrieval/search`（chunk 级）、`/retrieval/search-demo` | ⚠️ **可做最小版**：单 query、无可切换配置、无 gold 标注 |
| 评测中心 | 无（只有 `feedback/export-eval-case` 导出候选） | **无评测执行接口**（只有 `scripts/evaluate_*.py` 离线脚本） | ❌ **后端缺失** |
| 质量与运行概览 | `App.tsx` 内 ops/feedback 面板 | `/ops/metrics`、`/feedback/recent`、`/release/checklist` | ⚠️ **可做最小版**（只有 A 轨 chat 维度的 23 个计数） |
| 配置与发布 | `App.tsx` 内 prompt 版本面板 | `/prompt/*`、`/knowledge/publish-approved`、`/ingestion/indexes/rebuild` | ⚠️ **可做**，但**三条"发布"语义完全不同**，见 §3.5 |
| 安全与治理 | `App.tsx` 内审计面板 | `/audit/logs` | ⚠️ **可做最小版**（只有审计流水，无权限测试/撤权演练入口） |
| 反馈队列 | `App.tsx` 内反馈面板 + `/feedback/export-eval-case` | `/feedback`、`/feedback/recent` | ✅ **可做** |

**这张表就是「后端前置」的答案**：规范批次划分里，
**B/C/D/E/F 五个批次都缺后端前置**，只有 **A（契约与状态）** 现在就能开工。

## 2.3 M01–M28 指标的前端映射：现在能显示什么

规范 §10.3 要求每张卡片有 `metric_id / 口径 / 样本量 / 版本 / 状态`。
**后端没有指标聚合引擎**，所以下表回答的是「这个指标现在该怎么显示」。

| 指标 | 后端能不能算出 | 2026-09-23 前端应显示 | 依据 |
| --- | --- | --- | --- |
| M01 意图 macro-F1 | ❌ 无 gold 数据集、无评测执行接口 | N/A + 缺失原因「需要 gold 评测集」 | `reason_code: GROUND_TRUTH_REQUIRED` |
| M02 路由正确率 | ❌ 同上 | N/A + 原因 | 同 |
| M03 多意图覆盖 | ⚠️ 只有 `intent_analysis.secondary_intents` 原始字段 | 展示原始意图列表，**不显示分数** | `/chat/prompt` 实测返回 |
| M04 槽位正确率 | ❌ **后端没有槽位概念**（无 `required_slots`/`unresolved_slots`） | N/A + 原因「后端未实现槽位」 | 全仓 `unresolved_slots` 零命中 |
| M05 误拒率 | ❌ 无 | N/A | — |
| M06 攻击成功率 | ❌ 无攻击集接口（有 `safety_status` 单请求字段） | 单请求可显示 `safety_status.blocked/issues`；比率 N/A | `/chat/prompt` 实测 |
| M07 CER / M08 关键字段准确率 | ❌ **不适用**：当前解析器是文本级（PDF/DOCX/HTML/MD/TXT），**无 OCR、无表格结构** | 显示「不适用」+ 原因 | `tests/fixtures` + parser 注册表；`no_text_layer` 是警告而非 OCR 升级 |
| M09 解析发布通过率 | ⚠️ 可从 job `status` 逐条算，**无聚合接口** | 逐任务显示状态；总比率标「需前端聚合，样本可能不足」 | `/ingestion-jobs/{id}` |
| M10–M12 Recall/MRR/nDCG | ❌ 只有离线脚本（`scripts/evaluate_retrieval_metrics.py`），**无在线接口** | N/A + 原因「离线评测，未接入」 | 台账 §0.5 记的 F2：该脚本口径本身还待修 |
| M13–M18 | ❌ 无 claim 级核验接口 | N/A + 原因 | 无 `/claims`、无 `/verification` 路由 |
| M19 首安全正文延迟 | ❌ 无该计时字段（**非流式**，不存在"首正文"） | 显示 `trace.latency_ms`（总耗时），并注明「无流式，首正文延迟不适用」 | 实测 306.9ms 单值 |
| M20 端到端延迟 P50/P95/P99 | ⚠️ `/ops/metrics` 有 `average_latency_ms` / `p95_latency_ms`（**A 轨 chat 维度**） | 可显示两个值 + 注明样本来源与样本量 | `#ops/metrics` 实测 200 |
| M21 系统失败率 | ⚠️ `/ops/metrics.failure_count` / `request_count` | 可显示，注明窗口来源 | 同 |
| M22 单次成功任务成本 | ❌ 无价表、无金额（`token_usage` 也可能为空对象） | **只能显示 token**，金额标「无价表，不可估算」 | 实测 `token_usage = {}` |
| M23 未授权披露数 | ❌ 无接口（只有离线权限测试证据） | N/A + 指向 `reports/rag_ingestion_auth_review/` | 证据在测试而非接口 |
| M24 链路完整率 | ❌ `full_trace` 无 span/版本字段 | N/A + 原因 | — |
| M25 知识新鲜度 | ⚠️ 有 `created_at`/`updated_at` 可粗算，无事件流 | 可显示"最近更新时间"，不标"新鲜度延迟" | `/documents/{id}` 字段 |
| M26 撤权生效延迟 | ❌ 无撤权演练接口 | N/A | — |
| M27 用户满意度 | ✅ `/feedback/recent` 可算（`helpful` 比例） | **可显示**，同时显示反馈数 | 实测 200 |
| M28 纠正成本 | ❌ 无 | N/A | — |

**D 指标（D01–D24）**：`/ops/metrics` 提供了其中一部分的近似量（请求量、失败数、P95、空检索数、规则命中数、降级数、复核动作计数、token 统计），
但**其窗口、分母、切片口径都是 A 轨 chat 会话维度**，与规范 §10.4 的「5 分钟 / 滚动 24 小时、按阶段/配置切片」不是一回事。
**前端不要把它当作 D 指标的实现**，只能作为「有数据可看」的近似。

---

# 第三部分 · 契约真相（动手前必读）

> 这一部分是本批最值钱的内容。**前端仓库里现有的三份对接资料全部与本项目当前后端不一致**，
> 照它们写代码会得到 401、空白面板和 500。

## 3.1 后端「前后端文档」的现状

| 文档 | 位置 | 2026-09-23 状态 |
| --- | --- | --- |
| `docs/API_INTEGRATION.md` | 后端仓库 | ⚠️ **已过期**。它写的是 `X-User-Role` 时代，且把 `/retrieval/search` 描述成返回种子 FAQ 分数拆解 —— 这两件事在 B2/B7 都变了 |
| `docs/FRONTEND_DESIGN.md` | 后端仓库 | ⚠️ **部分过期**。它描述的第一版工作台（ModelInfoBar/QueryControlPanel/RetrievalPanel…）与前端实际实现**已经不同名** |
| `docs/FULLSTACK_RAG_IMPROVEMENT_PLAN.md` | 后端仓库 | 主计划（v2 全栈），**未执行完** |
| `docs/FULLSTACK_RAG_FRONTEND_SYNC_PLAN.md` | **前端仓库** | ⚠️ **有 13/14 个字段后端根本没有**，见 §3.3 |
| `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` | 后端仓库 | ✅ 验收基准 v2.1（本文的对齐依据之一） |
| `docs/frontend/FRONTEND_API_CONTRACT.md` | 后端仓库 | ✅ **新增，机器生成，唯一权威的字段清单** |

## 3.2 破坏性变更一：`/retrieval/search` 语义变了（**会让现有面板直接空白**）

**B7 把 `POST /retrieval/search` 重写成了 chunk 级正式路径**，旧的种子 FAQ 行为搬到了 `POST /retrieval/search-demo`。

| | B7 之前（前端现在按它写的） | B7 之后（现在的真实响应） |
| --- | --- | --- |
| 响应模型 | `RetrievalSearchResponse` | **`ChunkRetrievalResponse`** |
| 顶层字段 | `query / mode / count / results` | `retrieval_path / query / count / **index** / results` |
| 单条命中 | `rank / score / rerank_score / model_rerank_score / vector_score / keyword_bonus / direction_penalty / category / intent / question / answer` | `rank / **chunk_id / document_id / document_version / document_version_id / tenant_id** / title / document_title / chunk_type / **heading_path / page_start / page_end / acl** / source_uri / filename / source_type / content_hash / token_count / **text** / score / retrieval_origin` |
| 请求体 | `query / mode / limit / min_score` | `query / limit / min_score`（**没有 `mode`**） |
| 鉴权 | 无 | `read:retrieval_read` |
| 零命中语义 | 空数组 | 空数组 = 正常；**无生效索引 = 503 `chunk_index_unavailable`** |

**实测证据**（2026-09-23，本机 8011 端口）：

```json
// POST /retrieval/search  {"query":"退款多久到账","limit":3}  —— 库已迁移、索引未建
HTTP 503
{"detail":{"error_code":"chunk_index_unavailable",
 "message":"没有可用的 chunk 索引：索引指针不存在（本租户还没有生效索引）：...\\data\\faiss_store\\chunk_index\\document_chunks\\current.json"}}
```

**对前端的直接后果（已按组件代码逐行核对）**：`App.tsx:322-330` 每次发问都会并发调 `searchRetrieval()`，
而 `RetrievalPanel` 渲染的是 `result.intent` / `result.category` / `result.display_title ?? result.question` /
`result.evidence_summary ?? result.answer` 与 `ScoreBadge(score / rerank_score / vector_score / keyword_bonus)`。
新响应里这些字段**全都不存在**（新模型只有 `score`），于是面板会变成：

- 意图显示「未标注意图」、分类显示「未分类」（`??` 兜底把缺失伪装成了文案）；
- 标题与正文**整片空白**（两个 `??` 的两侧都是 `undefined`）；
- 分数徽章空白。

**注意这不是 401 那种"响亮的失败"**，而是一次**静默的数据错位** —— 排查时最容易走错方向。
见 §5.1 的 P0-2。

## 3.3 破坏性变更二：`X-User-Role` / `X-Operator-Id` 已彻底失效（**401**）

阶段 1 起身份**只来自** `Authorization: Bearer <JWT>`。前端 **11 个 `src/api/*.ts` 里硬编码了 32 处 `X-*` 头**（2026-09-23 实测）。

**实测证据**：

| 请求 | 结果 |
| --- | --- |
| `GET /model/info`（无头） | **401** `{"detail":"缺少 Authorization 头"}` |
| `GET /model/info`（`X-User-Role: admin`） | **401** 同上（服务端另记一行 `WARNING services.auth_service ignoring legacy identity headers (X-User-Role='admin')`） |
| `GET /model/info`（`Authorization: Bearer <合法令牌>`） | **200** |
| `GET /knowledge/items`（agent 角色令牌） | **403** `{"detail":"缺少所需权限 read:knowledge_read"}` |

**令牌 claim**：`sub` / `tenant_id` / `roles`（数组或字符串）/ `iss` / `aud` / `exp`。
**令牌里自带 `scopes` / `permissions` 会被忽略**（防自报提权）。
签发密钥只从 `RAG_JWT_SECRET` 读；缺失时受保护接口返回 **500**（fail closed），**不会静默放行**。

**开发期怎么拿令牌**：用本次新增的 `scripts/mint_dev_token.py`（§7.1 步骤 3）。
**不要**把 `X-*` 头加回后端去迁就前端。

## 3.4 前端 sync plan 期望的 14 个字段：**13 个后端没有**

`docs/FULLSTACK_RAG_FRONTEND_SYNC_PLAN.md`（前端仓库）要求前端展示下面这批字段。
2026-09-23 全仓源码核查（排除 `venv/`、`tmp/`）：

| 字段 | 后端命中数 | 结论 |
| --- | --- | --- |
| `retrieval_origin` | 5 | ✅ **存在**（`ChunkRetrievalItem.retrieval_origin`） |
| `answer_strategy` | 0 | ❌ 不存在 |
| `original_query` | 0 | ❌ 不存在 |
| `rewritten_query` | 0 | ❌ 不存在（**没有 query rewrite**） |
| `query_rewrite_applied` | 0 | ❌ 不存在 |
| `query_rewrite_reason` | 0 | ❌ 不存在 |
| `dense_rank` | 0 | ❌ 不存在 |
| `lexical_rank` | 0 | ❌ 不存在（**没有 BM25 稀疏检索**） |
| `dense_score` | 0 | ❌ 不存在 |
| `lexical_score` | 0 | ❌ 不存在 |
| `rrf_score` | 0 | ❌ 不存在（**没有 RRF 融合**） |
| `reranker_degraded` / `reranker_error` | 0 | ❌ 不存在 |
| `answer_claims` | 0 | ❌ 不存在 |
| `citation_validation` | 0 | ❌ 不存在 |
| `agent_enabled` / `agent_retry_count` / `agent_nodes` / `tool_plan` / `evidence_assessment` | 0 | ❌ 全部不存在（**没有 Agent 编排**） |

**结论**：`/retrieval/search` 的排序**不是 RRF，也不是双路混合**——
正式路径只有**单路稠密检索（FAISS `IndexFlatIP`）+ 服务端预过滤**（`retrieval_origin="chunk-index"`）；
A 轨演示路径才有 `vector` / `hybrid` 两种模式（`hybrid` 是 `vector_score + keyword_bonus − direction_penalty`）。
**前端不要按 sync plan 的「Dense rank / BM25 rank / RRF score」设计卡片** —— 那些数字永远不会有。

## 3.5 三件都被叫「发布」的事，是**三件不同的事**

前端做「配置与发布」页时最容易混：

| 名称 | 接口 | 管的是什么 | 影响面 |
| --- | --- | --- | --- |
| 知识条目发布 | `POST /knowledge/publish-approved`（+ `/knowledge/rollback-latest`） | `knowledge_ops.db` 里的**知识条目** → 导出 JSONL + 重建**种子 FAQ** 索引（A 轨） | 只影响 `/retrieval/search-demo` 与 `/chat/prompt` 的 A 轨语料 |
| 文档版本发布 | **没有接口** —— 由接入流水线在 `published` 阶段**自动完成**（`services/ingestion/pipeline.py:644`） | `document_versions.status = "published"` | 决定该文档的 chunk 能否进入检索可见集合 |
| chunk 索引重建 | `POST /ingestion/indexes/rebuild` | 全局一份 chunk 索引的整份重建 + 原子切换（`index:rebuild`，只给 supervisor/admin） | 影响**所有租户**的正式检索结果 |
| （第四条，容易漏）Prompt 版本 | `POST /prompt/versions*` | 系统提示词版本（`prompt_write`，只给 admin） | 影响生成阶段 |

**前端要显示「知识快照」时，必须说清是哪一条**：规范的 `knowledge_snapshot_id` 在后端**不存在**，
最接近的可观测值是 `IndexRebuildResponse.index_version` 与 `ingestion_jobs.document_version_id`。

## 3.6 流式（SSE）：后端**没有**，而且这是**有意不做**

规范 §11.3 给了一整套流事件信封（`stage.completed` / `answer.segment` / `cancel` / 断线续传 …）。
**后端当前一个流式端点都没有**（39 条路径里无 SSE）。
这不是漏做：验收规范 §9.1 明确写「**默认缓冲完整答案后核验，个人系统优先保证简单可靠**」——
与当前实现一致。

**因此：**

1. **前端不要做流式 UI**（打字机、逐段追加、跨 chunk 解析 SSE、`Last-Event-ID` 续传）——
   没有数据源，做了就是自欺；
2. **不要做「停止生成」** —— 无取消接口（`POST /chat/prompt` 是一次性请求；浏览器断开 ≠ 模型停止）；
3. `first_safe_body_latency_ms`（M19）**不存在**，也不该假装有；
4. 规范批次 A 里的「统一状态/指标对象、空态」**可以做**；「真实事件流」**做不了**。

---

# 第四部分 · 后端真实契约（权威）

**字段清单不在这里手抄**，请直接用机器生成的那份：
`docs/frontend/FRONTEND_API_CONTRACT.md`（逐端点字段 + 60 个模型展开）
与 `docs/frontend/backend_contract_types.ts`（TS 类型）。
本节只给前端设计时需要先想清楚的**形状与语义**。

## 4.1 通用约定

| 项 | 约定 |
| --- | --- |
| Base URL | `http://127.0.0.1:8000` |
| 交互文档 | `/docs`（已声明 `bearerAuth`，可粘令牌调试） |
| CORS | 只允许 `http://127.0.0.1:<port>` 与 `http://localhost:<port>`（**前端 dev server 必须在这两个 host 上**，用 LAN IP 会跨域失败） |
| 鉴权 | `Authorization: Bearer <JWT>`，无例外（除 `/` 与 `/health`） |
| 错误形状 | 普通错误 `{"detail":"文本"}`；**业务错误 `{"detail":{"error_code":"...","message":"..."}}`** |
| 422 | Pydantic 校验失败，`detail` 是**数组** |
| 分页 | `limit` + `offset` 查询参数，响应带 `total` |

**`client.ts` 必须按这三种形状分别处理**（现有实现只取字符串，会把 `{"error_code":...}` 显示成 `[object Object]`）。

## 4.2 端点总表（按前端功能区）

完整字段见契约文档。**鉴权那一列是必须全部满足**的意思。

### 问答工作台

| 方法 | 路径 | 鉴权 | 关键点 |
| --- | --- | --- | --- |
| POST | `/chat/prompt` | `read:chat_generate` | **非流式**；43 个顶层字段；**无 `query_id`/`attempt`/`cancel`** |
| GET | `/chat/history` | `read:chat_history` | `?user_id=&order_id=&session_id=&limit=`；优先 `session_id` |
| POST | `/chat/review-action` | `review:<action>` | 按 `action` 值分档：`marked_bad_case` 额外允许 qa / knowledge_ops |

### 检索（两条路径，入口即见分野）

| 方法 | 路径 | 鉴权 | 关键点 |
| --- | --- | --- | --- |
| POST | `/retrieval/search` | `read:retrieval_read` | **正式·chunk 级**。无 `mode`；空索引 → **503**；零命中是正常结果 |
| POST | `/retrieval/search-demo` | `read:retrieval_read` | **演示·种子 FAQ**。有 `mode`（`vector`/`hybrid`），**无权限过滤** |
| POST | `/retrieval/prompt-preview` | `read:retrieval_read` | 与上同链路，多返回 `prompt` / `prompt_context_items` |
| GET | `/retrieval/config` | `read:retrieval_read` | 暴露 embedding / reranker 模型名与权重（**这是 A 轨配置**） |

### 知识库 · 接入（前端**尚未接**的一条线）

| 方法 | 路径 | 鉴权 | 关键点 |
| --- | --- | --- | --- |
| POST | `/knowledge-bases/{kb_id}/documents` | `write:knowledge_create` + `document:upload` + KB 写成员 | **multipart，字段名必须是 `file`**；202 立即返回 |
| GET | `/documents/{document_id}` | `read:knowledge_read` + `document:read` | 只有 `latest_version` **摘要**，无 `metadata_json` |
| GET | `/documents/{document_id}/versions` | 同上 | 每版本：`id / version / content_hash / parser_name / parser_version / status / created_at` |
| POST | `/documents/{document_id}/reprocess` | `write:knowledge_rollback` | **每次新建 job**（不复用旧 job 改状态） |
| GET | `/ingestion-jobs/{job_id}` | `read:knowledge_read` + `document:read` | 有 `stage / error_code / retry_count / warnings[]` |
| POST | `/ingestion/indexes/rebuild` | `index:rebuild`（supervisor/admin） | 全局索引；空库返回 `skipped=true, skip_reason="no_chunks"` |

> ⚠️ **没有「列出知识库」的接口**。`knowledge_base_id` 只能由外部给定（见 §7.1 的种子脚本）。

### 知识库 · 运营（前端已接）

`/knowledge/items`（GET/POST）、`/knowledge/items/{id}`（PUT）、`/knowledge/items/{id}/archive`、`/knowledge/items/{id}/review`、
`/knowledge/export-approved`、`/knowledge/publish-approved`、`/knowledge/publish-history`、`/knowledge/rollback-latest`。

### 其余

- **反馈**：`POST /feedback`、`GET /feedback/recent`、`POST /feedback/export-eval-case`
- **配置与发布**：`/prompt/active`、`/prompt/versions`（GET/POST）、`/prompt/versions/{id}/status`、`/prompt/versions/{id}/activate`、`/prompt/rollback-latest`、`GET /release/checklist`
- **安全与治理**：`GET /audit/logs`
- **运行概览**：`GET /ops/metrics`、`GET /model/info`
- **订单状态**：`PUT /orders/{id}/state`、`GET /orders/{id}/state`
- **种子 FAQ 浏览**：`GET /examples/categories`、`GET /examples/by-category`、`POST /examples/search`

## 4.3 关键响应形状（实测抓取，2026-09-23）

### 上传：`POST /knowledge-bases/{kb_id}/documents` → 202

```json
{"document_id":"bd43a2...","job_id":"cbee4a...","knowledge_base_id":"4b7416...",
 "source_uri":"upload://demo_upload.md","source_type":"md","filename":"demo_upload.md",
 "size_bytes":30,"status":"accepted","job_status":"pending","job_stage":"received",
 "reused_document":false}
```

`reused_document=true` 表示这是**给已有文档追加新版本**（租户内同名 = 同一份文档）。

### 任务：`GET /ingestion-jobs/{job_id}` → 200

```json
{"id":"cbee4a...","tenant_id":"tenant-dev","document_id":"bd43a2...",
 "document_version_id":null,"status":"pending","stage":"received",
 "error_code":null,"error_message":null,"retry_count":0,
 "created_at":"2026-09-23T14:03:51","updated_at":"...","warnings":[]}
```

`stage` 取值（8 个，顺序固定）：`received / stored / parsed / normalized / chunked / persisted / indexed / published`。
`status` 取值：`pending / running / succeeded / failed / retrying / cancelled`。

> **前端必须能显示 `document_version_id: null`** —— 它表示「还没解析出任何版本」，不是"版本 0"。
> 这正是规范 §4.2「未测/无样本 ≠ 0」在字段层的例子。

### 索引重建：`POST /ingestion/indexes/rebuild` → 200

```json
{"index_name":"document_chunks","index_version":0,"chunk_count":0,
 "embedding_model":"BAAI/bge-small-zh-v1.5","embedding_dimension":0,
 "manifest_uri":"","switched":false,"skipped":true,"skip_reason":"no_chunks","tenant_count":0}
```

### 正式检索：`POST /retrieval/search` → 200（有索引时）

```json
{"retrieval_path":"chunk-index","query":"...","count":2,
 "index":{"index_name":"document_chunks","index_version":3,"embedding_model":"...",
          "embedding_dimension":512,"built_at":"...","chunk_count":128,
          "tokenizer_id":"heuristic-zh-v1","visible_chunk_count":42},
 "results":[{"rank":1,"chunk_id":"...","document_id":"...","document_version":2,
             "document_version_id":"...","tenant_id":"tenant-dev","title":"...",
             "document_title":"...","chunk_type":"paragraph",
             "heading_path":["第 2 章","退款"],"page_start":3,"page_end":3,
             "acl":[{"subject_type":"user",...}],"source_uri":"upload://x.pdf",
             "filename":"x.pdf","source_type":"pdf","content_hash":"...",
             "token_count":218,"text":"...","score":0.83,
             "retrieval_origin":"chunk-index"}]}
```

`index.visible_chunk_count` 是**当前身份可见**的 chunk 数（不是全库），
它是「为什么我零命中」最有用的一个数字，**前端一定要显示**。

### 聊天：`POST /chat/prompt` → 200（实测 43 个顶层字段）

降级场景实测（本机缺本地生成依赖时）：

```json
{"request_id":"08045f7a...","reply":"抱歉，这个问题我暂时无法稳定判断。…",
 "risk_level":"medium","confidence_level":"low","need_human_review":true,
 "conversation_status":"pending_agent_review","confidence_score":0.2,
 "answer_basis":"工具结果：order_not_found；order_not_found",
 "tool_results":[{"tool_name":"query_order_status","status":"failed",
                  "input":{"user_id":"dev_user","order_id":"DEMO-1001"},
                  "output":{},"error_type":"order_not_found",
                  "latency_ms":1.08,"retryable":false}],
 "intent_analysis":{"primary_intent":"退款进度","secondary_intents":[],
                    "risk_level":"medium","routing":"rag",
                    "intents":[{"name":"退款进度","confidence":0.86,...}]},
 "safety_status":{"passed":true,"blocked":false,"issues":[],"fallback_applied":false},
 "token_usage":{},
 "trace":{"retrieval_count":0,"latency_ms":306.9,"top1_intent":"",
          "used_fallback_prompt":true,"reply_rules_applied":false,
          "answer_source":"fallback","degraded":true,
          "failure_stage":"generation",
          "fallback_reason":"generation_failed: local generation dependencies are unavailable"}}
```

三个前端必须正确处理的地方：

1. **`token_usage` 可能是空对象 `{}`** → 显示「未记录」，**不能显示 0**；
2. **`trace.degraded=true` 必须显著提示**（规范 §11.1：降级要标记，不能宣称等同完整链路）；
   `failure_stage` 取值：`none / retrieval / generation / reply_rules`；
3. **`confidence_score: 0.2` 是未校准分数** —— 按规范 §3.4 标「模型评分，非正确概率」，
   不要画成「AI 可信度 20%」的圆环。

## 4.4 状态码与前端动作对照

| 码 | 触发场景（实测） | 前端应显示 | 可执行动作 |
| --- | --- | --- | --- |
| 401 | 缺 `Authorization` 头 / 令牌无效过期 | 「登录状态无效」 | 重新取令牌（开发期用 `mint_dev_token.py`） |
| 403 | 令牌合法但缺 scope | 「当前身份无此权限」 | **隐藏功能入口**，不是重试 |
| 404 | `/documents/{id}` → `文档不存在`；`/ingestion-jobs/{id}` → `任务不存在`；上传到不存在的 KB → `知识库不存在` | 按资源分别提示 | 返回列表 |
| 415 | 上传扩展名不支持 / 内容与扩展名不符 | 「文件类型不支持」 | 换文件 |
| 422 | 请求体校验失败（`detail` 是数组） | 表单字段级提示 | 修正输入 |
| 500 | 服务端未配 `RAG_JWT_SECRET`；解析器/embedding 依赖缺失；**开发库未迁移（`no such table`）** | 「服务端故障」+ trace 提示 | **不要重试**，报给后端 |
| 503 | 正式检索无生效索引：`error_code=chunk_index_unavailable` | 「检索服务尚未就绪」 | 提示先建索引（重建入口） |

> 503 与 500 的区别是**有意的**：索引没建是**可用性**问题（可稍后重试），
> 依赖缺失是**部署**问题（重试无用）。前端不要把它们合并成一句话。

## 4.5 空态与「缺失」语义（规范 §4.2 在字段层的落点）

| 情况 | 后端表现 | 前端必须 |
| --- | --- | --- |
| 无生效索引 | 503 + `error_code` | 显示「尚未接入」，**不是「没有找到资料」** |
| 有索引但本身份零命中 | 200 + `count: 0` | 显示「在当前授权范围内没有匹配内容」+ `visible_chunk_count` |
| 任务未开始处理 | `status=pending, document_version_id=null` | 显示排队中，**不显示「可问答」** |
| 索引为空库 | `skipped=true, skip_reason="no_chunks"` | 显示「库里还没有可索引内容」，**不是失败** |
| `token_usage={}` | 空对象 | 显示「未记录」，**不显示 0** |
| 无 gold / 无评测接口 | 接口不存在 | 显示 N/A + 原因，**不显示 0 分或 100 分** |

---

# 第五部分 · 前端修改建议

按「不改就一定坏」到「有余力再做」排序。**P0 是四个必修项。**

## 5.1 P0 · 必修（不改就必然 401 或空白）

### P0-1 · `src/api/client.ts`：改成 Bearer 认证 + 三种错误形状解析

**现状**：只发 `Content-Type`，错误只取字符串。
**要做**：

1. 令牌来源：开发期从 `import.meta.env.VITE_DEV_TOKEN` 读（或一个显式的 `authStore`），
   统一注入 `Authorization: Bearer ${token}`；
2. **业务错误要读 `error_code`**：

```ts
export type ApiErrorBody = { error_code?: string; message?: string } | string | Array<unknown>;

// 422 时 detail 是数组；业务错误时 detail 是 {error_code, message}
const detail = parsed?.detail;
if (typeof detail === "string") return { message: detail };
if (Array.isArray(detail)) return { message: "请求参数不合法", issues: detail };
if (detail && typeof detail === "object") return { message: detail.message, errorCode: detail.error_code };
```

3. `ApiError` 增加 `code`（= `error_code`）与 `retryable`（503 → true，500 → false）；
4. **删除所有 `src/api/*.ts` 里的 `const xxxHeaders = {"X-Operator-Id": ..., "X-User-Role": ...}`**（11 个文件、32 处）。

### P0-2 · 让「检索面板」用对接口（二选一，**建议 A**）

**现状**：`App.tsx:328` 在正常聊天流程里并发调 `/retrieval/search` + `/retrieval/prompt-preview`，
`RetrievalPanel` 按**种子 FAQ 形状**渲染 → 现在必然空白。

| 方案 | 做法 | 代价 | 适用 |
| --- | --- | --- | --- |
| **A（推荐）** | 正常聊天流程改调 `/retrieval/search-demo`（保持现有面板语义），把 `/retrieval/search` 留给**检索实验台**做一个新面板（chunk 级 + `index` 信息） | 小，面板不动 | 想快速恢复可演示状态 |
| B | 直接改造 `RetrievalPanel` 支持 chunk 级字段（`text/heading_path/page_start/acl/retrieval_origin`） | 中，且失去「分数拆解」视图 | 想让正式路径成为主线 |

**两个方案都必须做的两件事**：

1. 处理 **503 `chunk_index_unavailable`** → 显示「检索尚未接入」+ 建索引入口；
2. **`retrieval_path` 要显示出来** —— 前端必须让用户知道「我这次看的是正式路径还是演示路径」
   （这正是 B7 拆两条路径的目的）。

完整字段清单见 `docs/frontend/FRONTEND_API_CONTRACT.md` 的「检索（正式路径 · chunk 级）」一节。

### P0-3 · 正常聊天流程只打**一个**聊天请求

**现状**：`App.tsx:322-330` 用 `Promise.allSettled` 并发三个请求；
`buildOrderContextMessage()` 把订单号/金额/店铺拼进了 `message`。

**要做**：

1. `message` **只发用户原问题**（规范 §5.2：「不能偷偷替换用户原文」）；
   订单上下文通过 `order_id` 字段传（后端已有该字段）；
2. 正常聊天**只调 `/chat/prompt`**；`/retrieval/*` 只由「检索实验台」主动触发；
3. 从 `ChatResponse.retrieved_items` / `prompt_context_items` 更新证据区，
   不要用另一个请求的结果去补（**两条请求的证据不是同一次检索**）。

### P0-4 · 从 `/docs` 重新生成类型，删掉手抄的字段

**现状**：`src/types/api.ts`（531 行）是手写拷贝，已与后端漂移（例如它把 `/retrieval/search` 的类型
定义成 `RetrievalSearchResponse`，而真实响应是 `ChunkRetrievalResponse`）。

**要做**：把 `docs/frontend/backend_contract_types.ts` 复制到 `src/types/backendContract.ts`，
页面内 ViewModel 可以保留，但**接口返回值的形状以生成文件为准**。
后端一改，重跑生成器 + diff，而不是手改。

## 5.2 P1 · 按规范必须做，但需要先补后端或先降级

| # | 事项 | 前置 | 说明 |
| --- | --- | --- | --- |
| P1-1 | **状态语义统一**（`pending/running/completed/skipped/failed/cancelled` + 结果态 `partial/needs_clarification/refused`） | 无 | 后端 `job.status` 是 6 值，`stage` 是 8 值；**`skipped` 要说明原因**（后端有 `skip_reason`），规范 §4.2 要求 |
| P1-2 | **状态色 + 图标 + 文字三者同现** | 无 | 规范 §4.2：不能只靠颜色。现有组件里 `ScoreBadge` 可复用思路 |
| P1-3 | **开发视图 / 审计视图分离** | 无 | `canViewInternalDiagnostics` 已有，但要保证**切换角色时清除已缓存详情**（规范 §4.1） |
| P1-4 | **降级显著提示** | 无 | `trace.degraded` / `failure_stage` / `fallback_reason` 已在响应里，前端没展示 |
| P1-5 | **文档接入页**（上传 → 任务进度 → 版本 → 重处理 → 重建索引） | 需 `knowledge_base_id`（用种子脚本），且**需要 Redis 才能自动处理** | 见 §7.4 |
| P1-6 | **解析检查台**（原件↔结构对照） | ❌ 后端无页级/坐标/blocks | 现在只能做「任务 + 警告 + 解析器版本」最小版 |
| P1-7 | **检索实验台**（A/B 配置对照） | ⚠️ 后端只有一个 query 参数，无配置切换 | 只能做单例探索；且正式路径**没有 `mode`** |
| P1-8 | **`/ops/metrics` 的诚实展示** | 无 | 必须标注数据来源（A 轨 chat 会话）、样本量、时间窗 |
| P1-9 | **指标卡片统一结构**（`metric_id/口径/样本/版本/状态/缺失原因`） | 无 | 规范 §10.1；没有后端聚合时，卡片内容主要是「N/A + 原因」 |
| P1-10 | **反馈绑定 response 版本** | ⚠️ 后端 `POST /feedback` 用 `request_id`，无版本概念 | 现状可接受，但要记录这个差异 |

## 5.3 P2 · 有余力再做

- Markdown/HTML 渲染消毒（规范 §13）：文档片段、工具结果都是不可信内容；
- 导出防公式注入（CSV）；导出 JSON 时剔除非授权字段；
- 无障碍：键盘可达、焦点回到打开抽屉的控件、`prefers-reduced-motion`（前端 `PRODUCT.md` 已承诺，需核对实现）；
- 大列表虚拟化（检索候选、chunk 列表）；
- 引入 Vitest + Testing Library，把「一次聊天只发一个 chat 请求」「message 不含订单号」写成测试
  （前端 sync plan §4 列了 10 条关键测试，**一条都还没有**）。

## 5.4 反向清单：**现在不要做**的事（做了就是自欺）

| 不要做 | 原因 | 依据 |
| --- | --- | --- |
| SSE 流式解析 / 打字机 / 断线续传 / `Last-Event-ID` | 后端**没有**任何流式端点，且规范 §9.1 有意选择「缓冲后核验」 | §3.6 |
| 「停止生成」按钮 | 无取消接口；浏览器断开 ≠ 模型停止（规范 §11.4 明写） | §3.6 |
| 「首安全正文延迟」指标卡 | 非流式，该量不存在 | §2.3 |
| Dense/BM25/RRF 分数卡 | 后端无稀疏检索、无 RRF（13/14 字段不存在） | §3.4 |
| Query Rewrite 展开视图 | 后端无改写字段 | §3.4 |
| Agent 节点 / attempt=2 视图 | 后端无 Agent 编排 | §3.4 |
| 「AI 可信度 98%」总圆环 | 规范 §4.3 明文禁止 | 规范 §4.3 |
| 引用点击 → 原件高亮定位 | 后端**无** `/evidence/{id}`、无坐标、无签名资源 | §6.1 |
| 评测中心（数据集 / 批量评测 / 门禁） | 后端无评测执行接口、无指标引擎 | §6.1 |
| 把 `/ops/metrics` 当成 D01–D24 的实现 | 口径完全不同（A 轨 chat 维度，非按阶段/配置切片） | §2.3 |

---

# 第六部分 · 后端缺口清单（前端要显示但拿不到的东西）

> 规范 §18.2 要求「每个展示字段对应后端来源、缺失状态、授权范围和更新时机」。
> 下面按「缺接口」和「缺字段」两层列，**这是前端排期时不该被埋掉的部分**。

## 6.1 完全缺失的接口

| 规范建议接口 | 现状 | 影响的前端能力 |
| --- | --- | --- |
| `GET /api/capabilities`（能力协商） | ❌ 不存在 | 前端无法按真实能力显示「可用/未接入/无权限」；**只能靠本文 §7.4 的静态环境矩阵** |
| `POST /api/queries` + `GET /api/queries/{id}` | ❌ 不存在（只有 `/chat/prompt` 一次返回 + `/chat/history` 恢复） | 无 `query_id` / `event_cursor` / 权威快照重取 |
| `GET /api/queries/{id}/events`（SSE） | ❌ 有意不做 | 见 §3.6 |
| `POST /api/queries/{id}/cancel` | ❌ 不存在 | 无取消、无「已停止」状态 |
| `GET /api/traces/{id}`（诊断详情） | ❌ 不存在 | 诊断数据只能从 `/chat/prompt` 当次的 `trace`/`full_trace` 拿。`/chat/history` 的 `latest_response` 会**完整回放最近一次**的响应 JSON（`conversation_turns.response_json`，含 `trace`/`full_trace`），但**只有最近一次** —— **更早的请求诊断取不回来，也没有按 `request_id` 取任意一次的能力** |
| `GET /api/evidence/{id}`（引用与源定位） | ❌ 不存在 | **引用预览、原件高亮、读取时重新授权全部做不了** |
| `GET /api/metrics`（聚合指标） | ❌ 不存在 | M01–M28 几乎全不可在线展示 |
| `POST /api/evaluations` + `GET /api/evaluations/{id}` | ❌ 不存在（只有 `scripts/evaluate_*.py` 离线脚本） | 评测中心做不了 |
| `POST /api/releases`（含门禁与审计） | ❌ 不存在（只有 `GET /release/checklist` 只读检查 + 三条各自独立的"发布"） | 「配置与发布」页只能做只读检查 + 手工触发既有端点 |
| `POST /api/ingestion-jobs/{id}/retry` | ⚠️ 语义等价物是 `POST /documents/{id}/reprocess`（**新建 job**，不是重试原 job） | 前端动作名要写对：「重新处理文档」而非「重试任务」 |
| 知识库列表 / 创建 | ❌ 不存在 | **上传功能没有可选的库**（只能硬编码 `knowledge_base_id`） |
| 撤权 / 删除传播 / 权限测试入口 | ❌ 不存在 | 安全治理只能看审计流水 |

## 6.2 字段级缺口（这些是「规范要求的展示项，后端没这个字段」）

| 展示项 | 规范要求 | 后端实际 | 前端只能 |
| --- | --- | --- | --- |
| 三个状态字段 | `processing_status` / `quality_status` / `publication_status` 分开（规范 §7.3） | ⚠️ **只有单一 `status`**（`DOCUMENT_STATUSES` 9 值混装：`received…published/archived/failed/duplicate`） | 按值猜语义并**在 UI 上注明这是合并状态**，不要谎称有三维 |
| 页级解析方法 | 页级 `native/OCR/layout/vision/manual` + 解析器版本 + 耗时（规范 §7.2） | ⚠️ 只有**版本级** `parser_name/parser_version` | 显示版本级，**不显示"第 8 页走 OCR"** |
| 解析坐标 / bbox / 阅读顺序 | 规范 §6.5 | ❌ 无 | 双栏对照做不了 |
| 声明—证据（claim/evidence） | 规范 §6.4 核验面板 | ❌ 无 claim 概念（有 `evidence_citations` 但无 supported/conflicting 判定） | 只能显示"引用了哪些证据" |
| 槽位（`required_slots` / `unresolved_slots`） | 规范 §4.2、§5.3 | ❌ 无 | 澄清交互做不了（只能显示整段回答） |
| 子任务 / 任务图 | 规范 §4.2 | ❌ 无（只有 `intent_analysis` 的多意图标签） | 只能显示意图标签 |
| span / 阶段耗时瀑布 | 规范 §6.3 | ⚠️ `full_trace` 是**步骤列表**（`step/status/latency_ms/input_summary`），不是 span 树；**无 start/end、无父子、无 attempt** | 可做"步骤列表 + 耗时"最小版，**不能做瀑布图** |
| 配置版本 / 知识快照 ID | 规范 §5.4 | ⚠️ 无 `config_version` / `knowledge_snapshot_id`；接近量是 `index.index_version`、`prompt_version` | 显示实际有的，**不要造字段名** |
| 成本（金额） | 规范 §10.2 金额估算 | ❌ 无价表、无金额；`token_usage` 还可能为空 | 只显示 token + 「无价表」 |
| `first_safe_body_latency_ms` | M19 | ❌ 非流式，不存在 | N/A |

## 6.3 与「本项目进度」无关的规范条目（明确不做，别当欠账）

以下不是遗漏，而是**项目定位决定的**：

1. **多租户 SaaS 化**：单机 SQLite，租户/ACL 已做但只有开发数据；
2. **真实引擎复验**（PostgreSQL / Redis / 真 tokenizer + embedding / OCR）：本机无 Docker/psql/Redis；
3. **评测数据集 300 例 gold**：属独立批次；
4. **知识图谱 / 自由 Agent / 语义缓存**：规范 §1.2 与 R12 都标为「证明收益后再做」；
5. **A 轨路径加权限**：它读的种子语料本来就没有租户维度，加过滤是**假安全**（B7 已拍板不做）。

---

# 第七部分 · 本地联调（命令 2026-09-23 全部实跑过）

## 7.1 后端：四步起服务

```bash
cd /d/llm/llm-customer-service

# 1) 迁移开发库（★ 跳过这步，所有涉及 chunk/任务/审计的接口都会 500 "no such table"）
./venv/Scripts/python.exe -m alembic upgrade head

# 2) 种租户 / 用户 / 知识库（★ 跳过这步，上传会 404 "知识库不存在"，
#    重建索引会 500 FOREIGN KEY constraint failed —— 审计写入缺 tenants 行）
./venv/Scripts/python.exe scripts/seed_dev_tenant.py
#    → 记下打印出来的 knowledge_base_id

# 3) 设密钥 + 起服务（密钥必须与下一步签令牌时一致；HS256 至少 32 字节）
export RAG_JWT_SECRET='dev-secret-please-change-me-32bytes+'
./venv/Scripts/python.exe -m uvicorn main:app --reload

# 4) 另开一个终端签令牌
./venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --format decoded
#    或 --format env   → 输出可直接贴进前端 .env.local 的片段
#    或 --format curl  → 输出一条带令牌的 curl
```

**令牌按角色签**（`agent` / `supervisor` / `knowledge_ops` / `qa` / `admin`），
脚本会把该角色能用的 scope 全列出来，便于核对权限行为。

## 7.2 前端：起 dev server

```bash
cd /d/llm/front
# .env.local（不要提交）
# VITE_API_BASE_URL=http://127.0.0.1:8000
# VITE_DEV_TOKEN=<mint_dev_token.py --format token 的输出>
npm run dev
# 必须访问 http://127.0.0.1:5173 或 http://localhost:5173
# ⚠️ 用局域网 IP（如 192.168.x.x）访问会因 CORS 白名单被拒
```

## 7.3 最小验证清单（每条都实测过，可直接当验收用）

| # | 操作 | 期望 | 2026-09-23 实测 |
| --- | --- | --- | --- |
| 1 | 无令牌 `GET /model/info` | 401 `缺少 Authorization 头` | ✅ 一致 |
| 2 | 发 `X-User-Role: admin` 不带令牌 | **401**（不是放行） | ✅ 一致 |
| 3 | 带 admin 令牌 `GET /model/info` | 200 | ✅ 一致 |
| 4 | agent 令牌 `GET /knowledge/items` | 403 `缺少所需权限 read:knowledge_read` | ✅ 一致 |
| 5 | `POST /retrieval/search`（未建索引） | 503 `chunk_index_unavailable` | ✅ 一致 |
| 6 | `GET /ingestion-jobs/不存在的 id` | 404 `任务不存在` | ✅ 一致 |
| 7 | `GET /documents/不存在的 id` | 404 `文档不存在` | ✅ 一致 |
| 8 | 上传到不存在的知识库 | 404 `知识库不存在` | ✅ 一致 |
| 9 | 种完种子后上传一份 md | 202，`job_status=pending` | ✅ 一致 |
| 10 | `POST /ingestion/indexes/rebuild`（空库） | 200 `skipped=true, skip_reason="no_chunks"` | ✅ 一致 |
| 11 | `POST /chat/prompt` | 200；本机走降级，`trace.degraded=true` | ✅ 一致 |
| 12 | 13 个只读端点（model/info、retrieval/config、examples、knowledge/items、publish-history、prompt/active、prompt/versions、audit/logs、ops/metrics、feedback/recent、release/checklist、chat/history、health） | 200 | ✅ 全部 200 |

## 7.4 本机环境限制（**前端的"能力协商"就靠这张表**）

后端**没有** `GET /capabilities`，所以这张表是当前唯一的能力来源。
（★ 建议后端在 B8 或之后补这个端点 —— **当前它不存在，不要假设有**。）

| 能力 | 本机现状 | 说明 |
| --- | --- | --- |
| 鉴权 / 上传 / 任务查询 / 文档版本 / 重处理 / 索引重建 | ✅ 可用 | 迁移 + 种子之后 |
| 知识条目运营 / Prompt 版本 / 审计 / 发布检查 / 订单状态 / 反馈 | ✅ 可用 | 实测 200 |
| **正式 chunk 检索** | ⚠️ **空库时 503；一旦有 chunk 就会 500** | `venv` 缺 `sentence-transformers`（连带 `torch`/`transformers`），production embedder 无法加载 → 建索引阶段失败 |
| **A 轨种子 FAQ 检索 / prompt-preview** | ❌ **500** | 同上，缺模型依赖 |
| **`/chat/prompt` 真实生成** | ⚠️ 返回 200 但**走降级** | `trace.fallback_reason=generation_failed: local generation dependencies are unavailable`；这是**正确的降级行为**，不是缺陷 |
| 上传 → 自动处理 → 可检索 | ❌ **不闭环** | 无 Redis 时队列是**进程内**的（`services/ingestion/queue.py:105`），API 进程投递的消息**独立 worker 进程看不到**。实测：上传后跑 `worker --once`，任务仍停在 `pending/received/document_version_id=null` |
| 真实引擎（PostgreSQL / Redis / OCR） | ❌ 未复验 | 本机无 Docker / psql / Redis |

**要跑通完整闭环（上传 → 解析 → 建索引 → 检索），两条路：**

- **路 A（推荐）**：起一个 Redis，设 `RAG_REDIS_STREAM_URL=redis://127.0.0.1:6379/0`，
  再跑 `./venv/Scripts/python.exe -m services.ingestion.worker`；
- **路 B（无 Redis）**：补装模型依赖后在**同一进程**里调 `services.ingestion.pipeline.process_job`，
  或直接看 `tests/test_release_gate.py`（B8 将新增的四格式端到端测试）的实现方式。

**补装模型依赖**（想让检索真的返回结果时）：

```bash
./venv/Scripts/python.exe -m pip install --no-cache-dir \
  --trusted-host mirrors.aliyun.com -i https://mirrors.aliyun.com/pypi/simple/ \
  sentence-transformers
```

> 本机 pip 必须显式指定 HTTPS 源 + `--trusted-host`（见台账第 5 节环境坑 3）。

---

# 第八部分 · 验收与纪律

## 8.1 前端可达成的验收判据（从规范 §17.1 里挑出**现在能做的**）

| 编号 | 场景 | 前端通过标准 | 现在能做吗 |
| --- | --- | --- | --- |
| F01 | 正常文档问答 | 显示回复 + 证据 + `retrieval_path` | ⚠️ 检索部分需路 A/B 才能真跑通 |
| F02 | 多意图一项缺槽位 | ❌ 后端无槽位概念 | ❌ 做不了 |
| F04 | 有候选但缺关键事实 | 显示"证据不足"，不把高 rerank 当正确答案 | ⚠️ 只能显示 `evidence_citations` 为空 |
| F09 | SSE 重复/乱序/断线 | —— | ❌ 无不适用（无流式） |
| F10 | 取消与完成同时到达 | —— | ❌ 无取消接口 |
| F13 | gold 不存在 / 评测报错 | 显示 N/A 或 error，**不显示 0 分或 100 分** | ✅ **能做，且必须做** |
| F15 | 并行阶段 | 总时长按请求测量，非 span 求和 | ⚠️ 无并行阶段概念；用 `trace.latency_ms` |
| F16 | 低权限访问 trace/export | 后端 403（不是靠隐藏按钮） | ✅ 后端已保证；前端需正确呈现 403 |
| F17 | 新索引失败 | 旧快照仍可问答，失败版本可诊断 | ⚠️ 后端有原子切换；前端需显示失败态 |
| F18 | token 或账单数据缺失 | 标明缺失/估算，**不生成虚假精确金额** | ✅ **能做，且必须做**（`token_usage={}` 实测） |
| F19 | 页面大数据 | 分页/虚拟化可用，输入不卡 | ⚠️ 需自测 |
| F20 | 同一指标跨页 | 时间窗、口径、配置一致时数值一致 | ⚠️ 现在只有 `/ops/metrics` 一处数据源 |

**留证要求**（规范 §17.2）：不允许只截一张"页面漂亮"的图。
至少留存：一次真实成功请求、一次真实失败（500/503）、一次降级（`degraded=true`）、
一次 403 权限拒绝、一次空态（零命中）、一次 `token_usage` 缺失。

## 8.2 状态色与状态词表（前端统一成一个模块，别散落各处）

| 语义 | 色 | 状态值 |
| --- | --- | --- |
| 成功 | 绿 | `succeeded` / `published` / `completed` / `pass` |
| 进行中 | 蓝 | `pending` / `running` / `building` / `received`…`indexed` |
| 待澄清 / 警告 | 黄 | `retrying` / `needs_review` / `warn` / `marked_bad_case` |
| 失败 / 拦截 | 红 | `failed` / `blocked` / `fail` |
| 未运行 / 不适用 | 灰 | `skipped`（**必须带原因**）/ `cancelled` / `duplicate` / `archived` |

三个原则（都有规范依据）：

1. **颜色 + 图标 + 文字三者同现**，不能只靠颜色（§4.2）；
2. **安全拒绝是正常策略结果**，不与「系统崩溃」共用"请求失败"（§4.2）；
3. **`skipped` 必须说明为什么**，不能当"0 毫秒成功"（§4.2）——
   后端已提供 `skip_reason` / `warnings[].code`，用起来。

## 8.3 演示数据纪律

- 规范 §5.6 的多意图示例是**交互示例**，**不能**出现在运行概览里当真实请求数据；
- 用 fixture 验证 UI 时必须**全页可见标"演示数据"**，且不与真实统计混合（规范 §13）；
- `docs/frontend/capabilities` 这类能力表如果不是从接口来的，要标注**"来自文档，非接口"**。

## 8.4 记录纪律（沿用本项目既有五步闭环）

前端侧同样要留下证据：**改了什么文件、跑了什么命令、得到什么输出**。
关键节点（每次联调验收）写进前端仓库自己的文档，不要只留在聊天里。

---

# 第九部分 · 已知限制与声明（不许含糊）

1. **本文的所有"已实测"结论基于 2026-09-23 的仓库状态**，且运行在**轻量 venv**（缺
   `sentence-transformers` / `torch` / `transformers`）上。真实模型环境下的行为**未复验**；
2. **无 Docker / PostgreSQL / Redis**：租户与 ACL 逻辑在 SQLite 上验证过，真实引擎未复验；
3. **后端阶段 7（发布门禁）未完成**，索引回滚一族函数**零调用点、零测试**
   （台账 §0.4），因此**任何"发布门禁通过"的说法现在都不成立**；
4. **`GET /capabilities` 不存在**，本文 §7.4 的能力矩阵是人工核对结果，会随环境漂移；
5. **`docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` 缺 §17**（修订说明声称有），引用前先核实；
6. **前端没有测试框架**，§5.3 列的测试一条都没写；
7. 本次为跑通联调，**已在本机执行 `alembic upgrade head` 并种入开发数据**
   （`data/rag_metadata.db`，被 `.gitignore` 的 `*.db` 覆盖，不进版本库）；
   这是**环境状态变更**，如实记录在此；
8. 本次**没有做任何 git 提交/推送**（那是用户的事）。

---

# 第十部分 · 下一步（给接手方的动作清单）

**前端侧（按顺序）**

1. 读 §3（契约真相）→ 确认 P0-1~P0-4 的范围；
2. 起后端（§7.1）→ 跑 §7.3 的 12 条验证 → **先让 401 变成 200**；
3. 修 `client.ts`（Bearer + `error_code`）→ 删 11 处 `X-*` 头；
4. 改聊天流程（`message` 只发原问题、只打一个 chat 请求）+ 检索面板换接口（§5.1 P0-2）；
5. 引入 `backend_contract_types.ts`，删掉手抄字段；
6. 做状态语义模块（§8.2），把 `degraded` / `token_usage` 缺失 / `skipped` 原因补齐。

**后端侧（如果要让前端多做几件事）**

| 优先级 | 事项 | 收益 |
| --- | --- | --- |
| 1 | `GET /capabilities` | 前端不再靠文档猜能力（规范 §11.1 明确要求） |
| 2 | 文档 `processing/quality/publication` 三状态拆分 | 知识库页面才符合规范 §7.3 |
| 3 | `GET /traces/{id}` 或让 `/chat/history` 回放完整诊断 | 请求诊断页才能"重新打开" |
| 4 | Redis 队列落地 + worker 进程编排（台账 §0.5 的 F5） | 上传→可检索闭环才成立 |
| 5 | `/evidence/{id}`（带重新授权） | 引用预览才有可能做 |
| 6 | 完成阶段 7（B8） | 发布门禁才有结论 |
| 7 | 把 `data/object_store/` 加进 `.gitignore` | **实测缺口**：`.gitignore` 忽略了 `data/faiss_store/`、`data/knowledge_backups/`、`*.db`，**漏了对象存储目录**，上传一次就会让 `git status` 多出未跟踪目录 |

---

## 附：本批新增/修改文件一览（2026-09-23）

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `docs/FRONTEND_HANDOFF_AND_ALIGNMENT.md` | 新增 | 本文 |
| `docs/FRONTEND_AI_HANDOFF_PROMPT.md` | 新增 | **交接提示词**（可直接粘贴给前端窗口的 AI；含短版粘贴块。每次前端交付批次结束须重写） |
| `docs/frontend/FRONTEND_API_CONTRACT.md` | 新增（生成） | 接口与字段字典，2335 行 |
| `docs/frontend/openapi.json` | 新增（生成） | 契约源，39 路径 / 60 模型 |
| `docs/frontend/backend_contract_types.ts` | 新增（生成） | 前端可直接复制的 TS 类型，645 行 |
| `scripts/export_frontend_contract.py` | 新增 | 生成器（含端点鉴权登记表） |
| `scripts/mint_dev_token.py` | 新增 | 开发态 JWT 签发 |
| `scripts/seed_dev_tenant.py` | 新增 | 开发态租户/用户/知识库种子 |
| `data/rag_metadata.db` | 环境状态变更 | 已迁移建表并种入开发数据（gitignored） |

门禁自检（2026-09-23 实跑）：

```text
./venv/Scripts/python.exe -m ruff check scripts/        → All checks passed!
./venv/Scripts/python.exe -m compileall -q <三个新脚本>  → 退出码 0
```
