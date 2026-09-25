# RAG 改造：交接与作业规程（统一提示词）

> **交接时间**：2026-09-25（星期五）17:05
> **交接时状态**：主线 **B1~B8 全部完成**｜阶段 0~7 全 **PASS**｜发布门禁 **PASS**（带三条限制，见 §1.4）
> ｜**B17 检索层内容级去重已完成**（聊天链路有效证据 **2.14/3 → 3.00/3**）
> ｜**F6 生效 manifest 缓存已完成**（hybrid 单条中位 **268.5 → 25.9 ms，−90.3%**）
> **工作区**：**只有本批（F6）未提交** —— B17 已被老霸提交，HEAD = **`a7cc565`**
> **门禁**：**1045 / 0F / 0E / 0S**（`reports/rag_ingestion_auth_review/F6_junit.xml`）
> ｜**warning = 5（实测）**
>
> ### ★ 下一步（唯一定向，别自己另挑）
>
> **口语化查询改写 / 同义扩展** —— 口语化集 R@1 只有 **0.4000**，
> 而 B17 已用证据锁死方向：**不是金标问题**（7 条全是真·检索失败，假阴性 0 条），
> **也不是融合权重问题**（B8 已证等权有害、`w_dense=10` 是唯一不掉点配置），
> 而是**口语化改写与库内表述的词面距离过大**。完整起点见**第二部分**。
> 这一批**只做这一件事**，不要顺手带别的。
>
> **用法**：① 在新窗口说「读 `docs/RAG_NEXT_WINDOW_PROMPT.md`，按里面的规程继续」（**推荐**，
> 不会因复制截断失真）；② 整份粘贴（**别只贴第二部分**，第三部分的取数口径最容易踩错）。
>
> **⚠️ 先核对再动手**：本文所有数字都带时间戳。若实测与本文不符
> （例如全量不再是 `1045 / 0 failures`、或 HEAD 不再是 `a7cc565`），
> 说明**已经有人推进过** —— 先跑第三部分第 0 步核对，**不要照着本文盲改**。
> （上一轮就是这么发生的：本文曾写"HEAD 应为 `7d4a34b`、叠着三批未提交产出"，
> 而 B17 其实已被提交 —— **交接文件的数字也会过期，它自己也带时间戳。**）

---

# 第一部分 · 现状快照（唯一口径）

## 1.1 一句话

外卖售后场景的**客服 RAG 后端**（FastAPI + SQLite/PostgreSQL + FAISS）。
主链路「检索增强问答 + 客服工作流」：意图识别 → 检索 → 重排 → 证据分级 → 生成 →
规则兜底 → 风险分级 → 转人工。**12 个 router、42 个测试文件、1045 条用例**。

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
         └─ select_diverse_evidence(...)               ← ★ B17：截断前先去重
    → 响应带 retrieval_path="chunk-index" + index.sparse_available

【聊天链路】默认走 B 轨（F1 切轨 2026-09-25）
  POST /chat/prompt
    → require_read_operation_role（chat_generate）
    → get_answer_from_rag(request, auth)
    → resolve_chat_retrieval_path()                    ← 默认 "chunk"；RAG_CHAT_RETRIEVAL_PATH=seed 回退
    → retrieve_chunk_items_for_chat()                  ← ★ B17 已在这一层去重
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

**★ F6（2026-09-25）：manifest 现在有进程内缓存了。** 三条链路（`/retrieval/search`、
`/chat/prompt`、索引自描述）**都没有改一行**，受益点是 `load_active_manifest` 内部。
失效判据是**指针里的版本号/指纹**，指针每次都真读（0.09 ms）——
**不要**改成按 mtime 或时间判失效。细节见 §1.7。

## 1.3 必须遵守的既有守卫（改了会红，而且红得对）

