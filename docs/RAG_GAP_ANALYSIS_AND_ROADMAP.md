# RAG 项目差距分析与改造路线图

> **本文件回答三个问题**：现在的代码距离 `docs/goal.md` 的理想目标还差什么？差在哪几个文件？按什么顺序补？
>
> 相关文档：
> - `docs/goal.md` —— 理想目标（验收口径见其第十二节 A/B/C/D 四档）
> - `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` —— 可执行任务清单（阶段 0~7）
> - `docs/RAG_EXECUTION_PROGRESS.md` —— 执行进度台账（B1~B4 已完成）
> - `docs/RAG_DEV_PITFALLS.md` —— 开发踩坑与决策台账（面试复盘用，F 节列尚未修复的缺口）
>
> 实测时间：2026-09-23 · 代码基线：`a670373`（工作区含未提交的 B4 改动）

---

## 0. 一句话结论

**进度上，计划走完了 4/8 批（B1~B4，阶段 0~2），工程质量很高；但结构上存在一个致命的「双轨制」——新建的 ingestion 子系统（文档/版本/Chunk/ACL/租户）与线上真正在跑的检索/问答链路完全没有接上。**

线上检索读的还是那份手工整理的 781 条 FAQ 种子文件（`data/takeout_customer_service_seed.jsonl`），检索函数签名里**连 `tenant_id` 都没有**；而 `services/ingestion/repository.py` 只被上传接口 `routers/documents.py` 引用，**没有任何一条查询路径消费它**。

这意味着：

- 已建的 13 张表、ACL、租户隔离是**「已建模、未启用」**状态；
- `tests/test_tenant_isolation.py` 的 14 条用例**全绿，但测的是数据层 CRUD，不是检索层**；
- 用户上传的文档进了对象存储和数据库，**永远进不了 FAISS 索引，检索不到**。

所以现在的项目不是「差几个功能」，而是**差一次「让两条轨并成一条」的接线工程**。这也是后续所有阶段（B5~B8）真正要完成的事。

---

## 1. 实测基线（本文件所有结论的依据）

| 项目 | 实测值 | 验证方式 |
| --- | --- | --- |
| 全量测试 | **555 passed** | `pytest --collect-only` |
| git HEAD | `a670373`（ahead 1，B4 改动未提交） | `git status -sb` |
| 已完成批次 | B1 / B2 / B3 / B4（阶段 0 / 1 / 2 审查 PASS） | 台账第 2 节 |
| 未开始批次 | B5 / B6 / B7 / B8 | 台账第 1 节 |
| ingestion 模块 | `models` `repository` `db` `object_store` `content_sniff` `parser_registry` `queue` `parsers/`(8 个) | `find services/ingestion -name "*.py"` |
| **缺失模块** | **无 `chunkers/`、无 `pipeline.py`、无 `worker.py`** | 同上 |
| 检索器 | `utils/vector_retriever.py`（**901 行**），**`tenant`/`acl` 零命中** | `grep -n "tenant\|acl"` |
| 稀疏检索 | **全仓 `bm25` 零命中**（"混合"= FAISS + 规则加权） | `grep -rn bm25 --include=*.py` |
| 索引 manifest | `utils/` 中**无 manifest**；仅 DB 表 `index_builds.manifest_uri` 建了字段未使用 | `grep -rn manifest` |
| 流式输出 | **零命中**（无 `StreamingResponse` / SSE） | `grep -rn StreamingResponse` |
| 索引数据源 | `data/takeout_customer_service_seed.jsonl`（**781 行**手工 Q&A） | `utils/retriever.py:8,11` |
| ingestion 数据库 | **`data/rag_metadata.db` 不存在** → 迁移从未落到开发库 | `ls data/rag_metadata.db` |
| `document_chunks` 表 | `insert_chunks()` 已定义，**全仓零调用** | `grep -rn insert_chunks` |
| 版本创建 | `create_document_version()` **只被测试调用，生产代码零调用** | `grep -rn create_document_version` |
| 重复文件判定 | `find_version_by_content_hash()` **只被测试调用** | `grep -rn find_version_by_content_hash` |
| ACL 写入 | `grant_document_acl()` **只被测试调用**，无任何生产入口 | `grep -rn grant_document_acl` |
| 检索评测 | recall@1 = **0.9667**（90 例），但口径是 **intent 匹配**，非文档/Chunk 级 | `reports/retrieval_metrics/2026-09-14_02-50-12.json` |

---

## 2. 最大的结构性缺口：双轨制

### 2.1 两条链路目前的真实样子

