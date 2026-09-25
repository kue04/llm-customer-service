# RAG 改造：交接与作业规程（统一提示词）

> **交接时间**：2026-09-25（星期五）13:08 ·
> **交接时状态**：主线 **B1~B8 全部完成、阶段 0~7 全部 PASS、发布门禁 PASS**；
> **B 轨真混合检索已建成并给出实测数字**（稠密 + 稀疏 FTS5 双路 + 加权 RRF，默认仍 `dense`）；
> **F1 切轨已于 2026-09-25 完成** —— 聊天问答走 chunk 索引（9229 个 chunk 可检索）；
> **⚠️ 工作区里叠着两批未提交改动（F1 + B8-混合检索）**，清单见台账 §4 暂停点。
>
> **★ 最新状态一律以「第五部分」为准**（2026-09-25 B8-混合检索交接）——
> 前四部分是历史分层，其中标注的测试数 / 待办顺序 / 「检索质量无结论」等描述**已被第五部分覆盖**。
>
> **两种用法（推荐①）**：① 在新窗口说一句「读 `docs/RAG_NEXT_WINDOW_PROMPT.md`，按里面的规程继续」——
> 不会因复制截断而失真；② 把本文**整份**粘贴过去（适用于不能读本地文件的工具）——
> **不要只贴「第 2 步」的工作项清单**，第 0.4 节的**引用纪律**才是最容易踩错的地方。
>
> **⚠️ 先看日期再动手**：上面这个时间戳是判断本文是否过期的**唯一依据**。
> 若实际仓库状态与本文不符（例如全量测试不再是 `1004 / 0 failures`，或工作区已干净且已推送），
> 说明有人推进过了 —— **先按第 0 步核对，不要照着本文盲改。**
>
> **本文已收录**：系统全景、进度快照、**发布门禁的三条限制**、**F1 切轨的进展与 B17 量化**、
> **B8 混合检索的实测结论与它的反直觉之处**、**前端对齐批次的进展与待办**、
> 候选工作项、五步作业规程、硬性约束、环境坑速查、自检清单。
> 需要更深细节时按文中路径读原始文档（那是权威来源）。

---

# 第一部分 · 开工前必读：先把系统搞清楚

> **项目主线已经收尾**：按 `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 的
> 阶段 0~7（B1~B8）**已全部走完**，发布门禁已出具 PASS。
> 所以接下来的工作**不再是"按计划推进批次"**，而是从第 2 步的候选清单里挑事情做。
> 最容易犯的错是：**把"还能更好"当成"还没完成"**，或者反过来 ——
> **把 PASS 读成"什么都验收了"**（见 0.4 的三条限制）。

## 0.1 这个项目是什么

外卖售后场景的**客服 RAG 后端**（FastAPI + SQLite/PostgreSQL + FAISS）。

对外主链路是「检索增强问答 + 客服工作流」：意图识别 → 检索 → 重排 → 证据分级 →
生成 → 规则兜底 → 风险分级 → 转人工。已经有 **12 个 router、949 条测试**（39 个测试文件）。

**理想目标**写在 `docs/goal.md`（企业级 RAG：数据治理 / 权限控制 / 检索 / 生成 /
验证 / 追踪 / 反馈闭环）。它是**方向**，不是任务清单；
目标与现状的差距分析见 `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md`。

## 0.2 ★ 双轨制已经合流（B7 + F1）—— 两条链路现在都是真的

「两条链路都叫检索，没人知道哪条是真的」（F1）分两步合上：
**B7（2026-09-23）合上检索 API 侧**，**F1 切轨（2026-09-25）合上聊天侧**。
现在只有「演示 / 兼容接口」还是种子 FAQ，主链路（聊天 + 检索 API）都走 chunk 索引。

```
【B 轨 · 正式检索路径】✅ B7 已接线
  POST /retrieval/search
    → require_read_operation_role（retrieval_read）
    → build_chunk_access_filter(session, auth)        ← services/retrieval_access.py（唯一生产构造点）
    → retrieve_chunk_items(query, access=access, ...) ← FAISS 原生预过滤
    → 响应带 retrieval_path="chunk-index" + index.visible_chunk_count

【聊天链路】✅ F1 切轨（2026-09-25）
  POST /chat/prompt
    → require_read_operation_role（chat_generate）
    → get_answer_from_rag(request, auth)              ← 2026-09-25 起新增 auth 入参
    → resolve_chat_retrieval_path()                   ← 默认 "chunk"；RAG_CHAT_RETRIEVAL_PATH=seed 可回退
    → retrieve_chunk_items_for_chat() → build_chunk_access_filter（fail closed，无身份即零命中）
    → adapt_chunk_items_for_prompt()                  ← 把 chunk 的 text 映射成下游认得的 answer

【A 轨 · 演示 / 兼容路径】
  POST /retrieval/search-demo、POST /retrieval/prompt-preview
    → retrieve_by_real_vector（781 条种子 FAQ，数据源本身没有 tenant/ACL 两维）
    → 响应带 retrieval_path="seed-faq-demo"