| 守卫 | 位置 | 盯什么 |
| --- | --- | --- |
| `test_wiring_guards.py`（18 条） | tests/ | `routers/retrieval.py` 必须**引用并调用**检索与鉴权函数；`search_chunk_index` 的 `access` **无默认值且注解非 Optional**；`routers/*.py` **不得 import jwt** |
| `TestReadmeTrackConsistency` | `tests/test_ingestion_pipeline.py` | README 锚点 `<!-- f1-track: chat-service-retrieval=... -->` 与 `DEFAULT_CHAT_RETRIEVAL_PATH` **双向一致**；`<!-- b8-hybrid: default-retrieval-mode=... -->` 与 `DEFAULT_RETRIEVAL_MODE` **双向一致**；另有守卫自测（读不到 → 空串 → 判失败） |
| `TestHybridDecisionTrace` | `tests/test_hybrid_retrieval.py` | 把关键取舍做成可断言事实：`w_dense=10/w_sparse=1/k=60`、`DEFAULT_RETRIEVAL_MODE == "dense"`。**改权重必须重跑评测** |
| `TestDeploymentGuards` | `tests/test_ingestion_pipeline.py` | compose 真的起了 worker、共享卷、同元数据库与队列地址 |
| `tests/test_retrieval_dedup.py`（26 条，B17） | tests/ | 截断前先去重；`top_k == 20`（召回量不许偷偷变大）；`access` 对象原样透传；真实索引上收窄白名单后不越界且非空；混合路用 `fused_score`；判据与下游口径一致 |
| **`tests/test_manifest_cache.py`（15 条，F6）** | tests/ | 命中缓存不重读文件（**计数包装器**实测）；**切版本/回滚/指纹变必须失效**；`index_name` / `root` 不串味；命中缓存**不动磁盘**；缓存条数有上限；8 线程并发一致；**真实索引上"切版本后第一次检索就读到新索引"** |

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
> **不是**召回质量。对外说法是「聊天有效证据 2.14/3 → 3.00/3」，**不能**说成「检索质量提升」。

> **F6 之后再追加一条**：F6 改的是**延迟**（hybrid 单条中位 −90.3%），**不是**检索质量。
> 对外说「hybrid 单条中位 268.5 → 25.9 ms」，**不能**说成「检索又准又快」；
> 口语化集 R@1 仍是 **0.4000**，一个字没变。

> **规范引用纪律**（验收规范 §17.5）：规范编号（`I01~O01`、`§N`）与本项目编号
> （`B1~B8`、`阶段 N`）是**两套**，引用必须带前缀；且规范 §7.x 与执行计划「阶段 7.x」
> **编号撞车但内容不同**。

## 1.5 混合检索的三条硬结论（对外口径必须按这个讲）

实测（2026-09-25，走生产代码 `search_hybrid_chunks`；
**B17 与 F6 两次重跑复验：六行数字逐位不变**）：

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
   只有"短查询为主"的接口值得切（切之前见 §2.4 第 5 条）。

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
| 1 | ~~**F6**：`load_active_manifest` 无缓存，每次检索重读 19 MB manifest~~ | **★ 2026-09-25 已修**（见 §1.7） |
| 2 | **入库侧多格式孪生**：同一份内容被 md/html/pdf/docx 各入库一次（**906 条冗余**） | B17 暴露的欠账，需产品口径，**未修** |
| 3 | **F2**：`scripts/evaluate_retrieval_metrics.py:85` 按 intent 判相关（A 轨口径）| 未修 |
| 4 | **口语化查询改写**：7 条全库可达却进不了两路 top-50 | **★ 下一批就做它** |
| 5 | **冷启动首读**：进程起来第一次仍付约 100 ms manifest 反序列化 | F6 刻意不做预热，**未做** |
| 6 | **多进程内存**：manifest 缓存是**进程内**的，多 worker 每进程约 19MB 常驻 | F6 引入的新约束，**部署时须算进内存** |
| 7 | **F5**：worker 有 CLI 无进程编排 | 未修，已在门禁结论里声明 |
| 8 | 真实引擎未复验（PostgreSQL / Redis / 真 OCR） | 未做 |
| 9 | 索引重建并发竞态（**含"重建 vs 读缓存"真并发压测**） | 未测（单机 SQLite 难构造）|
| 10 | 图片块不进 chunk / PDF 跨页重复表头 | 设计如此 |
| 11 | **B6 第二步**：往 `食品安全投诉` 加「不新鲜」关键词 | **待老霸拍板**，属业务策略，**不许擅自加** |
| 12 | **前端**：界面复核（出截图）+ 补 `clarify` 词条 | 卡在前端 |