```
【A 轨 · 线上实际在跑的链路】
  用户提问
    → services/chat_service.py（1229 行）
    → utils/vector_retriever.retrieve_rag_items()          ← 无 tenant / 无 acl 参数
    → utils/retriever.iter_knowledge_items()
    → data/takeout_customer_service_seed.jsonl（781 条手工 FAQ）   ← 索引唯一来源
    → FAISS IndexFlatIP（data/faiss_store/real_vector.index）
    → CrossEncoder rerank

【B 轨 · 新建但没人用的链路】
  POST /knowledge-bases/{kb}/documents（routers/documents.py）
    → services/ingestion/（解析器 → 对象存储 → queue 投递）
    → ingestion_jobs 停在 pending（没有 worker 消费）
    → 【断点】没有 pipeline、没有 chunker、不进 FAISS、不进 document_chunks
```

**两轨之间唯一的接缝是**：`POST /knowledge/publish-approved`（`routers/knowledge.py:173`）
往种子 JSONL 追加已审核条目，然后调 `save_real_vector_store()` 重建 FAISS。
也就是说，**知识入库还是靠「人工审核 → 追加 JSONL → 重建索引」这套手工流程**，
跟上传接口、跟文档版本、跟 ACL 毫无关系。

### 2.2 这个缺口为什么最优先

1. **所有下游能力都被它卡住**：没有 chunk 就没有 chunk 级引用（goal.md 第九节）、
   没有权限元数据就无法做检索过滤（第四节）、没有版本就无法做「旧版本排除」（第十节）。
2. **它让已通过的测试产生虚假安全感**：`test_tenant_isolation.py` 14 条全绿，
   但检索层没有任何 tenant 参数 —— 换个说法：**测试守的是仓库层，漏的是检索层**。
3. **它是最能体现"工程判断力"的部分**：面试时「我建了完整的租户/ACL 数据模型，
   但我发现线上检索根本没接上去，于是把接线列为下一优先级」这个判断，
   比"我写了很多表和解析器"更有说服力。

### 2.2.1 「已建未启用」资产清单（关键证据）

以下函数/表**都已实现、都被测试覆盖**，但**在生产代码路径上没有任何调用点**：

| 资产 | 定义位置 | 生产调用点 | 测试调用点 |
| --- | --- | --- | --- |
| `create_document_version()` | `repository.py:279` | ❌ 无 | `test_ingestion_models.py`、`test_document_upload_api.py` |
| `find_version_by_content_hash()` | `repository.py:314` | ❌ 无 | `test_ingestion_models.py:237` |
| `grant_document_acl()` | `repository.py:370` | ❌ 无 | `test_ingestion_models.py` ×3 |
| `insert_chunks()` | `repository.py:506` | ❌ 无 | ❌ 无 |
| `document_acl` 表 | `models.py` | ❌ 无写入入口 | 有 |
| `index_builds.manifest_uri` | `models.py:430` | ❌ 无读写 | 有 |
| `queue.publish()` | `queue.py` | ✅ `routers/documents.py` | 有（含假客户端） |
| `queue` 消费端（`xreadgroup`） | — | ❌ **未实现** | — |

**读法**：这张表就是「测试全绿」与「功能可用」之间的差距。它也是下一批工作的**准确边界**——
B5/B6 要做的正是给上面每一行补上生产调用点。

> 补充一条方法论：把这张表加进台账第 4 节「执行暂停点」，
> 可以让后来者（或面试官）一眼看到「哪些是已就绪的积木、哪些还缺接线」。



### 2.3 接线的具体动作（对应 B5~B8）

| 步骤 | 交付物 | 关键点 |
| --- | --- | --- |
| 1. 切分 | `services/ingestion/chunkers/` + `config/chunking_config.py`（**B5**） | 输入是 `ParsedDocument`，输出带 `tenant_id`/`acl`/`page_start`/`heading_path` 的 chunk |
| 2. 落库 | `repository.insert_chunks`（已存在，待调用） | 父 chunk 必须先于子 chunk 插入（自引用复合外键） |
| 3. 索引 | `services/ingestion/pipeline.py` + `worker.py`（**B6**） | 消费 `queue` → 解析 → 切分 → 落库 → 建 manifest → 原子切换 |
| 4. 检索改造 | `utils/vector_retriever.py` 引入 manifest + **强制 tenant/ACL filter**（**B6/B7**） | 缺 filter 必须**拒绝查询**，不能默认全库 |
| 5. 路由改造 | `routers/retrieval.py` 从 `AuthContext` 生成 filter（**B7**） | 客户端不得传任意 filter / FAISS ID |
| 6. 负向验证 | 检索层隔离测试（**B7**） | 「被过滤文档的标题、分数、数量、引用、trace 均不泄漏」 |

> **接线进度（2026-09-23 更新）**：第 1~3 步由 B5 / B6 交付；**第 4 / 5 / 6 步由 B7 交付**。
> 第 4 步「检索改造」的强制 filter 在 B6 落地（`search_chunk_index` 必填 `access`，缺参报错），
> B7 补上**服务端构造点**（`services/retrieval_access.py`）与 **API 出口**（`routers/retrieval.py`）。
> 第 6 步负向验证见 `tests/test_retrieval_isolation.py`。
> **B7 期间在接线路径上炸出一个真缺陷**（多租户索引互相覆盖，见 `docs/RAG_DEV_PITFALLS.md` B14）——
> 这恰好是「先接线、再接线的路上找缺陷」这条方法论的收益，不是意外。

