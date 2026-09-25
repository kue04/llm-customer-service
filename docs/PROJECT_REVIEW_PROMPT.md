# 项目评审提示词（新窗口专用）

> 用途：新开窗口后**整份粘贴**，让 AI 只读地评审 `D:\llm\llm-customer-service` 的进度与差距。
> 与 `docs/RAG_NEXT_WINDOW_PROMPT.md` 的区别：那份是「接手继续干活」，本份是「只读评估 + 产出差距结论」。
> 生成日期：2026-09-25。

---

## 0. 你的角色与任务

你是被请来做**项目评审**的工程师，**不是来接活改代码的**。

项目仓库：`D:\llm\llm-customer-service`（分支 `optimize/interview-ready`）。
项目定位：外卖售后 RAG 客服系统的重构，**定位是面试作品** —— 所以「过程可讲」和「结果正确」同等重要。

请读懂项目后，输出三样东西：

1. **进度快照**：这套系统现在做到哪一步了（已完成 / 部分 / 未做，分档说清）；
2. **差距清单**：距离「可交付、可面试展示」还差什么，**按优先级排序**，并指出每一项的**性质**
   （是质量问题？部署问题？还是产品口径没定？）；
3. **证据**：每条结论都要给「文件路径 + 符号名 / 实测数字」。

**本次只读分析，禁止修改任何文件**（包括不改文档）。要动代码请先问我。

---

## 1. 硬纪律（不遵守视为任务失败）

1. **结论必须带证据**。写「检索层已经做完了」不算数；要写
   「`utils/hybrid_retriever.py::search_hybrid_chunks` + `reports/retrieval_hybrid/evaluation_20260925.txt` 实测 R@1=…」。
2. **不许把「未实现 / 未就绪 / 未演练 / 已建未启用」读成已完成**。
   尤其 `已建未启用`（代码写了、测试有、**生产调用点为零**）按**未完成**计。
3. **区分「能力已具备」与「已演练」**。例：索引回滚有端点、有测试，但**没有故障场景演练证据**，
   所以只能说「能力已具备、未演练」，**不能说「回滚能力已就绪」**。
4. **文档里的数字会漂**（HEAD 号、工作区状态、测试条数）。**引用前先核对**：
   `git rev-parse HEAD`、`git status --short`、以及 JUnit XML。
   本仓历史上出现过「交接文件写的 HEAD 和实际不一致、工作区状态描述已过期」——**不要照抄文档里的状态描述**。
5. **两套数字不可互认**（本项目最容易犯的错）：
   - **A 轨**：781 条种子 FAQ、按 `intent` 判相关、含 rerank —— 是**演示路径**（README §3.1 / §3.2）；
   - **B 轨**：9229 个文档 chunk、按 `span` 金标、稠密+稀疏混合检索 —— 是**正式路径**（README §3.6）。
   两套的指标数字**不能混用**，也不存在「A 轨数字 vs B 轨数字」的对比。
6. **门禁取数一律用 JUnit XML**（tests / failures / errors / skipped），
   **不要用 pytest 退出码**：本仓有环境守卫会吞掉汇总行并把退出码变成 1（测试其实全绿）。
7. 不确定的写「**待验证**」，不许为了凑完整答案而编。

---

## 2. 阅读清单

### A. 必读（按此顺序，6 份，约 1 小时）

| 序 | 文件 | 读什么 / 为什么 |
| --- | --- | --- |
| 1 | `docs/RAG_NEXT_WINDOW_PROMPT.md` | **先读这份**。它是「交接 + 作业规程」，会告诉你当前指针、待办顺序、以及哪些数字别信旧版 |
| 2 | `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` → **§1.0** | **差距答案的核心**。这是「现状总览」权威表：**13 条规范要求逐项给了「已实现/部分实现/未实现」+ 实测证据 + 详见章节**。再读它的 **§17**（作业规程） |
| 3 | `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md` | 差距清单与路线图。**§3 已经做过「按 goal.md 四档验收对表」**（完成度百分比），不必自己重做；重点看 §4 十大差距、§8 路线图、§8.3「明确暂不做」 |
| 4 | `docs/goal.md` | 原始目标与四档验收标准 —— 它是**标尺**，上面的 roadmap §3 就是对着它打分的 |
| 5 | `docs/RAG_EXECUTION_PROGRESS.md` | **台账**，3013 行，**别通读**。只读 §1（批次表）、§2（阶段审查结论汇总）、**§4（执行暂停点 = 当前指针，覆盖更新，最权威）** |
| 6 | `README.md` | **只读 §3.6（检索质量实测）+ §0.1 + 顶部状态/实测数据表**。注意 §3.1 / §3.2 是 A 轨演示路径口径（见纪律 5） |

