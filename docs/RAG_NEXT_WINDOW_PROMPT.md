# RAG 改造：交接与作业规程（统一提示词）

> **交接时间**：2026-09-25（星期五）15:35
> **交接时状态**：主线 **B1~B8 全部完成**｜阶段 0~7 全 **PASS**｜发布门禁 **PASS**（带三条限制，见 §1.4）
> ｜**B17 检索层内容级去重已完成**（聊天链路有效证据 **2.14/3 → 3.00/3**）
> **工作区**：**不干净** —— 叠着**三批**未提交产出（F1 / B8 / B17），HEAD 仍是 `7d4a34b`（提交归老霸）
> **门禁**：**1030 / 0F / 0E / 0S**（`reports/rag_ingestion_auth_review/B17_full_test_junit.xml`）
> ｜**warning = 5（实测值）**
>
> ### ★ 下一步（唯一定向，别自己另挑）
>
> **`[F6]` `load_active_manifest` 无缓存** —— 每次检索重读 **19.0MB** manifest，
> 使 hybrid 延迟 ≈ dense + sparse 之和。完整证据与修法见**第二部分**。
> 这一批**只做这一件事**，不要顺手带别的。
>
> **用法**：① 在新窗口说「读 `docs/RAG_NEXT_WINDOW_PROMPT.md`，按里面的规程继续」（**推荐**，
> 不会因复制截断失真）；② 整份粘贴（**别只贴第二部分**，第三部分的取数口径最容易踩错）。
>
> **⚠️ 先核对再动手**：本文所有数字都带时间戳。若实测与本文不符
> （例如全量不再是 `1030 / 0 failures`、或 HEAD 不再是 `7d4a34b`），
> 说明**已经有人推进过** —— 先跑第三部分第 0 步核对，**不要照着本文盲改**。

---

# 第一部分 · 现状快照（唯一口径）

## 1.1 一句话

外卖售后场景的**客服 RAG 后端**（FastAPI + SQLite/PostgreSQL + FAISS）。
主链路「检索增强问答 + 客服工作流」：意图识别 → 检索 → 重排 → 证据分级 → 生成 →
规则兜底 → 风险分级 → 转人工。**12 个 router、41 个测试文件、1030 条用例**。

理想目标在 `docs/goal.md`（方向，不是任务清单）；差距分析在 `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md`。

## 1.2 双轨制已合流 —— 两条链路现在都是真的

「两条链路都叫检索，没人知道哪条是真的」（F1）分两步合上：
**B7（2026-09-23）合上检索 API 侧**，**F1 切轨（2026-09-25）合上聊天侧**。

```
【B 轨 · 正式路径】
  POST /retrieval/search      retrieval_mode = dense | sparse | hybrid（默认 dense）
    → require_read_operation_role
    → build_chunk_access_filter(session, auth)         ← services/retrieval_access.py（唯一构造点）
    → search_hybrid_chunks(...)                        ← utils/hybrid_retriever.py（底层召回）
        ├─ 稠密：search_chunk_index（FAISS 原生预过滤）
        └─ 稀疏：search_sparse_index（FTS5 bm25，临时表 JOIN 预过滤）
        再按 1/(k+rank) 加权 RRF 融合（w_dense=10 / w_sparse=1 / k=60）
    → retrieve_hybrid_items / retrieve_chunk_items     ← ★ 条目层：这里截断到 limit
         └─ select_diverse_evidence(...)               ← ★ B17 新增：截断前先去重
    → 响应带 retrieval_path="chunk-index" + index.sparse_available

【聊天链路】默认走 B 轨（F1 切轨 2026-09-25）
  POST /chat/prompt
    → require_read_operation_role（chat_generate）
    → get_answer_from_rag(request, auth)
    → resolve_chat_retrieval_path()                    ← 默认 "chunk"；RAG_CHAT_RETRIEVAL_PATH=seed 回退
    → retrieve_chunk_items_for_chat()                  ← ★ B17 已在这一层去重（上面的两条入口之一）
    → adapt_chunk_items_for_prompt()                   ← chunk 的 text 映射成下游认的 answer
    → build_prompt_context_items()                     ← 下游还会按文本去重（口径见下）

【A 轨 · 演示 / 兼容路径】
  POST /retrieval/search-demo、/retrieval/prompt-preview
    → retrieve_by_real_vector（781 条种子 FAQ，数据源本身没有 tenant/ACL 两维）
    → 响应带 retrieval_path="seed-faq-demo"
```

**索引**：全局一份（B14 教训，B7 修掉）。`rebuild_index` 默认 `all_tenants=True`，
版本号**全局**递增。**不要退回「按租户分片」** —— 会让先入库的租户**静默消失**。