---

## 3. 按 `goal.md` 第十二节四档验收对表

### A. 文档入库 —— 完成度约 **65%**（解析层很强，下游缺失）

| 验收项 | 现状 | 差距 |
| --- | --- | --- |
| 支持 PDF / DOCX / HTML / Markdown | ✅ B3 完成，5 种解析器 + 10 个扩展名白名单 | — |
| 解析结构可验证 | ✅ `ParsedDocument.validation_errors()` + 产物自检前置 | — |
| OCR 低质量可识别 | ⚠️ 触发逻辑用假引擎覆盖，**真实引擎从未跑过** | 装 `rapidocr-onnxruntime` 后复验；OCR 置信度未量化入库 |
| Chunk 有稳定 ID | ❌ **无 chunker** | B5 |
| 版本和 ACL 完整 | ⚠️ 表与 CRUD 都有，但**上传不建版本**（[D-6] 设计），ACL 无写入入口 | B6 建版本；ACL 授权接口缺失 |
| 失败可重试 | ✅ 上传/reprocess/任务查询齐备 | worker 缺失，重试实际未跑起来 |
| 重复文件幂等 | ✅ DB 级 `uq(tenant_id, content_hash)` | 判定逻辑（`find_version_by_content_hash`）尚未被调用 |
| **解析质量门禁**（goal.md 第三节 4） | ❌ **完全没有** | 需新增：空文本/长度异常/乱码率/重复段/页码连续性/OCR 置信度 六项检查 + `parse_failed`/`parse_low_quality`/`parse_requires_review` 状态机 |
| **跨页段落 / 跨页表格连续性** | ⚠️ 按 PyMuPDF block 成块，跨页未显式合并 | 需在 chunker 或 parser 层补「跨页续接」判定 |
| **多栏排版 / 页眉页脚剔除** | ⚠️ PDF 未处理多栏；页眉页脚剔除在 chunker 的第 8 条要求里（B5） | B5 |

### B. 检索 —— 完成度约 **30%**（功能可用，生产属性基本为零）

| 验收项 | 现状 | 差距 |
| --- | --- | --- |
| Recall@5 达业务目标 | ⚠️ 报 0.9889，但**口径是 intent 匹配**（见第五节） | 需重建评测口径 |
| **权限过滤 100% 正确** | ❌ `retrieve_*` 无 `tenant_id`/`acl` 参数 | B6/B7 —— **最高优先级的安全缺口** |
| 过期和未发布内容不被召回 | ❌ 无 `effective_at`/`expired_at` 过滤 | models 已有字段，检索未用 |
| 多轮问题可正确改写 | ❌ 无 query rewrite（只是历史拼接 + 摘要 + 意图提示拼接） | `build_query_with_intent_hint` 是规则拼接，非改写 |
| 多跳问题可拆解并合并证据 | ❌ 完全没有 | 需新增问题分解模块 |
| 混合检索 | ⚠️ 只有 FAISS + `calculate_keyword_bonus` / `calculate_direction_penalty` 规则加权 | 需引入 BM25（`rank_bm25`）+ RRF 融合 |
| 检索失败有明确降级 | ⚠️ 有最小分数阈值，但无「证据不充分」判定 | 需 goal.md 第五节的证据充分性判定 |
| 索引 manifest + 原子切换/回滚 | ❌ 无 manifest，索引重建直接覆盖 | B6 |

### C. 回复 —— 完成度约 **40%**

| 验收项 | 现状 | 差距 |
| --- | --- | --- |
| 事实性通过率 | ⚠️ 有离线 grounding 诊断（`attach_grounding_diagnostics`），`judge_status="not_run"` | 运行时零核验 |
| 证据覆盖率 | ❌ 无 | 需「主证据是否覆盖核心问题」判定 |
| 拒答准确率 | ⚠️ 规则式软兜底（`apply_required_steps` / `query_requests_unsupported_guarantee` 硬编码话术） | 非基于证据置信度的拒答 |
| 高风险错误承诺为零 | ✅ `safety_guard.py` + `reply_rules.py` | — |
| 引用可定位到页码和 Chunk | ❌ 引用只到「知识条目」级（`PromptContextItem` 有 `knowledge_id`/`quote`，**无 `page`/`chunk_id`**） | 依赖 B5/B6 产出 chunk |
| 生成失败可降级 | ⚠️ `chat_service` 有降级分支（`test_chat_service_degrade.py`） | 完整降级链（重试→缩上下文→模板→规则→转人工）未实现 |
| **声明级证据核验**（goal.md 第九节） | ❌ 完全没有 | 需 claim 级 evidence 绑定 + 核验拦截 |
| **流式输出 + 校验后发送**（goal.md 第十节 7） | ❌ 无流式 | 需 SSE + 分句缓冲 + 核验后放行 |

