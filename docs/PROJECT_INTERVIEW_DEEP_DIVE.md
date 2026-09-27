# 外卖客服 RAG 项目面试深挖手册

> 用途：把代码、评测报告和演进记录整理成面试可复述的项目材料。
> 
> 数据口径：文中指标均来自当前仓库的本地固定索引和评测报告，不等同于线上 SLA。未实测能力会明确标注。

## 1. 一分钟介绍

这是一个面向外卖售后场景的企业级 RAG 客服后端。核心目标不是“调用大模型生成一句话”，而是把知识入库、权限过滤、混合检索、证据组织、模型生成、安全兜底、会话记忆、人工转接、评测和运营发布串成一条可追踪链路。

可以这样回答：

> 用户请求进入 FastAPI 后，系统先从 JWT 得到租户和权限，再做意图/风险分析、查询解析和必要的订单工具查询。正式聊天链路使用带 tenant、发布状态和 ACL 过滤的 chunk-index，默认是 FAISS 稠密检索加 SQLite FTS5 稀疏检索的加权 RRF。召回结果经过去重和证据分级，Top1 是 primary evidence，其余是 supporting evidence，再构造 prompt 调用本地 Qwen 或在线模型。生成后经过 answer composer、reply rules 和 safety guard；证据不足会澄清，高风险或需要人工处理会创建 handoff ticket。最终响应不仅有答案，还包含 request_id、引用、工具结果、decision_trace 和 full_trace，方便定位一次回答到底在哪一层出了问题。

## 2. 项目边界与技术栈

### 2.1 业务边界

- 场景：外卖订单、退款、配送异常、商家联系、优惠券、食品安全、隐私和高风险转账咨询。
- 当前是可运行的工程化演示/学习项目，不接真实订单系统、真实支付系统或真实客服工单平台。
- 样例语料由人工种子 FAQ 和清洗后的帮助中心 FAQ 组成，不代表真实线上客户分布。

### 2.2 技术栈

| 层次 | 技术 | 选择原因 |
| --- | --- | --- |
| HTTP | FastAPI + Pydantic | 类型约束、OpenAPI、异步接口和测试客户端成熟 |
| 元数据 | SQLite，Docker 可切 PostgreSQL | 本地零配置；生产形态保留迁移和外部数据库路径 |
| 队列 | Redis Stream，未配置时内存降级 | 文档入库是异步长任务，避免阻塞上传请求 |
| 文档解析 | PyMuPDF、python-docx、BeautifulSoup、trafilatura | 覆盖 PDF、DOCX、HTML，并保留解析警告 |
| 向量检索 | `BAAI/bge-small-zh-v1.5` + FAISS | 中文语义检索、CPU 可运行、索引持久化简单 |
| 稀疏检索 | SQLite FTS5 | 关键词和业务专有词命中稳定，部署成本低 |
| 融合 | 加权 RRF | 两路分数不在同一量纲，按 rank 融合更稳定 |
| 精排 | `BAAI/bge-reranker-base` | 用 cross-encoder 对 query-document 关系二次判断 |
| 生成 | 本地 Qwen2.5-1.5B-Instruct，可加载 LoRA；也支持在线 provider | 本地可复现、成本可控，在线模型可做 A/B |
| 评测 | 固定 gold、口语化集、盲测集、LLM-as-judge | 分别衡量检索、回答 groundedness 和泛化 |

## 3. 总体架构

```text
客户端
  -> FastAPI Router
  -> JWT/AuthContext
  -> Chat Service
       -> 会话/长期记忆
       -> 意图与风险分析
       -> 查询解析/改写
       -> 订单、退款、handoff 工具
       -> chunk-index 检索
            -> tenant/status/ACL 过滤
            -> dense FAISS
            -> sparse FTS5
            -> weighted RRF
            -> 内容去重
       -> evidence gate
       -> primary/supporting context
       -> prompt
       -> LLM generation
       -> answer composer
       -> reply rules
       -> safety guard
       -> grounding diagnostics / metrics / persistence
  -> Response(answer + citations + trace)
```