## 1.7 ★ F6 做了什么（2026-09-25）—— 别重做，也别改坏

一句话：**给"当前生效 manifest"加了进程内缓存**，hybrid 单条中位 **268.5 → 25.9 ms**。

- 位置：`services/ingestion/index_manifest.py::load_active_manifest` **内部**
  （唯一汇聚点 → 三条链路一行不改即受益）；
- 键：`(规范化绝对路径 root, index_name, index_version, manifest_path, fingerprint)`；
- **失效信号 = 指针里的版本号/指纹**，指针**每次都真读**（0.09 ms）。
  **不是 mtime、不是时间** —— 靠时间会有一个"版本已切、缓存仍以为自己是新的"静默窗口；
- 容量：同一 `(root, index_name)` 只留当前生效版本 + 全局上限 **8** 条
  （每份 19MB，不加约束长跑必爆内存）；
- **刻意不加"显式失效钩子"** —— 自动失效已完备，加钩子等于埋"忘了调 → 静默用旧索引"的口子。
  `reset_active_manifest_cache()` 因此**只有测试/探针调用点**，这是有意的；
- 决策与被否掉的 5 个备选：台账 **[D-14]**；审查：`F6_task_level_review.txt`。

**实测（同机同 query 集同进程）**：

| | before | after |
| --- | ---: | ---: |
| `load_active_manifest` 均值 / 中位 | 121.9 / 97.7 ms | **6.66 / 0.22 ms** |
| `faiss.read_index`（对照，本就很快） | 4.94 ms | 5.05 ms |
| **`read_manifest` 直调（对照组，绕过缓存）** | 121.5 ms | **125.5 ms ← 不变** |
| 端到端 dense 中位 | 127.1 ms | 14.9 ms |
| 端到端 sparse 中位 | 139.8 ms | 10.8 ms |
| **端到端 hybrid 中位** | **268.5 ms** | **25.9 ms（−90.3%）** |

**评测指标逐位不变**（含逐条名次表，18 个数字全同）；B17 的证据多样性仍 **3.00/3**。
**省掉的是 JSON 反序列化 + 对象构造（CPU），不是磁盘 I/O**（同进程连读走 OS 页缓存）。

---

# 第二部分 · ★ 下一步：**口语化查询改写 / 同义扩展**

> **本批的全名**：让"口语化提问"能在库里被找到 —— 修**查询侧**与**库内表述**之间的词面距离。
> **为什么是它**：F6 已划掉，它是剩下的**最高优先级**，而且是唯一一个
> **已有证据锁定方向**的欠账（B17 把两个候选解释都验掉了）。
> **改动面**：检索**查询侧**（不是索引、不是权重、不是金标）。

## 2.0 前两批刚做完什么（**别重做**）

- **B17（2026-09-25）**：检索层 Top-K 内容级去重 → 聊天有效证据 **2.14/3 → 3.00/3**。
  判据用**归一化文本**（压平空白），**不是** `content_hash`（已实测达不到目标）。
  ⚠️ **不要**回头改判据。若发现去重相关问题，读 `B17_task_level_review.txt` 第四、五节。
- **F6（2026-09-25）**：manifest 加缓存 → hybrid 单条中位 **−90.3%**。见 §1.7。

