# RAG 开发记录

> 用途：记录 AI 辅助开发中的架构决策、踩坑和验证证据，便于复盘与面试说明。

## 2026-09-25：正式检索链路默认切换为 hybrid

### 背景

项目同时存在正式 chunk 检索和 seed FAQ 演示检索。正式 API 与聊天链路原本默认 dense，hybrid 只通过显式参数启用，容易造成“代码支持 hybrid，但线上没有使用”的误判。

### 决策

- `routers/retrieval.py::DEFAULT_RETRIEVAL_MODE` 改为 `hybrid`。
- `services/chat_service.py::DEFAULT_CHAT_RETRIEVAL_MODE` 改为 `hybrid`。
- README 机器可读锚点同步改为 `hybrid`。
- `dense` 保留为显式诊断和离线对照模式。
- sparse 索引缺失时保持显式 503，不静默退回 dense。
- 正式聊天固定使用 chunk；seed FAQ 仅保留在显式 demo API。

### 验证

- `venv\Scripts\python.exe -m pytest tests/test_hybrid_retrieval.py tests/test_ingestion_pipeline.py -q`
- 结果：113 passed，3 warnings。
- 当前正式索引 `v4` 已存在 `sparse.sqlite`、dense FAISS 索引和 manifest。

### 后续问题

1. 口语化查询仍需 query preprocessing / rewrite，当前 B 轨口语化集 R@1 = 0.4000。
2. 评测口径需要统一到正式 chunk 的 span 金标。
3. 已新增统一 query preprocessing seam 和 evidence gate trace。
4. 解析质量门禁、声明级证据核验和失败重试仍需补齐。

## 记录模板

- 问题：
- 影响：
- 根因：
- 决策：
- 改动：
- 验证：
- 遗留：
