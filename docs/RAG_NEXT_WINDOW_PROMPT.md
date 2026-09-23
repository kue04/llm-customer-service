# RAG 改造：项目收尾交接与作业规程（统一提示词）

> **交接时间**：2026-09-23（星期三）· **交接时状态：B1~B8 全部完成，阶段 0~7 全部 PASS**
> （阶段 7 = 发布门禁，结论见 `reports/rag_ingestion_auth_review/stage7_review.txt`）。
> **两种用法（推荐①）**：① 在新窗口说一句「读 `docs/RAG_NEXT_WINDOW_PROMPT.md`，按里面的规程继续」——
> 不会因复制截断而失真；② 把本文**整份**粘贴过去（适用于不能读本地文件的工具）——
> **不要只贴「第 2 步」的工作项清单**，第 0.4 节的**引用纪律**才是最容易踩错的地方。
>
> **⚠️ 先看日期再动手**：上面这个时间戳是判断本文是否过期的**唯一依据**。
> 若实际仓库状态与本文不符（例如全量测试不再是 918 / 0 failures，或工作区已干净且已推送），
> 说明有人推进过了 —— **先按第 0 步核对，不要照着本文盲改。**
>
> **本文已收录**：系统全景、进度快照、**发布门禁的三条限制**、候选工作项、五步作业规程、
> 硬性约束、环境坑速查、自检清单。需要更深细节时按文中路径读原始文档（那是权威来源）。

---

# 第一部分 · 开工前必读：先把系统搞清楚

> **项目已经收尾**：按 `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 的
> 阶段 0~7（B1~B8）**已全部走完**，发布门禁已出具 PASS。
> 所以接下来的工作**不再是"按计划推进批次"**，而是从第 2 步的候选清单里挑事情做。
> 最容易犯的错是：**把"还能更好"当成"还没完成"**，或者反过来 ——
> **把 PASS 读成"什么都验收了"**（见 0.4 的三条限制）。

## 0.1 这个项目是什么

外卖售后场景的**客服 RAG 后端**（FastAPI + SQLite/PostgreSQL + FAISS）。

对外主链路是「检索增强问答 + 客服工作流」：意图识别 → 检索 → 重排 → 证据分级 →
生成 → 规则兜底 → 风险分级 → 转人工。已经有 **12 个 router、918 条测试**。

**理想目标**写在 `docs/goal.md`（企业级 RAG：数据治理 / 权限控制 / 检索 / 生成 /
验证 / 追踪 / 反馈闭环）。它是**方向**，不是任务清单；
目标与现状的差距分析见 `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md`。

## 0.2 ★ 双轨制已经合流（B7 完成）—— 但「哪条是真的」仍必须知道

B7 之前的坑是「两条链路都叫检索，没人知道哪条是真的」（F1）。**B7 已把它合上**：

```
【B 轨 · 正式检索路径】✅ B7 已接线
  POST /retrieval/search
    → require_read_operation_role（retrieval_read）
    → build_chunk_access_filter(session, auth)        ← services/retrieval_access.py（唯一生产构造点）
    → retrieve_chunk_items(query, access=access, ...) ← FAISS 原生预过滤
    → 响应带 retrieval_path="chunk-index" + index.visible_chunk_count

【A 轨 · 演示 / 兼容路径】保留但三处显式标注（模块 docstring / 端点 summary / README 第 0 节）
  POST /retrieval/search-demo、POST /retrieval/prompt-preview
    → retrieve_by_real_vector（781 条种子 FAQ，数据源本身没有 tenant/ACL 两维）
    → 响应带 retrieval_path="seed-faq-demo"
```

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
  ingestion/        models / repository / pipeline / worker / queue / index_builder / index_manifest
utils/
  vector_retriever.py  A 轨 retrieve_by_real_vector + B 轨 search_chunk_index / retrieve_chunk_items
tests/
  retrieval_fixtures.py       ★ B7：确定性假 embedder + RetrievalEnv（两租户入库/重建/授权一条龙）
  test_retrieval_isolation.py ★ B7：检索层隔离
  test_tenant_isolation.py    数据层 + 真实路由层
  test_retrieval_api.py       API 出口层
  test_index_rollback.py      ★ B8-1：回滚 8 条
  test_wiring_guards.py       ★ B8-2：接线 AST 守卫 18 条
  test_release_gate.py        ★ B8-3：四格式端到端 7 条（**项目首条从 HTTP 到检索结果的测试**）
```

## 0.4 ★★ 发布门禁 PASS，以及它的**三条限制**（引用时必须一起带上）

阶段 7（发布门禁）结论：**PASS**（`stage7_review.txt`）。判据：
7.1 四件套全绿 / 7.2 四格式端到端通过 / 7.3 六条逐项可指认 / 7.4 六条成立；
全量 **918 / 0 failures / 0 errors / 0 skipped**，warning 5 条未新增。

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