## 2.1 前置取证（**已完成，直接接上**）

B17 已把"为什么口语化集低"这个问题查到底，**两个候选解释都被否掉**：

| 候选解释 | 复核结论 |
| --- | --- |
| 「金标是假的（假阴性）」 | **否掉**：7 条两路 top-50 都捞不到的，span / 文档 / 章节**三样都在库里**，假阴性 **0 条** |
| 「融合权重没调好」 | **否掉**：B8 扫过权重；等权有害，`w_dense=10` 已是唯一在两套集上都不掉点的配置 |

**剩下的解释只有一个：口语化改写后的查询串，与库内表述在词面上距离过大。**

**要接上的证据**（都在 git 里，可复跑）：

- `scripts/audit_colloquial_gold.py --top-k 50` → `B17_colloquial_gold_recheck_20260925.txt`
  （判据刻意**绕开检索**：全库 chunk 压平文本拼串做子串判断）；
- 三模式评测：`reports/retrieval_hybrid/evaluation_20260925.txt`（口语化集 sparse R@1 = 0.1000）。

**★ 结构性观察（动手前先消化）**：
口语化集上 **sparse R@1 = 0.1000**，而 hybrid == dense（每一列完全相同）。
说明**融合没有带来任何新信息** —— 因为稀疏路在这个语料形态下**几乎全错**。
所以"查询改写"这件事真正的价值不在"让 Q 更像库内文本以便词法命中"这么简单，
而在于：**改写是否能补上稠密路也抓不到的语义桥**（否则改写完 hybrid 仍然 == dense）。
这一点必须先用数据回答，**不要直接上手写改写器**。

## 2.2 交付清单（按顺序做，**第 1 步不许跳过**）

| # | 交付物 | 验收判据 |
| --- | --- | --- |
| 1 | **先量基线**：把"口语化 30 条"落到**可解释的分布**上 —— 每条 query 在 dense / sparse 两路的 top-50 里**能捞到什么**、捞不到的 7 条**离命中有多远**（rank / 相似度 / 词面重合度），并给出**改写应当达到什么才叫成功**的判据 | 数字落盘；**若量出来发现改写收益上限本来就接近 0（例如稠密路连语义都够不着），如实报告并停下讨论，不要硬做**（§2.3 第 4 条 / D28） |
| 2 | **写决策记录**：改写放在哪一层（检索前 / 融合后 / 只在某条路）、用什么手段（LLM 改写 / 同义词典 / 规则模板 / 伪相关反馈）、怎么保证**不改变已有 81 条 title 集的成绩**、离线怎么评测 | 决策写进台账 §3，含**被否掉的备选** |
| 3 | **实现** | 生产调用点明确（本项目纪律：**定义在、测试在、没人调用 = 未完成**）|
| 4 | **测试**（≥8 条）：改写只在需要时触发 / 幂等 / 失败降级（**改写失败必须退回原始 query，不得让检索直接报错**）/ 不改写路径逐位不变 / 权限过滤仍生效 | 新增用例；既有 **1045 条只增不减** |
| 5 | **量化验证**：重跑三模式评测 | 口语化集 R@1 **上升**；**title 集不许掉点**（81 条那六行必须逐位不变，或只允许变好）；把"改写前后"两列并排落盘 |
| 6 | **成本与延迟说明**：改写若走 LLM，写清**每查询增加多少 ms / 多少 token**，并给"关掉改写的降级路径" | 写得出来才叫想清楚；写不出来就显式记成**未验证** |
| 7 | 门禁 + 任务级审查 + 四处记录 + 重写本文件 | 见第三部分第 3~5 步 |

## 2.3 动手前**必须想清的 4 件事**（想不清就别写码）