### D. 工程稳定性 —— 完成度约 **35%**

| 验收项 | 现状 | 差距 |
| --- | --- | --- |
| 并发压测达目标 QPS | ❌ 未做过 | 无压测脚本、无 QPS 目标值 |
| P95 延迟满足目标 | ⚠️ 有 `latency_ms` 采集（实测首请求 ~10s / 稳定 ~4s），无 P50/P95/P99 聚合 | `ops_metrics.py` 需扩展分位数 |
| 超时、取消、重试正常 | ❌ 未实现 | — |
| 索引构建失败不影响线上 | ❌ 直接覆盖，无临时目录 + 原子切换 | B6 |
| 数据库和 Redis 可恢复 | ❌ 无 Redis 实例；DB 迁移未落到开发库 | 阶段 7 门禁 |
| 所有关键请求有 request ID / trace ID | ⚠️ 有 `request_id` 与 `full_trace` 事件链 | trace 未与 chunk/evidence ID 打通 |
| **PostgreSQL 复验** | ❌ 全仓库只在 SQLite 上测过 | 台账已标为待办风险 |

---

## 4. 十个核心差距（对 `goal.md` 末段「最核心差距」的逐条落地）

`goal.md` 结尾点名的六项，加上本次实测新发现的四项：

| # | 差距 | 现状定位 | 归属批次 |
| --- | --- | --- | --- |
| 1 | **文档解析质量门禁** | `services/ingestion/parsers/base.py` 只有产物结构自检，无质量检查 | B5 之后新增批次 |
| 2 | **结构化 Chunk** | 无 `chunkers/`，`document_chunks` 表零调用 | **B5** |
| 3 | **真实身份与 ACL 落到检索** | `utils/vector_retriever.py` 无 tenant/acl 参数 | **B6/B7** |
| 4 | **历史问题改写** | 无 rewrite 模块，只有 `build_query_with_intent_hint` 拼接 | B7 之后 |
| 5 | **多跳规划** | 完全没有 | B7 之后 |
| 6 | **声明级证据核验** | 只有离线 grounding 诊断，`judge_status="not_run"` | B7 之后 |
| 7 | **生产级失败处理** | 无索引原子切换、无重试/取消、无完整降级链 | B6 + 后续 |
| 8 | 🆕 **双轨制（ingestion 未接入检索）** | 见第 2 节 | **B5/B6 即为此** |
| 9 | 🆕 **检索评测口径不可信** | recall 实为 intent 匹配，见第 5 节 | 需独立批次修正 |
| 10 | 🆕 **任务队列无消费端** | `queue.publish` 已就绪，无 `xreadgroup`/`worker` | **B6** |

---

## 5. 必须先解决的一个问题：评测口径失真

**这是本次调研最重要的发现，且它会污染上面所有「完成度」数字的可信度。**

看 `scripts/evaluate_retrieval_metrics.py:85`：

```python
def is_relevant(candidate: dict, expected_intents: list[str]) -> bool:
    intent = candidate.get("source", {}).get("intent", "")
    return intent in expected_intents
```

**「相关」的判定 = 召回条目的 `intent` 字段是否等于标注的 `expected_intent`。**

后果：

1. 报出来的 recall@1 = 0.9667 是**「意图分类命中率」**，不是**「检索到正确文档/Chunk 的比例」**。
   语料 781 条、意图只有个位数个枚举值，**只要召回任意一条同意图的 FAQ 就算命中** ——
   门槛比真正的 retrieval 低一个量级。
2. 用例集 `data/chat_grounding_cases.jsonl` 是从原始固定评测集迁移来的，
   语料 `takeout_customer_service_seed.jsonl` 是同一批人按同一套意图体系手工写的，
   **存在构造性重叠（近似 train/test leakage）**。
3. 因此「Recall@5 = 98.89%」这个数字**不能用来证明检索质量**，
   更不能写进简历/README 当作检索能力证据（README 已引用它）。

**修正方向**（建议独立成一个小批次，早于 B7）：

- 金标从「意图」升级为**「文档 ID + Chunk ID」**：标注「这个问题的答案在第几份文档的哪个 chunk」；
- 引入**真实长文档**做评测集（不是 FAQ 对），否则永远测不出「长文档里找对段落」的能力；
- 保留 intent 命中率作为**辅助指标**（Intent Hit Rate，goal.md 第八节确实列了它），
  但必须与 retrieval recall **分开统计、分开命名**；
- 补充 goal.md 第八节要求的 `MRR` / `NDCG` / `Evidence Coverage`，
  以及「无答案问题的拒答准确率」负样本集（现在没有负样本，拒答率无从计算）。