知识侧是另一条异步链路：

```text
上传文档 -> object store -> Redis Stream -> worker
-> parse -> normalize -> chunk -> persist -> build index
-> active manifest -> publish
```

发布使用版本化 manifest，检索只读取当前 active 版本。发布失败可以用备份和回滚恢复，避免半成品索引直接在线。

## 4. 一次聊天请求怎么走

面试时按下面顺序讲，重点是每一步的输入、输出和失败策略。

1. **认证和租户上下文**：身份只来自 `Authorization: Bearer <JWT>`。请求体里的 `tenant_id` 和 `operator_id` 不能提权，缺少合法身份时正式检索 fail closed。
2. **创建/恢复会话**：读取最近消息、摘要、facts、订单号和可选 Redis 缓存；长期记忆优先级低于订单状态和知识证据。
3. **意图与风险分析**：`services/intent_service.py` 用规则、置信度和上下文指代继承识别主意图、次意图及风险等级。置信度低于阈值走 clarify，高风险进入安全增强路径。
4. **查询解析**：处理多意图、口语化问题、未解决槽位和子查询；保留原始 query，改写只服务于检索，不覆盖用户原话。
5. **工具调用**：需要订单/退款状态时调用 order tool；工具失败要在 trace 中记录，并进入可解释降级，不伪造查询结果。
6. **构造访问过滤器**：服务端根据 tenant、发布状态、文档 ACL 生成 `ChunkAccessFilter`，过滤发生在召回前。
7. **混合检索**：默认 dense + sparse；每一路先取候选，再按 weighted RRF 融合。稀疏索引不可用时显式报错，不把 hybrid 静默说成 dense。
8. **去重和证据选择**：按内容/来源做多样性去重，避免 TopK 全来自同一文档相邻 chunk。
9. **证据门禁**：判断证据是否足够、是否覆盖所有子问题以及风险是否允许直接回答。结果可能是 `complete`、`partial`、`clarify`、`human_review`。
10. **Prompt 构造**：Top1 标成 primary，其他是 supporting；supporting 只能补充流程和入口，不能覆盖主证据业务结论。
11. **模型生成**：调用本地 Qwen 或在线 provider。加载失败、生成异常时返回固定安全兜底回复，并记录 `failure_stage=generation`。
12. **答案整理**：`answer_composer` 负责普通回答的直接性、步骤和去重；`reply_rules` 负责验证码、私下转账、隐私、食品安全和赔付承诺等高风险硬边界。
13. **安全校验**：检查禁用词、承诺、敏感信息和高风险表述；失败时保留安全版本或固定回复。
14. **持久化和返回**：保存消息、事实、摘要、反馈和完整 trace，响应返回 `answer_basis`、citations、tool_results、memory_snapshot、decision_trace、full_trace。

## 5. 为什么选择这些方案

### 5.1 为什么不是纯向量检索

语义相似不等于业务意图正确。“取消订单后钱多久退回来”和“支付超时取消”可能有很高语义相似度，但答案方向不同。纯向量容易被“取消、扣钱、退款”等表面词带偏。

因此保留 dense 的泛化能力，再加 sparse 的精确词命中，并用业务规则补充方向信息。代价是需要维护 FTS 索引和少量领域规则，换领域时规则不能原样复用。

### 5.2 为什么用 RRF，不直接相加两路分数

FAISS 内积通常在可比较的相似度范围内；FTS5 `bm25()` 是无上界负分，量纲和分布受语料影响。直接相加需要归一化，而 min-max 对异常值敏感，换语料还要重调。

RRF 只使用排名：

```text
score(d) = dense_weight / (k + dense_rank)
         + sparse_weight / (k + sparse_rank)
```

当前默认 `dense=10, sparse=1, k=60`。权重扫描显示等权在口语集上会把稀疏噪声放大，R@1 从 0.4000 降到 0.2667；10:1 是两套数据上都不掉点的配置。