1. **改写必须"可关、"可观测、"可降级"**。
   这是**检索质量**的改动（不像 F6 只改速度），一旦改写把 title 集打坏，
   表现是"检索结果整体变差"而**不报错**。所以：① 要有开关；② 响应/trace 里要能看出
   "这次查询被改写过、改成了什么"；③ LLM 改写失败或超时必须**退回原始 query**，
   不能让它变成一次检索失败。
2. **评测口径不能混**。口语化集是 B 轨 span 金标；**A 轨 intent 口径（F2）仍未统一**，
   两套数字**不可互认**。本批只能拿 B 轨那两套集说话，**不许**顺手把 F2 也"修"了
   （那是另一批的事）。
3. **别动已有的成绩**。title 集 81 条那六行数字（dense 0.7160 / hybrid 1.0000 …）
   是本项目对外能站住的部分。改查询侧**会**影响它 —— 所以第 2 步必须写清
   "怎么保证 title 集不掉点"，第 5 步必须**并排**给出两套集的数字。
4. **登记里的"修法方向"也可能错**（D28，B17 与 F6 各验过一次）。
   本批的登记方向是"做查询改写"，但这**仍是推测**：
   必须先量到"改写要跨过多大的词面/语义距离"，再决定方案。
   **如果量出来发现瓶颈不在查询侧（例如稠密模型本身够不着该语义），
   如实报告并停下讨论**，不要为了完成清单而做无效优化。
   同理，本批**不要**顺手去调融合权重或换 embedding 模型 —— 那会把归因搅乱。

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
7. **不许动 B17 的去重语义**（召回量仍是 `top_k = limit×5` 下限 20，截断在去重之后，有测试锁）
   **也不许动 F6 的缓存语义**（键的 5 维、失效取指针、无显式钩子 —— 见 §1.7 与 [D-14]）；
8. **本批不新增模型依赖、不动已冻结决策**（[D-6] [D-12] [D-13] [D-14]）；
   若改写要用 LLM，**走已有的在线生成配置**（`services/online_generation`），不要引入新 SDK；
9. **不许为了让数字好看而放宽门槛**（尤其：不许为了让改写"看起来有效"而改动金标或判据）；
10. **`git add` 一律精确路径**（E2：工作区可能有并行会话产出）；
11. **提交/推送由老霸自己执行**，AI 只给指令（见第三部分第 5 步）。

## 2.5 明确**不做**的（防 scope creep）

- **不改金标、不改判据、不改融合权重、不换 embedding 模型**（B8/B17 已把这几条验过）；
- **不动语料构造逻辑**（多格式入库是解析器覆盖测试的产物，清理是**独立决策**）；
- **不做数据迁移 / 删库清理**；
- **不切线上默认模式**；
- **不碰前端**；
- **不做冷启动预热 / 多进程内存改造**（F6 遗留的两条，属**部署层**，另批）。

---

# 第三部分 · 作业规程（五步闭环，缺一步都不算完成）

> **核对基线 → 读进度与约束 → 干活 → 门禁自检 → 审查 → 记录。**
> 只跑通测试**不算交付**。四处记录**全部必须带日期**（`YYYY-MM-DD`）。

## 第 0 步：核对基线（**不要跳过**）

```bash
git status -sb                 # ⚠️ 期望：只叠着本批（F6）未提交产出
git rev-parse HEAD             # 期望：a7cc565（F6 那批还没提交，所以 HEAD 仍不动）
git log --oneline -3
./venv/Scripts/python.exe -m pytest -q --junitxml=tmp/baseline_junit.xml
./venv/Scripts/python.exe -c "import xml.etree.ElementTree as ET; \
  s=ET.parse('tmp/baseline_junit.xml').getroot().find('testsuite'); \
  print('tests=',s.get('tests'),'failures=',s.get('failures'),'errors=',s.get('errors'))"
# 当前基线：1045 / 0 / 0 / 0 —— 以 reports/rag_ingestion_auth_review/F6_junit.xml 为准
# 历史基线（判断"有没有人推进过"用）：F1 收尾 949 → B8 收尾 1004 → B17 收尾 1030 → F6 收尾 1045
```