**当前生效索引**：`v4` —— 稠密 18.9 MB + manifest 19.0 MB + 稀疏 32.4 MB，**9229 chunk**。

## 1.3 必须遵守的既有守卫（改了会红，而且红得对）

| 守卫 | 位置 | 盯什么 |
| --- | --- | --- |
| `test_wiring_guards.py`（18 条） | tests/ | `routers/retrieval.py` 必须**引用并调用**检索与鉴权函数；`search_chunk_index` 的 `access` **无默认值且注解非 Optional**；`routers/*.py` **不得 import jwt** |
| `TestReadmeTrackConsistency` | `tests/test_ingestion_pipeline.py` | README 锚点 `<!-- f1-track: chat-service-retrieval=... -->` 与 `DEFAULT_CHAT_RETRIEVAL_PATH` **双向一致**；`<!-- b8-hybrid: default-retrieval-mode=... -->` 与 `DEFAULT_RETRIEVAL_MODE` **双向一致**；另有守卫自测（读不到 → 空串 → 判失败） |
| `TestHybridDecisionTrace` | `tests/test_hybrid_retrieval.py` | 把关键取舍做成可断言事实：`w_dense=10/w_sparse=1/k=60`、`DEFAULT_RETRIEVAL_MODE == "dense"`。**改权重必须重跑评测** |
| `TestDeploymentGuards` | `tests/test_ingestion_pipeline.py` | compose 真的起了 worker、共享卷、同元数据库与队列地址 |
| **`tests/test_retrieval_dedup.py`（26 条，B17 新增）** | tests/ | 截断前先去重；`top_k == 20`（召回量不许偷偷变大）；`access` 对象原样透传（去重层不得自造过滤器）；真实索引上收窄白名单后不越界且非空；混合路用 `fused_score`；判据与下游口径一致（拿同一批字符串喂两边比对） |

**判据换过一次，别换回旧写法**（踩坑 **D21**）：轨道守卫原来盯「AST 里有没有调
`retrieve_rag_items`」，改成开关制后**两条轨道同时调它**，那条判据会永远返回 `seed-faq`。
现在盯的是**默认值本身**。

## 1.4 ★ 发布门禁 PASS，以及它的**三条限制**（引用时必须一起带上）

阶段 7 结论：**PASS**（`reports/rag_ingestion_auth_review/stage7_review.txt`）。

1. **判据范围只到计划第 7 节的四条**。**不覆盖**：检索质量指标（M10 Recall@k 等）、
   真实 OCR 引擎、病毒扫描与限流、压测与 P95/P99、成本账本。这些在
   `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md` §1.0 / §10.5 里标为 `未实现` / `未就绪`，
   是**差距清单，不是成绩单**；
2. **roadmap 建议归入 B8 的两条 P3 未做**：真实 OCR 引擎复验、压测与 P95/P99 聚合；
3. **检索质量无统一结论** —— 这条**部分更新**为：
   > 「B 轨有**一套**带 span 金标的结论（title 81 / 口语化 30 / 负样本 40），
   > 但 A 轨 intent 口径（F2）仍未统一，**两套数字不可互认**。」
   `test_release_gate.py` 验证的是**链路连通性**不是检索质量。
   **不得把 PASS 读作「检索效果已验收」**，也不得说「检索质量已全面评测」。

> **B17 之后追加一条口径提醒**：去重改的是**证据条数**（聊天 3 个名额装几条不同内容），
> **不是**召回质量。对外说法是「聊天有效证据 2.14/3 → 3.00/3」，
> **不能**说成「检索质量提升」。

> **规范引用纪律**（验收规范 §17.5）：规范编号（`I01~O01`、`§N`）与本项目编号
> （`B1~B8`、`阶段 N`）是**两套**，引用必须带前缀；且规范 §7.x 与执行计划「阶段 7.x」
> **编号撞车但内容不同**。

## 1.5 混合检索的三条硬结论（对外口径必须按这个讲）

实测（2026-09-25，走生产代码 `search_hybrid_chunks`；**B17 已重跑复验：六行数字逐位不变**）：

| 数据集 | mode | R@1 | R@5 | R@10 | MRR | NDCG@10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| title（81） | dense | 0.7160 | 0.9630 | 0.9877 | 0.7698 | 0.8204 |
| title（81） | sparse | **0.7901** | 0.9012 | 0.9877 | **0.8257** | **0.8621** |
| title（81） | **hybrid** | 0.7407 | 0.9630 | **1.0000** | 0.7922 | 0.8404 |
| 口语化（30） | dense | **0.4000** | **0.5000** | **0.5667** | **0.4274** | **0.4588** |
| 口语化（30） | sparse | 0.1000 | 0.1000 | 0.1333 | 0.1037 | 0.1100 |
| 口语化（30） | **hybrid** | **0.4000** | **0.5000** | **0.5667** | **0.4274** | **0.4588** |