```

**F1 切轨的三条硬约束（动手前先读，别推翻）**：

1. **默认轨道由 `services/chat_service.py` 的 `DEFAULT_CHAT_RETRIEVAL_PATH` 决定**。
   改它就是里程碑事件：README 的 `f1-track` 锚点必须同步改，两边由
   `tests/test_ingestion_pipeline.py::TestReadmeTrackConsistency` **双向校验**
   （判据已换过一次，为什么换见踩坑 **D21**）；
2. **零命中不回退 A 轨**（fail closed）。「拿不到身份」与「没有权限」都必须是零命中 ——
   为了让用户别看到空回答而加回退，等于绕过 ACL（承 D-12 / D-13）；
3. **`build_prompt_context_items` 的判空守卫不许改松**。B 轨的正文在 `text`，
   适配层负责映射成 `answer`；让下游去兼容两套字段 = 用降低契约强度换兼容。

**这条接线现在有测试锁着**（B8-2 新增 `tests/test_wiring_guards.py`，18 条）：
`routers/retrieval.py` 必须**引用并调用**那两个函数、`search_chunk_index` 的 `access`
**无默认值且注解非 Optional**、`routers/*.py` **不得 import jwt**。
**别改这些约束** —— 改了守卫会红，而它红得对。

**索引是全局一份**（B14 的教训，B7 修掉）：`rebuild_index` 默认 `all_tenants=True`，
版本号**全局**递增，manifest `extra["scope"] / extra["tenants"]` 留痕。
**不要退回「按租户分片」** —— 那会让先入库的租户**静默消失**（B7 之前的状态）。

**索引回滚已经接线**（B8-1 新增）：`POST /ingestion/indexes/rollback`（权限
`index:rollback`，与 `index:rebuild` **分开成键**，虽然当前角色集合相同）。
它只改指针、不重建不删文件；**目标版本不可达时指针保持不动**（有 8 条测试锁着）。

## 0.3 目录地图（哪条是主链路）

```
routers/
  retrieval.py      ★ B7 重写：/search（B 轨）+ /search-demo、/prompt-preview（A 轨）
  documents.py      上传 / 详情 / 版本 / 任务查询
                    + POST /ingestion/indexes/rebuild（B7 新增）
                    + POST /ingestion/indexes/rollback（★ B8 新增，需 index:rollback）
services/
  auth_context.py   JWT 身份上下文 + 三张授权表（含资源维度 RESOURCE_SCOPE_ROLES，**10 个权限**）
  auth_service.py   FastAPI 依赖层 + require_resource_scope
  retrieval_access.py ChunkAccessFilter 的**唯一**生产构造点
  intent_service.py ★ 前端对齐批次改写：修饰词归一化 + 上下文继承 + clarify 路由
  chat_service.py   ★ 前端对齐批次改写：full_trace 四个 step 的 metadata 补实
  ingestion/        models / repository / pipeline / worker / queue / index_builder / index_manifest
utils/
  vector_retriever.py  A 轨 retrieve_by_real_vector + B 轨 search_chunk_index / retrieve_chunk_items
scripts/
  verify_trace_fix.py  ★ 前端对齐批次新增：/chat/prompt trace 端到端验收（令牌走环境变量）
tests/
  retrieval_fixtures.py       ★ B7：确定性假 embedder + RetrievalEnv（两租户入库/重建/授权一条龙）
  test_retrieval_isolation.py ★ B7：检索层隔离
  test_tenant_isolation.py    数据层 + 真实路由层
  test_retrieval_api.py       API 出口层
  test_index_rollback.py      ★ B8-1：回滚 8 条
  test_wiring_guards.py       ★ B8-2：接线 AST 守卫 18 条
  test_release_gate.py        ★ B8-3：四格式端到端 7 条（**项目首条从 HTTP 到检索结果的测试**）
  test_chat_retrieval_track.py ★ F1 切轨：轨道解析 4 + 形状适配 5 + 分发 3 + 端到端 3 = 12 条
  test_intent_and_safety.py   ★ 前端对齐批次：新增 `IntentContextAndRoutingTest` 9 条
```

## 0.4 ★★ 发布门禁 PASS，以及它的**三条限制**（引用时必须一起带上）

阶段 7（发布门禁）结论：**PASS**（`stage7_review.txt`）。判据：
7.1 四件套全绿 / 7.2 四格式端到端通过 / 7.3 六条逐项可指认 / 7.4 六条成立；
当时的全量 **918 / 0 failures / 0 errors / 0 skipped**，warning 5 条未新增。

**三条限制（漏掉任何一条都是过度声明）**：

1. **判据范围只到计划第 7 节的四条**。它**不覆盖**：检索质量指标（M10 Recall@k 等，
   口径未就绪）、真实 OCR 引擎（当前只有接口 + 阈值判定）、病毒扫描与限流（未实现）、
   压测与 P95/P99（未做）、成本账本（未实现）。这些在
   `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` §1.0 / §10.5 里分别标为
   `未实现` / `未就绪`，是**已知差距清单**。
2. **roadmap 建议归入 B8 的两条 P3 未做**：真实 OCR 引擎复验、压测与 P95/P99 聚合。
   它们是 roadmap 8.2 的**建议**，不在计划 7.4 判据里，故不阻断 PASS，但如实标注。
3. **检索质量无结论**。`test_release_gate.py` 验证的是**链路连通性**不是检索质量
   （它的 query 取自待检索文档自己的 chunk）。**不得把 PASS 读作「检索效果已验收」。**

> **规范引用纪律**（验收规范 §17.5）：规范编号（`I01~O01`、`§N`）与本项目编号
> （`B1~B8`、`阶段 N`）是**两套**，引用必须带前缀；
> 且规范 §7.x 与执行计划「阶段 7.x」**编号撞车但内容不同**。
> 规范里标 `未实现 / 未就绪 / 已建未启用 / 未演练` 的 13 项是**差距清单**，不是成绩单。

## 0.5 ★ F1 切轨（2026-09-25）—— 本次最新进展

**台账**：`docs/RAG_EXECUTION_PROGRESS.md` §3 的 `[F1 切轨]` 条目（§4 暂停点已覆盖更新）。

做了什么：聊天问答从 A 轨（781 条种子 FAQ）切到 **B 轨（chunk 索引）** ——
**9229 个 chunk 终于进得了聊天**。带 `RAG_CHAT_RETRIEVAL_PATH=seed` 回退开关。
交付物清单与设计取舍见台账条目，这里只留**新窗口必须知道的四件**：

1. **默认轨道 = chunk**，由 `DEFAULT_CHAT_RETRIEVAL_PATH` 决定，README 锚点与它双向校验（见 §0.2）；
2. **零命中不回退 A 轨**（fail closed）—— 别为了「别让用户看到空回答」而加回退；
3. **`scripts/evaluate_chat_grounding.py` 已显式锚定 seed** ——
   它的用例是 A 轨 `expected_intent` 口径，不锚定就会以「无身份 + B 轨」静默跑成**整片空**，
   而空结果看起来像「模型答不出来」，不像配置问题；
4. **切轨把 B17 的代价暴露出来了**：约 **29%** 的 Top-K 名额被父子块重叠占掉
   （证据 `reports/rag_ingestion_auth_review/b17_topk_diversity_20260925.json`）。
   **这是下一步最该做的事**，见本文件 B 部分。

**除此之外，前端对齐批次（2026-09-23）的三件事仍然卡着**：

1. **界面复核没做** —— 字段在 JSON 里出现 ≠ 前端真的用上了。
   要前端跑 `D:\llm\front\docs\diag_panel_probe.cjs` **出截图，看到界面内容才算完成**；
2. **B6 第二步等老霸拍板** —— 往 `食品安全投诉`（`risk_level="high"`）加「不新鲜」关键词，
   会把用户整句话抬成 high risk 链路（安全前缀 / 可能转人工）。
   **属业务策略不是技术修复，不许擅自加**；
3. **前端补 1 行词表** —— `src/lib/status.ts` 的 `ROUTING_TEXT` 增加
   `clarify: "澄清链路（置信度过低，应先向用户确认诉求）"`。

**⚠️ 两条容易误读的事实**：

- **`routing` 不是链路开关**：它在后端**零分支消费**
  （只有 `chat_service.py:638` 透传 + `:925` 展示）。加了 `clarify` 之后
  **链路仍照常检索生成**，只是界面多一个可辨识的信号。要让链路真的澄清，
  需要新增分支代码 + 前后端契约再对齐一轮（踩坑 **B16**）。
- **继承来的意图置信度恒为 0.6**，且带 `inherited_from_context` 标记；
  直接命中不受影响（最低一档 0.76）。这个「低于直接命中」的分层是刻意的，不要抹平。

## 0.6 已知但**范围外**的问题（登记在此，防止被遗忘）

| # | 问题 | 状态 | 归属 |
| --- | --- | --- | --- |
| 1 | **F2**：`scripts/evaluate_retrieval_metrics.py:85` 按 intent 判相关，不是文档级指标 | 未修 | 修完才谈得上 M10 Recall@k |
| 2 | ~~**F4 残留**~~：README 测试数已统一改 JUnit 口径（**949**，2026-09-25）| **已消** | 复现：`scripts/update_readme_testcount.py` |
| 3 | **F5**：worker 有 CLI 无进程编排，容器化后队列没人消费 | 未修 | 已在门禁结论里声明为已知限制 |
| 4 | 真实引擎未复验（PostgreSQL / Redis / 真 tokenizer+embedding / OCR） | 未做 | **PostgreSQL 复验**已在门禁结论里如实标注未做 |
| 5 | 索引重建的并发竞态未测 | 未测 | 单机 SQLite 难构造 |
| 6 | 图片块不进 chunk / PDF 跨页重复表头 | 设计如此 | 后续版本 |
| 7 | 7.3 第 4 条的 **trace / 错误信息**泄漏维度无专项用例 | 未覆盖 | 判为低风险，已记入 `stage7_review.txt` 第六节 |
| 8 | **B6 第二步**（高风控关键词） | **待决策** | 业务策略，等老霸拍板，不许擅自加 |
| 9 | **B7 只补信号不改链路** | 已知落差 | 要真改链路需新增分支 + 契约再对齐 |

---

# 第二部分 · 作业规程（五步闭环，缺一步都不算完成）

> **核对基线 → 读进度与约束 → 干活 → 门禁自检 → 审查 → 记录。**
> 只跑通测试**不算交付**。四处记录**全部必须带日期**（`YYYY-MM-DD`）。

## 第 0 步：核对基线（**不要跳过**，防止在错误基线上工作）

```bash
git status -sb                 # 分支 optimize/interview-ready
git rev-parse HEAD             # 2026-09-25 B8 交接时为 d2ee0d8（F1 + B8 两批改动都**尚未提交**）
./venv/Scripts/python.exe -m pytest -q --junitxml=tmp/baseline_junit.xml
./venv/Scripts/python.exe -c "import xml.etree.ElementTree as ET; \
  s=ET.parse('tmp/baseline_junit.xml').getroot().find('testsuite'); \
  print('tests=',s.get('tests'),'failures=',s.get('failures'),'errors=',s.get('errors'))"
# 当前基线：以 reports/retrieval_hybrid/final_junit_20260925.xml 为准
#   （2026-09-25 B8 交接时 tests=1004 / failures=0 / errors=0 / skipped=0）
# 历史基线（判断"有没有人推进过"时有用）：F1 收尾 949 → B8 之前 936
```

- **⚠️ 取数口径（踩坑 A6）**：**不要**用退出码和 stdout 汇总行判断绿红 ——
  环境删除守卫会在 sessionfinish 吃掉汇总行、把退出码变成 1，**测试其实全绿**。
- **⚠️ warning 条数**取自**全量 stdout** 的 `N warnings`（踩坑 **D15**），
  **不是** `--collect-only`（它不执行测试，输出里没有 warning 汇总）。
  若汇总行被守卫吞掉 → 记 `N/A（汇总行被吞）`，**不许估算或沿用上次数字**。
  历史预期是 **5 条**（fastapi/httpx ×1、starlette/anyio ×1、faiss SWIG ×3）——
  但 **B8 批次全量跑的汇总行确实被吞掉了**（踩坑 A6 复现），所以那一批记的是 `N/A`，**不是 5**。
- **⚠️ 测试数两个口径（踩坑 D18）**：stdout 的 `N passed` **不含 subtest**，
  JUnit XML 的 `tests=` **含**（仓库里唯一一处 `subTest` 在
  `tests/test_evaluate_chat_grounding.py`：5 个坏输入 → XML 比 stdout 多 4）。
  **门禁只认 XML**；对不上账时把新旧 XML 的 testcase 名字做**集合 diff**，不要做减法。
- **⚠️ B8 批次同样没有落 stdout 证据文件**（只落了 `final_junit_20260925.xml`）→
  **不要用「1004 − 4」倒推 stdout 数字**；要报 stdout 就重跑一次，并按 D15 取 warning。
- 若对不上：先查是不是有人推进过（`git log` / 台账 §4），**不要**照本文盲改。

## 第 1 步：读进度与约束（按顺序，不要跳）

1. 台账 `docs/RAG_EXECUTION_PROGRESS.md`：
   §1 分批表（**B1~B8 全部 ✅**）→ §2 审查汇总（**阶段 0~7 全部 PASS**）→
   §3 任务执行记录（**按时间追加，越靠后越新**；最新一条是 `[F1 切轨]`）→
   **§4 执行暂停点（当前指针）** → §5 提交与仓库同步记录（**commit 前必读的环境坑**）；
2. `reports/rag_ingestion_auth_review/stage7_review.txt` —— 发布门禁结论 + 三条限制 + 测试盲区；
3. **前端对齐批次**：`docs/BACKEND_TRACE_FIX_PROMPT_2026-09-23.md`（需求）+ 
   `docs/BACKEND_TRACE_FIX_DELIVERY_2026-09-23.md`（后端交付）+ 台账 §3 该条目；
4. `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`（v2.1）：**§1.0 现状总览**（13 项能力状态）
   与 **§17 验收作业规程**（取数口径 / 结论状态机 / 「已建未启用」判据 / 编号映射表）；
5. 踩坑 `docs/RAG_DEV_PITFALLS.md`（**73 条**）：**A5/A6**（门禁取数）、**D12**（红绿判据）、
   **D15**（warning 口径）、**D16**（守卫会静默变空）、**D17**（坏输入要按层构造）、
   **D18**（测试数两个口径）、**D21**（守卫判据会随架构失效）、**D22**（改默认配置静默打破 mock 型测试）、
   **B16**（routing 不是链路开关）、**C7**（凭据不许硬编码进脚本）、
   **E2**（并行会话下的提交纪律）、**E5**（外部方案要补隐含前提的守卫）、**E9**（半提交）。

## 第 2 步：干活 —— 候选工作项（**不是必须全做，按需挑选**）

> 计划里的 B1~B8 已全部完成，前端对齐批次的后端部分也已改完。
> 以下都是「还可以更好」或「卡在别人手上」，**按价值排序**，
> 每做一项就走完第 3~5 步（门禁 → 审查 → 记录）。

| 优先级 | 事项 | 为什么值得做 | 入口 |
| --- | --- | --- | --- |
| 1 | **前端跨端复核（出截图）** | 提示词 §5.4 明写：**看到界面内容才算完成** | `D:\llm\front\docs\diag_panel_probe.cjs` |
| 2 | **B6 第二步拍板** | 加「不新鲜」会抬高整条链路风险等级，是业务策略 | 问老霸；不许擅自加 |
| 3 | **前端补 `clarify` 词条** | 不补界面会显示「未收录的路由值，词表待补」 | `src/lib/status.ts` 的 `ROUTING_TEXT` |
| 4 | **提交 + 推送** | 主线 6 个提交未推，本批改动也未提交 | 台账 §5（坑 1~4，PowerShell 方言） |
| 5 | ~~**F4：README 测试数**~~ | **已消**（2026-09-25，统一 JUnit 口径 **949**）；**残留**：§3.5 表里「测试文件数 `30`」与实际 **39** 不一致 | `scripts/update_readme_testcount.py <junit.xml>`；README §3.5 |
| 6 | **F2：检索评测口径** | 它是"所有优化决策的判据来源"；不修则 M10 永远给不出可信数字 | `scripts/evaluate_retrieval_metrics.py:85` |
| 7 | **PostgreSQL 复验** | roadmap 8.1 写进了 B8 内容，但计划未要求 → 门禁结论里如实标注未做 | 需要 Docker/psql |
| 8 | **roadmap 8.2 的两条 P3** | 真实 OCR 引擎复验、压测与 P95/P99 聚合 | `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md` 8.2 |
| 9 | **验收规范 §1.0 的其余未实现项** | 解析质量门禁、限流、病毒扫描、成本账本、总 deadline / 背压 / 死信 | 规范 §1.0 的「未实现」行 |

### 硬性约束（每条都有依据，**不是可选建议**）

1. **不要退回「按租户分片」索引**（B14）：`all_tenants=True` 是有意的；
2. **`access` 参数不得加默认值**，注解不得含 `None`/`Optional`；
   `ChunkAccessFilter` 只能服务端构造（唯一构造点 `services/retrieval_access.py`）
   —— 这三条现在有 `tests/test_wiring_guards.py` 锁着；
3. **过滤必须 FAISS 原生预过滤**，不改回「先全局 top-k 再后筛」；
4. **无权限对查询者 = 零命中，缺 filter 对调用方 = 报错**（决策 [D-12]，不要重开）；
5. **ACL 语义三条**（无记录=租户内可见 / `write` 不隐含 `read` / `group` fail closed，
   决策 [D-13]）；
6. **发布、索引重建、索引回滚三者各自授权**（`document:publish` / `index:rebuild` /
   `index:rollback`），角色集合的差异是有意的；
7. **鉴权只走 `services/auth_context.py`**，router 不 import `jwt`；
8. 切分入口签名冻结；删除 chunk 必须单条 DELETE（B13）；读回顺序只信 `metadata_json["ordinal"]`；
9. **`git add` 一律用精确路径**（E2：工作区可能有并行会话的产出，
   `git add docs/ scripts/` 会把它们一锅端）；
10. **前端契约的 metadata 字段名不许改**（改名 = 前端静默回落到兜底值，不报错不告警，最难查）；
11. **不许用默认值掩盖字段缺失**（`confidence` / `long_term_summary` 取不到就**不放键**）；
    `requires_safety_prefix` 必须是 **int 0/1**，传 boolean 前端会静默忽略；
12. **不许擅自往 `食品安全投诉` 加关键词**（会抬高整条链路风险等级，属业务策略）；
13. **凭据不许硬编码进要提交的脚本**（C7）：走环境变量，缺失即报错，**不退化成跳过鉴权**。

### 本批**不做**的事（防 scope creep）

- 不引入新模型 / 新框架 / 新存储（要换必须单独评估并留决策记录）；
- 不动已冻结的决策（[D-6] [D-12] [D-13] 等）：要改必须**补新的决策记录**；
- 不为了让数字好看而放宽门槛（验收规范的判据不因"做不到"而改）；
- **不擅自改业务策略**（关键词表、风险等级、是否需要转人工 —— 都要老霸拍板）。

## 第 3 步：门禁自检（做完必须跑，全绿才能进第 4 步）

四件套，证据落 `reports/rag_ingestion_auth_review/`（**前缀按批次命名**）：

```bash
R=reports/rag_ingestion_auth_review
./venv/Scripts/python.exe -m ruff check .                     > $R/<批>_ruff-output.txt 2>&1
./venv/Scripts/python.exe -m compileall -q main.py routers services schemas utils config scripts tests \
                                                              > $R/<批>_compileall-output.txt 2>&1
./venv/Scripts/python.exe scripts/check_repo_data_size.py     > $R/<批>_data-size-output.txt 2>&1
./venv/Scripts/python.exe -m pytest -q --junitxml=$R/<批>_junit.xml 2>&1 | tee $R/<批>_full_test-output.txt | tail -3
```

**硬性**：四件套全绿；`pytest` 数 **≥ 949**（只增不减）；warning **≤ 5**（A6/D15 口径）；
**一轮只跑一次全量**（A5：守卫按轮计数，重跑会把绿跑成红）。

## 第 4 步：审查（两层，别混）

- **任务级**（每次交付都写）：`reports/rag_ingestion_auth_review/<批>_<主题>_review.txt`。
  至少含：门禁结果 / 逐条对照规格 / 超出规格处**标注方向**（更严 or 更宽，见 B10）/
  中途发现问题与处置 / **结论 + 声明范围**（PASS 或 NEEDS_WORK）。
- **阶段级**（阶段末才写）：**阶段未完成不得写 PASS**；
  **阶段 PASS ≠ 发布门禁 PASS**，两者分别出具、分别声明范围。
- **结论纪律**（规范 §17.2）：只有 `PASS` / `NEEDS_WORK`；
  拿不到可信数字的判据写「**无法判定（缺 X）**」——
  **不许写 PASS，也不许写"未达标"**（拿不到数 ≠ 没达标）。
- **别忘了测试盲区**：任务级审查里要显式写"哪些没测、为什么、风险多大"。

## 第 5 步：记录（四处都要写，**且全部必须带日期**）

| 去处 | 写什么 | 格式要求 |
| --- | --- | --- |
| `docs/RAG_EXECUTION_PROGRESS.md` §3 | 本次做了什么、门禁结果、决策、遗留 | **append-only**；条目名带 `[T-x.y]` / `[D-n]`，标题后括注日期 |
| `docs/RAG_DEV_PITFALLS.md` | 新踩的坑 | **append-only**；四段式「现象 → 根因 → 解决 → 面试怎么讲」；分类 A~E（新类开 G）；**无新坑也要写「本批未新增坑」** |
| `reports/rag_ingestion_auth_review/` | 审查文件 + 门禁证据 | 前缀按批次；**红/绿两份都要留**（"问题现场"不能只留修好的那份） |
| `.workbuddy/memory/YYYY-MM-DD.md` | 当日工作日志 | **带时间戳小标题**；长期约定写进同目录 `MEMORY.md` |
| `docs/RAG_NEXT_WINDOW_PROMPT.md` | 本文 | **每批结束必须重写**（五件事齐全） |

> **唯一允许覆盖更新**的是台账 §4「执行暂停点」——**必须覆盖**，它是"当前指针"。

## 纪律要求（硬性，逐条都有依据）

1. **带日期**：四处记录全部要 `YYYY-MM-DD`；没有日期的记录无法判断新鲜度；
2. **append-only**：台账 §3 / 踩坑 / 阶段审查只追加，不覆盖、不删历史；
3. **`git add` 用精确路径**（E2）；**提交后必须 `git rev-parse HEAD` 复核**（A1，坑 1）；
4. **提交/推送归用户** —— 除非用户明确要求，不要动 git 历史；
5. **不确定的事标"待验证"**，不许把没验证的说成已完成；
6. **环境噪声与真缺陷要分开报**（D12 的三判据 + 单独复跑）；
7. **接线守卫要防静默变空**（D16）；**构造坏输入先搞清它会被哪一层拦下**（D17）；
8. **新模块必须登记生产调用点**：定义在、测试在、**没人调用 = 未完成**。

---

# 附录 A · 环境坑速查（动手前必读）

| 坑 | 症状 | 处置 |
| --- | --- | --- |
| **1（每次 commit 必踩）** | `git commit` 成功，但 `git rev-parse HEAD` 不前进 | 先**核对**（`HEAD` vs `refs/heads/<branch>`），**不一致才修**：`mkdir -p .git/refs/heads/optimize` 后写 **40 位完整 SHA** |
| **2** | `git push` 被拒 | 直连与代理**都**会间歇失效 → 探测 + **交替重试**（有过第 5 次才成功的记录）；**探测有效期很短，探测与执行尽量同批做** |
| **3** | 轻量 venv 缺源配置 | `pip install` 显式指定镜像 + trusted-host |
| **4** | Bash 与 PowerShell 通道能力不互补 | 按需换通道；**给用户的命令按 PowerShell 写**（E4：`timeout` / `env -u` / `A \|\| B` 在 PS 里都不是那个意思） |
| **5** | 全量测试 stdout 汇总行消失、退出码 1 | **环境删除守卫**（A6）；改用 JUnit XML 取数 |

**另三条"看起来像坑"的纪律**：warning 条数取全量 stdout（D15），取不到就记 `N/A`，不许估算；
测试数认 JUnit XML 不认 `N passed`（D18）；**终端方言先确认**（E4）。

# 附录 B · 常用命令

```bash
# 基线核对
git status -sb && git rev-parse HEAD
git log --oneline -6

# 全量测试（A6 口径：JUnit XML 是权威）
./venv/Scripts/python.exe -m pytest -q --junitxml=reports/rag_ingestion_auth_review/<批>_junit.xml

# warning 条数（不要用 --collect-only！见 D15）
grep -i "passed.*warnings" reports/rag_ingestion_auth_review/<批>_full_test-output.txt

# 门禁其余三件套
./venv/Scripts/python.exe -m ruff check .
./venv/Scripts/python.exe -m compileall -q main.py routers services schemas utils config scripts tests
./venv/Scripts/python.exe scripts/check_repo_data_size.py

# 只跑本批 / 单文件（不重跑全量，A5）
./venv/Scripts/python.exe -m pytest tests/test_intent_and_safety.py -q

# 前端对齐批次：trace 端到端验收（令牌走环境变量，见 C7）
export RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+
./venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
./venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --format token
export TRACE_FIX_TOKEN=<签出的令牌>
./venv/Scripts/python.exe scripts/verify_trace_fix.py

# 复核接线仍在（B7 交付物，现由 AST 守卫盯）
grep -n "retrieve_chunk_items\|build_chunk_access_filter" routers/retrieval.py

# 复核回滚端点仍在（B8-1 交付物）
grep -n "indexes/rollback" routers/documents.py

# 复核前端契约字段名没被改（改名前端会静默失效，不报错）
grep -n "recent_preview\|long_term_summary\|requires_safety_prefix\|matched_high_risk_intents" services/chat_service.py

# 规范改动后的回归门禁（v2.1 专用）
"C:/Users/kk/.workbuddy/binaries/python/envs/default/Scripts/python.exe" scripts/verify_acceptance_spec.py
```

# 附录 C · 自检清单（开工前对一遍，交付前再对一遍）

**开工前：**

- [ ] `git log` / 台账 §4 已核对，本文的基线（**949 / 0 / 0**）与实测一致
- [ ] 已读 §1 分批表 + §2 审查汇总 + §3 最新记录（`[F1 切轨]`）
- [ ] 本次工作项的**验收判据**已明确（不许"做着看"）

**交付前：**

- [ ] 门禁四件套全绿；pytest ≥ **949**；warning ≤ 5（口径见 D15 / D18）
- [ ] 任务级审查已写，含**结论**与**声明范围**；测试盲区已排查
- [ ] 台账 §3 追加（带日期）；§1/§2 如涉及则更新；**§4 已改为新的当前指针**
- [ ] 踩坑追加（带日期；**无坑也写"未新增"**）
- [ ] 工作记忆追加（带时间戳小标题）
- [ ] **本文已重写**（进度 / 工作项 / 基线 / 约束，五件事齐全）
- [ ] 证据文件已落 `reports/rag_ingestion_auth_review/`（含**红/绿两份**）
- [ ] 提交用精确路径；提交后 `git rev-parse HEAD` 已复核（坑 1）

---

# 第四部分 · 2026-09-23 追加：语料专项批次交接（**覆盖第一部分的部分现状描述**）

> **追加时间**：2026-09-23（星期三）23:50。
> **本部分是对第一部分的补充**：第一部分说「系统全景」时还没有真实语料，
> 现在有了。**冲突处以本部分为准。**

## A. 状态变化（最重要的一句）

**ingestion 链路第一次有真实数据流过。**

| 项 | 开工前 | 现在 |
| --- | --- | --- |
| `document_chunks` / `document_versions` / `document_acl` / `index_builds` | **全 0 行** | 9229 / 209 / — / **v3 active** |
| `data/faiss_store/` 下的版本目录 | **不存在** | `v1`（superseded）、`v2`（superseded）、**`v3`（active，2026-09-24 实测）** |
| 检索能查到什么 | 781 条 FAQ 问答对 | **9229 个 chunk，带标题路径 + 页码 + 章条结构** |

## B. 现在最该做的一件事：**B17 按 parent 去重**

**切轨（F1）已于 2026-09-25 完成**，A 轨不再是默认 —— 于是「最该做的事」换成了它暴露出来的问题：
**父子块重叠占了约 29% 的 Top-K 名额**。

实测（`tmp/probe_topk_diversity.py`，7 条 query，走真实聊天路径
`retrieve_chunk_items_for_chat` → 适配 → `build_prompt_context_items`）：

| 粒度 | 结果 |
| --- | --- |
| `chunk_id` 重复 | **0 条** —— 检索层没有返回重复 chunk，这点它是对的 |
| `heading` 重复 | 普遍存在：父块 `p_*` 与子块 `c_*` **同 heading 双进 Top-K** |
| 下游文本去重 | 只吃掉了能吃得掉的那 1/6（文本**完全相同**的） |
| **浪费的名额** | **6 / 21 ≈ 29%**；平均有效证据 **2.14 / 3** |

**动手前先看清楚这三件事**：

1. **`ChunkHit` 目前没有 `parent_chunk_id` 字段** —— 要么给数据模型加，
   要么从 `chunk_type` / 父子关系表推导。**别拿 chunk_id 的 `p_` / `c_` 前缀当判据**，
   那是当前实现的巧合，不是契约；
2. **去重放在哪一层要想清楚**：检索层（`retrieve_chunk_items`）还是适配层？
   放检索层能让 `POST /retrieval/search` 一并受益，但那是 B 轨 API 的对外契约，改动面更大；
3. **「保留哪一条」别拍脑袋** —— 父块给全上下文、子块给精确匹配，通常保留分数高的，
   但要先用这条 query 集验一遍再定。

完整数据与证据：`reports/rag_ingestion_auth_review/b17_topk_diversity_20260925.json`。

## C. 三个新脚本（都已跑通，别重写）

| 脚本 | 用途 | 跑法 |
| --- | --- | --- |
| `scripts/build_corpus_documents.py` | 生成语料 | `./venv/Scripts/python.exe scripts/build_corpus_documents.py --limit 25 --sleep 0.6` |
| `scripts/bulk_import_documents.py` | 灌库 + 重建索引 | `RAG_JWT_SECRET=... HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ./venv/Scripts/python.exe scripts/bulk_import_documents.py` |
| `tmp/smoke_retrieval.py` | 端到端检索冒烟 | 同上 |
| `tmp/probe_topk_diversity.py` | 量化 B17（Top-K 里父子块重叠占比） | `RAG_JWT_SECRET=... HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ./venv/Scripts/python.exe tmp/probe_topk_diversity.py` |

**⚠️ 两个环境变量不能少**（踩坑 A10）：`HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`。
不加会去连 HuggingFace Hub，本机代理返回 502。

**⚠️ 依赖装在 `venv/rag_ml_deps/`**（踩坑 A7）：环境的删除守卫会杀掉 pip 的覆盖安装，
所以用了 `--target` 装独立目录 + `.pth` 插到 `sys.path[0]`。
**别再用常规 `pip install` 动这几个包**，会装成半残。

## D. 门禁与记录

- **全量门禁 949 / 0F / 0E / 0S**（JUnit XML 口径）。证据
  `reports/rag_ingestion_auth_review/junit_f1_track_final_20260925.xml`；
  **红的那份也留了**（`junit_f1_track_20260925.xml`，01:17 轮 failures=1：
  红用例 `tests.test_chat_api.ChatPromptApiTest::test_chat_prompt_returns_retrieved_documents_and_trace`，
  根因是伪造替身少一个位置参数 —— 踩坑 D22 第 2 形态；两轮 tests 都是 949，
  **是修好不是删测试**）；`compileall` / `check_repo_data_size` 证据
  `f1_track_compileall-output.txt` / `f1_track_data-size-output.txt`；
- **⚠️ 引用「全仓 `ruff check .`」必须带时间戳** —— 本批改动的 **8 个 Python 文件**
  定向 ruff 全过（⚠️ 别把 `.md` 喂给 `ruff check <file>`，它会按 Python 解析报上千条假错）；
  但全仓状态在这个仓库是"活的"：01:38 全过 → 01:45 又冒出 3 条，**全在并行会话的
  `tests/test_hybrid_retrieval.py`**（缺 `dataclasses.replace` 的 import），**与本批无关**。
  **判据只锚本批文件**；
- **任务级审查**：`reports/rag_ingestion_auth_review/f1_track_review.txt`
  （六节：门禁 / 逐条判据 / 超范围标注方向 / 中途问题处置 / 测试盲区 / 结论 + 声明范围）；
  ⚠️ **warning 条数记 `N/A`** —— 本批没落全量 stdout 证据文件，且**不重跑**
  （A5 一轮只跑一次全量 + 并行会话正在改 `tests/`，见审查第四节第 3 条）；
- **取数口径按踩坑 A6/A5**：以 JUnit XML 的 tests/failures/errors 为权威，
  **不要用退出码或 stdout 汇总行判绿红**（环境删除守卫会吞掉汇总行）；**一轮只跑一次全量**；
- **README 的测试数已改 JUnit 单一口径**（不再有 `932 passed` vs `936` 那套双数字）——
  更新用 `scripts/update_readme_testcount.py <junit.xml>`，它带命中数断言，
  且**门禁非全绿时拒绝写数字**；
- 本批踩坑：**D21（守卫判据随架构一起失效）+ D22（改默认配置静默打破 mock 型测试）**，
  见 `docs/RAG_DEV_PITFALLS.md`；
- 进度台账见 `docs/RAG_EXECUTION_PROGRESS.md` §3 的 `[F1 切轨]`（§4 暂停点已覆盖更新）。

## E. 未做项（按优先级）

1. **B17 按 parent 去重**（本部分 B）—— 已有量化（29% 名额被重叠内容占掉），动手前读 B 的三条；
2. **评测金标重建**（F2）—— 现有 recall 按 `intent` 判相关，文档语料下算不了。
   **切轨没有改变它**，所以对外不能说「检索质量已评测」；
3. **chat 响应暴露 `retrieval_path`** —— 目前只有 trace 里有，前端拿不到走的哪条轨；
4. **语料扩容** —— 2026-09-25 调研结论：`flk.npc.gov.cn/api/` **已废弃**
   （返回前端 SPA HTML，网上 2024 年的爬虫教程全部过期）；可达源是
   `samr.gov.cn` / `cca.org.cn` / `openstd.samr.gov.cn` / 淘宝·拼多多规则中心；
   `sousuo.www.gov.cn/search-gov/data` 接口活着且有数据但参数未调通；
   `rules.meituan.com` 被本机代理 502 拦（踩坑 A10 同款）；
5. 前端对齐批次遗留的两项（界面复核、B6 第二步待老霸拍板）仍然有效。

### F. 与检索轨道相关的守卫 —— 改之前先读

| 守卫 | 位置 | 盯什么 |
| --- | --- | --- |
| `TestDeploymentGuards` | `tests/test_ingestion_pipeline.py` | compose 真的起了 worker、共享索引/对象卷、同元数据库与队列地址、启动日志点破 process-local 队列 |
| `TestWorker::test_consumed_job_leaves_real_chunks_behind` | 同上 | worker 消费后 **chunk > 0 且版本 published 且进可见集合** |
| `TestReadmeTrackConsistency` | 同上 | README 的机器可读锚点 `<!-- f1-track: chat-service-retrieval=... -->` 与 `chat_service.py` 的 `DEFAULT_CHAT_RETRIEVAL_PATH` **双向一致**；另有 1 条**守卫自测**（喂源码样本，验证两种取值都读得出、读不到返回空串） |
| `tests/test_chat_retrieval_track.py`（12 条） | 新文件 | 轨道解析 / 形状适配（含反证）/ 分发 / **端到端（真实索引）** |

**判据已经换过一次，别换回旧写法**：原来盯的是「AST 里有没有调用 `retrieve_rag_items`」，
改成开关制后两条轨道**同时**调它，那条判据会永远返回 `seed-faq`、彻底失去区分度
（踩坑 **D21**）。现在盯的是**默认值本身**。

**再切一次轨道（改默认值）时要同步做三件事**：改常量 → 改 README 锚点 → 跑全量。
漏了第三件会踩 **D22**：mock 型测试会被静默架空 —— 本次撞出 4 条红，
其中 1 条 `TypeError` 来自伪造模块的替身签名没跟着真身改。

---

# 第五部分 · 2026-09-25 B8-混合检索交接（**覆盖前四部分的状态描述**）

> 本部分是最新一层。**凡与前四部分冲突的，以本部分为准**；前四部分保留是为了讲清演进过程。

## A. 状态变化（最重要的一句）

**B 轨（正式路径）真正实现了混合检索，并且有实测数字。**

| 项 | 现在 |
| --- | --- |
| 机制 | 稠密（FAISS `IndexFlatIP`）+ 稀疏（SQLite FTS5 `bm25()`，中文 bigram 切词）**两路独立召回** → **加权 RRF** 融合 |
| 在哪 | `utils/hybrid_retriever.py`（融合）、`utils/sparse_retriever.py`（稀疏查询）、`services/ingestion/sparse_index.py`（稀疏索引构建/校验） |
| 怎么用 | `POST /retrieval/search` 带 `retrieval_mode=dense\|sparse\|hybrid`；聊天链路用 `RAG_CHAT_RETRIEVAL_MODE` |
| **默认** | **仍是 `dense`** —— 见「D. 三条硬结论」第 3 条 |
| 索引 | 生效索引 `v4`：稠密 18.9MB + manifest 19.0MB + 稀疏 32.4MB，9229 chunk |
| 门禁 | **1004 / 0F / 0E / 0S** —— 以 `reports/retrieval_hybrid/final_junit_20260925.xml` 为准 |
| 踩坑 | 全文 **84 条**（A11 / B21 / C8 / D27 / E11 / F6） |
| 待提交 | **两批叠着**：F1 切轨 + B8-混合检索（清单见台账 §4 暂停点，`git add` 用精确路径） |

> ⚠️ **前四部分里凡是「检索质量无结论 / 纯稠密单路 / 没有 BM25」的表述，从本部分起作废。**
> 但**不要**因此说"检索质量已全面评测" —— F2 的 A 轨口径仍未统一（见 F. 未做项）。

## B. 现在最该做的一件事：**B17（父块去重）+ F6（manifest 缓存）**

两件都是"混合检索做完后暴露出来的、与检索算法本身无关的欠账"，**都有量化，别凭印象**：

| 项 | 量化 | 修法要点 |
| --- | --- | --- |
| **B17 去重** | 父块 `p_*` 与子块 `c_*` 同 heading 双进 Top-K → **约 29% 名额被重叠内容占掉**，平均有效证据 **2.14/3**；下游文本去重只拦得住 1/6 | 检索层按 parent 去重。**`ChunkHit` 目前没有 `parent_chunk_id` 字段**，先看清再动手。证据：`reports/rag_ingestion_auth_review/b17_topk_diversity_20260925.json` |
| **F6 延迟** | hybrid **355.7ms** ≈ dense(172.7) + sparse(185.7)，根因是 `load_active_manifest` **无缓存**，每次检索重读 19.0MB manifest | 键取 `(root, index_name, index_version)` 的缓存 + **版本切换时显式失效** + 一条"切版本后第一次检索就读到新的"测试 |

**两件别混在同一个批次做**：一个改召回质量、一个改延迟，混在一起出问题分不清是谁造成的。

## C. 本批新增/改动的东西（**别重写，先读**）

| 文件 | 用途 |
| --- | --- |
| `services/ingestion/sparse_index.py` | 稀疏索引构建 / 校验 / 元数据；`row_id` 与 manifest 对齐 + `row_id_checksum` 自校验 |
| `utils/sparse_retriever.py` | 稀疏查询：**临时表 JOIN 做权限前置过滤**（比 `rowid IN (9229参数)` 快 400 倍）、fail closed |
| `utils/hybrid_retriever.py` | `FusionConfig`（`w_dense=10/w_sparse=1/k=60`）、`fuse_rankings`（**按 rank 融合**）、`search_hybrid_chunks` |
| `scripts/rebuild_chunk_index.py` | **只重建索引**（不重跑解析灌库）。**改了切词算法 / 加了稀疏路之后必须跑它**，否则线上仍是旧构建 |
| `scripts/build_retrieval_gold.py` / `build_colloquial_cases.py` | 弱监督金标：81 条标题式 + 30 条口语化（每条可回溯）+ 40 条负样本；命中判据用 **span** |
| `scripts/evaluate_hybrid_retrieval.py` | 三模式对比评测，**走生产代码路径**；输出落 `reports/retrieval_hybrid/` |
| `tests/test_hybrid_retrieval.py`（53 条） | 切词 / MATCH 构造 / 索引构建校验 / 融合 / **权限隔离** / 聊天开关 / 决策留痕 |

## D. 混合检索的三条硬结论（**对外口径必须按这个讲**）

1. **增益上限本来就很小**：两路 top-10 的**并集召回** = title 81/81、口语化 18/30 ——
   这是**任何融合策略的天花板**；而 dense-only 已达 80/81，title 集最多再赢 1 条。
2. **等权融合有害**：口语化集上等权 RRF 把 R@1 从 0.4000 拉到 **0.2667**；
   扫完权重后 **`w_dense=10` 是唯一在两套集上都不掉点**的配置（title R@10 正好打到上限 1.0000）。
3. **不是"指标全面上涨"**：收益 = title 集 R@1 **+2 条** + R@10 打满；
   口语化集**零退化（逐列完全相同）**。所以：
   - **聊天链路不建议切 hybrid** —— 它的输入正是口语提问，实测**零收益、双倍延迟**；
   - 只有"短查询为主"的接口值得切。切之前：重建索引 → 确认 `index.sparse_available=true`
     → 改 `routers/retrieval.py::DEFAULT_RETRIEVAL_MODE` **与 README 的 `b8-hybrid` 锚点**
     （**改一边不改另一边会红，故意留的**）。

数字与全部口径说明：README §3.6 + `reports/retrieval_hybrid/evaluation_20260925.txt`。

## E. 门禁与记录

- **门禁**：全量 **1004 / 0F / 0E / 0S**（`reports/retrieval_hybrid/final_junit_20260925.xml`）+
  `ruff check .` 全仓过 + `scripts/check_repo_data_size.py --max-mb 1` 过 +
  `scripts/verify_acceptance_spec.py` 8 项过；**warning 记 `N/A`**（汇总行被守卫吞掉，踩坑 A6）；
- **⚠️ 规范回归门禁此前跑不起来**：`scripts/verify_acceptance_spec.py` 依赖 `markdown`，
  而环境里只有 `markdown_it`（另一个库）。本批补进 `requirements-dev.txt` 并**挂进 CI** ——
  这道门禁过去只在本地手动跑过一次，**等于不存在**；
- **进度台账**：`docs/RAG_EXECUTION_PROGRESS.md` §3 的 `[B8-混合检索]`（§4 暂停点已覆盖更新）；
- **本批踩坑 10 条**：B19（手拼索引目录）/ B20（假守卫·同源校验）/ B21（`mode` 跨轨撞车）/
  D23（评测口径绑在格式差异上）/ D24（FAISS 并列顺序不稳定）/ D25（隔离测试假绿）/
  D26（靶子取自顺序不保证的集合）/ **D27（规范「唯一口径」表的判据会过期）** /
  E10（占位代码）/ **E11（门禁脚本依赖没进清单）**；另补历史缺口 **F6**。

## F. 未做项（按优先级）

1. **口语化金标复核** —— 30 条里 7 条两路 top-50 全捞不到，疑似假阴性；
   R@1 **0.4000** 这个绝对水平对外时**必须主动说明**，不能只报 title 集的 1.0000；
2. **B17 + F6**（见 B 节，两件分批判）；
3. **F2 口径** —— `scripts/evaluate_retrieval_metrics.py:85` 按 `intent` 判相关（**A 轨口径**）；
   B 轨这套 span 金标是**新增的第二套**，两者**不可互认**；
4. **切线上默认到 hybrid**（可选，见 D 节第 3 条）；
5. **chat 响应暴露 `retrieval_path` / 召回模式** —— 目前只有 trace 里有；
6. **界面复核** —— `D:\llm\front\docs\diag_panel_probe.cjs`；
7. **B6 第二步待老霸拍板** —— 往 `食品安全投诉` 加「不新鲜」会把整句话抬成 high risk，**未做**；
8. **前端补 1 行词表** —— `src/lib/status.ts` 的 `ROUTING_TEXT` 增 `clarify` 项；
9. **语料扩容**（源可达性见第四部分 E）；**PostgreSQL 复验**、roadmap 8.2 两条 P3。

## G. 新增守卫（**改代码前先读，否则会撞红还不知为什么**）

| 守卫 | 位置 | 盯什么 |
| --- | --- | --- |
| `TestReadmeTrackConsistency::test_readme_marker_matches_the_default_retrieval_mode` | `tests/test_ingestion_pipeline.py` | README 锚点 `<!-- b8-hybrid: default-retrieval-mode=... -->` 与 `routers/retrieval.py::DEFAULT_RETRIEVAL_MODE` **双向一致**；另有 1 条**守卫自测**（喂源码样本，验证读得出、改名/改成运行时值则返回空串） |
| `TestHybridDecisionTrace`（`tests/test_hybrid_retrieval.py`） | 新文件 | 把本批关键取舍做成**可断言的事实**：默认权重 `w_dense=10/w_sparse=1/k=60`、`DEFAULT_RETRIEVAL_MODE == "dense"`。**改动必须重跑评测** |

**切默认模式要同步做四件事**：改常量（`routers/retrieval.py`）→ 改 README 锚点 →
同步 README §0.1 的上线顺序说明与 §3.6 现状表 → 跑全量。
漏任一步要么变红（锚点/§3.6），要么静默误导读者（§0.1 的上线顺序）。
