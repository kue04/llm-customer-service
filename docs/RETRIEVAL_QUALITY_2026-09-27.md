# 5.3 检索质量：真实基线、最小修复与标注缺口

日期：2026-09-27。状态：**已完成本轮可执行评测与修复；未达到真实问法质量验收条件**。不将离线固定集结果表述为线上 SLA。未推进 5.4，未提交、推送或部署。

## 1. 数据与实际执行路径

- 历史正式索引：`data/faiss_store/chunk_index/document_chunks/v4/`，9,229 个 chunk。原始正式文档库含 211 个文档记录、209 个版本（并非每条文档都有可检索版本）。
- `scripts/prepare_retrieval_quality_snapshot.py` 只读历史 SQLite 和 FAISS，复制到现有 PostgreSQL 的专用租户 `640365ad22e54edf8ba794ffa4ea2089`，输出 `tmp/retrieval-quality-20260927/`。原数据库、manifest、向量文件 SHA-256 在完成后均未变化。没有修改或重建共享数据库。
- 复制保留文本、发布状态、解析元数据与 chunk ID；重映射文档/版本主键，复用原模型的真实缓存向量。通过现有 `rebuild_index(all_tenants=False)` 在专用目录构建索引。源语料 ACL 为空；脚本遇到非空 ACL 会拒绝复制，不会删除权限。
- **这是历史正式语料的隔离迁移评测，不是重新上传全部文档的入库验收。** 历史无 parse_quality 的版本依现有兼容约定继续可用；没有补造“门禁通过”结果。5.2 的隔离、状态、授权代码未改。
- 查询向量实际执行本机 CPU `BAAI/bge-small-zh-v1.5`，`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，没有下载。抽查原始向量第 0、4,614、9,228 行，与当前模型重算向量余弦均为 1.0。
- 原始召回调用 `search_hybrid_chunks`；使用 PostgreSQL `build_chunk_access_filter` 产生的租户、发布状态和 ACL 可见集合，9,229 个 chunk 可见。选择证据调用 `retrieve_chunk_items_for_chat`；生产查询计划调用 `retrieve_with_query_plan`。没有使用 demo/legacy 意图命中冒充 chunk 检索。
- 查询改写是现有确定性 `resolve_query`，意图分类是 `analyze_intents`。正式这条检索路径使用 RRF 与内容去重，**此次没有执行 BGE reranker 或 Qwen 答案生成**，也没有复跑无关的 API/worker 全链路。报告中的检索质量不能证明回答后处理正确。

### 数据集来源与划分

| 集合 | 数量 | 来源与标注状态 | 本次用途 |
| --- | ---: | --- | --- |
| `retrieval_gold_cases.jsonl` | 81 | 正式语料章节标题及答案 span；弱监督 | 固定开发/回归集，未改 gold |
| `retrieval_colloquial_cases.jsonl` | 30 | 现有脚本中的固定改写，文件标为 handwritten；无独立标注者审计证据 | 固定口语代理集，不能称真实用户日志或独立人工集 |
| `retrieval_quality_candidates.jsonl` | 20 | 本轮 Agent 合成，继承上述源 span；全部 `candidate_pending_human_review` | 开发候选；口语、错字、指代、多意图各 5 条 |
| `chat_grounding_cases.jsonl` | 90 | 既有主意图标签；来源为早期固定评测集 | 单独评估分类，不作为 chunk gold |
| 独立检索盲测 | 0 | 尚无满足独立性、上下文、来源证据要求的标注 | N/A，门槛阻塞，不能制造通过 |

既有 `chat_grounding_blind_cases.jsonl` 保留，未读取其案例内容、未参与调参；仅记录文件哈希 `9c550c6395e8526092d8b38a18a92bd86008f10317f0cd58e52e6c1d07245357`。它是答案 grounding 盲测文件，不能直接冒充已经具备独立 chunk 标签的检索盲测。

候选每条记录 source_case_ids、源文件 SHA-256 和原 span。指代样本保留 messages/facts；多意图记录各子问题与各自证据，不用“任一问题答对”替代全覆盖。开发集与候选共享源证据，**没有独立性**。

## 2. 指标定义与当前基线

历史称为 Recall@5 的指标实际是 query-level **Hit@5**：每个问题前五条至少一个 chunk 含 gold span。继续保留全部样本分母，缺 gold 不静默删题。

- Query-level：Hit@k、All-evidence@k（每个子问题的 span 都覆盖）、Evidence coverage@k。单 span 数据上前三者相等；多意图必须单独看 All-evidence。
- Chunk-level：先扫描同一权限范围完整语料，为含 gold span 的 chunk 建立相关集合；分别报告 precision、recall，去掉重复 chunk ID。它们是 **span-derived silver relevance**，不是完整人工语义相关性判断。同义改写可能被算不相关；父子/格式孪生 chunk 会增加 recall 分母，去重后 chunk recall 降低不自动代表答案质量下降。
- Intent-level：已有主标签集报告主意图一致率；多标签只有候选中明确提供的 3 条退款样本可算，不能用此 3/3 宣称整体分类达标。标题/口语缺意图标签，报告 N/A。
- 诊断 @5 使用每路 top_k/limit=10；同时单独执行生产默认 limit=3 并报告 @3，避免用放大预算的数字代表当前聊天效果。RRF 始终为 dense:sparse=10:1，route_k=50，constant=60，未调参。

### Query-level 结果

| 集合/路径 | 修复前 | 最终 |
| --- | ---: | ---: |
| 标题：原始 hybrid Hit@5 | 78/81 = 0.9630 | 78/81 = 0.9630 |
| 口语：原始 hybrid Hit@5 | 15/30 = 0.5000 | 15/30 = 0.5000 |
| 标题：生产查询计划 Hit@5 | 80/81 = 0.9877 | 81/81 = 1.0000 |
| 口语：生产查询计划 Hit@5 | 18/30 = 0.6000 | 19/30 = 0.6333 |
| 标题：生产默认计划 Hit@3 | 77/81 = 0.9506 | 79/81 = 0.9753 |
| 口语：生产默认计划 Hit@3 | 16/30 = 0.5333 | 16/30 = 0.5333 |
| 合成候选：计划 All-evidence@5 | 13/20 = 0.6500 | 14/20 = 0.7000 |
| 合成候选：默认计划 All-evidence@3 | 10/20 = 0.5000 | 12/20 = 0.6000 |
| 独立检索盲测 | N/A | N/A |

原始 hybrid 的标题/口语结果复现历史数字；原数据和 span 判定未变，模型、融合配置相同，因此**在该固定语料、固定指标口径内可比**。隔离迁移重映射版本 ID 导致索引行顺序不同，同分排名仍可能变化，不能说与旧报告逐位相同。生产计划/内容去重路径和原始召回路径不能交叉比较为“提升了原始 Recall”。

### Chunk-level 最终结果

| 集合 | 原始 hybrid P@5 / R@5 | 生产计划 P@5 / R@5 |
| --- | --- | --- |
| 标题 81 | 0.6346 / 0.7617 | 0.2519 / 0.2642 |
| 口语 30 | 0.3267 / 0.4208 | 0.1400 / 0.1708 |
| 合成候选 20 | 0.3000 / 0.2562 | 0.2300 / 0.1792 |
| 盲测 | N/A | N/A |

这些 chunk 指标与 query 命中不是同一分母。原始检索常返回多个相同答案的父/子/HTML/Markdown 副本；选择证据后留一个答案覆盖即可。未对未标注的语义等价 chunk 建立强行负标签。

### Query rewrite 与 intent classification 分开看

不提供意图提示、只运行 rewrite.resolved_query 的检索，与 selected_raw 相比：标题/口语/候选 Hit@5 均无增益或损失。此结论只覆盖当前固定集，不证明改写在所有场景无效。

完整查询计划包含分类提示、拆分、查询合并，因此收益不能全部归给改写。独立意图主标签一致率前后均为 **34/90 = 37.78%**。其中 46 条期望标签不在当前分类器标签集合内；保留全量分母，不重命名 gold 让其“通过”。现有标签可表示的 44 条中正确 34 条（77.27%），只作为覆盖审计的补充，不能代替 90 条全量结果。

意图分组：baseline 14/46、boundary_promise 5/9、inducement 4/9、long_context 1/8、multi_intent 5/8、oral 5/10。尚缺标签体系映射、真实问题标注和多标签全集；未为追求指标调整安全意图规则。

候选计划 All-evidence@5：口语 2/5→3/5，错字 5/5→5/5，指代 1/5→1/5，多意图 5/5→5/5。默认 @3：口语 1/5→3/5，错字 4/5→4/5，指代 1/5→1/5，多意图 4/5→4/5。候选数量小且未人工审核，不作为发布验收。

## 3. 失败证据与最小修复

**已修复的根因：查询拆分把条件从问题中剥离。** 例如 `g034`“订单拒收后，退款什么时候返还？”被拆为“订单拒收后”和“退款什么时候返还”。有 sub_queries 时生产计划不再使用原始完整问题回退，检索丢失拒收条件。`c002`“用卡付的钱，开票时抬头应该写谁”同类。

只修改 `services/query_resolution.py::_split_hop_text`：先保留完整句边界，再仅在每个片段都可独立提问时拆分句内连接词。保留条件、原始查询回退以及原有多跳依赖逻辑。没有改 embedding、权重、ACL、gold 或回答后处理。

第一版修复在新候选 `s53_018` 出现 All-evidence@5 退化：把“支付方式以及优惠券”中的对象连接词当成问题边界。该失败报告 `20260927_after.json` 保留，未删除样本；补充失败回归测试并修正完整句优先的拆分方式，最终恢复。最终所有已评集合逐样本 All-evidence 非退化门槛通过。

最终增益：计划 @5 恢复 `g034`、`c002`、`s53_005`；默认 @3 恢复 `g013`、`g034`、`s53_003`、`s53_005`。

**仍未解决的类别：**

1. 原始召回/排序：标题 3 条 gold 位于第 6–10；口语 2 条位于第 6–10、13 条 top10 未命中。不能把“top10 无结果”直接定性为整个语料不可召回；报告保留层级边界。所有测试 gold span 在可见语料中存在。
2. 问题单独成 chunk：`c003/c006/c012` 等召回头部可见只有用户问题的章节。这是历史解析/切块内容的诊断线索，不在本轮批量重切历史语料或宣称 5.2 已解决此问题。
3. 查询或标签歧义：`c018`“那些积分”、`c022`“用这个付的钱”等丢失特定产品名/上下文；`c019` 的平台/品牌替换可能导致语义相近证据不能通过严格 span。需要人工裁决，未改 gold、删难题或补造上下文。
4. 意图提示干扰：`c009` 的正常刷银行卡问法被现有安全规则判为验证码诈骗，完整计划 @5 仍低于原始完整问句选证据结果。规则与标签审计已记录；没有缩减安全规则绕过问题。
5. 指代能力：候选有必要上下文，但当前 rewrite 主要解析订单实体，未解析发票/评价/自提柜等通用主题；仅 1/5 全覆盖。没有把“上下文存在”冒充“模型使用了上下文”。

答案生成、证据是否足够支撑业务承诺、answer composer 不在本次检索层评测输出内；不能由本报告证明最终回答正确。

## 4. 质量门槛与人工输入

| 门槛 | 依据 | 结果 |
| --- | --- | --- |
| 标题原始 Hit@5 ≥78/81；口语原始 ≥15/30 | 本轮固定基线；建议回归门槛 | 通过，均持平 |
| 查询计划、默认 @3、原始模式逐样本 All-evidence 不丢失原成功案例 | 保留现有行为的建议回归门槛；比总均值更严格 | 最终通过；中间失败已保留 |
| 意图主标签一致率不低于 34/90 | 同一现有标签集的建议回归门槛 | 通过，仅代表无回归 |
| 真实问法质量目标 | 旧 M10 曾提 R@5≥0.85，但原口径有问题；只能作为待确认业务目标线索 | 未建立正式验收。当前口语 raw=0.50、plan=0.6333，均低于 0.85；不得宣称目标通过 |
| 独立盲测门槛 | 首先须有独立人工标注和冻结划分；再由业务确认目标 | 阻塞。N/A，不能用标题集或合成集代替 |
| Chunk 绝对目标 / 多标签意图目标 | 缺完整相关性标注与统一标签体系 | 暂不设任意数值；报告观测值，不制造 SLA |

最小人工输入：先审核候选 `s53_011`–`s53_015` 的 5 条指代问题是否能够唯一确定含义，并确认该用哪段原文作证据。其余候选同样待复核。完整验收另需业务提供脱敏真实问法，记录原始对话、来源文档/版本、每个子问题的答案 span、全部适用意图、标注者与复核状态。

盲测采集建议：由未参与本轮调参者维护独立文件，按来源文档/FAQ 家族分组，避免从当前 81/30/20 集改写而来；冻结文件哈希后再执行一次最终评测。可先收集四类各 10 条作试点，但这是采样建议，不是统计充分性证明。当前没有盲测正文，不创建空文件假装完成。既有 grounding 盲测也不得用于下一轮调参。

## 5. 复现与证据

现有评测入口已扩展，未另建验收框架：`run_formal_rag_evaluation.py --quality-snapshot` 调用 `evaluate_formal_rag.py` 中的分层统计；沿用 `evaluate_hybrid_retrieval.py` 的数据读取与 span 判据。`build_colloquial_cases.py::build_quality_candidates` 可生成同一候选集，拒绝覆盖人工修改。

以下命令要求本机已有模型、PostgreSQL 连接环境变量 `RAG_DATABASE_URL`。连接串不写入报告；评测不需要 Redis worker。缺模型或下载失败立即停下，不自动改镜像/依赖。

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:PYTHONIOENCODING='utf-8'
$env:OMP_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'

# 可复用本次专用快照；如重新准备，run-root 必须不存在。
venv/Scripts/python.exe scripts/prepare_retrieval_quality_snapshot.py --run-root tmp/retrieval-quality-next
$env:RAG_FAISS_STORE_DIR=(Resolve-Path tmp/retrieval-quality-next/index).Path
venv/Scripts/python.exe -m scripts.run_formal_rag_evaluation --quality-snapshot tmp/retrieval-quality-next/snapshot.json --quality-output reports/retrieval_quality/next.json --candidate-cases data/retrieval_quality_candidates.jsonl

# 重放本轮不可变 before/final 报告的比较；output-dir 必须没有 comparison.json。
venv/Scripts/python.exe -m scripts.evaluate_formal_rag --quality-before reports/retrieval_quality/20260927_before.json --quality-after reports/retrieval_quality/20260927_final.json --output-dir reports/retrieval_quality/recheck
```