1. **增益上限本来就很小**：两路 top-10 **并集召回** = title 81/81、口语化 18/30 ——
   这是**任何融合策略的天花板**；dense-only 已达 80/81；
2. **等权融合有害**：口语化集等权 RRF 把 R@1 从 0.4000 拉到 **0.2667**；
   `w_dense=10` 是扫完权重后**唯一在两套集上都不掉点**的配置；
3. **不是「指标全面上涨」**：收益 = title 集 R@1 **+2 条** + R@10 打满；口语化集**零退化**。
   所以 **聊天链路不建议切 hybrid**（输入正是口语提问 → 零收益、双倍延迟）；
   只有"短查询为主"的接口值得切（切之前见 §2.4 第 6 条）。

> **★ 口语化 R@1 0.4000 的归因已经查清（B17，2026-09-25）**：
> 它**不是**金标质量造成的 —— 30 条里两路 top-50 都捞不到的 7 条，
> 经复核**全部是真·检索失败**（金标 span / 文档 / 章节三样都在库里），**假阴性 0 条**。
> 真问题是**口语化改写与库内表述的词面距离过大**。
> 所以：**不能**说"金标有问题"来搪塞，也**不能**把它当干净结论引用（F2 口径未统一）。
> 证据：`reports/rag_ingestion_auth_review/B17_colloquial_gold_recheck_20260925.txt`。

数字与全部口径说明：README §3.6 + `reports/retrieval_hybrid/evaluation_20260925.txt`。

## 1.6 已知但**范围外**的问题（登记在此，防止被遗忘）

| # | 问题 | 状态 |
| --- | --- | --- |
| 1 | **F6**：`load_active_manifest` 无缓存，每次检索重读 19 MB manifest | **★ 下一批就做它** |
| 2 | **入库侧多格式孪生**：同一份内容被 md/html/pdf/docx 各入库一次（**906 条冗余**） | B17 暴露的新欠账，需产品口径，**未修** |
| 3 | **F2**：`scripts/evaluate_retrieval_metrics.py:85` 按 intent 判相关（A 轨口径）| 未修 |
| 4 | **口语化查询改写**：7 条全库可达却进不了两路 top-50 | **未修**（B17 已定位方向） |
| 5 | **F5**：worker 有 CLI 无进程编排 | 未修，已在门禁结论里声明 |
| 6 | 真实引擎未复验（PostgreSQL / Redis / 真 OCR） | 未做 |
| 7 | 索引重建并发竞态 | 未测（单机 SQLite 难构造）|
| 8 | 图片块不进 chunk / PDF 跨页重复表头 | 设计如此 |
| 9 | **B6 第二步**：往 `食品安全投诉` 加「不新鲜」关键词 | **待老霸拍板**，属业务策略，**不许擅自加** |
| 10 | **前端**：界面复核（出截图）+ 补 `clarify` 词条 | 卡在前端 |

---

# 第二部分 · ★ 下一步：`[F6]` `load_active_manifest` 无缓存

> **本批的全名**：给「当前生效 manifest」加一层**带版本失效语义**的缓存，
> 把每次检索都要重读的 19.0MB manifest 变成一次读、多次用。
> **为什么是它**：B17（去重）已经做完，F6 是**剩下的唯一高优先级欠账**，
> 而且它是**纯延迟问题**、改动面小、有明确判据（见 §2.2）。
> **上一批刻意没顺手做它** —— 一个改召回内容、一个改缓存与失效，
> 混在一起出问题分不清是谁造成的。

## 2.0 上一批（B17）刚做完什么（**别重做**）

一句话：**检索层加了 Top-K 内容级去重**，聊天链路的有效证据从 **2.14/3 升到 3.00/3**。

- 新模块 `utils/retrieval_dedup.py`；接入 `retrieve_chunk_items` / `retrieve_hybrid_items`
  （**截断点后移**，召回条数**一条没改**——这点有测试钉住）；
- 判据用**归一化文本**（压平空白），**不是** `content_hash`
  （原文 hash 与下游"压平空白"口径不一致 → 只到 19/21，达不到 3.00）；
- 同分并列用确定性兜底键 `(-score, parent 优先, chunk_id)`（防 D24/D26）；
- 门禁 1030（基线 1004，+26）；ruff / compileall / 体积门禁全过；warning = 5；
- 交付证据与审查：`reports/rag_ingestion_auth_review/B17_*`、`B17_task_level_review.txt`。

⚠️ **不要去重做它**，也**不要**回头改判据为 `content_hash`（已实测达不到目标）。
若发现去重相关的问题，读 `B17_task_level_review.txt` 第四、五节再动手。