### 5.3 为什么保留 reranker，但权重很小

cross-encoder 能直接判断 query 和候选文本的关系，但输出是未归一化 logit，不能和 0-1 范围的检索分直接等权相加。当前使用：

```text
rerank_score = retrieval_score + model_score * 0.01 + rule_bonus
```

0.01/0.03/0.05 的扫描中，0.03 和 0.05 会把退款/取消 case 带偏，0.01 最稳。要主动承认：在小规模评测集上 reranker 改变 Top1 的次数有限，它更多是候选排序辅助，不应包装成决定性模块。

### 5.4 为什么使用 chunk-index，而不是继续使用 seed FAQ

seed FAQ 适合演示，但没有正式的 tenant、发布状态和 document ACL 过滤，不能作为企业多租户聊天默认路径。chunk-index 能保留文档、版本、页码、标题路径、ACL 和来源 URI，适合知识运营和审计。

seed 路径仍保留在 `/retrieval/search-demo` 和显式兼容评测中，但正式聊天固定使用 chunk-index。

### 5.5 为什么安全规则不能只写在 Prompt

Prompt 是软约束，模型可能误解、遗漏或生成承诺。验证码、私下转账、真实手机号、食品安全和“保证全额退款”等错误的代价不对称，所以命中硬规则后直接返回安全话术，不依赖检索或模型自觉。

代价是规则覆盖不到所有改写、错别字和新型表达，因此它是高风险兜底网，不是完整分类器。

### 5.6 为什么要 primary/supporting 证据分级

TopK 证据全部平铺时，模型可能把不同业务方向拼成一个结论。Top1 作为主证据，后续材料只允许补充流程、入口和凭证；Top1/Top2 分差小于 0.08 时标记 `close_match`，提示模型避免串意图。

这是一种“减少模型自由度”的工程约束，不是提高召回率的手段。

### 5.7 为什么需要 answer composer

检索负责找到对的证据，模型负责生成自然语言，但产品还要求首句直接、步骤稳定、无重复。composer 将答案组织成“结论 -> 操作 -> 限制/补充”，对普通低质量输出条件介入；安全规则仍无条件执行。

曾经无条件覆盖模型输出，导致 15 条样本中 14 条最终回复不同于模型原文。后续改为 `on/off/auto` 三模式并做 A/B：纯模型 judge pass 0.6444，auto 0.7556，全规则 0.8333。当前默认 auto，保留约 60% 合格模型原文，说明仍有进一步收紧低质量判定的空间。

## 6. 数据、入库和索引

### 6.1 文档入库阶段

```text
received -> parsed -> normalized -> chunked -> persisted -> indexed -> published
```

- `parsed`：记录 parser 名称、版本、页数、block 数和警告。
- `normalized`：合并知识库级切分配置、ACL 和文档上下文。
- `chunked`：按 token/标题结构切片，保存统计信息。
- `persisted`：先删后写，保证重试结果幂等。
- `indexed`：构建 FAISS 和 FTS5，写入版本目录。
- `published`：只有发布版本才允许进入检索。

失败从具体 stage 恢复；重复文件依据 content hash 复用已有版本。空文档是可解释结果，会跳过索引而不是制造一个“空索引假象”。

### 6.2 为什么需要 manifest

manifest 是索引的事实来源，记录 index version、chunk 数、embedding 模型、向量维度、稀疏索引文件和内容签名。加载前校验索引条数、维度和文档签名，避免 FAISS、数据库和 FTS5 不一致时返回错误证据。

### 6.3 为什么 ACL 要在索引查询前过滤

先查全库再在 Python 过滤，会让无权限内容进入进程内存，并且全库排序可能把授权内容挤出 TopK。稀疏路使用临时授权 row 表 JOIN；在 9229 个 row_id 的本地测试中，临时表 JOIN 约 0.0037 秒，而大 IN 参数约 1.60 秒。