### B. 按需查（有具体问题时再查，不要通读）

| 文件 / 目录 | 什么时候查 |
| --- | --- |
| `docs/RAG_DEV_PITFALLS.md`（2569 行，**87 条踩坑**） | 想判断「某个坑是不是已知的 / 有没有被修」时**按编号或关键词 grep**，别通读 |
| `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` | 需要「原始任务清单（阶段 0~7）」来判断**哪些任务根本没做**时 |
| `reports/rag_ingestion_auth_review/` | 找阶段结论：`stage0~7_review.txt`、`B*_task_level_review.txt`、`F6_task_level_review.txt` |
| `reports/retrieval_hybrid/evaluation_20260925.txt` | 要看检索质量的完整数字（三模式 × 两数据集 + 互补性 + 延迟） |
| `docs/RAG_DOCUMENT_CORPUS_PLAN.md` | 关心「语料从哪来、多少、怎么灌」时 |
| `.workbuddy/memory/MEMORY.md` | 想快速拿到「现役取值表」（默认模式、融合权重、缓存键等）时 |
| `docs/RAG_B3_B4_INTEGRATION.md` | 只想知道 B3→B4 的接口契约时（历史交付说明，不看也不影响结论） |

### C. 别读（浪费时间，或会把结论带偏）

| 文件 | 为什么别读 |
| --- | --- |
| `HANDOFF.md`（1795 行，2026-06-27） | **最浪费时间的一份**。6 月的老交接，已被 `docs/RAG_NEXT_WINDOW_PROMPT.md` 完全取代；读它只会拿到过期状态 |
| `STUDY.md`（1723 行） | 个人学习笔记，与工程进度无关 |
| `RESUME_NOTES.md`、`RESUME_OPTIMIZATION_2026-09-25.md`、`PROJECT_SHOWCASE.md` | 简历 / 对外展示包装材料，不是工程事实来源 |
| `README - 副本.md` | 文件副本，无价值 |
| `docs/RAG_OPTIMIZATION_DECISION_TREE.md`、`docs/RAG_STAGE_LESSONS.md`、`docs/RAG_EVALUATION_LESSONS.md`、`docs/BAD_CASES.md` | 都是 2026-06 的 **A 轨老链路**笔记。**读了会混淆两套数字口径**（纪律 5），对判断当前进度无益 |
| `docs/BASELINE_2026-07-16.md` | 7-16 的基线，已被 2026-09-23 的新基线取代 |
| `docs/EVALUATION.md` | 文件自己第一行就声明「已被 README 取代，请以 README 为准」 |
| `docs/FULLSTACK_RAG_IMPROVEMENT_PLAN.md`（1610 行，7-16） | 老的实施计划，已被 `RAG_EXECUTION_PLAN_*` 取代 |
| `docs/ENTERPRISE_AI_CUSTOMER_SERVICE_PRD.md`（1054 行） | 产品需求背景（PRD）；判断「做到什么算合格」请以 `RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` 为准 |
| `docs/API_INTEGRATION.md`、`docs/FRONTEND_*.md`、`docs/frontend/` | 前端对接文档。除非你被明确要求评估**前端进度**，否则与本任务无关 |
| `AGENT.md` | 通用 LLM 编码行为守则，与项目进度无关 |
| `data/`、`tmp/`、`venv/`、`models/`、`local_models/`、`eval_outputs/`、`output/`、`reports/` 里的 `.jsonl`/`.db`/`.png` | 数据与环境产物，不要当文档读 |

---

## 3. 分析框架（要回答的问题）

1. **主线做到哪了**：把 `docs/RAG_EXECUTION_PROGRESS.md` §1 + §2 读成一句话 —— 哪些批次完成、哪些阶段 PASS。
   注意区分 **「阶段 PASS」与「发布门禁 PASS」是两件事**，后者还带限制声明。
2. **对照验收规范逐项过**：用 `RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` §1.0 那 13 条，
   逐条复述「现状 + 依据」，并**标出哪些是 `未实现` / `未演练` / `已建未启用`**。
   **重点**：`未实现` ≠ 成绩，它列的是目标与差距。