## 2.1 前置取证（**已完成，直接接上**）

**现象（待复现）**：评测报告里三模式的单条耗时是

```
dense      均值 172.7 ms
sparse     均值 185.7 ms
hybrid     均值 355.7 ms   ← ≈ dense + sparse 之和
```

hybrid 是**串行跑两路**，所以"和"本身正常；不正常的是**每一路都贵**：

| 事实 | 数字 | 出处 |
| --- | --- | --- |
| manifest 体积 | **19.0 MB**（索引 v4） | `describe_chunk_index` |
| hybrid 单条耗时 | 355.7 ms | `reports/retrieval_hybrid/evaluation_20260925.txt` |
| 稠密路单条耗时 | 172.7 ms | 同上 |
| 一次请求读取 manifest 次数 | dense 1 次 / sparse 1 次 / hybrid 2 次 | 调用点见下 |

**根因**：`services/ingestion/index_manifest.py:621 load_active_manifest` **没有缓存** ——
每次调用都把 19.0 MB 的 manifest 从磁盘读出来并反序列化。
检索侧三个调用点，每次检索都要走：

```
utils/vector_retriever.py:1074   ← search_chunk_index 用
utils/vector_retriever.py:1277   ← describe_chunk_index 用
utils/sparse_retriever.py:106    ← search_sparse_index 用
（另有 services/ingestion/index_builder.py:543，属写入侧，本批**不动**）
```

也就是说 **hybrid 一次请求 = 读两份 19MB manifest**（稠密一份、稀疏一份）——
这解释了"hybrid ≈ dense + sparse"里**为什么两边都慢**。

**修法方向（上一批已定，但动手前先自己验一遍 —— 见 §2.3 第 4 条）**：
键取 `(root, index_name, index_version)` 的缓存 + **版本切换时显式失效**
+ 一条「切版本后第一次检索就读到新的」测试。

## 2.2 交付清单（按顺序做，**第 1 步不许跳过**）

| # | 交付物 | 验收判据 |
| --- | --- | --- |
| 1 | **先量基线**：同一环境、同一 query 集，记录打缓存**之前**的 dense / sparse / hybrid 单条耗时（均值 / 中位 / P95） | 数字落盘，且能解释"为什么 hybrid ≈ dense + sparse" |
| 2 | **写决策记录**：缓存放哪一层、键怎么取、失效怎么触发、并发怎么处理 | 决策写进台账 §3，含**被否掉的备选**（如"进程级全局单例""按 mtime 判失效""干脆把 manifest 拆小"）|
| 3 | **实现**缓存 + 显式失效 | 生产调用点明确（本项目纪律：**定义在、测试在、没人调用 = 未完成**）|
| 4 | **测试**（≥8 条）：命中缓存 / 版本切换后失效 / 不同 `index_name` 不串味 / **切版本后第一次检索读到新索引** / 只读语义不变 | 新增用例；既有 **1030 条只增不减** |
| 5 | **量化验证**：重跑同一 query 集 | hybrid 单条耗时应**显著下降**；**评测指标必须逐位不变**（缓存只该改速度，不该改结果） |
| 6 | **并发安全说明**：写清"缓存被两个请求同时命中""重建索引与读缓存并发"时的行为 | 写得出来才叫想清楚；写不出来就显式记成**未验证** |
| 7 | 门禁 + 任务级审查 + 四处记录 + 重写本文件 | 见第三部分第 3~5 步 |

## 2.3 动手前**必须想清的 4 件事**（想不清就别写码）

1. **失效必须显式，不能靠时间/mtime 猜**。
   索引切换是**原子发布**（新版本目录 + 指针文件），所以正确的失效信号是
   **指针/版本号变了**，而不是"manifest 文件 mtime 变了"。
   靠 mtime 会有一个静默窗口：**版本已切、但缓存还认为自己是新的**，
   表现是"发了新索引，检索还在用旧的" —— 不报错、只是答案不对。

2. **缓存键必须含 `index_name`**。
   本仓同时存在 chunk 索引与别的索引名（`CHUNK_INDEX_NAME` 是默认值不是唯一值），
   键少一维就会**互相串味**：查 A 索引拿到 B 索引的 manifest，
   后果是命中一堆不属于该索引的 chunk_id。

3. **只读语义不能变**。
   `load_active_manifest` 现在**不修改磁盘**，加缓存后也必须保持只读；
   尤其**不要**为了"顺便预热"而在检索路径里触发写操作（`index_builder` 那条调用点属写入侧，
   **本批不要碰**）。