## 7. 指标怎么测

### 7.1 检索指标

- `Recall@K`：正确 chunk/span 是否出现在前 K 个结果中。适合衡量“有没有找回来”。
- `MRR`：正确结果首次出现位置的倒数平均。适合衡量“正确结果是否靠前”。
- `NDCG@K`：考虑相关性等级和位置的排序质量。
- `top1_intent_hit_rate`：Top1 的业务意图是否等于 gold intent。客服场景比单纯 cosine 分数更接近业务正确性。

当前正式 chunk/span gold：标题式 81 条，Recall@5 0.9630、MRR 0.7922、NDCG@10 0.8404；口语化 30 条，Recall@5 0.5000、MRR 0.4274、NDCG@10 0.4588。口语集是当前主要质量短板。

### 7.2 回答指标

- `direct_answer`：是否直接回答问题。
- `grounded`：回答是否能被检索证据支持。
- `useful`：是否给出用户可执行的下一步。
- `forbidden_hit_count`：是否出现高风险承诺/违规词。
- `evidence_keyword_coverage`：必要证据关键词覆盖程度。
- `clarify_count`、`human_handoff_count`：降级路由是否符合预期。

LLM-as-judge 输出 JSON，但只作为初筛工具；系统保存原始 judge 响应、解析错误和 reason。judge 与生成使用同一个小模型，存在系统性偏差，不能把 judge 分数当绝对真值。

### 7.3 性能指标

`full_trace` 为每一步记录耗时，区分 manifest、dense、sparse、hybrid、rerank、generation 和总耗时。当前固定索引 hybrid 平均约 26.29 ms；端到端 CPU P50 约 4.2 秒，主要瓶颈是本地 1.5B 模型生成，不是 FAISS。没有做并发压测，因此不能声称 QPS 或线上 SLA。

### 7.4 为什么必须同时报固定集和盲测集

固定集用于回归，盲测集用于泛化。固定集反复调参后容易过拟合；当前口语/盲测表现明显低于标题式固定集，说明“固定集通过”不代表真实用户表达也稳定。面试中应主动报告差距，而不是只报最好看的数字。

## 8. 版本演进与“以前怎么做、为什么改”

1. **早期：seed FAQ + 简单向量检索**。目标是先跑通 RAG，缺点是没有 ACL、版本和发布治理。
2. **加入 keyword bonus、direction penalty 和 reranker**。原因是退款、取消、超时等相近意图容易混淆；通过小评测集做消融，而不是直接换更大模型。
3. **增加 prompt preview、retrieved_items、final_prompt 和 trace**。原因是只看最终答案无法判断错误发生在召回、prompt 还是生成。
4. **从固定 12/30 条扩展到正式 chunk/span gold、口语集和盲测集**。原因是小集合很快饱和，无法区分改动收益。
5. **从无条件 composer 改成 on/off/auto**。原因是发现规则层覆盖了过多模型输出，先用 A/B 分离模型和规则的真实贡献。
6. **聊天默认从 seed/dense 切到 chunk/hybrid**。原因是正式链路需要多租户隔离和词法召回；dense 保留为诊断模式，hybrid 缺 sparse 时显式失败。
7. **加入知识草稿、审核、发布、回滚**。原因是知识库是持续变化的产品数据，不能靠直接修改 JSON 文件上线。
8. **加入异步 worker 和 Redis Stream**。原因是文档解析、切片和建索引耗时，不应阻塞 HTTP 上传；部署时必须同时启动 worker。

## 9. 最困难的问题和排查方法

### 最困难：定位“答案错”到底是哪一层

RAG 的坏回答通常不是单点错误，可能是检索错、证据不全、prompt 组织错、模型没用证据、规则误伤或 judge 误判。解决方式不是直接改 prompt，而是保存中间状态并建立归因表：