> 顺带：`README.md` 第 6 行的 badge 写的是 `tests-332 passed`，实际已是 555；
> 第 1 节快速验证也写「实测：332 passed」。README 与实测已脱节，需要一起更新。

---

## 6. 对 `docs/RAG_EXECUTION_PROGRESS.md` 的评估

**结论：这是整个仓库里质量最高的工程文档之一，应当保留并继续沿用，但有两处需要补。**

### 6.1 做得好的地方（值得继续）

| 做法 | 为什么有价值 |
| --- | --- |
| **决策记录 `[D-n]` 单独成节**，写「决定 + 理由 + 被否决方案 + 可逆性对比」 | 这是唯一能在半年后回答「当初为什么这么做」的东西。[D-1] 那段用「可逆性」当判据否决三列约束，是教科书级的取舍论证 |
| 每个任务记录 **「未解决问题」** | 不粉饰进度。[T-2.2] 明说「PDF 标题识别是启发式的，会误判」「OCR 未在真实引擎上跑过」 |
| **用测试固化纪律**，而不是写在文档里 | `test_parser_modules_do_not_depend_on_database` 扫描源码；`test_parsers_do_not_accept_tenant_id` 锁签名。文档会腐化，测试不会 |
| 记录**与上游文档的偏离及方向** | [D-8] 那张三列对比表（「更严 / 更宽 / 更可测」）明确标出哪些是超出计划原文的加固 |
| **基线零失败**的纪律 | 每批结束都跑全量并写明起点数字，任何失败不得归因历史遗留 |
| 环境坑单列一节 | 坑 1（嵌套 ref）每次 commit 必踩，已经写成可复用命令 |

### 6.2 两处需要补

1. **缺少「距离目标」的横向视图**。台账是**时间线**（按批次追加），
   没有一张**面向 goal.md 验收口径的完成度表**。翻完 993 行台账，
   也很难回答「现在的系统能做什么、不能做什么、离目标多远」—— 这正是本文件要补的。
2. **缺少「已知未启用资产」的登记**。目前 `document_chunks` 表、`index_builds.manifest_uri`、
   `document_acl`、`tenant_id` 过滤等一堆「建好了没用上」的东西散落在各批记录里，
   没有一处集中说明。**建议在台账第 4 节「执行暂停点」里加一张「已建未启用清单」**，
   避免后来者（或面试官）误以为功能已就绪。

### 6.3 台账的一个隐性风险

台账第 4 节写的是「B5 开工前必读」，第 5 节是环境坑。这两节**每次开工都要读**，
但目前内容已经很长（第 4 节约 80 行、第 5 节约 120 行）。
建议把**环境坑抽成独立文件**（例如 `docs/ENV_PITFALLS.md`），台账只留一行链接 ——
否则后续每批都要在噪声里找关键信息。

---

## 7. 代码结构需要改什么

### 7.1 必须新增（对应 B5~B8）

```
config/chunking_config.py                       # B5  chunk 默认配置 + 知识库覆盖 + 越界启动失败
services/ingestion/chunkers/
    __init__.py
    base.py           # Chunk 契约 + 确定性 ID
    structural.py     # 结构感知切分（9 条要求）
    tokenizer.py      # tokenizer 抽象（mock 可注入，测试不下载模型）
services/ingestion/pipeline.py                  # B6  幂等流水线（状态机）
services/ingestion/worker.py                    # B6  queue 消费端（xreadgroup/xack）
services/ingestion/index_manifest.py            # B6  manifest 读写 + 原子切换 + 回滚
services/ingestion/quality_gate.py              # 🆕 解析/索引质量门禁（goal.md 第三节 4）
routers/retrieval.py                            # B7  从 AuthContext 生成 filter
tests/test_chunking.py                          # B5
tests/test_ingestion_pipeline.py                # B6
tests/test_retrieval_isolation.py               # B7  🆕 检索层隔离（现缺）
```

### 7.2 必须改造

| 文件 | 改什么 | 风险 |
| --- | --- | --- |
| `utils/vector_retriever.py`（901 行） | 加 `index_manifest.json`；`retrieve_*` **强制接收** tenant/ACL filter；**缺失 filter 直接抛异常**；命中返回 chunk/document/version/tenant/ACL | 这是**最大的重构点**。901 行里混了 toy 索引、真实索引、rerank、规则加权四类逻辑，建议先拆分再改造 |
| `routers/retrieval.py` | filter 只能由服务端从 `AuthContext` 构造；禁止客户端传 FAISS ID / filter 表达式 | — |
| `services/chat_service.py`（1229 行） | 引用结构加 `page_start`/`page_end`/`chunk_id`；接证据充分性判定与拒答 | 文件已 1229 行，改动前建议先拆 `retrieval_orchestration` / `response_building` |
| `services/knowledge_service.py` | 现在的「追加 JSONL → 重建 FAISS」手工链路，要逐步让位给 pipeline（或明确保留为"人工审核直发"旁路） | **双轨收敛点，最需要想清楚** |
| `services/grounding_diagnostics.py` | `judge_status="not_run"` → 接入运行时核验 | — |
| `README.md` | badge 332 → 555；补充「已建未启用」的诚实说明；更新架构图 | 对外文档失真会影响面试可信度 |