4. **登记里的"修法方向"也要先验**（B17 的教训，已登记为**D28**）。
   B17 登记的修法（按 parent 去重）与实际正确的修法（按归一化文本去重）**不是一回事**，
   是本批取证才发现的。所以：**先量到"读 manifest 到底占多少 ms"**，
   再决定缓存是"必要"还是"只是看起来该做"。
   如果量出来发现耗时主要在别的环节（比如模型加载 / FAISS 构建），
   **如实报告并停下讨论**，不要为了完成清单而做无效优化。

## 2.4 硬性约束（**不要改坏** —— 每条都有依据）

1. **不退回「按租户分片」索引**（B14）；
2. **`access` 参数不得加默认值**，注解不得含 `None` / `Optional`；
   `ChunkAccessFilter` 只能服务端构造（唯一构造点 `services/retrieval_access.py`）——
   现有 `tests/test_wiring_guards.py` 锁着；
3. **过滤必须 FAISS 原生预过滤**，不改回「先全局 top-k 再后筛」；
4. **无权限对查询者 = 零命中；缺 filter 对调用方 = 报错**（决策 [D-12]，不要重开）；
5. **ACL 语义三条**（无记录 = 租户内可见 / `write` 不隐含 `read` / `group` fail closed，[D-13]）；
6. **改默认检索模式要同步四件事**：改 `DEFAULT_RETRIEVAL_MODE` → 改 README 的
   `b8-hybrid` 锚点 → 同步 README §0.1 上线顺序与 §3.6 现状表 → 跑全量。
   前提：先 `rebuild_chunk_index.py` 并确认 `index.sparse_available=true`，否则**发版即 503**；
7. **不许动 B17 的去重语义**：召回量仍是 `top_k = limit×5`（下限 20），
   截断仍在去重之后（有测试锁）；本批只该让"读 manifest"变快；
8. **本批不新增模型依赖、不动已冻结决策**（[D-6] [D-12] [D-13]）；
9. **不许为了让数字好看而放宽门槛**；
10. **`git add` 一律精确路径**（E2：工作区可能有并行会话产出）；
11. **提交/推送由老霸自己执行**，AI 只给指令（见第三部分第 5 步）。

## 2.5 明确**不做**的（防 scope creep）

- **不改语料构造逻辑**（多格式入库是解析器覆盖测试的产物，清理是**独立决策**）；
- **不做数据迁移 / 删库清理**；
- **不切线上默认模式**；
- **不顺手做口语化查询改写**（B17 已定位方向，但那是**另一个批次**的事）；
- **不碰前端**。

---

# 第三部分 · 作业规程（五步闭环，缺一步都不算完成）

> **核对基线 → 读进度与约束 → 干活 → 门禁自检 → 审查 → 记录。**
> 只跑通测试**不算交付**。四处记录**全部必须带日期**（`YYYY-MM-DD`）。

## 第 0 步：核对基线（**不要跳过**）

```bash
git status -sb                 # ⚠️ 期望：**不干净**（叠着 F1 / B8 / B17 三批未提交产出）
git rev-parse HEAD             # 期望：7d4a34b（三批都没提交，所以 HEAD 仍不动）
git log --oneline -3
./venv/Scripts/python.exe -m pytest -q --junitxml=tmp/baseline_junit.xml
./venv/Scripts/python.exe -c "import xml.etree.ElementTree as ET; \
  s=ET.parse('tmp/baseline_junit.xml').getroot().find('testsuite'); \
  print('tests=',s.get('tests'),'failures=',s.get('failures'),'errors=',s.get('errors'))"
# 当前基线：1030 / 0 / 0 / 0 —— 以 reports/rag_ingestion_auth_review/B17_full_test_junit.xml 为准
# 历史基线（判断"有没有人推进过"用）：F1 收尾 949 → B8 收尾 1004 → B17 收尾 1030
```

> ⚠️ **`git status` 不干净是交接时的真实状态，不是异常**：
> F1 / B8 / B17 **三批改动都还没提交**（提交归老霸，见 §4.1 坑 1）。
> 看到 `M` / `??` 一大堆时**不要 `git checkout .`**（那会把三批产出全毁）。

**⚠️ 取数口径（踩坑 A6/A5/D15/D18）—— 这几条最容易搞错，全在这一段：**

- **不要用退出码和 stdout 汇总行判绿红** —— 环境删除守卫会在 sessionfinish 吃掉汇总行、
  把退出码变成 1，**测试其实全绿**；
- **warning 条数**取自**全量 stdout** 的 `N warnings`（**不是** `--collect-only`，
  它不执行测试、输出里没有 warning 汇总）。汇总行被吞 → 记 `N/A（汇总行被吞）`，
  **不许估算或沿用上次数字**。**B17 批次实测到 5 条**（那一轮汇总行没被吞）；
  B8 批次曾被吞、记的是 `N/A` —— 两种情况都发生过，别预设；