```text
Top1 intent 错 -> retrieval / rerank / knowledge
Top1 正确但资料缺 -> corpus / ingestion
资料足够但回答没用 -> prompt / composer / generation
出现高风险表述 -> reply_rules / safety_guard
回答合理但 judge 否 -> judge calibration / human review
```

当前固定集归因示例：pass 75、retrieval_failure 8、generation_not_using_evidence 7，另有部分 judge 偏严。这个归因表直接决定下一轮改哪一层，避免“分数低就调 Prompt”。

### 另一个困难：权限过滤和两路检索的一致性

混合检索新增 sparse 路后，最容易漏掉 ACL。项目让 dense、sparse 和 hybrid 共用 `ChunkAccessFilter`、`ChunkHit` 和同一份 manifest；任何缺 filter 的入口直接拒绝。这样安全边界不会因新增检索方式而漂移。

### 典型部署坑：API 投递了任务，但 worker 没有运行

只启动 API 时，上传任务会停在 `pending/received`，因为进程内内存队列无法跨进程传递。现在启动检查会明确告警，Docker Compose 同时编排 api、worker、PostgreSQL、Redis。

## 10. 高频面试问题与回答

### Q1：为什么不用纯向量？

答：语义相似不等于业务意图正确，退款、取消和支付超时存在方向混淆。我们保留 dense 的泛化能力，同时加入 FTS5 词法召回和 weighted RRF。代价是多一条索引和领域规则，换领域需要重新验证权重与规则。

### Q2：RRF 的权重怎么定？

答：不是拍脑袋。扫描 dense:sparse 的 1:1、10:1、20:1，在标题式和口语式两套集上比较。等权会让口语集掉点，20:1 又接近纯 dense，10:1 是当前两套数据上都不劣的折中。改权重必须重跑两套评测。

### Q3：为什么 sparse 不可用时不自动退回 dense？

答：因为请求声明的是 hybrid，静默退回会让 `retrieval_origin` 和实际行为不一致，调用方无法发现质量变化。故障隔离可以显式选 dense；正式 hybrid 缺 sparse 时返回明确错误。

### Q4：模型生成失败怎么办？

答：生成层捕获异常，返回固定安全兜底回复，同时记录 `failure_stage=generation`、`fallback_reason` 和 trace。不能把错误吞掉后继续返回看似正常的答案。

### Q5：为什么需要规则，不能全交给大模型？

答：高风险错误代价不对称，Prompt 约束不具备硬保证。验证码、私下付款、真实手机号和赔付承诺命中规则后直接阻断。规则不是通用写作层，普通表达交给 composer 和模型。

### Q6：LLM-as-judge 可靠吗？

答：它是自动初筛，不是标准答案。系统处理空响应、非 JSON、缺字段、非法枚举和空 reason，并把疑似 judge 误判单独归因。当前 judge 与生成同模型，外部模型一致性尚未验证。

### Q7：固定集 0.9 以上说明线上可用吗？

答：不能。固定集用于回归，盲测/口语集才反映泛化。当前标题式 Recall@5 0.9630，但口语式只有 0.5000，说明查询改写和真实表达仍是短板。

### Q8：系统延迟主要在哪里？

答：检索在固定索引上是毫秒级，端到端 CPU 延迟主要来自本地 Qwen 生成。full_trace 会把每步耗时拆出来，优化前先确认瓶颈。可选方向是 GPU、量化、限制输出长度、缓存高频问题和异步化，但并发 QPS 尚未实测。

### Q9：如何保证多租户隔离？

答：JWT 解析身份；服务端构造 tenant/status/ACL 过滤器；dense 和 sparse 都在召回前应用过滤；无身份或缺 filter 直接零命中/拒绝，不从无权限的 seed 路径兜底。测试覆盖 tenant isolation、ACL 和 retrieval isolation。

### Q10：知识库更新如何生效和回滚？

