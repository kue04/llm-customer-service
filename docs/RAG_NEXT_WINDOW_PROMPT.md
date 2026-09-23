# 继续 RAG 改造：B8 交接与作业规程（统一提示词）

> **交接时间**：2026-09-23（星期三）· **基线校正时间：2026-09-23（同日，开 B8 前核对，见下方校正块）** · **交接给下一个窗口执行 B8。两种用法（推荐①）**：① 在新窗口说一句「读 `docs/RAG_NEXT_WINDOW_PROMPT.md`，按里面的规程执行 B8」——不会因复制截断而失真；② 把本文**整份**粘贴过去（适用于不能读本地文件的工具），**不要只贴"第 2 步"的任务规格**，第一部分（阶段 4 已 PASS 的现状 / 门禁取数口径已变 / 必须先处置的 3 件事）才是关键。
> **交接时状态**：批次 **B7 结束**（任务 4.1 权限规则 + 4.2 路由改造 + 4.3 检索 API + 隔离测试），
> **阶段 4 三个任务全部完成、阶段级审查 PASS**（`reports/rag_ingestion_auth_review/stage4_review.txt`）。
> B5 + B6 + B7 改动**已于 2026-09-23 由用户提交并推送**：`HEAD = 624d4968`
> （提交信息「B7: 检索 API 正式接线（chunk 级 + 权限过滤），阶段 4 收尾 PASS」），
> 工作区**干净**、本地与 `origin/optimize/interview-ready` **同 SHA**。
> **提交/推送仍归用户**，不要代劳（本文写就时写的是「尚未提交」，已被用户的提交改变）。
>
> **★ 2026-09-23 基线校正（开 B8 前实测，逐条覆盖本文写就时的过期陈述）**：
>
> | # | 本文原文 | 2026-09-23 实测 |
> | --- | --- | --- |
> | ① | 「B5 + B6 + B7 尚未提交」 | **已提交并推送**，`HEAD = 624d4968…`，工作区 0 改动 |
> | ② | `HEAD = a6703731…`（B7 收尾当时） | B7 提交后为 **624d4968…**；两者都合法，差异只是"是否已提交" |
> | ③ | 「预期尚未提交的改动很多」 | 实测 `git status --porcelain` **0 行** |
> | ④ | README badge 测试数是「一行的事」 | ❌ 不准确：`332 passed` 共 **3 处**（第 8 行 badge、第 286 行表格、第 427 行目录树），见前置项 3 |
>
> 基线测试 **884 / 0 failures / 0 errors / 0 skipped**（JUnit XML 口径，`tmp/b8_baseline_junit.xml`）。
> ⚠️ 这次跑的过程里 **A6 那个环境守卫再次出现**
> （`SAFE_DELETE_BULK_CONFIRM_REQUIRED {"count":108,"threshold":50,"scope":"turn"}`）：
> 进度条全绿到 `[100%]`、**没有汇总行**、退出码 1 —— **测试实际全绿**，按 JUnit XML 取数。
> 其余核对全部与本文一致：回滚 6 符号仍零调用点零测试（前置项 1 未做）、
> B7 检索接线仍在（`routers/retrieval.py` 引用 `retrieve_chunk_items` +
> `build_chunk_access_filter`，tenant/acl 命中 10 处）。
>
> **★ 验收规范已落盘（2026-09-23）**：企业级验收规范 **v2.1** 现在
> **`docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`**（v2.0 原件留在 Downloads，未改动）。
> B8 的发布门禁**以它为判据来源**，但有两条纪律必须先看：
> ① 规范 §10.5 标「**不可测**」的 4 类指标（输入 / 检索 / 性能 / 成本）属 B8 **之后**的路线，
>    **不得据此给 B8 记 NEEDS_WORK** —— 拿不到数 ≠ 未达标；
> ② 规范的编号（`I01~O01`、`§N`）与本项目的编号（`B1~B8`、`阶段 N`）是**两套**，
>    对照表在规范 **§17.5**；引用规范编号时必须同时写出本项目批次。
>    另注意规范 §7.x 与执行计划「阶段 7.x」**编号撞车但内容不同**，引用要带前缀。
> 该规范里标 `未实现 / 未就绪 / 已建未启用 / 未演练` 的 13 项，是**差距清单**不是成绩单；
> 落盘的任务级审查见 `reports/rag_ingestion_auth_review/spec_v2.1_review.txt`（PASS）。
>
> ⚠️ **先看日期再动手**：上面这个时间戳是判断本文是否过期的**唯一依据**。
> 若实际仓库状态与本文不符（例如已有 B7 的提交、或全量测试不再是 884 / 0 failures），
> 说明有人推进过了 —— **先按第 0 步核对，不要直接开工。**
>
> **本文已收录**：系统全景、进度、任务规格、硬性约束、环境坑速查、自检清单。
> 需要更深的细节时再按文中标注的路径去读原始文档（那是权威来源，冲突时以它们为准）。