3. **检索质量到底多少**：只引 **B 轨**（§3.6）数字，说清「增益上限本来就很小」和「口语化集是短板」。
   若引用延迟数字，必须同时说清那是**延迟**不是**质量**。
4. **剩余差距按性质分类**（这一步是本次评审最有价值的部分）：
   - **质量问题**（如口语化查询召回低）；
   - **部署问题**（如容器编排从未真跑过）；
   - **产品口径未定**（如「同名不同格式算不算同一文档」）；
   - **口径未统一**（如评测指标两套不可互认）；
   - **有代码但没演练**（如回滚、RPO/RTO）。
5. **给一句判断**：距离「只差最后一步」还差几步？哪一步最堵？

---

## 4. 输出格式

- 用**中文**，短句，先给结论再给理由；不要客套铺垫。
- 差距清单用表格：`项 | 现状 | 依据（文件/数字） | 性质 | 优先级`。
- 每条结论都要能指回**具体文件 + 符号 / 数字**；指不回去的写「待验证」。
- 最后单独列一节：**「我读不出来的 / 需要人拍板的」**（诚实列出盲区，别硬凑）。

---

## 5. 已知结论（**待你核对**，不要照抄）

以下是上一批（2026-09-25）留下的判断，请你**独立核对后**再决定采信或修正：

- 全量测试 **1045 passed / 0 failed / 0 errors**（基线 1030）；`ruff .` / `compileall` / 体积门禁全过。
- 阶段 0~7 全部 PASS，**发布门禁 PASS 但带三条限制声明**：判据范围只到计划第 7 节 / roadmap 两条 P3 未做 / 检索质量口径未统一。
- 索引：`v4` · **9229 chunk** · `BAAI/bge-small-zh-v1.5`(512 维)；稀疏路 `unicode61-bigram-v1`。
- **默认召回模式仍是 `dense`**（hybrid 需显式启用）。
- B 轨实测：title(81) hybrid R@1 **0.7407** / R@10 **1.0000**；口语化(30) hybrid R@1 **0.4000**（**与 dense 完全相同，零收益**）。
- **docker 部署从未真跑过**（本机无 docker 命令）。已发现两个**部署硬缺口**（**请自行核实**）：
  1. `requirements.txt` 里 `psycopg[binary]` **被注释掉**，但 `docker-compose.yml` 把 `RAG_DATABASE_URL` 写死成 `postgresql+psycopg://…` → 镜像里没有驱动；
  2. `.env.example` 里 `RAG_JWT_SECRET=` **为空**，而 compose 两个服务都用 `env_file: .env.example` → 服务能起、但每个受保护请求 500（fail closed）。
  连锁后果：`/health` 不碰 DB 也不碰鉴权 → healthcheck 会**假绿**（`docker compose ps` 显示 healthy，实际链路是断的）。
- 其余待办（见台账 §4 有完整版）：口语化查询改写、入库侧多格式孪生、F2 评测口径、冷启动预热与多 worker 内存预算、PostgreSQL 迁移/鉴权复验、压测 P95/P99、真实 OCR 复验。
- **（2026-09-25 新发现，请核对）CI 基本没在生效**：`.github/workflows/ci.yml` 只在 `master` / `main` 触发，
  而本项目开发分支是 `optimize/interview-ready` → **这几道门禁（ruff / 体积 / 验收规范 / pytest）从未在本分支自动跑过**。
  另：`scripts/check_doc_render.py` **不在 CI 里**（CI 只挂 `scripts/verify_acceptance_spec.py`，仅校验验收规范那一份），
  它目前是**人工脚本**，别把它当成自动门禁 —— 「有脚本」≠「有门禁」。
- **（2026-09-25 新发现）文档渲染风险实测**：`check_doc_render.py` 报 **3 / 26 份文档**有问题，
  渲染时**代码块会静默消失**：`docs/goal.md`、`docs/RAG_DEV_PITFALLS.md`（26 处）、`docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md`（2 处）。
  **注意 `docs/goal.md` 在列** —— 它是验收标尺，读它时若是渲染版，可能有大段内容被吞。

---

## 6. 一句话收尾

**这次评审的价值不在于「还差什么」，而在于「把差距分对类」** ——
把部署问题当成质量问题去优化、把没演练的能力说成已具备，
都会让后续的投入花在错的地方。