## 0.5 已知但**本次范围外**的问题（登记在此，防止被遗忘）

| # | 问题 | 状态 | 归属 |
| --- | --- | --- | --- |
| 1 | **F2**：`scripts/evaluate_retrieval_metrics.py:85` 按 intent 判相关，不是文档级指标 | 未修 | 修完才谈得上 M10 Recall@k |
| 2 | **F4 残留**：README 三处测试数（`332 passed` → **918**）+ 测试文件数 | 未改 | 顺手项，见第 2 步 |
| 3 | **F5**：worker 有 CLI 无进程编排，容器化后队列没人消费 | 未修 | 已在门禁结论里声明为已知限制 |
| 4 | 真实引擎未复验（PostgreSQL / Redis / 真 tokenizer+embedding / OCR） | 未做 | **PostgreSQL 复验**已在门禁结论里如实标注未做 |
| 5 | 索引重建的并发竞态未测 | 未测 | 单机 SQLite 难构造 |
| 6 | 图片块不进 chunk / PDF 跨页重复表头 | 设计如此 | 后续版本 |
| 7 | 7.3 第 4 条的 **trace / 错误信息**泄漏维度无专项用例 | 未覆盖 | 判为低风险（与检索共用同一 `ChunkAccessFilter`），已记入 `stage7_review.txt` 第六节 |

---

# 第二部分 · 作业规程（五步闭环，缺一步都不算完成）

> **核对基线 → 读进度与约束 → 干活 → 门禁自检 → 审查 → 记录。**
> 只跑通测试**不算交付**。四处记录**全部必须带日期**（`YYYY-MM-DD`）。

## 第 0 步：核对基线（**不要跳过**，防止在错误基线上工作）

```bash
git status -sb                 # 分支 optimize/interview-ready
git rev-parse HEAD             # 收尾时为 135d280（B8-3）；此后每批都会前进
./venv/Scripts/python.exe -m pytest -q --junitxml=tmp/baseline_junit.xml
./venv/Scripts/python.exe -c "import xml.etree.ElementTree as ET; \
  s=ET.parse('tmp/baseline_junit.xml').getroot().find('testsuite'); \
  print('tests=',s.get('tests'),'failures=',s.get('failures'),'errors=',s.get('errors'))"
# 收尾基线：tests=918 failures=0 errors=0 skipped=0
```

- **⚠️ 取数口径（踩坑 A6）**：**不要**用退出码和 stdout 汇总行判断绿红 ——
  环境删除守卫会在 sessionfinish 吃掉汇总行、把退出码变成 1，**测试其实全绿**
  （B8 的四轮都没触发它，但 B7 那轮触发了 —— 它是**间歇**的，不是已修好）。
- **⚠️ warning 条数**取自**全量 stdout** 的 `N warnings`（踩坑 **D15**），
  **不是** `--collect-only`（它不执行测试，输出里没有 warning 汇总）。
  若汇总行被守卫吞掉 → 记 `N/A（汇总行被吞）`，**不许估算或沿用上次数字**。
- 预期 warning：**5 条**（fastapi/httpx ×1、starlette/anyio ×1、faiss SWIG ×3）。
- 若对不上：先查是不是有人推进过（`git log` / 台账 §4），**不要**照本文盲改。

## 第 1 步：读进度与约束（按顺序，不要跳）

1. 台账 `docs/RAG_EXECUTION_PROGRESS.md`：
   §1 分批表（**B1~B8 全部 ✅**）→ §2 审查汇总（**阶段 0~7 全部 PASS**）→
   §3 任务执行记录（**按时间追加，越靠后越新**）→ **§4 执行暂停点（当前指针）** →
   §5 提交与仓库同步记录（**commit 前必读的环境坑**）；
2. `reports/rag_ingestion_auth_review/stage7_review.txt` —— 发布门禁结论 + 三条限制 + 测试盲区；
3. 三份任务级审查：`B8_step1_rollback_review.txt` / `B8_step2_wiring_guards_review.txt` /
   `B8_step3_release_gate_review.txt`；
4. `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`（v2.1）：**§1.0 现状总览**（13 项能力状态）
   与 **§17 验收作业规程**（取数口径 / 结论状态机 / 「已建未启用」判据 / 编号映射表）；
5. 踩坑 `docs/RAG_DEV_PITFALLS.md`（**47 条**）：**A5/A6**（门禁取数）、**D12**（红绿判据）、
   **D15**（warning 口径）、**D16**（守卫会静默变空）、**D17**（坏输入要按层构造）、
   **E2**（并行会话下的提交纪律）。

## 第 2 步：干活 —— 候选工作项（**不是必须全做，按需挑选**）