---

# 第一部分 · 开工前必读：先把系统搞清楚

> 这一部分**不跑命令、不写代码**。B8 是**发布门禁批次**：
> 你要做的是「出证据 + 做终审」，不是再堆一个新模块。
> **理解错这一点最可能的结果是：把 7.2/7.3/7.4 的证据做成"又一段漂亮的描述"，**
> **而门禁真正卡住的那两条（四格式端到端、索引回滚）被描述掩盖掉。**

## 0.1 这个项目是什么

外卖售后场景的**客服 RAG 后端**（FastAPI + SQLite/PostgreSQL + FAISS）。

对外主链路是「检索增强问答 + 客服工作流」：意图识别 → 检索 → 重排 → 证据分级 →
生成 → 规则兜底 → 风险分级 → 转人工。已经有 12 个 router、**884 个测试**。

**理想目标**写在 `docs/goal.md`（企业级 RAG：数据治理 / 权限控制 / 检索 / 生成 /
验证 / 追踪 / 反馈闭环）。**但别把 `goal.md` 当成本批任务清单** ——
它是方向，本批只做 B8（阶段 7）。目标与现状的差距分析见
`docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md`。

## 0.2 ★ 双轨制已经合流（B7 完成）—— 但"哪条是真的"仍必须知道

B7 之前的坑是「两条链路都叫检索，没人知道哪条是真的」（F1）。**B7 已把它合上**：

```
【B 轨 · 正式检索路径】✅ B7 已接线
  POST /retrieval/search
    → require_read_operation_role（retrieval_read）
    → build_chunk_access_filter(session, auth)        ← services/retrieval_access.py:162（唯一生产构造点）
    → retrieve_chunk_items(query, access=access, ...) ← FAISS 原生预过滤 IDSelectorBatch
    → 响应带 retrieval_path="chunk-index" + index.visible_chunk_count

【A 轨 · 演示 / 兼容路径】保留但三处显式标注（模块 docstring / 端点 summary / README 第 0 节）
  POST /retrieval/search-demo、POST /retrieval/prompt-preview
    → retrieve_by_real_vector（781 条种子 FAQ，数据源本身没有 tenant/ACL 两维）
    → 响应带 retrieval_path="seed-faq-demo"
```

**对 B8 意味着什么**：7.3 的安全审查全部围绕 **B 轨**；A 轨只需要验证
「标注没有被删掉」（`test_demo_endpoint_is_explicitly_labeled` 守着）。

**索引是全局一份**（B14 的教训，B7 修掉）：`rebuild_index` 默认 `all_tenants=True`，
版本号**全局**递增，manifest `extra["scope"] / extra["tenants"]` 留痕。
**不要退回"按租户分片"** —— 那会让先入库的租户**静默消失**（B7 之前的状态）。

## 0.3 目录地图（哪条是主链路）

```
routers/
  retrieval.py      ★ B7 重写：/search（B 轨）+ /search-demo、/prompt-preview（A 轨）
  documents.py      上传 / 详情 / 版本 / 任务查询 + POST /ingestion/indexes/rebuild（B7 新增）
  knowledge.py      发布（document:publish）
services/
  auth_context.py   JWT 身份上下文 + 两张授权表（操作维度 ROLE_TABLE + 资源维度 RESOURCE_SCOPE_ROLES）
  auth_service.py   FastAPI 依赖层 + require_resource_scope（B7 新增）
  retrieval_access.py ★ B7 新增：ChunkAccessFilter 的**唯一**生产构造点（4.1 判定顺序六段）
  ingestion/        models / repository / pipeline / worker / queue / index_builder / index_manifest
utils/
  vector_retriever.py  A 轨 retrieve_by_real_vector + B 轨 search_chunk_index / retrieve_chunk_items
tests/
  retrieval_fixtures.py       ★ B7 新增：确定性假 embedder + RetrievalEnv（两租户入库/重建/授权一条龙）
  test_retrieval_isolation.py ★ B7 新增：检索层隔离 23 条
  test_tenant_isolation.py    数据层 14 条 + 真实路由层 TestAclOnRealRoutes 7 条
  test_retrieval_api.py       API 出口层 14 条（TestChunkRetrievalApi 9 条）
```

