# RAG 语料建设与批量入库方案

> **调研日期：2026-09-23**
> **调研触发**：当前向量库里只有 781 条单条 FAQ 数据，从未有过「很多文档 → 解析 → 切分 → 向量库」的真实流量。
>
> 相关文档：
> - `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` —— 可执行任务清单（阶段 0~7）
> - `docs/RAG_EXECUTION_PROGRESS.md` —— 执行进度台账（B1~B7 完成，B8 未开始）
> - `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md` —— 差距清单与存储选型评估
>
> **本文件与上述文档的关系**：台账记录「已交付的批次」，本文件记录「一个不在原计划批次编号内的
> 新问题 —— 管道建好了但没有语料流过」。它不是新阶段，也不改变 B8 的范围。

---

## 0. 一句话结论

**文档解析与切分的代码已经是完整的、可用的（B3 解析器 / B5 切分器 / B6 流水线+索引 / B7 隔离），
但实测 `document_chunks` 表 0 行、`document_versions` 0 行、`index_builds` 0 行、
`data/faiss_store/` 下没有 `v1/` 目录 —— 这条链路从未有过一次真实流量。**

所以现在缺的不是能力，是**三样东西**：语料（没有文档文件）、批量入口（只有单文件 HTTP 上传）、
以及把问答主链路切到新索引（否则灌了也检索不到）。

---

## 1. 现状实测（本文件所有结论的依据）

### 1.1 向量库里到底有什么

| 项 | 实测值 | 来源 |
| --- | --- | --- |
| 索引文件 | `data/faiss_store/real_vector.index`（1.6 MB） | `ls -la data/faiss_store/` |
| 索引类型 | `faiss.IndexFlatIP`（精确暴力检索） | `utils/vector_retriever.py:186` |
| 向量数 | **781** | 解析 `real_vector_docs.json` |
| 维度 | 512（bge-small-zh-v1.5） | `781×512×4 + 45 = 1,599,533` 字节反推 |
| 文本长度 | min 64 / **p50 139** / max 3168 / **avg 203 字符** | 同上 |
| 数据来源 | `data/takeout_customer_service_seed.jsonl`（781 行手工 FAQ） | `utils/retriever.py` |
| 每条内容 | `分类/意图/问题/答案` 拼成的一段短文本 | 抽样首条 |
| 携带的元数据 | `id` / `text` / `answer` / `source`（source 里是 seed 原始行） | 字段统计 |

**结论**：这 781 条是**短问答对**，一条一个向量，没有文档、没有版本、没有页码、
没有标题路径、没有 chunk_id、没有 tenant/ACL。它不是"文档切分"的产物，
而是"一行 JSONL 一个向量"的直灌。这正是你说的「简单的几行单条数据」。

### 1.2 ingestion 链路的状态：代码齐、数据零

| 资产 | 代码状态 | 数据状态 |
| --- | --- | --- |
| 解析器（PDF/DOCX/HTML/MD/TXT + OCR 接口） | ✅ B3 完成 | ⚠️ 只跑过 `tests/fixtures/sample.*` |
| 切分器（结构感知、parent-child、80 token 重叠） | ✅ B5 完成 | ❌ 0 次真实运行 |
| 幂等流水线（8 阶段状态机） | ✅ B6 完成 | ❌ `ingestion_jobs` 仅 2 行（联调残留） |
| 索引重建 + manifest + 原子切换 + 回滚 | ✅ B6 完成 | ❌ `index_builds` **0 行**，磁盘无 `v1/` |
| chunk 落库 | ✅ B6 完成 | ❌ `document_chunks` **0 行** |
| 文档版本 | ✅ B6 完成 | ❌ `document_versions` **0 行** |
| ACL | ✅ B1/B7 完成 | ❌ `document_acl` **0 行**（无写入入口） |
| 检索隔离（tenant/ACL 前置过滤） | ✅ B7 完成 | ⚠️ 索引为空，过滤无对象可过滤 |

实测命令：

```bash
./venv/Scripts/python.exe tmp/inspect_rag_state.py
```

### 1.3 检索端：双轨并存，主链路仍在旧轨