> 计划里的 B1~B8 已全部完成。以下都是「还可以更好」，**按价值排序**，
> 每做一项就走完第 3~5 步（门禁 → 审查 → 记录）。

| 优先级 | 事项 | 为什么值得做 | 入口 |
| --- | --- | --- | --- |
| 1 | **推送本地提交** | 本地领先远端（收尾时 ahead 6）；坑 2：直连/代理**交替重试**，一次失败别换策略 | 台账 §5 |
| 2 | **F4：README 测试数** | 对外文档失真（写 `332 passed`，实际 918） | README 第 8 行 badge / §286 表格 / §427 目录树 |
| 3 | **F2：检索评测口径** | 它是"所有优化决策的判据来源"；不修则 M10 永远给不出可信数字 | `scripts/evaluate_retrieval_metrics.py:85` |
| 4 | **PostgreSQL 复验** | roadmap 8.1 把它写进了 B8 内容，但计划未要求 → 门禁结论里如实标注未做 | 需要 Docker/psql |
| 5 | **roadmap 8.2 的两条 P3** | 真实 OCR 引擎复验、压测与 P95/P99 聚合 | `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md` 8.2 |
| 6 | **验收规范 §1.0 的其余未实现项** | 解析质量门禁、限流、病毒扫描、成本账本、总 deadline / 背压 / 死信 | 规范 §1.0 的「未实现」行 |

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
   `git add docs/ scripts/` 会把它们一锅端）。

### 本批**不做**的事（防 scope creep）

- 不引入新模型 / 新框架 / 新存储（要换必须单独评估并留决策记录）；
- 不动已冻结的决策（[D-6] [D-12] [D-13] 等）：要改必须**补新的决策记录**；
- 不为了让数字好看而放宽门槛（验收规范的判据不因"做不到"而改）。

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

**硬性**：四件套全绿；`pytest` 数 **≥ 918**（只增不减）；warning **≤ 5**（A6/D15 口径）；
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
7. **接线守卫要防静默变空**（D16）；**构造坏输入先搞清它会被哪一层拦下**（D17）。

---

# 附录 A · 环境坑速查（动手前必读）

| 坑 | 症状 | 处置 |
| --- | --- | --- |
| **1（每次 commit 必踩）** | `git commit` 成功，但 `git rev-parse HEAD` 不前进 | `mkdir -p .git/refs/heads/optimize` 后写 **40 位完整 SHA**（同一条命令内）；**ref 文件必须写全 SHA** |
| **2** | `git push` 被拒 | 直连与代理**都**会间歇失效 → 探测 + **交替重试**（有过第 5 次才成功的记录） |
| **3** | 轻量 venv 缺源配置 | `pip install` 显式指定镜像 |
| **4** | Bash 与 PowerShell 通道能力不互补 | 按需换通道 |
| **5** | 全量测试 stdout 汇总行消失、退出码 1 | **环境删除守卫**（A6）；改用 JUnit XML 取数 |

**另两条"看起来像坑"的纪律**：warning 条数取全量 stdout（D15）；
取不到就记 `N/A`，不许估算。

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
./venv/Scripts/python.exe -m pytest tests/test_wiring_guards.py -q

# 复核接线仍在（B7 交付物，现由 AST 守卫盯）
grep -n "retrieve_chunk_items\|build_chunk_access_filter" routers/retrieval.py

# 复核回滚端点仍在（B8-1 交付物）
grep -n "indexes/rollback" routers/documents.py

# 规范改动后的回归门禁（v2.1 专用）
"C:/Users/kk/.workbuddy/binaries/python/envs/default/Scripts/python.exe" scripts/verify_acceptance_spec.py
```

# 附录 C · 自检清单（开工前对一遍，交付前再对一遍）

**开工前：**

- [ ] `git log` / 台账 §4 已核对，本文的基线（918 / 0 / 0）与实测一致
- [ ] 已读 §1 分批表 + §2 审查汇总 + §3 最新记录
- [ ] 本次工作项的**验收判据**已明确（不许"做着看"）

**交付前：**

- [ ] 门禁四件套全绿；pytest ≥ 918；warning ≤ 5（口径见 D15）
- [ ] 任务级审查已写，含**结论**与**声明范围**；测试盲区已排查
- [ ] 台账 §3 追加（带日期）；§1/§2 如涉及则更新；**§4 已改为新的当前指针**
- [ ] 踩坑追加（带日期；**无坑也写"未新增"**）
- [ ] 工作记忆追加（带时间戳小标题）
- [ ] **本文已重写**（进度 / 工作项 / 基线 / 约束，五件事齐全）
- [ ] 证据文件已落 `reports/rag_ingestion_auth_review/`（含**红/绿两份**）
- [ ] 提交用精确路径；提交后 `git rev-parse HEAD` 已复核（坑 1）
