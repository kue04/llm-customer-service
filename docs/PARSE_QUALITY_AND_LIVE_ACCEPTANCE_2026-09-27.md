# 非 Docker 真实验收与 5.2 解析质量门禁

执行日期：2026-09-27。按顺序先完成真实端到端验收，再实现解析质量门禁。

## 真实链路证据

- 第一步通过报告：`reports/live_rag_acceptance/20260927T052637Z-572b8f5d.json`。
- 门禁实现后的完整复验：`reports/live_rag_acceptance/20260927T054720Z-9ad51537.json`，包含四类质量拒绝、重处理、重复上传、索引重建和旧版本问答。
- 实际运行 PostgreSQL、Redis、本地 CPU 模型、独立 API/worker 子进程；模型使用已有本地文件和缓存，`HF_HUB_OFFLINE=1`，没有下载或假模型替代。
- 两份通过报告均验证：上传→worker→正式索引发布→问答与文档引用；API/worker 重启后问答与引用仍正确；另一租户不能读取文档、检索内容或取得引用；worker 心跳失效、PostgreSQL/Redis 连接中断均使公开 readiness 返回 503，恢复后重新就绪。
- 故障注入只关闭脚本专属 TCP 代理或该次 worker，不停止共享 PostgreSQL/Redis 实例。子进程退出时由脚本清理；数据库、模型、对象文件、索引、日志和报告保留。
- 本地生成证据为 `provider=local`、`counting_method=local_tokenizer`、正数 completion token 和成功 generation trace。最终答复可能经过现有 answer composer；不能把最终答复表述成未经后处理的模型原文。

### 已修复的验收阻断

1. 原报告 `20260927T051419Z-daa4cfbe.json` 上传 403：bootstrap 成员关系正确，但 JWT `sub` 使用了内部 `users.id`。真实接口按 `users.external_id` 解析主体。脚本改用 `external_id`，生产授权规则未改。
2. `retry1` / `retry2` 在答案内容断言失败：导出 Markdown 把问题、答案分到不同标题章节，主证据成为仅含问题的 chunk，最终答复遗漏“售后”入口。转换时将同一 FAQ 放在同一章节，问题和答案内容保留，严格关键词与引用断言未删减。失败报告及脱敏响应均保留。

### 运营知识到正式文档的衔接

审核为 approved → 导出同一份 approved 内容 → publish-approved 发布运营快照 → 导出内容转 Markdown → 普通文档上传 → worker 处理并发布正式索引。

`publish-approved` 将运营条目标记 published，仅写运营快照，不创建正式文档或更新正式索引。脚本断言发布快照前后正式文档数仍为零、索引文件哈希不变。此处为显式验收衔接，并非新增自动发布产品功能。

## 质量门禁

`services/ingestion/parse_quality.py` 返回版本化结果，保存在 `document_versions.metadata_json.parse_quality`，任务详情 API 同时返回 `parse_quality`。结果只含规则、原因和统计，不复制文档正文。

| 检查 | 当前规则 |
| --- | --- |
| 空文本 | 去空白后为空，或完全没有字母/数字等有意义字符 |
| 乱码 | replacement、非法控制/代理/私用字符与常见 UTF-8 误解码标记占比达到 2% |
| 重复内容 | 长度至少 20 字符的相同段落/行，多余重复文本占正文至少 50% |
| 结构异常 | block 顺序异常、页码倒退/跳页/非法页码、标题路径跳级、表格宽度不一致、只有标题没有正文 |

短文本不按任意最低字数拒绝；合法短答复有测试。以上为确定性启发规则，并非通用语义纠错器，不能保证发现所有乱码和结构问题。未增加 OCR、OCR 置信度处理、XLSX/CSV 或 Docker 改动。

### 隔离和恢复语义

- 低质量任务在 parsed 阶段终止，任务和版本为 `requires_review`；无已发布版本的文档也标为 `requires_review`。错误码为 `parse_quality_requires_review`，worker 正常 ACK。
- 空文档解析异常 `empty_document` 转成可复核的空解析结果；其他解析或依赖故障保持原失败语义。
- 质量判断在内容 hash 判重成功返回之前执行；重复上传不能被标为成功发布。
- 重试即使从 indexed 等后续阶段恢复，也会重算解析并执行门禁。已有隔离结果不能靠 reprocess 或重复投递解除。
- 全局/租户索引重建都排除隔离版本，调用方传入状态过滤也不能解除；遗留 chunk 即使仍在数据库中也不纳入重建。
- 新版本被拒绝时保留同一文档已有 published 版本和旧生效索引。真实验收比较拒绝前后的全部索引文件哈希，并在重建后再次验证旧文档问答与引用。
- 本次不新增人工审批放行端点。修正内容后重新上传（新 content hash）重新评估；重复上传同一隔离内容继续待复核。
- 已存在且无 `parse_quality` 的历史版本继续可用，未批量改写或隔离用户历史数据。对这些历史版本的全量补评估不在本次范围。

### 数据库迁移

新增 `0003_parse_quality`，只为 documents / ingestion_jobs 的 CHECK 约束加入 `requires_review`，结果复用现有 metadata JSON，不增加表或列。部署代码前执行 `venv/Scripts/python.exe -m alembic upgrade head`。真实 PostgreSQL 迁移及随后写入/读取待复核状态已验证。

降级会恢复原 CHECK 约束；若仍有待复核文档或任务，降级应失败，不自动放行或改写隔离记录。此次未对复用的真实数据库执行降级，避免影响保留的验收证据。

## 检查记录

- 直接相关：`test_parse_quality.py`、`test_ingestion_pipeline.py`、`test_ingestion_models.py`、`test_document_upload_api.py`，209 passed。旧的“空文档发布”测试按新需求改为“待复核且不发布”，保留索引不得产生的断言。
- 扩大验证：上传 API、索引回滚、混合检索、租户隔离、检索 API、release smoke 和 release gate，213 passed。两批包含重复的上传 API 测试，不将其相加表述为独立测试数量。
- 改动 Python 文件 Ruff 通过；只出现现有依赖弃用警告。未重新执行全量 1154 项基线，不把旧基线当作本次完整回归。
- 真实门禁验收运行命令（环境变量使用现有本机连接，不在文档重复凭据）：

```powershell
$env:HF_HUB_OFFLINE='1'
$env:PYTHONIOENCODING='utf-8'
venv/Scripts/python.exe scripts/accept_live_rag.py --run-root tmp/live-rag-acceptance-quality1 --timeout 300 --verify-parse-quality
```

复跑必须换一个不存在的 `--run-root`。本次未提交或部署；原有前端契约两文件及 `PROJECT_INTERVIEW_DEEP_DIVE.md` 改动保留。