| 轨道 | 入口 | 数据源 | 状态 |
| --- | --- | --- | --- |
| **A 轨** | `services/chat_service.py:966` → `retrieve_rag_items()` | `real_vector.index`（781 条 FAQ） | ✅ 实际在跑 |
| **B 轨** | `POST /retrieval/search` → `build_chunk_access_filter` → `retrieve_chunk_items` | manifest 索引（`document_chunks`） | ⚠️ 接线完成，**索引为空** |

`routers/retrieval.py` 的 docstring 自己写明了这个划分：正式路径是 chunk 索引，
`/retrieval/search-demo` 才是种子 FAQ 演示路径。**但问答主链路 `chat_service` 走的还是 A 轨。**

> 这条结论意味着：**即使今天把 100 份文档灌进去，聊天问答也一个字都检索不到** ——
> 因为它读的是另一份索引。这是本次调研第二个关键发现。

---

## 2. 后续应该怎么调整（五个动作，按依赖顺序）

### 动作 1 · 补批量导入器（最关键的一件，现在完全没有）

**现状**：只有 `POST /knowledge-bases/{kb}/documents` 这个单文件 HTTP 入口，
灌 100 份文档要发 100 次带 JWT 的请求 —— 不可行。

**新增**：`scripts/bulk_import_documents.py`

```
输入：本地目录（递归扫描 .pdf/.docx/.html/.md/.txt）
流程：逐文件 → 内容嗅探（复用 content_sniff）→ 对象存储 put（复用 build_object_key）
      → 建 document → 建 ingestion_job → 投队列
参数：--root <目录> --tenant <id> --kb <id> --dry-run --limit N
```

**三个硬约束（照抄现有实现的约定，不要另起一套）**：

1. **对象 key 必须用 `object_store.build_object_key(tenant_id, document_id, filename)`** ——
   因为 `pipeline._stage_stored` 会用同一个函数反解 key 去找字节，自己拼 key 会导致
   `object_error_missing`（而且这个错误看起来像"文件丢了"，很难查）。
2. **不建版本、不建 chunk** —— 版本由流水线在 `parsed` 阶段建（[D-6] 已定论），
   导入器抢着建会撞 `uq(tenant_id, content_hash)`。
3. **幂等靠内容 hash** —— 同一个文件导入两次，第二次会在 `parsed` 阶段判为
   `duplicate` 并复用已有版本，不产生第二份有效数据。这条已有测试覆盖。

**抽验要求**：批量走内部路径，但**至少 4 份文件（PDF/DOCX/HTML/MD 各一）必须走 HTTP 真实上传**，
证明公开入口也是通的。用 `scripts/mint_dev_token.py` 签令牌。

### 动作 2 · 让 worker 真的消费一批

**现状**：worker 有完整入口，但**没有任何地方会启动它**。
`scripts/seed_dev_tenant.py:161` 自己都写了：「API 进程投递的任务不会被独立的 worker 进程消费 ——
上传后任务会停在 pending」。

```bash
# 批量导入后，一次性消费干净
./venv/Scripts/python.exe -m services.ingestion.worker --once --batch 500

# 或者常驻（开发时）
./venv/Scripts/python.exe -m services.ingestion.worker
```

**注意**：`--once` 只跑**一轮**，`--batch` 是每轮最多取多少条。
投了 300 个 job、`--batch 100`，一轮只处理 100 个 —— 要么 batch 设得比投递数大，
要么循环调用到队列空。

### 动作 3 · 用一批真实文档做端到端验收（这才是能力的证明）

跑完前两步后，逐项核对：

| 核对项 | 期望 | 命令/位置 |
| --- | --- | --- |
| 文档版本已发布 | `document_versions` 行数 = 文档数，`status='published'` | 查 `data/rag_metadata.db` |
| chunk 已落库 | `document_chunks` 行数 = 各文档 chunk 之和，**每份 ≥ 2 个** | 同上 |
| 索引已切换 | `index_builds` 有 1 条 `status='active'`，`manifest_uri` 非空 | 同上 |
| 磁盘有索引 | `data/faiss_store/v1/index_manifest.json` + `chunks.index` 存在 | `ls` |
| manifest 条数一致 | `chunk_count` = `document_chunks` 行数 | 读 manifest |
| 检索可命中 | `POST /retrieval/search` 返回 `retrieval_path='chunk-index'`，且命中带 `chunk_id`/`page_start`/`heading_path` | curl |
| 切分质量 | 无 `oversize` 异常堆积；`warnings` 里没有大面积"空文本/低质量" | 读 `document_versions.metadata_json` |