- **测试数两个口径**：stdout 的 `N passed` **不含 subtest**，JUnit XML 的 `tests=` **含**
  （唯一 `subTest` 在 `tests/test_evaluate_chat_grounding.py`）。
  **B17 实测：JUnit 1030 / collect-only 1026，差 4**。
  **门禁只认 XML**；对不上账时做 testcase 名字的**集合 diff**，不要做减法；
- **一轮只跑一次全量**（守卫按轮计数，重跑会把绿跑成红）；
  逐符号核实用 `--collect-only` 或定向跑单文件。

## 第 1 步：读进度与约束（按顺序）

1. 台账 `docs/RAG_EXECUTION_PROGRESS.md`：§1 分批表 → §2 审查汇总 →
   §3 任务执行记录（**按时间追加，越靠后越新**，最新是 `[B17-内容级去重]`）→
   **§4 执行暂停点（当前指针）** → §5 提交与仓库同步记录（**commit 前必读**）；
2. `reports/rag_ingestion_auth_review/stage7_review.txt`（发布门禁 + 三条限制 + 测试盲区）；
   本批另读 `reports/rag_ingestion_auth_review/B17_task_level_review.txt`（上一批的取舍与盲区）；
3. `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`（v2.1）：**§1.0 现状总览** + **§17 作业规程**
   （取数口径 / 结论状态机 / 「已建未启用」判据 / 编号映射表）；
4. 踩坑 `docs/RAG_DEV_PITFALLS.md`（**86 条**，分类 A11 / B22 / C8 / D28 / E11 / F6）：
   本批必读 **F6**（延迟根因，本批主线）、**D28**（登记里的猜测可能方向反了）、
   **A6/A5**（门禁取数）、**E2**（并行会话下的提交纪律）、**E9**（半提交）、
   **B22**（探针会覆盖自己的基线证据）。

## 第 2 步：干活

见**第二部分**（本批唯一主线）。硬性约束见 §2.4。

## 第 3 步：门禁自检（全绿才能进第 4 步）

```bash
R=reports/rag_ingestion_auth_review
./venv/Scripts/python.exe -m ruff check .                             > $R/<批>_ruff-output.txt 2>&1
./venv/Scripts/python.exe -m compileall -q main.py routers services schemas utils config scripts tests \
                                                                      > $R/<批>_compileall-output.txt 2>&1
./venv/Scripts/python.exe scripts/check_repo_data_size.py             > $R/<批>_data-size-output.txt 2>&1
./venv/Scripts/python.exe -m pytest -q --junitxml=$R/<批>_junit.xml 2>&1 | tee $R/<批>_full_test-output.txt | tail -3
```

**硬性**：四件套全绿；`pytest` **≥ 1030**（只增不减）；**一轮只跑一次全量**。

> ⚠️ **全仓 `ruff check .` 的状态在这个仓库是"活的"**（并行会话会动 `tests/`）。
> 引用时**必须带时间戳**，且判据只锚本批改动的文件。
> ⚠️ **别把 `.md` 喂给 `ruff check <file>`** —— 它会按 Python 解析，报上千条假错。

## 第 4 步：审查（两层，别混）

- **任务级**（每次交付都写）：`reports/rag_ingestion_auth_review/<批>_<主题>_review.txt`。
  至少含六节：① 门禁结果 ② 逐条对照判据 ③ 超出规格处**标注方向**（更严 or 更宽，见 B10）
  ④ 中途发现问题与处置 ⑤ **测试盲区**（显式写"哪些没测、为什么、风险多大"）
  ⑥ **结论 + 声明范围**；
- **阶段级**（阶段末才写）：**阶段未完成不得写 PASS**；
  **阶段 PASS ≠ 发布门禁 PASS**，两者分别出具、分别声明范围；
- **结论纪律**（规范 §17.2）：只有 `PASS` / `NEEDS_WORK`；
  拿不到可信数字的判据写「**无法判定（缺 X）**」——
  **不许写 PASS，也不许写"未达标"**（拿不到数 ≠ 没达标）。

## 第 5 步：记录（四处都要写，**且全部必须带日期**）