答：知识先 draft，再 review、approved、publish。发布前备份，发布时写版本、重建索引、切换 active manifest、清理缓存并记录历史；失败恢复旧文件和索引。回滚恢复最近一次成功发布。当前功能测试覆盖完整闭环，但“改一条知识后指标提升多少”还没有量化实验。

### Q11：大模型在这个系统里到底做什么？

答：模型负责把证据和上下文转成自然语言；检索负责找到证据，规则负责高风险边界，composer 负责稳定渲染，安全层负责最终校验。A/B 结果证明在当前 1.5B 模型上，规则和渲染层对 judge pass 有显著贡献，所以不能把整个质量归因给模型大小。

### Q12：如果重新做，你会先改什么？

答：先扩充独立人工标注的口语/多轮评测集，统一 span gold 和意图口径；再优化 query rewrite 和口语召回；随后做更强 judge 的一致性校准和并发压测。不会继续只调固定集分数。

## 11. 必须诚实说明的不足

- 没有真实线上流量、真实订单和真实 SLA。
- 语料规模有限，主要是合成/清洗 FAQ。
- 口语化查询 Recall@5 只有 0.5000，是主要短板。
- judge 与生成使用同一个本地模型，存在偏差。
- 没有完成真实并发压测、QPS、GPU 与在线 API 的稳定性验证。
- LoRA adapter 的独立增益尚未形成严格 A/B 报告。
- 知识发布/回滚有功能测试，但没有做“知识变更带来的检索收益”实验。
- 规则和 intent hint 有领域硬编码，换业务域需要重写和重新评估。

## 12. 面试前建议实际跑的命令

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe scripts\run_formal_rag_evaluation.py --tenant-id tenant-dev --top-k 10
.\venv\Scripts\python.exe scripts\audit_manifest_load_cost.py --label perf_interview --repeat 15 --query-limit 30
.\venv\Scripts\python.exe scripts\check_repo_data_size.py
```

准备回答指标时，至少记住四组数字：

1. 正式索引：v4、9229 chunks、`bge-small-zh-v1.5`、稀疏索引可用。
2. 标题式 gold：81 条，Recall@5 0.9630，MRR 0.7922。
3. 口语式 gold：30 条，Recall@5 0.5000，MRR 0.4274。
4. hybrid 平均检索延迟约 26.29 ms；端到端 CPU P50 约 4.2 秒，未做 QPS 压测。

## 13. 代码导航表

| 面试主题 | 代码/文档 |
| --- | --- |
| 应用入口和路由 | `main.py`、`routers/` |
| 聊天编排和降级 | `services/chat_service.py` |
| 意图与风险 | `services/intent_service.py`、`services/safety_guard.py` |
| 查询解析 | `services/query_resolution.py`、`services/query_rewrite_provider.py` |
| 混合检索 | `utils/hybrid_retriever.py`、`utils/sparse_retriever.py`、`utils/vector_retriever.py` |
| 证据上下文 | `utils/rag_context.py` |
| Composer 和安全规则 | `services/answer_composer.py`、`services/reply_rules.py` |
| 入库和索引 | `services/ingestion/` |
| 权限过滤 | `services/retrieval_access.py` |
| 知识运营 | `services/knowledge_service.py`、`routers/knowledge.py` |
| 会话记忆 | `services/conversation_service.py`、`services/conversation_store.py` |
| 评测 | `scripts/run_formal_rag_evaluation.py`、`scripts/evaluate_hybrid_retrieval.py`、`scripts/analyze_grounding_report.py` |
| 研发演进记录 | `docs/AI_DEVELOPMENT_LOG.md`、`HANDOFF.md`、`RESUME_NOTES.md` |

## 14. 最后记忆框架

回答任何项目细节时，按这五句话组织：

```text
问题是什么？
我选择了什么方案？
为什么不用另一个方案？
我怎么测它是否有效？
当前还有什么边界和下一步？
```

这个项目最重要的工程结论是：RAG 不是“检索加 Prompt”两个模块，而是一条需要权限、证据、降级、安全、评测和运营共同约束的服务链路。