### 7.3 结构层面的两个建议

1. **`utils/vector_retriever.py` 应该拆**。901 行、4 类职责、无 tenant 参数，
   在 B6 里同时塞进 manifest 和 filter，只会让它变成 1200 行的不可维护文件。
   建议拆成 `retrieval/{embedding, faiss_store, sparse, fusion, rerank, filters}.py`。
2. **`services/ingestion/` 应该补 `README.md` 或模块 docstring 地图**。
   现在 8 个顶层模块 + 8 个解析器 + 待加的 6 个模块，新人（或面试官）
   打开目录看不出「哪条是主链路、谁调谁」。一张 15 行的调用顺序图就够。

### 7.4 不需要改的（明确别动）

- `services/ingestion/parsers/base.py` 的契约（`Block` 七字段、`ParsedDocument` 四+三字段、
  三个封闭枚举集合、`heading_path` 语义）—— B3 已冻结，改要走「改测试 + 提 parser_version + 补决策记录」三件套；
- `services/ingestion/models.py` 的 13 张表结构 —— 已通过「迁移产物 vs 模型定义逐项对照」测试锁定；
- 上传接口的职责边界（不建版本、不判重复、不做内容 hash）—— [D-6] 已定论，自洽。

### 7.5 存储选型评估：FAISS / SQLite / PostgreSQL / pgvector（2026-09-23 实测）

> 结论先给：**FAISS 够用，不用换向量库；PostgreSQL 需要但目前不是瓶颈；pgvector 现在不要动。**

#### （1）FAISS 够不够用 —— 够用，而且缺口不在向量引擎

**实测当前规模**（`data/faiss_store/real_vector.index`）：

| 项 | 值 |
| --- | --- |
| 索引类型 | `faiss.IndexFlatIP`（**精确暴力检索**，非近似） |
| 向量数 | **781** |
| 维度 | **512**（bge-small-zh-v1.5；由 `781×512×4 + 45 = 1,599,533` 字节反推） |
| 索引体积 | 1.6 MB |
| 单查询成本 | 781 次点积 → 微秒级，可忽略 |

**规模上限测算**：512 维 × 4 字节 = 2 KB/向量。
10 万 chunk ≈ 200 MB、约 5 ms/查询；100 万 chunk ≈ 2 GB、约 20–30 ms/查询（单线程）。
**平铺索引到百万级才需要换 IVF/HNSW**，本项目离得很远。

**真正的问题不是引擎，是过滤** —— 而且是两个层次的问题：

1. **现在完全没有过滤**：`utils/vector_retriever.py` 零处 `tenant`/`acl`；
   索引里只有 781 条手工 FAQ，**连租户字段都不存在** —— 多租户隔离今天不可能实现，
   不是"未启用"而是"无数据可依"。
2. **一旦加过滤，"怎么过滤"决定了召回会不会塌。** 实测对比（5000/20000 条语料，
   租户可见 20 条 ≈ 0.1%，查询是目标向量的改写版，目标全局排名第 **125**）：

   | 方案 | top-10 | top-50 | top-100 | top-500 |
   | --- | --- | --- | --- | --- |
   | **后过滤**（全局 top-k → 再按租户筛） | 0 条 ❌ | 0 条 ❌ | 1 条 ❌ | 2 条 ✅ |
   | **预过滤**（`IDSelector`，只在租户内搜） | — | — | — | **必然含答案** |

   后过滤在 `top-50` 时**返回 0 条**——系统会对用户说"没找到资料"，
   而证据其实就在库里。原因：目标被成千上万条**其他租户**的向量挤出了全局 top-k。
   租户越小越严重，这正是多租户系统的典型失效。

3. **好消息：FAISS 原生支持预过滤，不需要换库。** 实测（faiss 1.13.2，本机已验证）：

   ```python
   params = faiss.SearchParameters()
   params.sel = faiss.IDSelectorBatch(allowed_ids)   # allowed_ids 由服务端构造
   D, I = index.search(query, k, params=params)      # 只在允许集合内检索
   ```
   实测 `IndexFlatIP` 支持该参数，且结果全部落在允许集合内；
   `index.remove_ids(IDSelectorBatch(...))` 也可用（版本更新/撤回时删旧 chunk）。

   → **这直接回答了计划 3.4「检索前强制传入服务端构造的 tenant/ACL filter」怎么落地**：
   用 `IDSelector` 预过滤，不需要引入向量数据库。
   前置条件是 manifest 里那张 `chunk_id ↔ FAISS int64 id` 映射
   （计划原文「向量序号不得再隐式等于 JSONL 行号」说的就是这件事）。