| 去处 | 写什么 | 格式 |
| --- | --- | --- |
| `docs/RAG_EXECUTION_PROGRESS.md` §3 | 做了什么、门禁结果、决策、遗留 | **append-only**；条目名带 `[T-x.y]` / `[D-n]`，标题后括注日期 |
| `docs/RAG_DEV_PITFALLS.md` | 新踩的坑 | **append-only**；四段式「现象 → 根因 → 解决 → 面试怎么讲」；分类 A~F（新类开 G）；**无新坑也要写「本批未新增坑」** |
| `reports/rag_ingestion_auth_review/` | 审查文件 + 门禁证据 | 前缀按批次；**红/绿两份都要留**（"问题现场"不能只留修好的那份） |
| `.workbuddy/memory/YYYY-MM-DD.md` | 当日工作日志 | **带时间戳小标题**；长期约定写进同目录 `MEMORY.md` |
| `docs/RAG_NEXT_WINDOW_PROMPT.md` | 本文 | **每批结束必须重写**（五件事齐全） |

> **唯一允许覆盖更新**的是台账 §4「执行暂停点」—— **必须覆盖**，它是"当前指针"。

### 纪律要求（硬性）

1. **带日期**：四处记录全部要 `YYYY-MM-DD`；没有日期的记录无法判断新鲜度；
2. **append-only**：台账 §3 / 踩坑 / 阶段审查只追加，不覆盖、不删历史；
3. **提交后必须复核指针**（见 §4.1 坑 1）；
4. **提交/推送归老霸** —— 除非他明确要求，不要动 git 历史；
5. **不确定的事标"待验证"**，不许把没验证的说成已完成；
6. **环境噪声与真缺陷要分开报**（D12 三判据 + 单独复跑）；
7. **接线守卫要防静默变空**（D16）；**构造坏输入先搞清会被哪一层拦下**（D17）；
8. **新模块必须登记生产调用点**：定义在、测试在、**没人调用 = 未完成**；
9. **前后对比类探针必须把"状态"编进文件名**，且**默认拒绝覆盖**已存在的证据文件（B22）。

---

# 第四部分 · 环境坑与常用命令

## 4.1 五个环境坑（动手前必读）

| 坑 | 症状 | 处置 |
| --- | --- | --- |
| **1（每次 commit 必看）** | `git commit` 成功但 `git rev-parse HEAD` 不前进 | **先核对**（`HEAD` vs `refs/heads/<branch>`），**不一致才修**：`mkdir -p .git/refs/heads/optimize` 后写 **40 位完整 SHA** |
| **2** | `git push` 被拒 | 直连与代理**都可能**间歇失效（连只读的 `ls-remote` 也会）→ **探测有效期很短，探测与执行同批做**，交替重试 |
| **3** | 轻量 venv 缺源配置 | `pip install` 显式指定镜像 + trusted-host |
| **4** | Bash 与 PowerShell 通道能力不互补 | **给老霸的命令一律按 PowerShell 写**（E4：`timeout` / `env -u` / `A \|\| B` 在 PS 里**都不是那个意思**，会报语法错或行为诡异） |
| **5** | 全量测试 stdout 汇总行消失、退出码 1 | **环境删除守卫**（A6）→ 改用 **JUnit XML** 取数 |

## 4.2 跑 RAG 脚本的环境前置（**两个环境变量 + 一个装法**）

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+ \
./venv/Scripts/python.exe scripts/evaluate_hybrid_retrieval.py
```

- **不加 offline** → 会去连 HuggingFace Hub，本机代理返回 **502**（A10；模型其实已在本地 cache）；
- **缺 `RAG_JWT_SECRET`** → 受保护接口与审计写入直接 **500**（C7，fail closed）；
- `torch / transformers / sentence-transformers` 装在 **`venv/rag_ml_deps/`**，
  靠 `venv/Lib/site-packages/_rag_ml_deps_prepend.pth` 插到 `sys.path[0]`。
  **别再用常规 `pip install` 动这几个包**（A7：删除守卫会杀掉覆盖安装，会装成半残）。

## 4.3 常用命令

```bash
# 基线核对
git status -sb && git rev-parse HEAD && git log --oneline -6

# 门禁四件套（见第三部分第 3 步）

# 本批相关：量延迟（都要带 4.2 的环境变量）
./venv/Scripts/python.exe scripts/evaluate_hybrid_retrieval.py --out-prefix <新前缀>
#   ↑ 单条耗时明细在这里；**务必用 --out-prefix 换新文件名**，别覆盖旧报告
./venv/Scripts/python.exe scripts/audit_chat_evidence_diversity.py --label <状态>
#   ↑ 证据多样性（B17 的验收探针；默认拒绝覆盖已存在的文件）

# 上一批留下的两个审计脚本（只读，可复跑）
./venv/Scripts/python.exe scripts/audit_colloquial_gold.py --top-k 50
#   ↑ 口语化 7 条金标复核（判"真·检索失败"还是"金标假阴性"）
./venv/Scripts/python.exe scripts/rebuild_chunk_index.py     # 只重建索引（改了切词/索引结构必跑）

# 规范改动后的回归门禁
./venv/Scripts/python.exe scripts/verify_acceptance_spec.py

