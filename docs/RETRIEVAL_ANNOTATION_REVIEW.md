# 检索与意图人工复核入口

本轮队列：`data/retrieval_annotation_tasks.jsonl`，共 140 条：90 条原主意图案例、30 条原口语检索案例、20 条新合成候选。全部是 **development_not_blind / pending**，不是已完成人工标注。队列不包含模型预测，避免直接照抄预测作为标签；原参考标签/span 保留，来源文件及 SHA-256 可追溯。

## 先处理什么

1. 优先级 1：5 条跨轮指代候选（s53_011–s53_015），先判断上下文是否足以唯一确定问题。
2. 优先级 2：46 条期望意图不在运行时词表中的案例，涉及 19 个不同名称。先决定是否需要扩展分类体系，不把它们直接归为通用客服咨询。
3. 其余：复核已有词表内的分类错误，以及口语问题的来源证据/实体缺失、多意图全部证据。

## 标签口径

当前程序支持的标签以 `services.intent_service.INTENT_RULES` 和 `FALLBACK_INTENT_NAME` 为唯一运行时来源。冻结快照和逐条缺口见 `reports/retrieval_quality/20260927_intent_labels.json`，不要另造一份生产枚举。

原 90 条参考标签与运行时词表不是同一个覆盖范围：全量一致率 34/90；44 条可表示标签中正确 34 条，另外 46 条是词表缺项。新评测显式区分 `classification_mismatch` 与 `taxonomy_gap`，保留 90 条完整分母与原标签。**这只是口径收敛，不是分类能力已提升。**

业务主题和风险信号要区分：正常银行卡支付咨询不应因为出现“银行卡”就被人工标成诈骗；涉及索要验证码等风险时，需要同时记录业务问题及风险证据。当前主意图字段兼有业务与风险路由含义，若业务要求拆成两轴，应另定兼容方案，不能在本轮擅自修改 API 或把不同语义的旧标签合并。

## 每条怎样填写

- 保留 task_id、source_*、query/context 以及所有 reference_* 字段。
- decision 选择 `accept_reference`、`revise_reference`、`ambiguous_query`、`taxonomy_change_required` 或 `need_evidence`，并在 notes 写理由。不要根据现有得分选择更容易命中的答案。
- human_primary_intent / human_intents 填复核后的结果；无法确定就留空并说明原因，不能用 fallback 隐藏词表缺项。
- human_evidence 记录来源文档/版本、标题位置、原文 span；多意图逐个子问题填写。语义等价但原 span 不同的答案要明确说明，不能静默替换旧 gold。
- 完成后填写 reviewer、reviewed_at；所有必要信息齐全后才将 status 改为 reviewed。生成脚本拒绝覆盖已有任务文件，以保护人工修改。

已看过这些任务/结果的人员不能再把它们称为独立盲测。独立盲测应另收真实问法、保留原对话，由未参与本次调参者标注并冻结来源划分和哈希。

## 历史 FAQ 还需要的处理

售后流程、评价反馈的 HTML/Markdown 原文存在大量重复问法/答复，完整文件按现有 parse_quality 进入 requires_review。本轮只修复清洗器误删答案，没有放宽门禁，也没有发布这些完整文件。

内容负责人需确认哪些重复问法应保留、哪些是无价值的合成扩增，再产生有来源记录的新修订并正常上传。不能仅拆小文件来规避门禁后批量发布；本轮三问答子集仅为明确标识的诊断夹具，不是历史语料迁移方案。

## 复现任务生成

调用现有 `scripts.build_colloquial_cases.build_annotation_tasks(candidate_path, intent_path, retrieval_path, output_path)`，输入分别为原 20 条候选、90 条意图集、30 条口语集。输出必须是新路径；不要覆盖已复核文件。