**FAISS 真正的局限（要记录、要缓解，但都不构成换库理由）**：

| 局限 | 影响 | 缓解 |
| --- | --- | --- |
| 索引驻留进程内存，**多进程各自一份** | 重建索引后其他 worker 仍是旧索引 → 读到已删除内容 | 重建后广播 reload（或按 mtime/版本号轮询）；**索引必须能随时从 DB 重建** |
| 无事务，**与元数据双写不一致** | chunk 落库成功但建索引失败 → 元数据与向量分叉 | manifest + 构建到临时目录 + 原子切换（计划 3.4 已要求）；索引入口幂等 |
| 删除是 O(n) 且非并发安全 | 版本更新时不能边搜边删 | 走"建新索引再切"，不在线上原地删 |
| 无内建权限/RBAC | 完全靠应用层 | `IDSelector` + 服务端构造 filter（见上） |

> **一句话**：FAISS 在本项目的规模下**绰绰有余**，把它换成向量数据库
> 解决不了当前任何问题（当前问题是**没有接线**）；而它的真实局限
> 都能被计划里已有的 manifest + 原子切换 + `IDSelector` 覆盖。

#### （2）需不需要换 PostgreSQL —— 需要，但不是现在的瓶颈

`docs/RAG_EXECUTION_PLAN...md` 第 1 节已经定了方向：
「**PostgreSQL 保存文档、版本、ACL、处理任务和审计数据**；FAISS 保留为第一期检索后端」。
所以这不是"要不要换"的问题，是"什么时候切"。

**SQLite 在本项目会真实碰到的问题**：

| 问题 | 何时变成真问题 |
| --- | --- |
| **单写者**（库级写锁，WAL 下也只允许 1 个写事务） | **B6 引入 worker 时**——worker 写 chunk / 推 job 状态，同时 API 在写 job / 审计 → `database is locked` |
| 无 `JSONB` / `GIN` 索引 | ACL、有效期过滤要走 SQL 时（B7） |
| 无 `ARRAY` 类型 | `acl` 多值存储只能塞 JSON 字符串 |
| 外键需**连接级** `PRAGMA foreign_keys=ON` | 已踩过（[D-1] 阶段的决策记录：不打开则跨租户外键测试形同虚设） |
| 无行级安全（RLS） | 无法用 DB 兜底租户隔离 |

**但结论是"不急"，理由**：

1. **ingestion 数据库今天还不存在**（`data/rag_metadata.db` 缺失），
   迁移在 SQLite 上也只跑过临时库 —— 先让它跑起来比换引擎重要；
2. **切换成本已经很低**：迁移是方言无关写法，连接串只从 `RAG_DATABASE_URL` 读，
   `docker-compose.yml` 里 postgres:16 + redis:7 已经定义好 —— 换机器/加 Docker 只改环境变量；
3. 本机**无 Docker、无 psql**，现在切过去**无法验证**，只会得到一个没测过的分支。

**建议时点**：**B6 实现 worker 时**把 PG 一起验（worker + API 并发写正好是 SQLite 的死穴）；
最迟在 **B8 发布门禁**里作为必过项（计划 7.4 已要求）。

#### （3）pgvector 要不要上 —— 不要，现在不要

**它诱人的地方**：把向量和元数据放进**同一个事务**，
一举消掉「chunk 落库成功但建索引失败」的双写不一致；
过滤 + 向量检索一条 SQL 搞定；ACL/租户用 WHERE 天然生效。

**但不该现在做的四个理由**：

1. 要替换 `utils/vector_retriever.py`（901 行）——而它现在**连 manifest 和 filter 都还没加**，
   一次改两件事，风险不可控；
2. 本机无 PG，**改了没法跑测试**，等于写一个验证不了的分支；
3. 计划明确「FAISS 保留为第一期检索后端」——偏离要先有决策记录和实测依据；
4. **它解决的不是当前最痛的问题**。当前最痛的是双轨制（没有接线），
   而 pgvector 是在"接线已经通了"之后才谈的优化。

**什么时候重新评估**（触发条件，写下来就不用每次争论）：

- chunk 数 > **100 万**，平铺检索延迟顶不住；
- 需要**多实例/多 pod** 且索引要实时更新（FAISS 各进程一份副本的代价超过收益）；
- 双写不一致**真的发生过**且原子切换无法覆盖（届时用 data 说话，而不是猜）；
- 需要**跨字段联合过滤 + 向量**的复杂查询（如「租户 A + ACL 含 role:agent + 有效期未过 + 标题匹配」）。