**这一步才是「文档解析和切分真的实现了」的证据** —— 在此之前，
所有相关能力都只有单元测试级别（`tests/fixtures/sample.*`）的证明力。

### 动作 4 · 把问答主链路切到 chunk 索引

**这是最容易漏掉的一步**。`chat_service.py:966` 现在调 `retrieve_rag_items()`（A 轨）。
灌完文档后如果不切，表现是「索引里有数据，但聊天还是答不出新内容」。

**建议做法（不要一次切死）**：

1. 加一个配置开关（环境变量，如 `RAG_RETRIEVAL_PATH=chunk|seed`），默认仍走 seed，
   保证现有评测与前端不被打断；
2. `chat_service` 按开关调 `retrieve_chunk_items` 或 `retrieve_rag_items`；
3. 切过去要处理**字段差异**：A 轨 item 带 `intent`/`category`，
   B 轨命中带 `chunk_id`/`page_start`/`heading_path`。
   下游 `build_prompt_context_items` 与 `build_top1_intent` 依赖前者 ——
   解决办法是**在 ingest 时把 category/intent 写进 chunk 的 metadata**，
   让 B 轨也能提供这些字段（而不是让下游降级）。
4. 双跑对比一批问题，确认回答质量不劣化，再切默认值。

### 动作 5 · 重建评测金标（口径必须换）

现有 recall 口径是「召回条目的 `intent` 字段 ∈ 标注意图」——
`scripts/evaluate_retrieval_metrics.py:85` 的 `is_relevant()`。
语料从 FAQ 换成文档后，这个口径**连勉强能算都算不了**（文档 chunk 没有 intent）。

金标要升级为：**「问题的答案在第几份文档的哪个 chunk」**（doc_id + chunk_id + 位置），
并补 30~50 条**无答案负样本**（用于算拒答准确率，现在没有负样本，拒答率无从算起）。

> 这一条 `RAG_GAP_ANALYSIS_AND_ROADMAP.md` 第 5 节已经列为 P0，本文件补充的是
> **换语料之后它从"应该修"变成了"必须修"** —— 否则整个优化循环没有判据。

---

## 3. 文档从哪里获得

### 3.1 核心判断：现有语料全部"形态不对"

| 现有资产 | 内容 | 为什么不够 |
| --- | --- | --- |
| `data/takeout_customer_service_seed.jsonl`（781 行） | 合成 FAQ 问答对 | 太短（均 203 字），无结构 → 切分器**无活可干**（一份 203 字内容切不出多个 chunk） |
| `data/raw/jd_help_faq.jsonl.gz`（672 条） | 京东帮助中心抓取的 FAQ | 同样是问答对，且**爬虫存的是抽好的 QA，不是原文 HTML**，HTML 解析器也用不上 |

**要展示"文档解析 + 切分"的能力，必须补真正有结构的长文档** ——
有标题层级、有页码、有表格，才谈得上"切"。

### 3.2 第 1 档：把已有 FAQ 聚合成文档（零外部依赖，立刻可做）

**做法**：按 `parent_category > category` 把 FAQ 聚合，每个分类生成一份 Markdown：

```markdown
# 配送方式 / 京东配送服务说明

## 自提订单是否收费（自提订单运费）？
京东自提订单运费规则与送货上门订单运费规则一致，详情请见京东订单运费收取标准。

## 自提点/自提柜可以保留货物几天？
货物到达自提点后可以保留三个自然日。收到自提短信后，请尽快前往自提点提取货物。
...
```

**产出的规模估算**：672 条 ÷ 约 15 个分类 ≈ **每份 40 条 QA ≈ 4000~10000 字
→ 每份切出 8~25 个 chunk**。781 条 seed FAQ 同样可聚合，再加 15~20 份。