# README 测试数（带命中数断言，门禁非全绿时拒绝写）
./venv/Scripts/python.exe scripts/update_readme_testcount.py <junit.xml>
```

> ⚠️ `tmp/` **不进 git 且随时可能被清**，里面的探针（`probe_*.py`、`audit_corpus_coverage.py`
> 等）**动手前先确认它还在**；不在就重建，或先把有长期价值的移进 `scripts/`（B22 的教训）。

## 4.4 语料与入库（本批**不要**动语料，留档备查）

```bash
./venv/Scripts/python.exe scripts/build_corpus_documents.py --limit 25 --sleep 0.6
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ./venv/Scripts/python.exe scripts/bulk_import_documents.py
```

- `data/corpus/`（业务主语料，103 份）与 `data/corpus_samples/`（形态补缺，23 份）
  **都是生成物，已 gitignore**；
- **补语料后必须跑覆盖审计**（`tmp/audit_corpus_coverage.py`）——
  只看文件数会掩盖"某个解析器分支从未被真实文档走过"（**D19**）。

---

# 第五部分 · 自检清单

**开工前：**

- [ ] `git status -sb` 看清叠着哪几批（**期望：不干净，F1/B8/B17 三批**）；`HEAD == 7d4a34b`
- [ ] 全量基线核对过：**1030 / 0 / 0 / 0**（JUnit XML 口径）
- [ ] 已读台账 §3 最新条目（`[B17-内容级去重]`）+ §4 暂停点 + 踩坑 **F6** / **D28** / **B22**
- [ ] 已读 `B17_task_level_review.txt`（上一批的取舍与盲区，**别重做已做的事**）
- [ ] 本批的**验收判据**已明确（不许"做着看"）
- [ ] 已想清 §2.3 的四件事，并把它写进了决策记录

**交付前：**

- [ ] 门禁四件套全绿；`pytest` **≥ 1030**；warning 按第 0 步口径取（取不到记 `N/A`）
- [ ] **缓存命中/失效的测试**都在：尤其「**切版本后第一次检索读到新索引**」
- [ ] **评测指标逐位不变**（缓存只该改速度、不该改结果）—— 用**新前缀**跑，别覆盖旧报告
- [ ] **延迟变化已量到并如实报告**（打缓存前后的均值 / 中位 / P95，同一环境）
- [ ] 若量出来耗时不在 manifest 读取上 → **停下讨论，不要硬做**
- [ ] 任务级审查已写（六节齐全，含**结论**与**声明范围**、**测试盲区**）
- [ ] 台账 §3 追加（带日期）；**§4 已改为新的当前指针**
- [ ] 踩坑追加（带日期；**无坑也写"未新增"**）
- [ ] 工作记忆追加（带时间戳小标题）
- [ ] **本文已重写**（进度 / 下一步 / 基线 / 约束，五件事齐全）
- [ ] 证据文件已落 `reports/rag_ingestion_auth_review/`（含**红/绿两份**）
- [ ] 提交指令已给老霸（PowerShell、分开标明、一屏给完）；提交后 `git rev-parse HEAD` 已复核

---

# 附录 · 历史批次速查（要细节就读原始文档）

| 批次 | 一句话 | 权威出处 |
| --- | --- | --- |
| B1~B7 | 摄取 / 分块 / 权限 / 检索 API 接线（B7） | 台账 §3，`stage1~6_review.txt` |
| B8-1/2/3 | 索引回滚 + 接线守卫 + 四格式端到端 | `stage7_review.txt` |
| 前端对齐批次 | trace 内容层 + 意图识别缺陷（B1~B7 的子批次） | `docs/BACKEND_TRACE_FIX_DELIVERY_2026-09-23.md` |
| 语料专项 | 781 条 FAQ → 9229 个真实 chunk | 台账 §3 `[语料专项]` |
| 形态补缺 | 让解析器每个分支都被真实语料走过（撞出 B18） | 台账 §3 `[形态补缺批次]` |
| F1 切轨 | 聊天问答切到 chunk 索引（9229 chunk 进得了聊天） | 台账 §3 `[F1 切轨]` |
| B8-混合检索 | 稠密 + 稀疏 FTS5 双路 + 加权 RRF，已实测 | 台账 §3 `[B8-混合检索]`、`reports/retrieval_hybrid/` |
| **B17-内容级去重** | **检索层 Top-K 去重，聊天有效证据 2.14/3 → 3.00/3** | 台账 §3 `[B17-内容级去重]`、`reports/rag_ingestion_auth_review/B17_task_level_review.txt` |

**踩坑 86 条**（A11 / B22 / C8 / D28 / E11 / F6），全文 `docs/RAG_DEV_PITFALLS.md`。