## 0.4 「已建未启用」清单（2026-09-23 B7 收尾后复核）—— B8 的第一个工作项

**检索侧欠账已清空**（B7 把 `search_chunk_index()` / `ChunkAccessFilter` /
「检索层 API 出口」三行全部消掉）。**剩下的一族欠账全部在 `index_builder.py`**：

| 资产 | 定义位置 | 生产调用点 | 测试调用点 | 处置 |
| --- | --- | --- | --- | --- |
| **`rollback_index()`** | `index_builder.py:416` | ❌ 无（也没有 API 出口） | ❌ **无** | **B8 三选一（见第 2 步前置项 1）** |
| `available_versions()` / `active_index_version()` / `index_root_for()` / `build_entries()` / `ERROR_INDEX_ROLLBACK_FAILED` | `index_builder.py` | ❌ 无 | ❌ 无 | 与上一条一并处置 |

**为什么它卡门禁**：计划 7.4 的阶段完成条件明写「索引原子切换 / **回滚**通过」，
而回滚**没有任何测试** → 该判据**现在无法判定为通过**。
计划 3.4 原文就要求「增加索引版本回滚接口/函数**和测试**」—— 函数有、测试没有。

## 0.5 已知但**不在 B8 范围**的问题（登记在此，防止被遗忘）

| # | 问题 | 状态 | 归属 |
| --- | --- | --- | --- |
| 1 | **F2**：`scripts/evaluate_retrieval_metrics.py:85` 按 intent 判相关，recall@1 不是文档级指标 | 未修 | 独立小批次（7.4 若要引用检索质量数字，必须先修或显式声明不可用） |
| 2 | **F5**：worker 有 CLI 无进程编排，容器化部署后队列没人消费 | 未修 | B8 的 release_gate 结论里**必须显式声明**为已知限制 |
| 3 | 真实引擎未复验（PostgreSQL / Redis / 真 tokenizer + embedding / OCR） | 未做 | B8 结论里**必须显式声明**为已知限制（本机无 Docker/psql/Redis） |
| 4 | 索引重建的并发竞态未测 | 未测 | 已登记，单机 SQLite 难构造 |
| 5 | 图片块不进 chunk / PDF 跨页重复表头 | 设计如此 | 后续版本 |
| 6 | `data/rag_metadata.db` 不存在（alembic 0001 未落开发库） | 与 B1~B7 相同 | 有 Docker 的机器复验 |
| 7 | **F4 残留**：README badge 的测试数是否同步到 884 | 未核对 | B8 顺手核对并更新（一行的事） |

---

# 第二部分 · 作业规程（五步闭环，缺一步都不算完成）

> **核对基线 → 读进度与约束 → 干活 → 门禁自检 → 审查 → 记录。**
> 只跑通测试**不算交付**。四处记录**全部必须带日期**（`YYYY-MM-DD`）。

## 第 0 步：核对基线（**不要跳过**，防止在错误基线上工作）

```bash
git status -sb                 # 分支 optimize/interview-ready；预期「尚未提交」的改动很多（B5~B7）
git rev-parse HEAD             # B7 收尾时是 a670373185cd074b4ceae0c4008e55135276f341
./venv/Scripts/python.exe -m pytest -q --junitxml=tmp/b8_baseline_junit.xml
./venv/Scripts/python.exe -c "import xml.etree.ElementTree as ET; \
  s=ET.parse('tmp/b8_baseline_junit.xml').getroot().find('testsuite'); \
  print('tests=',s.get('tests'),'failures=',s.get('failures'),'errors=',s.get('errors'))"
# 预期：tests= 884 failures= 0 errors= 0
```

- **⚠️ 取数口径（踩坑 A6，B7 起）**：**不要**用退出码和 stdout 汇总行判断绿红 ——
  环境删除守卫会在 sessionfinish 吃掉汇总行、把退出码变成 1，**测试其实全绿**。
  权威计数 = JUnit XML；warning 条数 = `pytest --collect-only -q` 的 warnings summary。