> ⚠️ **看到 `M` / `??` 一大堆时不要 `git checkout .`** —— 那会把未提交的产出全毁。
> 但也要**先分清哪几批**：本轮交接时 B17 已被提交（HEAD 是 `a7cc565`），
> 未提交的**只有 F6 一批** + 一个并行会话的 `metrics_dashboard.html`。

**⚠️ 取数口径（踩坑 A6/A5/D15/D18）—— 这几条最容易搞错，全在这一段：**

- **不要用退出码和 stdout 汇总行判绿红** —— 环境删除守卫会在 sessionfinish 吃掉汇总行、
  把退出码变成 1，**测试其实全绿**；
- **warning 条数**取自**全量 stdout** 的 `N warnings`（**不是** `--collect-only`，
  它不执行测试、输出里没有 warning 汇总）。汇总行被吞 → 记 `N/A（汇总行被吞）`，
  **不许估算或沿用上次数字**。历史实测：B8 被吞记 `N/A`、B17 与 F6 都拿到 **5（实测）**，
  别预设；
- **测试数两个口径**：stdout 的 `N passed` **不含 subtest**，JUnit XML 的 `tests=` **含**
  （唯一 `subTest` 在 `tests/test_evaluate_chat_grounding.py`）。
  **F6 实测：JUnit 1045 / stdout 1041 passed + 4 subtests，差 4**。
  **门禁只认 XML**；对不上账时做 testcase 名字的**集合 diff**，不要做减法；
- **一轮只跑一次全量**（守卫按轮计数，重跑会把绿跑成红）；
  逐符号核实用 `--collect-only` 或定向跑单文件。

## 第 1 步：读进度与约束（按顺序）

1. 台账 `docs/RAG_EXECUTION_PROGRESS.md`：§1 分批表 → §2 审查汇总 →
   §3 任务执行记录（**按时间追加，越靠后越新**，最新是 `[F6-manifest缓存]`，
   决策看 **[D-14]**）→ **§4 执行暂停点（当前指针）** → §5 提交与仓库同步记录（**commit 前必读**）；
2. `reports/rag_ingestion_auth_review/stage7_review.txt`（发布门禁 + 三条限制 + 测试盲区）；
   本批另读 `F6_task_level_review.txt`（上一批的取舍与盲区）+ `B17_task_level_review.txt`；
3. `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`（v2.1）：**§1.0 现状总览** + **§17 作业规程**
   （取数口径 / 结论状态机 / 「已建未启用」判据 / 编号映射表）；
4. 踩坑 `docs/RAG_DEV_PITFALLS.md`（**87 条**，分类 A11 / B22 / C8 / D29 / E11 / F6）：
   本批必读 **D28**（登记里的猜测可能方向反了 —— B17/F6 各验过一次）、
   **D29**（前后对比类取证必须留**对照列**）、**D22 / D23**（改默认配置与评测口径的连带影响）、
   **A6/A5**（门禁取数）、**E2**（并行会话下的提交纪律）、**B22**（探针会覆盖自己的基线证据）。

## 第 2 步：干活

见**第二部分**（本批唯一主线）。硬性约束见 §2.4。

## 第 3 步：门禁自检（全绿才能进第 4 步）

```bash
R=reports/rag_ingestion_auth_review
./venv/Scripts/python.exe -m ruff check .                             > $R/<批>_ruff-output.txt 2>&1
./venv/Scripts/python.exe -m compileall -q main.py routers services schemas utils config scripts tests \
                                                                      > $R/<批>_compileall-output.txt 2>&1
./venv/Scripts/python.exe scripts/check_repo_data_size.py             > $R/<批>_data-size-output.txt 2>&1
./venv/Scripts/python.exe -m pytest -q --junitxml=$R/<批>_junit.xml > $R/<批>_full_test-output.txt 2>&1
# 取数用 JUnit XML，不要看汇总行（见第 0 步）
```