**能验证到什么**：装箱（target 450 / max 700）、相邻 chunk 80 token 重叠、
标题路径前缀、parent-child 归属。

**验证不到什么**：页码（Markdown 无 page 概念）、表格重复表头、代码块不截断、OCR 触发。

### 3.3 第 2 档：真实长文档（推荐作为主体）

| 来源 | 类型 | 拿到什么能力 | 备注 |
| --- | --- | --- | --- |
| **平台规则/帮助中心原文 HTML** | HTML | `html.py` 的 script/style/nav/footer 清理、标题层级、表格 | 现有 `crawl_jd_help.py` 已能定位文章页（`div.help-tit1` + `#pdfContainer .contxt`），改造为**保存原文 HTML 片段**即可，改动很小；美团/饿了么规则中心同理 |
| **法规与国家标准全文** | PDF | `pdf.py` 的页码保留、跨页段落、长文档切分压力 | 《电子商务法》《消费者权益保护法》《网络交易监督管理办法》《个人信息保护法》等全文公开；国家标准全文公开系统（openstd.samr.gov.cn）部分可下载。**真实长文档、有条文编号层级、有表格，是 PDF 解析最好的素材** |
| **公开中文长文档问答数据集** | 混合 | 多跳/长文档检索评测 | 体积大，注意许可与仓库体积门槛（见 3.5） |
| **自造业务手册**（强烈推荐） | DOCX/PDF/MD | 端到端 + **评测金标** | 见 3.4 |

### 3.4 强烈推荐：自造 3~5 份业务手册

**理由不是"省事"，是"它决定评测金标的质量"**：

- 你可以**精确设计"答案在哪一章哪一节"**，金标不依赖人工盲标；
- 内容与业务一致（外卖客服），检索结果可读、可判断对错；
- 无版权风险，可进演示材料；
- 素材现成：把 781 条 seed FAQ 按主题**扩写成手册体**——FAQ 是"一问一答"，
  手册是"制度条文 + 处理流程 + 例外条款"，两者正好是**短文本 → 长文档**的转换。

建议的三份：

| 文档 | 结构（用来触发不同切分分支） |
| --- | --- |
| 《外卖平台配送服务规范》 | 章节层级 + 时效对照**表格** + 例外条款列表 |
| 《售后与退款处理手册》 | 分角色流程（用户/商家/骑手）+ 金额分级**表格** |
| 《食品安全与投诉应急指引》 | 分级响应 **列表** + 上报流程 + **附录表格** |

### 3.5 三个必须注意的约束

1. **仓库体积门槛**：`scripts/check_repo_data_size.py` 在 CI 里守护单文件 1 MB（见 `.gitignore:57`）。
   现状是**只有 `data/faiss_store/` 与 `data/knowledge_backups/` 被忽略，`data/raw/` 没有** ——
   现有 672 条 FAQ 之所以存成 `.jsonl.gz`（498 KB）就是为了压在门槛内。
   因此：**PDF/DOCX 原始文件不要直接放 `data/raw/` 提交**，
   三选一 —— 加进 `.gitignore`、压缩、或只提交生成脚本 + 来源说明。
   （索引本身在已忽略的 `data/faiss_store/` 下，不受影响。）
2. **版权**：京东帮助中心、平台规则页面的内容仅作**本地学习用**，
   不进仓库、不进对外演示材料（现有 `crawl_jd_help.py` 已经走的是本地 gz，这个做法保持）。
3. **不要用真实客服对话**：隐私风险，`data/dataset_sources.md` 已明确不建议。

### 3.6 推荐组合（约 35~55 份，产出 400~1000+ chunk）

| 优先级 | 语料 | 份数 | 验证到的能力 |
| --- | --- | --- | --- |
| **P0** | FAQ 按分类聚合的 Markdown | 15~20 | 装箱 / 重叠 / 标题路径 / 空文档 |
| **P0** | 自造业务手册（DOCX + PDF 各半） | 3~5 | 端到端闭环 + **评测金标** |
| **P1** | 法规/标准全文 PDF | 5~10 | 页码 / 跨页 / 长文档压力 |
| **P1** | 帮助中心原文 HTML | 10~20 | HTML 清洗 / 表格 |