- 预期 warning：**5 条**（fastapi/httpx ×1、starlette/anyio ×1、faiss SWIG ×3）。
- 若对不上：先查是不是有人推进过（看 `git log` / 台账第 4 节），**不要**按本文盲改。

## 第 1 步：读进度与约束（按顺序，不要跳）

1. 台账 `docs/RAG_EXECUTION_PROGRESS.md`：
   第 1 节分批表（B7 ✅ / B8 ⏳）→ 第 2 节审查汇总（阶段 4 PASS）→
   **第 3 节 B7 的四条记录**（[T-4.1]/[D-12]/[D-13]/[T-4.2]/[T-4.3] + B14 缺陷 + 回滚资产发现）→
   **第 4 节执行暂停点**（B8 入口 + 已建未启用表 + 既有约束清单）→ 第 5 节环境坑（commit 前必读）；
2. `reports/rag_ingestion_auth_review/stage4_review.txt` —— **第六节 C 表就是 B8 的工作清单来源**
   （7.1~7.4 每条的"当前状态 + 处置建议"）；
3. `reports/rag_ingestion_auth_review/B7_task_level_review.txt` —— 第八节**测试盲区 7 条**、
   第十节**遗留 8 条**；
4. `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 第 7 节（B8 的范围来源）；
5. 踩坑 `docs/RAG_DEV_PITFALLS.md`：**A5 / A6**（门禁取数）、**B13 / B14**（契约）、
   **D12**（红绿判据）、[D-12] / [D-13]（B7 拍板的两条，不要重新讨论）。

## 第 2 步：干活 —— 本批 = 阶段 7（7.1 + 7.2 + 7.3 + 7.4）+ 前置 3 件事

### 进度快照（新窗口可直接开工，无需重做 B1~B7）

| 里程碑 | 状态 |
| --- | --- |
| 阶段 0 基线 / 阶段 1 数据库+身份 / 阶段 2 解析+接入 / 阶段 3 切分+索引 / 阶段 4 权限+隔离 | ✅ 全部 PASS |
| 检索 API 已接 B 轨（服务端构造 filter + FAISS 预过滤 + `retrieval_path` 标注） | ✅ B7 |
| 检索层隔离测试（23 条）+ 真实路由层 ACL 测试（7 条）+ API 出口测试（9 条） | ✅ B7 |
| 全量测试 884 / 0 failures / 0 errors；ruff / compileall / 体积全绿；warning 5 | ✅ B7 收尾 |
| **7.2 四种文件端到端链路** | ❌ 未做 |
| **7.4 索引回滚**（函数有、测试无） | ❌ 无法判定 |
| 7.3 安全审查的**逐条证据落盘** | ⚠️ 测试已有，证据未归档 |

### ★ 前置 3 件事（开工第一小时就做，做完才有资格写门禁结论）

**1. 处置索引回滚资产（阻断 7.4）—— 三选一并登记**

   `index_builder.py` 的 6 个导出符号零调用点、零测试：
   `rollback_index` / `available_versions` / `active_index_version` / `index_root_for` /
   `build_entries` / `ERROR_INDEX_ROLLBACK_FAILED`。

   * **选项①（推荐）补测试**：`rollback_index` 只改指针、不重建、不删文件，
     且回滚前会重新校验旧版本目录。用 `tests/retrieval_fixtures.py` 的现成环境就能测：
     建两个版本 → 回滚到 v1 → `load_active_manifest` 确认指针与条目 →
     再断言「待回滚版本目录被删/损坏时回滚**失败**且指针不动」。
     顺手断言 `available_versions` / `active_index_version` 的行为。
   * **选项② 补 API 出口**：若判断运维需要后手，新增 `POST /ingestion/indexes/rollback`，
     权限**不得低于** `index:rebuild`。成本高（新端点 + 审计 + 测试）。
   * **选项③ 显式声明**：在台账 `[D-14]` 里写明「回滚为预留能力，不在本次验收范围」+
     理由。**代价**：7.4 的该条判据只能写 NEEDS_WORK 或"不适用（声明）"。
   * **禁止**既不测、不用、也不声明。
   * 拍板后按 [D-8] 写法标注方向，登记为 **[D-14]**（现有决策记录到 [D-13]）。

**2. 给 B7 的接线补 AST 守卫**（B7 挂出来的遗留 1）

   现有 `TestProductionCallPoints` 只扫 `pipeline.py` / `queue.py`。建议在
   `tests/test_ingestion_pipeline.py` 或新文件补三条断言（沿用 D1 的 AST 手法，不查字符串）：
   * `routers/retrieval.py` 必须引用 `retrieve_chunk_items` 与 `build_chunk_access_filter`；
   * `utils/vector_retriever.py::search_chunk_index` 的 `access` 参数**必须没有默认值**
     （AST 查 `arg.annotation` + `defaults`，防"顺手加个默认全库"）；
   * `routers/*.py` 不得 import `jwt`（防在 router 里重建身份解析）。

**3. 顺手项**：核对并更新 README 的测试数到 **884**（F4 残留）。
   2026-09-23 实测：**不是「一行的事」，共 3 处要改** ——
   · 第 8 行 badge：`badge/tests-332%20passed-brightgreen`；
   · 第 286 行「测试文件数 | 30」表格行；
   · 第 427 行目录树注释「30 个测试文件 / 332 用例」。
   测试文件数也要重数（`ls tests/*.py | wc -l`），改完顺手核一遍三处口径一致。

### 7.1 自动检查（四件套，收尾时跑最终一轮，证据落 `B8_*`）

```
pytest -q --junitxml=reports/rag_ingestion_auth_review/B8_full_test_junit.xml
ruff check .            → B8_ruff-output.txt
compileall services routers schemas models alembic main.py scripts utils config
                        → B8_compileall-output.txt
scripts/check_repo_data_size.py → B8_data-size-output.txt
pytest --collect-only -q        → B8_collectonly_warnings.txt（取 warning 汇总）
```

### 7.2 四种文件端到端链路（**B7 未做，本批必做**）

对 **PDF / DOCX / HTML / Markdown 各跑一次**（样本用 `tests/fixtures/` 现成的，
由 `scripts/make_parser_fixtures.py` 生成，可复现）：

1. 走**真实路由**上传（不是直接调 pipeline）→ 拿 `job_id`；
2. 消费队列（`services.ingestion.worker` 或测试内直接 `process_job`）→ 跑完 8 阶段；
3. 重建索引（`POST /ingestion/indexes/rebuild` 或 `rebuild_index`）；
4. `POST /retrieval/search` 检索，**断言能命中该文档的 chunk**；
5. 验证链路串联：`document_id` → `document_versions` → `document_chunks` →
   manifest 条目 → 检索结果的 `document_id` / `document_version_id` / `page_start` /
   `heading_path` / `acl` 一致；
6. 长文档至少 2 个 chunk（样本里选最长的，或临时造一份多段 Markdown）；
7. 重复上传同一文件 → 不产生重复**有效**版本（content_hash 判重，B6 已实现）；
8. 失败任务可查错（`GET /ingestion-jobs/{id}` 有 `error_code`）且可重试（`/reprocess`）。

交付：一条端到端测试（建议 `tests/test_release_gate.py`，4 格式 parametrize）
+ 运行输出证据 `B8_e2e_four_formats.txt`。
**注意**：DOCX/PDF 是二进制 fixture，走真解析器（pymupdf / python-docx 已装），
**不要**为省事只测 md/html。

### 7.3 安全审查逐项（六条，逐条把"已覆盖"变成**可指认的证据**）

| # | 计划 7.3 原文 | 现有覆盖（B7 收尾） | B8 要做什么 |
| --- | --- | --- | --- |
| 1 | 伪造 `X-User-Role` 不提升权限 | `test_forged_tenant_header_cannot_change_scope` 等（B2） | 在结论文件里指认用例名 |
| 2 | body 的 `tenant_id` 不影响范围 | `test_body_tenant_id_does_not_widen_scope` + `test_client_supplied_tenant_and_filter_are_ignored` | 同上 |
| 3 | 跨租户资源返回 404/403 且不泄漏存在性 | `test_cross_tenant_and_missing_resource_are_indistinguishable` + `TestAclOnRealRoutes::test_controlled_document_does_not_leak_through_job_or_detail_endpoints` | 同上 |
| 4 | 无 ACL 文档不出现在结果、引用、trace、错误信息 | `test_retrieval_isolation.py` 23 条 | 同上；**补查一条**：错误路径（503 等）的 `detail` 不含 chunk 明细 |
| 5 | 未发布 / 归档 / 过期 / 旧版本不进入索引 | `TestVersionVisibility` 3 条（未发布）+ `list_published_chunk_refs` 只取 published | **补**「旧版本」：同一文档发布 v2 后，v1 的 chunk 是否仍可见？若可见 = 缺陷（写用例证明现状并登记） |
| 6 | 索引构建失败不破坏旧版本 | B6 的原子切换测试 | 指认用例名 + 复跑一次 |

### 7.4 阶段完成条件 → 写结论

- 全部自动测试通过（7.1）
- 四种文件端到端通过（7.2）
- 权限负向测试全部通过（7.3 的负向集）
- 索引原子切换 ✅ / **回滚**（取决于前置项 1 的拍板）
- 审查材料保存在 `reports/rag_ingestion_auth_review/`
- 结论写 `PASS` 或 `NEEDS_WORK`（**NEEDS_WORK 时不得进入后续阶段**）

### 硬性约束（每条都有依据，不是可选建议）

1. **不要退回"按租户分片"索引**（B14）：`all_tenants=True` 是有意的，manifest 留痕 +
   `tenant_count` 是外部可证明观测量；
2. **`access` 参数不得加默认值**；`ChunkAccessFilter` 只能服务端构造
   （唯一构造点 `services/retrieval_access.py:162`）；
3. **过滤必须 FAISS 原生预过滤**，不改回"先全局 top-k 再后筛"；
4. **无权限对查询者 = 零命中，缺 filter 对调用方 = 报错** —— 拍板见 **[D-12]**，不要重开；
5. **ACL 语义三条**（无记录=租户内可见 / `write` 不隐含 `read` / `group` fail closed）——
   拍板见 **[D-13]**；要改必须补新的决策记录；
6. **发布与索引重建分开授权**，`index:rebuild` = {supervisor, admin} 的收窄是有意的；
7. 鉴权只走 `services/auth_context.py`，router 不 import `jwt`；
8. 切分入口签名冻结；删除 chunk 必须单条 DELETE（B13）；读回顺序只信 `metadata_json["ordinal"]`；
9. **每个新模块必须登记生产调用点**（B7 在 `rollback_index` 上吃过这个亏）；
10. **提交/推送归用户**，你不做 git 写操作。

### 本批**不做**的事（防 scope creep）

- 不修 F2（评测口径）—— 它要动评测脚本与数字口径，单独立批；
- 不实现 worker 进程编排（F5）—— 属部署面，在结论里声明为已知限制；
- 不引入真实引擎复验（无 Docker/Redis）—— 同上；
- 不给 A 轨路径加权限（它读的语料本来就没有租户维度，加了是假安全）。

## 第 3 步：门禁自检（做完必须跑，全绿才能进第 4 步）

见第 2 步 7.1 的命令清单。判读规则：

- 起点 **884 / 0 failures / 0 errors**；结束时**只应增加、不应减少**，
  任何新失败都不得归因于"历史遗留"；
- warning 基线 **5 条**：只能持平或减少；新增 warning 必须解决而不是接受
  （用 `B8_collectonly_warnings.txt` 逐条核对来源）；
- 任一项不过 → 修完再跑，**不允许**带着红灯进入第 4、5 步；
- **⚠️ 一轮只跑一次全量**（A5）；取数一律 JUnit XML（A6）；
- 红/绿双份保留：中途出现过红灯，"问题现场"和"修复后"的输出**都**留在证据目录。

## 第 4 步：审查（两层，别混）

**任务级（本批必写）**：`reports/rag_ingestion_auth_review/B8_task_level_review.txt`，
格式照 `B7_task_level_review.txt`：门禁 / 交付物与生产调用点 / 逐条核对（7.1~7.4）/
真缺陷（若有）/ 偏离方向标注 / 测试盲区 / 硬性约束核对 / 遗留问题 / 结论。

**阶段级（本批必写，这是发布门禁）**：
`reports/rag_ingestion_auth_review/stage7_review.txt`，开头两行：

```
阶段编号：7（范围：7.1 / 7.2 / 7.3 / 7.4）
审查日期：<YYYY-MM-DD>
```

结论 **PASS / NEEDS_WORK** 必须明确；**PASS 只覆盖阶段 7**，
且若前置项 1 选了选项③（声明回滚不在范围）或 F2/F5 未修，
必须在结论里**逐条显式声明**为"已知限制"，不能让 PASS 看起来像"全部完美"。
**阶段未完成不得写 PASS** —— 7.2 或 7.3 或 7.4 有一条判据没落证据就不能 PASS。

## 第 5 步：记录（四处都要写，**且全部必须带日期**）

### 5.1 台账追加 → `docs/RAG_EXECUTION_PROGRESS.md`

```
### [T-7.1] 阶段 7：自动检查（<YYYY-MM-DD>）
任务编号：7.1
修改文件：…（只列新增/修改）
执行命令：…（等价 bash）
测试结果：…（JUnit XML 口径 + warning 条数 + 四件套）
验收结果：PASS
未解决问题：…
下一任务：…

### [T-7.2] 阶段 7：四种文件端到端链路（<YYYY-MM-DD>）   ← 若做了
### [T-7.3] 阶段 7：安全审查逐项（<YYYY-MM-DD>）
### [D-14] 决策记录：索引回滚资产的处置（<YYYY-MM-DD>）   ← 三选一拍板
### 阶段 7 收尾：阶段级结论 PASS / NEEDS_WORK（<YYYY-MM-DD>）
```

同时更新：第 1 节分批表（B8 → ✅/❌）、第 2 节审查汇总表（阶段 7）。
第 4 节是**唯一**允许覆盖更新的一节（它是"当前指针"）——
B8 是最后一批，把暂停点改写为「全部批次结束，等待用户提交/推送」即可。

### 5.2 踩坑追加 → `docs/RAG_DEV_PITFALLS.md`

四段式（**现象 → 根因 → 解决 → 面试怎么讲**），分类 A~E，新类开 **G**
（F 已被"尚未修复缺口"占用）。标题后括注日期。**无新坑也要写「本批未新增坑」，禁止断更。**

### 5.3 阶段审查 → `reports/rag_ingestion_auth_review/stage7_review.txt`

见第 4 步。证据文件统一 `B8_` 前缀落在同目录。

### 5.4 工作记忆 → `.workbuddy/memory/YYYY-MM-DD.md`

文件名用当天日期；**每次追加**用「## HH:MM 做了什么」的时间戳小标题，append-only。
长期有效的项目约定（若有新增）另写入同目录 `MEMORY.md`。

### 5.5 交接规程 → `docs/RAG_NEXT_WINDOW_PROMPT.md`（**每批结束必须重写**）

B8 之后如果没有下一批，就把它重写为「**项目收尾交接**」版本：
现在什么进度（全部批次状态）/ 还剩什么（0.5 节的未修清单）/ 怎么验证（门禁命令 +
JUnit XML 口径）/ 提交与推送注意事项（第 5 节环境坑）。**五件事的框架不变。**

## 纪律要求（硬性，逐条都有依据）

1. **记录必须带日期**（`YYYY-MM-DD`），台账/踩坑/审查/记忆四处都要 ——
   日期是判断"这份信息是什么时候的"的唯一依据；
2. **append-only**：台账第 3 节、踩坑记录、审查文件只追加不覆盖
   （例外：台账第 4 节是"当前指针"，允许覆盖更新）；
3. **踩坑四段式**，无坑也要写"未新增"；
4. **五步闭环**缺一不可，只跑通测试不算交付；
5. **阶段未完成不得写 PASS**；
6. **不确定的事标"待验证"**，环境噪声导致的红要判根因（A5/A6/D12）；
7. **提交/推送归用户**；对外动作先问，对内动作（读文件、跑测试、整理记录）大胆做。

---

# 附录 A · 环境坑速查（5 条，动手前必读）

| # | 坑 | 对策 |
| --- | --- | --- |
| 1 | **commit 嵌套 ref**：每次 commit 必踩 | 提交前按台账第 5 节的步骤操作；提交/推送由用户自理 |
| 2 | **push 间歇抽风**：代理与直连都被拒过 | 交替重试，以**远端 SHA** 为准（GitHub API 交叉核对） |
| 3 | **轻量 venv**：pip 直连镜像被拒 | 必须显式 `--trusted-host mirrors.aliyun.com -i https://mirrors.aliyun.com/pypi/simple/` |
| 4 | **删除守卫按轮计数**（A5）：一轮里反复重跑全量，会把绿跑成红 | **一轮只跑一次全量**；要多次取数就「一次运行 + 落盘 + 从文件读」 |
| 5 | **删除守卫吃掉汇总行**（A6）：pytest sessionfinish 被守卫打断，退出码 1 但测试全绿 | **取数用 `--junitxml`**；warning 用 `--collect-only -q`；判据 =「有无 F/E」+ JUnit XML，**不是退出码** |

# 附录 B · 常用命令

```bash
# 基线核对
git status -sb && git rev-parse HEAD

# 全量测试（A6 口径：JUnit XML 是权威）
./venv/Scripts/python.exe -m pytest -q --junitxml=reports/rag_ingestion_auth_review/B8_full_test_junit.xml
./venv/Scripts/python.exe -c "import xml.etree.ElementTree as ET; \
  s=ET.parse('reports/rag_ingestion_auth_review/B8_full_test_junit.xml').getroot().find('testsuite'); \
  print('tests=',s.get('tests'),'failures=',s.get('failures'),'errors=',s.get('errors'))"

# warning 汇总（不触发删除守卫）
./venv/Scripts/python.exe -m pytest --collect-only -q 2>&1 | tail -30

# 门禁其余三件套
./venv/Scripts/python.exe -m ruff check .
./venv/Scripts/python.exe -m compileall -q services routers schemas models alembic main.py scripts utils config
./venv/Scripts/python.exe scripts/check_repo_data_size.py

# 只跑本批 / 单文件（不重跑全量）
./venv/Scripts/python.exe -m pytest tests/test_retrieval_isolation.py -q

# 复核回滚资产是否仍无人调用（B8 处置前用）
for fn in rollback_index available_versions active_index_version index_root_for build_entries ERROR_INDEX_ROLLBACK_FAILED; do \
  echo "--- $fn ---"; grep -rn "\b$fn\b" --include=*.py . | grep -v venv | grep -v index_builder.py; done

# 复核检索侧接线仍在（B7 交付物）
grep -n "retrieve_chunk_items\|build_chunk_access_filter" routers/retrieval.py
grep -n "tenant\|acl" routers/retrieval.py | wc -l   # 预期 ≥ 10（B6 时是 0）
```

# 附录 C · 本批自检清单（开工前对一遍，交付前再对一遍）

**开工前：**

- [ ] 本文交接时间未过期；`HEAD` / **884 tests / 0 failures / 0 errors**（JUnit XML 口径）/ 工作区状态与本文一致
- [ ] 已读懂 0.2：检索 API 已接 B 轨、**索引是全局一份**（不要退回按租户分片）
- [ ] 已看清 0.4：回滚一族资产零调用点零测试，是本批第一个工作项
- [ ] 已确认 0.5「本次不做」清单（F2 / F5 / 真实引擎），不打算扩大范围
- [ ] 已读 stage4_review 第六节 C 表（B8 工作清单来源）+ B7 任务级审查第八/十节
- [ ] 已读 [D-12] / [D-13]（B7 的拍板，不要重新讨论）
- [ ] 已读踩坑 A5 / A6 / B13 / B14 / D12 与台账第 5 节环境坑

**交付前：**

- [ ] 前置项 1 已拍板并记录 **[D-14]**（回滚资产三选一）；若选补测试，用例已落盘并绿
- [ ] 前置项 2 已补接线 AST 守卫（router 引用检索函数 / `access` 无默认值 / router 不 import `jwt`）
- [ ] **7.2 四种文件端到端已逐格式跑通**，证据 `B8_e2e_four_formats.txt`（或等价）
- [ ] 7.3 六条逐条有**可指认的用例名或证据文件**；「旧版本不进索引」那条已实测并如实记录
- [ ] 7.4 每条判据都有结论；**PASS / NEEDS_WORK 已明确写出**
- [ ] pytest（≥884）/ ruff / compileall / 体积检查 全绿；warning ≤5（A6 口径）
- [ ] 任务级审查逐条核对完；"超出文档"处已标注方向；测试盲区已排查
- [ ] **`stage7_review.txt` 已写**，开头有「阶段编号：7」与「审查日期」两行
- [ ] 台账追加（**带日期**）+ 分批表/审查汇总表已更新 + 第 4 节暂停点已改写为收尾状态
- [ ] 踩坑追加（**带日期**，无坑也写"未新增"）
- [ ] 记忆日志追加（**带时间戳小标题**）
- [ ] **`docs/RAG_NEXT_WINDOW_PROMPT.md` 已重写**（B8 之后 = 项目收尾交接版，五件事齐全）
- [ ] 证据文件已落 `reports/rag_ingestion_auth_review/`（`B8_` 前缀；红/绿两份都在）
- [ ] **没有做任何 git 提交/推送**（那是用户的事）
