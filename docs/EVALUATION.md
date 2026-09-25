# RAG 评测报告

> 当前正式口径（2026-09-25）：以 chunk/span gold、固定 tenant 和 active index 为准。早期 seed FAQ 结果仅保留为历史参考，不用于当前质量门禁。

## 2026-09-25 P1 链路口径

正式检索评测入口是 `scripts/evaluate_hybrid_retrieval.py`。它直接调用 chunk-index 生产检索代码，使用 span gold 计算 Recall@k、MRR、NDCG，并在报告中记录 `retrieval_path=chunk-index`、数据集和 `index_version`。

`scripts/evaluate_chat_grounding.py` 的现有用例仍以 seed FAQ 的 `expected_intent` 为金标，因此默认拒绝运行；只有显式传 `--legacy-seed` 才运行兼容 grounding。该报告必须标记 `retrieval_path=seed-faq-demo`，不得当作正式 chunk 质量。正式聊天 grounding 若要纳入 chunk 评测，需要提供带 tenant/ACL 身份上下文的固定评测索引和 chunk/span 金标。

报告还应关注证据覆盖、citation/grounding 检查，以及 `clarify` 和 `human_handoff` 路由统计；聊天 grounding 汇总会输出 `route_counts`、`clarify_count`、`human_handoff_count`。


## 当前正式评测命令

```powershell
python scripts/run_formal_rag_evaluation.py --tenant-id tenant-dev --top-k 10
```

该命令固定 tenant 身份，使用 chunk/span gold，运行 dense、sparse、hybrid 检索，并生成正式聊天 grounding、citation 和 clarify/human_handoff 路由统计。报告中的 `retrieval_path` 固定为 `chunk-index`。

性能基线（2026-09-25，本地两条 query）：hybrid 平均 30.3 ms，dense 平均 431.1 ms，hybrid 相对 dense 约降低 93.0%；manifest 冷读 95.25 ms，稳态平均 47.84 ms。样本量较小，只用于回归比较。

## 当前实测指标

| 数据集 | 样本数 | Recall@1 | Recall@5 | Recall@10 | MRR | NDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 标题式 chunk/span gold | 81 | 0.7407 | 0.9630 | 1.0000 | 0.7922 | 0.8404 |
| 口语化 chunk/span gold | 30 | 0.4000 | 0.5000 | 0.5667 | 0.4274 | 0.4588 |

索引版本为 `v4`，共 `9,229` 个 chunk，embedding 模型为 `BAAI/bge-small-zh-v1.5`，稀疏索引可用。性能对比显示，manifest、dense、sparse、hybrid 平均耗时分别下降约 `94.5%`、`76.4%`、`93.2%`、`91.6%`。性能数字来自本地固定索引，不能直接当作线上 SLA。