**硬性**：四件套全绿；`pytest` **≥ 1045**（只增不减）；**一轮只跑一次全量**。
**若改了 README 里的派生数字**，跑 `scripts/update_readme_testcount.py $R/<批>_junit.xml`
（带命中数断言，非全绿拒绝写）。

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
9. **前后对比类探针必须把"状态"编进文件名**，且**默认拒绝覆盖**已存在的证据文件（B22）；
10. **前后对比必须留对照列**（D29）：留一个**绕过被改动代码**的直调，它在两轮里应当不变。

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

# 本批相关：评测与取证（都要带 4.2 的环境变量）
./venv/Scripts/python.exe scripts/evaluate_hybrid_retrieval.py --out-prefix <新前缀>
#   ↑ 三模式指标 + 单条耗时明细；**务必用 --out-prefix 换新文件名**，别覆盖旧报告
./venv/Scripts/python.exe scripts/audit_chat_evidence_diversity.py --label <状态>
#   ↑ 证据多样性（B17 的验收探针；默认拒绝覆盖已存在的文件）
./venv/Scripts/python.exe scripts/audit_manifest_load_cost.py --label <状态>
#   ↑ ★ F6 新增：延迟组件级拆分（manifest vs faiss）+ 读 manifest 次数 + 端到端耗时
./venv/Scripts/python.exe scripts/audit_colloquial_gold.py --top-k 50
#   ↑ 口语化金标复核（判"真·检索失败"还是"金标假阴性"）—— 下一批会用到

./venv/Scripts/python.exe scripts/rebuild_chunk_index.py     # 只重建索引（改了切词/索引结构必跑）

# 规范改动后的回归门禁
./venv/Scripts/python.exe scripts/verify_acceptance_spec.py

# README 派生数字（带命中数断言，门禁非全绿时拒绝写）
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

- [ ] `git status -sb` 看清叠着哪几批（**期望：只有 F6 一批**）；`HEAD == a7cc565`
- [ ] 全量基线核对过：**1045 / 0 / 0 / 0**（JUnit XML 口径）
- [ ] 已读台账 §3 最新条目（`[F6-manifest缓存]` + `[D-14]`）+ §4 暂停点 + 踩坑 **D28** / **D29** / **B22**
- [ ] 已读 `F6_task_level_review.txt`（上一批的取舍与盲区，**别重做已做的事**）
- [ ] 本批的**验收判据**已明确（不许"做着看"）
- [ ] 已想清 §2.3 的四件事，并把它写进了决策记录
- [ ] **已量到"改写要跨过多大的距离"**（第 1 步），不是直接上手写改写器

**交付前：**

- [ ] 门禁四件套全绿；`pytest` **≥ 1045**；warning 按第 0 步口径取（取不到记 `N/A`）
- [ ] **改写可关、可观测、可降级** 的测试都在（LLM 失败必须退回原始 query）
- [ ] **title 集 81 条那六行不许掉点**；口语化集的提升有并排对比数字
- [ ] **延迟/成本已量到并如实报告**（改写若走 LLM：每查询增加多少 ms / token）
- [ ] 若量出来瓶颈不在查询侧 → **停下讨论，不要硬做**
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
| B17-内容级去重 | 检索层 Top-K 去重，聊天有效证据 2.14/3 → 3.00/3 | 台账 §3 `[B17-内容级去重]`、`B17_task_level_review.txt` |
| **F6-manifest缓存** | **生效 manifest 加进程内缓存，hybrid 单条中位 −90.3%** | 台账 §3 `[F6-manifest缓存]` + `[D-14]`、`F6_task_level_review.txt`、`F6_manifest_load_cost_{before,after}_20260925.txt` |

**踩坑 87 条**（A11 / B22 / C8 / D29 / E11 / F6），全文 `docs/RAG_DEV_PITFALLS.md`。