基线在修改查询解析代码前执行；初次 111 条报告为 `20260927_baseline.json`，补齐默认预算/候选/独立意图后的完整修复前报告为 `20260927_before.json`。最终比较只使用 before 与 final，相同数据哈希、专用快照、可见集合、预算和融合参数；比较函数遇到数据或配置变更会拒绝。重跑前后源码应放在隔离目录，不在当前有用户改动的工作区回滚。

| 证据 | 内容 |
| --- | --- |
| `reports/retrieval_quality/20260927_summary.json` | 可版本化摘要、逐样本前后名次、三层/分组指标、门槛和意图明细（约 300 KB） |
| `reports/retrieval_quality/20260927_provenance.json` | 原数据/报告/代码哈希、模型依赖版本、向量核验、盲测保留哈希、意图标签覆盖审计 |
| `reports/retrieval_quality/20260927_checks.txt` | 失败复现、最终测试、静态检查与未运行项 |
| `reports/retrieval_quality/20260927_{baseline,before,after,final}.json` | 本机完整报告，含每条查询计划、上下文、各路径 top10；大文件留本机、未加入版本化白名单 |
| `reports/retrieval_quality/final_paired/comparison.json` | 完整配对比较，含各候选类型的前后统计 |
| `tmp/retrieval-quality-20260927/snapshot.json` | 专用租户、文档映射、源文件指纹与索引准备记录；数据和索引保留 |

检查结果：最终相关测试 **162 passed + 3 subtests passed**；仅已有 SWIG 依赖弃用警告。`ruff check .`、`git diff --check`、仓库 1 MB 文件门槛通过；新摘要另检查小于 1 MB。未把中间 56/59 项或前阶段 209/213 项相加。未重跑完整 1154 项历史基线、未复跑无关 API/worker 重启和故障注入；未执行生成模型评测、部署、Docker、5.4 压测或成本任务。