**规模是否够**：FAISS 平铺索引在 512 维下约 2 KB/向量，
**1000 条 ≈ 2 MB、单查询微秒级**；即使 10 万 chunk 也只有 200 MB、约 5 ms。
本项目的规模离需要换向量库还差两个数量级 —— 详见
`RAG_GAP_ANALYSIS_AND_ROADMAP.md` 第 7.5 节的实测测算。**所以"很多文档"在
这个项目里指 50 份左右就足够说明问题，不需要真的灌一万份。**

---

## 4. 验收口径（做完这一轮，应当能回答的问题）

| # | 问题 | 达标判据 |
| --- | --- | --- |
| 1 | 有真实文档走过全链路吗？ | `document_versions` 行数 = 导入文档数，`status='published'` |
| 2 | 切分真的发生了吗？ | 每份文档 chunk 数 ≥ 2；`document_chunks` 行数与 manifest `chunk_count` 一致 |
| 3 | 索引可检索吗？ | `POST /retrieval/search` 返回 `retrieval_path='chunk-index'` 且命中带 `chunk_id`/`page_start`/`heading_path` |
| 4 | 问答主链路用上新语料了吗？ | `chat_service` 可切到 B 轨，回答引用的来源含文档名与章节 |
| 5 | 评测口径可信吗？ | 金标为 doc/chunk 级，含负样本；intent 命中率**单独命名**统计 |
| 6 | 失败可诊断吗？ | 故意放一个坏文件，任务 `error_code` 可查、可重试、不污染已发布版本 |

---

## 5. 明确不做 / 暂不做

- **不换向量库**：当前缺口是"没有数据"，不是"引擎不够"。FAISS + `IDSelector` 预过滤
  在本项目规模下足够，理由与实测数据见 `RAG_GAP_ANALYSIS_AND_ROADMAP.md` 第 7.5 节。
- **不追求语料数量**：50 份结构良好的文档 ≫ 5000 条 FAQ，前者能验证切分，后者不能。
- **不在本文件范围内做 query rewrite / 多跳 / 声明级核验**：
  先有 chunk 级检索，那些才有落点。

---

## 6. 本文件的实测依据（可复跑）

```bash
# 向量库内容与规模
./venv/Scripts/python.exe tmp/inspect_rag_state.py

# 两轨接线现状
grep -rn "search_chunk_index\|retrieve_rag_items" --include=*.py routers/ services/ utils/
#   → chat_service.py:966 用 retrieve_rag_items（A 轨）
#   → routers/retrieval.py 用 retrieve_chunk_items（B 轨）

# 索引从未构建
find data/faiss_store -maxdepth 2
#   → 只有 real_vector.index / real_vector_docs.json，无 v*/ 目录

# worker 无启动点
grep -rn "services.ingestion.worker" --include=*.py scripts/ main.py
```

> 排查脚本落在 `tmp/inspect_rag_state.py`（只读，不改数据）。
> `tmp/` 不是持久位置，如需保留请迁移到 `scripts/`。

---

## 7. 执行结果（2026-09-23 实测，本文件不再是「计划」而是「已落地」）

> 本批把第 5 节「五个动作」里的**前三个**做完了，第 4、5 个（切轨、重建评测金标）
> 属于代码改动 + 业务口径，**未做**。下面全部是实测数字，不是估算。

### 7.1 三个新脚本（都已跑通）

| 脚本 | 干什么 | 跑法 |
| --- | --- | --- |
| `scripts/build_corpus_documents.py` | 生成语料（法规抓取+缓存 / FAQ 聚合 / 自造手册 → 四格式 + `manifest.jsonl`） | `./venv/Scripts/python.exe scripts/build_corpus_documents.py --limit 25 --sleep 0.6` |
| `scripts/bulk_import_documents.py` | 批量灌库（走 `repository` + `pipeline.process_job`，末尾自动重建索引） | `RAG_JWT_SECRET=... HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ./venv/Scripts/python.exe scripts/bulk_import_documents.py` |
| `tmp/smoke_retrieval.py` | 端到端检索冒烟（走生产路径 `build_chunk_access_filter` → `search_chunk_index`） | 同上 |