**在那之前，正确动作是**：把 manifest、`IDSelector` 过滤、原子切换、
"索引可从 DB 完整重建"这四件事做好 —— 它们**同时也是将来迁 pgvector 的前置条件**
（有了 manifest 和重建能力，迁移才是可验证的替换而不是重写）。

---

## 8. 路线图

### 8.1 已排定的（沿用现有计划，不重复设计）

| 批次 | 内容 | 完成后解锁 |
| --- | --- | --- |
| **B5** | Chunk 配置 + 结构化切分器 | chunk 级引用、权限元数据随 chunk 进索引 |
| **B6** | 幂等流水线 + worker + FAISS manifest 原子切换 | 上传 → 可检索**闭环打通**，双轨收敛 |
| **B7** | 资源级权限 + 路由改造 + 检索 API 隔离 | 租户/ACL 真正生效 |
| **B8** | 阶段 7 总审查与发布门禁（含 PostgreSQL 复验） | 可对外的「生产就绪」声明 |

### 8.2 建议插入的（本文件新增建议）

| 优先级 | 事项 | 理由 |
| --- | --- | --- |
| **P0** | 修正检索评测口径（第 5 节） | 现在的数字不可信，越晚修越难改；且它会影响后续所有优化决策的判断依据 |
| **P0** | 补 `tests/test_retrieval_isolation.py` | 现有 14 条隔离测试守的是数据层，检索层裸奔 |
| **P1** | 解析质量门禁（`quality_gate.py`） | goal.md 明确要求；且它决定「坏文档静默入库」这个最危险的失效模式 |
| **P1** | README 与实测对齐 | 对外文档失真 |
| **P2** | 拆 `vector_retriever.py` / `chat_service.py` | 不急，但 B6/B7 一动它们就必须拆，否则改动风险失控 |
| **P2** | 环境坑抽成独立文件 | 台账可读性 |
| **P3** | 真实 OCR 引擎复验 | 归入 B8 门禁 |
| **P3** | 压测与 P95/P99 聚合 | 归入 B8 门禁 |

### 8.3 明确「暂不做」的（避免范围失控）

以下都在 `goal.md` 里，但**不建议现在做**，理由是要先有 chunk 与权限过滤这两块地基：

- query rewrite / 多跳分解（没有 chunk 级检索，改写了也定位不到证据）；
- 声明级 evidence 核验（没有 chunk_id，claim 绑不到证据）；
- 流式输出 + 校验后放行（先把问答链路跑通，再谈流式体验）；
- ACL 授权管理接口（先有检索层过滤，再有授权入口）；
- 跨库共用文档的关联表（[D-1] 已论证是纯增量，不急）。

---

## 9. 建议的验收口径（给 B8 用）

`goal.md` 第十二节给了四档验收，但没给**可执行的判据**。建议：

| 档 | 判据 | 现在 |
| --- | --- | --- |
| A 文档入库 | 四种文件端到端：上传 → 解析 → chunk → 索引 → 可检索，且 chunk 数 ≥ 2 | ❌ 断在 chunk |
| B 检索 | ① 检索层隔离负向测试全绿；② **文档/Chunk 级** recall@5 ≥ 目标；③ 缺 filter 的查询被拒绝 | ❌ ①②③ 全缺 |
| C 回复 | ① 引用含 `page_start`/`chunk_id`；② 无证据时拒答而非编造；③ 高风险承诺 = 0 | ⚠️ 只有 ③ |
| D 工程 | ① 索引构建失败可回滚且不影响线上；② PostgreSQL 上全量测试通过；③ 有 P95 数字 | ❌ 全缺 |

**建议把这张表作为 B8 门禁的 checklist**，逐项写「通过 / 未通过 / 证据位置」，
比现在台账里「阶段审查结论」的文字描述更容易验证。

---

## 10. 附：本文件结论的实测命令

```bash
# 测试数
./venv/Scripts/python.exe -m pytest -q --collect-only | tail -1        # 555 tests collected

# 检索层是否有 tenant/acl
grep -n "tenant\|acl" utils/vector_retriever.py                        # 零命中

# 是否有 BM25 / manifest / 流式
grep -rn "bm25\|BM25" --include=*.py . | grep -v venv                  # 零命中
grep -rn "manifest" --include=*.py utils/ services/ | grep -v venv     # 仅 DB 表字段
grep -rn "StreamingResponse" --include=*.py routers/                   # 零命中

# ingestion 是否被检索链路引用
grep -rn "from services.ingestion" --include=*.py routers/ services/ utils/ main.py
#   → 只有 routers/documents.py 和 parsers 内部互相引用

# 索引数据源
grep -n "takeout_customer_service_seed\|iter_knowledge_items" utils/*.py services/*.py

# chunk 表是否被写过
grep -rn "insert_chunks" --include=*.py . | grep -v venv                # 仅定义，零调用

# ingestion 库是否落过盘
ls data/rag_metadata.db                                                 # 不存在
```
