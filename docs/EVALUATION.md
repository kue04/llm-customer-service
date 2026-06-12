# RAG 评测报告

> 记录日期：2026-06-12  
> 说明：本文只记录仓库内已有评测产物，不夸大为线上真实业务指标。

## 数据规模

| 数据 | 文件 | 数量 |
|---|---|---:|
| 客服知识库样本 | `data/takeout_customer_service_seed.jsonl` | 511 |
| SFT 消息样本 | `data/messages/takeout_sft_messages_all.jsonl` | 500 |
| Grounding 评测集 | `data/chat_grounding_cases.jsonl` | 90 |
| Blind grounding 评测集 | `data/chat_grounding_blind_cases.jsonl` | 30 |
| 检索评测问题 | `data/eval_prompts.jsonl` | 20 |

## 最新 Grounding 评测

报告文件：`reports/chat_grounding/2026-06-06_20-09-36.json`

| 指标 | 结果 |
|---|---:|
| 评测问题数 | 30 |
| Judge 成功数 | 30 |
| Direct answer = yes | 29 |
| Grounded = yes | 29 |
| Useful = yes | 29 |
| Grounded = partial | 1 |
| Manual review count | 3 |

结论：当前版本在 30 条固定评测上整体可用，但仍有少量边界问题需要人工复核。后续不应只追求 100% 分数，而应扩大评测集并保留失败样本。

## 最新 Retrieval 评测

报告文件：`reports/retrieval_eval/2026-05-11_17-00-20.json`

| 指标 | 结果 |
|---|---:|
| 评测问题数 | 12 |
| Top1 命中 | 11 |
| Top3 召回但 Top1 错误 | 1 |
| 未命中 | 0 |
| Rerank 改变 Top1 | 1 |

配置：

- Embedding：`BAAI/bge-small-zh-v1.5`
- Reranker：`BAAI/bge-reranker-base`
- 向量库：FAISS
- `model_rerank_weight`：0.01
- `min_vector_score`：0.4

结论：检索链路已能覆盖当前小规模评测集，但评测集规模仍偏小。下一步应扩展到 100-300 条人工校验问题，并单独比较 keyword、vector、hybrid、rerank 四种配置。

## 面试可讲的评测闭环

1. 每个回答保存 `retrieved_items`、`prompt_context_items`、`final_prompt`、`reply` 和 judge 结果。
2. 如果 Top1 意图错误，优先定位 retrieval / rerank / knowledge。
3. 如果 Top1 正确但回答混入辅助证据，优先定位 context builder。
4. 如果回答正确但 judge 误判，记录为 judge calibration，不强行修改业务链路。
5. 对高风险问题使用 reply rules 兜底，避免模型承诺退款、赔付或私下交易。

## 后续计划

- 将 blind grounding 评测扩展到 100 条以上。
- 将 retrieval 评测扩展到 100 条以上，并输出 recall@1、recall@3、MRR。
- 增加一次本地压测，记录平均延迟和 P95 延迟。
- 部署公网 Demo 后补充在线访问地址。