**依赖**：`requirements.txt` 锁的 `transformers==5.6.1` / `sentence-transformers==5.4.1`
已按踩坑 A7 的做法装到 `venv/rag_ml_deps/`（配 `.pth` 插到 `sys.path[0]`）。
模型走本地缓存（`~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5`），
**必须带 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`**，否则会去连 Hub 撞代理 502（踩坑 A10）。

### 7.2 语料实测

```
103 份文档 · 209 个文件 · 6.4 MB
格式：pdf 18 · docx 18 · html 88 · md 85
类别：法律法规 30（15 部真实法规全文）· 客服问答 170 · 业务手册 9
字数：min=429  p50=5609  max=23421
```

法规来自 `policy.mofcom.gov.cn/claw/clawContent.shtml?id=<id>`（商务部政策法规库，
全国人大/国务院各部委的法规全文），正文用 `trafilatura` 抽取后按「第X章 / 第X节 / 第X条」
切 section —— **这一步决定了切分器能不能切出标题路径**，实测能（见 7.4）。

> 取 id 的办法是**枚举扫描**（`65150~66000`，1001 个 id 中 647 个有效），
> 命中业务相关关键词的只有 27 条（2.7%）—— 该区间的法规偏税务/工程。
> 列表接口是 JS 渲染的，没有可用的 JSON 端点，**扩量要先解决取 id 的问题**。

### 7.3 入库实测

```
导入 209/209 成功（判重 6）· 失败 0 · 耗时 112.4s
产出 chunk 8993
document_versions published = 209
生效索引 v2 active · 9229 chunk（v1 的 236 + 本批 8993，v1 superseded）
索引体积 38.1 MB · embedding BAAI/bge-small-zh-v1.5 512 维
```

对比开工前：`document_chunks` / `document_versions` / `document_acl` / `index_builds`
**全 0 行**，`data/faiss_store/` 下连 `v1/` 都没有。

### 7.4 端到端检索实测（5 条 query 全通）

| query | Top1 命中 | score |
| --- | --- | --- |
| 电子商务经营者应当履行的义务 | `中华人民共和国电子商务法 > 第一章 总 则 第五条 …` | 0.7832 |
| 广告不得含有哪些内容 | `中华人民共和国广告法（2018修正） > 第二章 广告内容准则 第八条 …`（page 4） | 0.7506 |
| 食品经营许可证怎么办 | `国境口岸食品卫生监督管理规定 > 第二章 食品生产经营单位的许可管理 第十条 …` | 0.7372 |
| 消费者七天无理由退货的规定 | `退款售后 常见问题解答 > 39. 平台自营商品7天无理由退货标准 …` | 0.8061 |
| 外卖配送超时怎么赔偿 | `外卖平台配送服务规范（2026版） > 第三章 超时与异常处置 第六条 …` | — |

**标题路径 + 页码 + 章条结构全部带上了** —— 这才是「切分真的实现了」的证据，
比"表里有 8993 行"有说服力得多。

### 7.5 还没做（按顺序）

1. **切轨**（`services/chat_service.py:966` 仍是 A 轨）—— 未做之前，
   聊天问答检索不到本批任何一个 chunk；
2. **检索层按 parent 去重**（踩坑 B17）—— Top-K 被同一段内容的 2~3 个副本占满；
3. **评测金标重建** —— 现有 recall 按 intent 判相关，文档语料下算不了；
4. **法规扩量** —— 15 部偏少，且只扫了一个 id 区间。

### 7.6 门禁

| 项 | 结果 |
| --- | --- |
| 全量测试 | **927 / 0F / 0E / 0S**（JUnit XML 口径，与基线持平，零回归） |
| `ruff check .` | All checks passed |
| `check_repo_data_size.py` | 通过 |
| 证据文件 | `reports/rag_ingestion_auth_review/junit_corpus_20260923.xml` |

新增踩坑 7 条，见 `docs/RAG_DEV_PITFALLS.md` 的 **A7~A11 / B17 / E6**；
进度台账见 `docs/RAG_EXECUTION_PROGRESS.md` §3 的 `[语料专项]` 条目。

---

## 8. 形态补缺样本（2026-09-24 追加）

> 第 7 节解决「有没有真实语料」，本节解决「**解析器的分支有没有被真实语料走到过**」。

### 8.1 为什么要补

`tmp/audit_corpus_coverage.py` 对 209 份主语料的实测：

```
table  2.9%（6/209）· code 0% · image 0%
无文本层/OCR 0% · 非 UTF-8 编码回退 0%
标题层级最深 h2（h3/h4 从未出现）· PDF 最长 8 页
```

当时全量测试 927 条全绿 —— 那证明的是**单测覆盖了**，不是**在真实文档上验证过**。
这两件事的区别见踩坑 **D19**。

### 8.2 补了什么（23 个文件 / 14 份文档 / 0.95 MB）

```bash
./venv/Scripts/python.exe scripts/build_corpus_samples.py --per-source 4 --sleep 0.8
```

| 来源 | 份数 | 补的缺口 | 真实性 |
| --- | --- | --- | --- |
| 国家统计局统计发布 | 4 | 表格（单篇 60+ 表格行） | 真实 |
| 快递鸟 / 高德开放平台 API | 4 | 代码块 + 参数表 | 真实 |
| MDN 中文文档 | 3 | 代码块（单篇 40 个围栏） | 真实 |
| 餐饮行业资讯 | 4 | 图片（单篇 55 张） | 真实 |
| 法规四层标题重排 | 3 | h3 / h4（章→节→条→款） | 真实内容，层级显式化 |
| 扫描件 PDF | 1 | 无文本层 / OCR | 派生（真 PDF 渲染成图） |
| GBK 编码 txt | 1 | 编码回退 | 派生（真文本转码） |

**原则：能抓真的一律抓真的；只有真实语料拿不到的形态（扫描件、GBK）才派生，
并且派生样本在 manifest 里用 `purpose` 字段明确标注，不跟真实语料混为一谈。**

抓取走 trafilatura 的 **markdown 输出**，表格 / 代码块 / 图片原样保留，不做二次渲染；
结果落 `tmp/sample_cache/`，重跑不联网。

### 8.3 补缺结果

| 指标 | 主语料 | 补缺后 |
| --- | --- | --- |
| `image` | 0% | **43.5%** |
| `code` | 0% | **21.7%**（39 个代码块） |
| `table` | 2.9% | **8.7%**（19 个表格块） |
| 标题层级 | h1 / h2 | **h1~h4**（h3 95 / h4 5） |
| `no_text_layer` 告警 | 0 | **1**（OCR 分支触发） |
| `encoding_fallback` 告警 | 0 | **1**（编码回退触发） |
| 解析失败 | — | **0**（修 B18 前是 2） |

### 8.4 顺带修掉的真 bug（B18）

抓来的 MDN 文档里有**光秃秃的 `>`** 行 → markdown 解析器产出空文本 quote block
→ 契约校验判定「block 没有文本」→ **整份 1.1 万字的文档被 `parse_failed` 拒绝**。

- 根因：同一个 `_scan` 里 paragraph 分支有 `if text:` 判空，**quote 分支漏了**；
- 修法：**源头丢弃**空引用块（不在校验层放水，放水会让契约名存实亡）；
- 已加 `test_markdown_empty_blockquote_is_dropped_not_fatal` 锁定。

**这是补样本的直接回报 —— fixtures 永远不会造一个孤立的 `>` 出来。**

### 8.5 未做（明确的决策点）

1. **样本未灌库** —— 有意的。统计局 / MDN 跟客服不相关，灌进 `takeout-policy`
   会污染检索。建议业务相关的灌、纯形态样本另建知识库或不灌；
2. `html` 解析器可能有同源的空 block 问题（B18 只修了 markdown）；
3. PDF 跨页压力仍在（最长 8 页，`page_break` 未在 20+ 页文档上验证）。
