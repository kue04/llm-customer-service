# 外卖客服 RAG 智能问答系统

面向外卖售后场景的企业级 RAG 客服后端。系统将身份认证、知识入库、文档切片、稠密/稀疏/混合检索、证据引用、订单工具、规则安全、澄清、转人工、会话记忆和评测治理串成一条可追踪的服务链路。

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/kue04/llm-customer-service/actions/workflows/ci.yml/badge.svg)](https://github.com/kue04/llm-customer-service/actions/workflows/ci.yml)

**当前状态（2026-09-27）**：核心后端闭环已打通，稳定性与成本控制的主要实现已落地，并保留本机真实负载、故障恢复、调用账本和备份恢复证据。可用于项目演示与业务试用；独立真实问法质量验收、实际部署容量及生产 SLA 尚未确认。

## 项目能力

- **RAG 问答**：意图识别、风险预检、查询改写、多子问题召回、Reranker 精排、证据分级和引用返回。
- **混合检索**：正式 chunk 索引支持 FAISS 稠密检索、SQLite FTS5 稀疏检索和加权 RRF 融合；默认模式为 `hybrid`。
- **企业数据隔离**：JWT 身份认证，服务端按 tenant、发布状态和 document ACL 构造检索范围。
- **持久化与入库门禁**：开发/生产统一使用 PostgreSQL；Redis Stream 驱动独立 worker，解析质量不合格的文档进入 `requires_review`，不发布、不进入新索引。
- **工具增强**：订单状态、退款状态等工具结果可进入回答依据；工具失败时保留可诊断的降级信息。
- **稳定降级**：证据不足时返回用户可见澄清回复；高风险或需要人工处理时创建真实转人工工单并返回工单号。
- **安全与治理**：敏感信息脱敏、回复规则、安全拦截、grounding 诊断、审计、反馈和发布/回滚流程。
- **可观测性**：响应包含 `request_id`、`answer_basis`、`citations`、`decision_trace` 和 `full_trace`。
- **资源保护**：聊天总 deadline、有界执行与租户并发额度；超额请求明确拒绝，超时/取消后待实际工作退出才归还名额。
- **任务恢复**：Redis pending 接管、执行心跳、PostgreSQL 执行锁、有限退避重试、死信查询与经授权的幂等重投；`requires_review` 不自动重试或放行。
- **调用账本**：生成、embedding、reranking 按请求/任务/尝试关联，记录 token 来源、耗时、状态及价格版本；未配置已确认价格时货币成本为 `null`，不视为免费。

## 系统架构

![企业级 RAG 智能客服系统架构](docs/images/system-architecture.jpg)

上半部分是正式在线推理链路：用户请求与身份认证 → 意图识别、风险预检与查询改写 → 正式 `chunk-index` 混合检索 → 精排与证据分级 → Prompt 构造与模型生成 → 安全校验与结果路由。下半部分是知识库构建与索引管理链路（上传解析 → chunk 切分 → 索引构建与 `active manifest` → 发布/回滚），以及右侧的 trace、评测与运营指标。图中 `seed-faq-demo` 仅用于演示和兼容调试，不进入正式聊天链路。

### 两条检索路径：正式链路与演示链路

<!-- f1-track: chat-service-retrieval=chunk-index -->
<!-- b8-hybrid: default-retrieval-mode=hybrid -->

聊天问答默认使用正式的 `chunk-index` 路径：从文档 chunk 索引召回，并在服务端执行 tenant、发布状态和 document ACL 过滤。`seed-faq-demo` 只保留给演示和兼容调试，不代表正式多租户检索能力。

正式检索接口是 `POST /retrieval/search`，演示接口是 `POST /retrieval/search-demo`。响应中的 `retrieval_path` 会标明实际使用的路径，调用方不需要根据文本猜测检索来源。正式聊天响应还会返回 `data_source`、`index_name`、`index_version`，并在 `trace` 中保持同一份元数据。

正式路径的默认召回模式是 `hybrid`（FAISS 稠密检索 + SQLite FTS5 稀疏检索 + 加权 RRF）。切换或发布 `hybrid` 前，必须先重建同时包含 dense 和 sparse 两路的生效索引，并确认索引状态返回 `sparse_available=true`；否则服务会显式返回不可用错误，不会静默退回其他模式。

核心代码：

| 能力 | 位置 |
| --- | --- |
| HTTP 应用入口 | `main.py` |
| 聊天编排、降级和 trace | `services/chat_service.py` |
| 查询解析与改写 | `services/query_resolution.py`、`services/query_rewrite_provider.py` |
| 意图与风险 | `services/intent_service.py`、`services/safety_guard.py` |
| chunk 检索 | `routers/retrieval.py`、`utils/hybrid_retriever.py`、`utils/sparse_retriever.py` |
| 证据上下文 | `utils/rag_context.py` |
| 回答规则 | `services/answer_composer.py`、`services/reply_rules.py` |
| 文档入库 | `services/ingestion/` |
| 知识运营 | `services/knowledge_service.py` |
| 会话与人工工单 | `services/conversation_store.py`、`services/order_tool_service.py` |
| 请求预算与有界执行 | `services/request_budget.py` |
| 入库容量、租约与重试 | `services/ingestion/admission.py`、`services/ingestion/delivery.py` |
| 模型调用账本与运营指标 | `services/call_ledger.py`、`services/ops_metrics.py`、`routers/ops.py` |

## 快速开始

### 1. 安装依赖

```powershell
git clone https://github.com/kue04/llm-customer-service.git
cd llm-customer-service
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

仅运行测试和静态检查不需要下载大模型：

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
```

### 2. 配置数据库、队列与身份认证

开发与生产都必须使用 PostgreSQL；SQLite 仅允许在 `RAG_ENV=test` 下使用。以下为本机开发示例，先准备 PostgreSQL 和 Redis；若已安装 Docker，可使用仓库编排仅启动这两个依赖：

```powershell
docker compose up -d postgres redis
$env:RAG_ENV = "development"
$env:RAG_DATABASE_URL = "postgresql+psycopg://rag:rag@127.0.0.1:5432/rag_metadata"
$env:RAG_REDIS_STREAM_URL = "redis://127.0.0.1:6379/0"
$env:RAG_JWT_SECRET = "dev-only-secret-change-me-please-32-bytes"
```

上述数据库凭据仅对应本地 Compose 示例；使用已有数据库时替换连接串。默认运行环境是 `production`，不会自动退回 SQLite。`.env.example` 是配置参考，本机 Python 启动不会自动加载它，需将配置写入进程环境。

服务端从 JWT 推导租户和权限。请求头 `X-User-Role`、`X-Operator-Id` 不能单独认证或提权；请求体中的 `tenant_id`、`operator_id` 不是权限来源。

### 3. 初始化数据库并启动 API 与 worker

完整 RAG 链路需要运行时依赖，以及 embedding、reranker 和本地/在线生成模型配置。先迁移数据库，再初始化开发身份：

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe scripts/seed_dev_tenant.py
.venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --tenant-id tenant-dev --user-id dev_user --format token
```

保存初始化脚本输出的 `knowledge_base_id`，上传时会用到；JWT 以 `Authorization: Bearer <token>` 发送。初始化脚本默认授予开发用户全部已知角色，仅用于开发联调。

从项目根目录分别在两个终端启动，两个终端都需设置第 2 步的环境变量，并使用相同数据库、Redis Stream、对象存储和索引目录：

```powershell
# 终端 1：迁移成功后启动 API
.venv/Scripts/python.exe -m scripts.start_runtime api --host 127.0.0.1 --port 8000

# 终端 2：迁移成功后启动入库消费者
.venv/Scripts/python.exe -m scripts.start_runtime worker
```

只启动 API 无法完成上传入库。进程内队列无法跨进程传递任务，不能替代 API 与独立 worker 之间的 Redis Stream。新库还需要完成下节的数据入库与索引构建，才能进行正式检索。

启动后检查：

- OpenAPI 文档：`http://127.0.0.1:8000/docs`
- 存活检查：`GET /health` 或 `GET /health/live`，仅说明进程可响应。
- 就绪检查：`GET /health/ready`，依赖未就绪时返回 503。
- 依赖诊断：`GET /health/dependencies`，需要具备运维读取权限的 JWT。

完整配置和运行约束见 [运行说明](docs/P1_RUNTIME_OPERATIONS_2026-09-27.md) 与 [持久化迁移说明](docs/RUNTIME_DATA_MIGRATION_2026-09-27.md)。

### 4. 稳定性配置

使用新代码前执行 `alembic upgrade head`；5.4 新增 `0004_delivery_attempts`、`0005_model_call_ledger`、`0006_ledger_audit_fields`。主要默认值如下，完整变量见 `.env.example`：

| 配置 | 默认值 | 范围 |
| --- | ---: | --- |
| `RAG_CHAT_DEADLINE_SECONDS` | 30 秒 | 聊天总预算，包含模型等待 |
| `RAG_CHAT_CAPACITY` / `RAG_CHAT_TENANT_CAPACITY` | 4 / 2 | 每 API 进程全部 / 单租户尚未退出的聊天工作 |
| `RAG_MODEL_CAPACITY` | 1 | 每 API 进程生成并发 |
| `RAG_INGESTION_CAPACITY` / `RAG_INGESTION_TENANT_CAPACITY` | 64 / 16 | 全库 / 单租户未完成入库任务 |
| `RAG_WORKER_MAX_ATTEMPTS` | 3 | 包括进程被杀的执行尝试 |
| `RAG_WORKER_LEASE_SECONDS` | 30 秒 | pending 接管候选阈值，仍需取得数据库执行锁 |
| `RAG_MODEL_PRICES_FILE` | 不配置 | 未知价格保留 `null`，按币种汇总已确认费用 |

生产队列需要支持 `XAUTOCLAIM` 的 Redis（6.2+）。聊天并发额度按 API 进程计，不是跨实例集中式限流。超时/取消不会强杀 Python 线程；原生模型单次计算可能稍后才退出，期间继续占用名额。

## 主要接口

| 接口 | 用途 |
| --- | --- |
| `POST /chat/prompt` | 客服问答主入口，返回回答、引用、工具结果和完整 trace |
| `POST /retrieval/search` | 正式 chunk 检索，执行 tenant/ACL/发布状态过滤 |
| `POST /knowledge-bases/{knowledge_base_id}/documents` | 以 multipart 字段 `file` 上传文档并创建异步入库任务 |
| `GET /documents/{document_id}` | 查询权限范围内的文档详情 |
| `GET /documents/{document_id}/versions` | 查询文档版本 |
| `GET /ingestion-jobs/{job_id}` | 查询入库进度、错误、尝试次数、重试时间、死信与待复核状态 |
| `POST /ingestion-jobs/{job_id}/redrive` | 经授权、幂等地重投死信任务；不能放行待复核文档 |
| `POST /ingestion/indexes/rebuild` | 重建正式 chunk 索引 |
| `POST /ingestion/indexes/rollback` | 回滚正式 chunk 索引 |
| `POST /knowledge/...` | 知识草稿、审核、发布和回滚 |
| `POST /chat/review-action` | 接受、编辑发送、转人工等人工动作 |
| `GET /ops/metrics` | 已完成聊天轮次指标与独立的模型尝试/成本汇总 |
| `GET /ops/model-calls` | 当前租户模型调用明细，支持 request_id/job_id 筛选与分页 |
| `GET /ops/capacity` | 当前 API 名额、模型等待、连接池及当前租户积压信息 |

`POST /retrieval/search-demo` 和 `POST /retrieval/prompt-preview` 都是种子 FAQ 演示接口，不代表正式多租户检索链路。仅在 `RAG_ENV=development/test` 且 `RAG_ENABLE_DEMO_ENDPOINTS=true` 时注册，调用还需要 admin 角色及 `demo:read` 权限；生产环境不开放。

### 聊天响应中的稳定模式

- `answer_mode=complete`：证据充分，返回正常回答。
- `answer_mode=clarify`：证据不足，返回用户可见的澄清问题，状态为 `awaiting_clarification`。
- `answer_mode=human_review`：高风险或证据不足需要人工处理，创建真实 handoff ticket，状态为 `human_handoff`，响应包含 `handoff_ticket`。

## 数据与索引

正式聊天链路使用文档 chunk 索引，而不是默认使用种子 FAQ。先完成数据库迁移和开发身份初始化，再通过上传接口和 worker 入库；需要生成、批量导入仓库语料时可执行：

```powershell
.venv/Scripts/python.exe scripts/build_corpus_documents.py
.venv/Scripts/python.exe scripts/bulk_import_documents.py --dry-run
.venv/Scripts/python.exe scripts/bulk_import_documents.py
```

语料构建会读取本地源数据并抓取法规正文，需要相应输入和网络。构建文件本身不会入库；批量导入脚本才会执行解析、切分、入库和最终索引重建。已有 `document_chunks`、只需更新索引时使用 `.venv/Scripts/python.exe scripts/rebuild_chunk_index.py`，不必重复导入。

解析质量不合格时任务进入 `requires_review`，不会继续切分、发布和建索引；修正内容后重新上传。重试或重复上传同一隔离内容不会自动放行，已有发布版本保持可用。历史缺少 `parse_quality` 元数据的版本仍按兼容规则处理，不表示已经通过新门禁。

索引目录包含 FAISS 向量、manifest 和 FTS5 稀疏索引。发布状态、租户和 ACL 在服务端过滤后才进入召回范围。原始数据、模型和生成索引不应提交到 Git；仓库中的样例数据仅用于测试和演示。

## 检索质量进展（2026-09-27）

本轮在 PostgreSQL 隔离快照中复用历史 9,229 个 chunk，以真实 BGE 查询向量验证检索。历史报告中的“Recall@5”实际是 query-level **Hit@5**（前五条至少一条覆盖 gold span），不等于 chunk-level recall。

| 路径与指标 | 标题集（81 条） | 固定口语代理集（30 条） |
| --- | ---: | ---: |
| 原始 hybrid Hit@5 | 0.9630 | 0.5000 |
| 生产查询计划 Hit@5（诊断预算） | 1.0000 | 0.6333 |
| 生产默认查询计划 Hit@3 | 0.9753 | 0.5333 |

诊断 @5 使用每路 top_k/limit=10，生产默认计划 limit=3，两种预算不能混比。标题集为弱监督标注，口语集是固定改写代理集；另有 20 条待人工审核的合成候选，独立检索盲测尚无合格标注。本轮未执行答案生成或 BGE reranker，因此这些结果不能证明最终回答正确或真实业务质量达标。来源与逐样本结果见 [检索质量报告](docs/RETRIEVAL_QUALITY_2026-09-27.md)。

### 历史基线（2026-09-25）

以下保留当时的正式 chunk RAG 系统基线。指标来自固定 chunk/span gold 和本地性能报告，不代表当前部署状态或线上真实业务 SLA。

| 指标 | 当时结果 | 说明 |
| --- | ---: | --- |
| 正式索引 | v4 / 9,229 chunks | `BAAI/bge-small-zh-v1.5`，512 维，sparse 可用 |
| Hybrid Hit@5（标题集，原报告名 Recall@5） | 0.9630 | 81 条 gold case |
| Hybrid MRR（标题集） | 0.7922 | 81 条 gold case |
| Hybrid NDCG@10（标题集） | 0.8404 | 81 条 gold case |
| Hybrid Hit@5（口语集，原报告名 Recall@5） | 0.5000 | 30 条固定口语代理 case |
| Hybrid MRR（口语集） | 0.4274 | 30 条固定口语代理 case |
| Hybrid NDCG@10（口语集） | 0.4588 | 30 条固定口语代理 case |
| Hybrid 平均延迟 | 26.29 ms | n=30 性能基线 |

相较缓存优化前的同口径性能报告：manifest 平均加载耗时下降约 94.5%，dense 下降约 76.4%，sparse 下降约 93.2%，hybrid 下降约 91.6%。这些是本地固定索引上的相对变化，不等同于线上 SLA。

标题集的 hybrid Hit@5 达到历史固定集目标 0.85；固定口语代理集 Hit@5 为 0.50，口语泛化仍需改进并通过独立标注验证。历史 seed FAQ 指标与正式 chunk/span 指标不混用。

## 稳定性与成本实测（2026-09-27）

以下是一份固定诊断语料下的本机对照数据：**CPU 推理、6 核 / 12 线程、约 31.7 GiB 内存、1 API + 1 worker、CPU 线程配置 6**，使用既有 Qwen2.5-1.5B 和 BGE。前后 seed Markdown 哈希一致。环境为 `torch 2.11.0+cpu`，**没有使用本机 RTX 5060**，也不是历史 9,229 chunks 全量语料的容量成绩。

| 负载 | 并发 | 改动前成功请求 P95 | 改动后成功请求 P95 | 改动后成功 / 尝试 | 改动后拒绝 / 尝试 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 聊天 | 1 | 9.50 秒 | 11.52 秒 | 2/2 | 0/2 |
| 聊天 | 4 | 48.27 秒 | 19.30 秒 | 2/8 | 6/8 |
| 文档入库 | 4 | 1.92 秒 | 2.34 秒 | 8/8 | 0/8 |
| 聊天 + 入库 | 4 | 52.89 秒 | 23.97 秒 | 6/8 | 2/8 |

每级仅 `2 × 并发数` 次尝试，样本量小，P99 接近最大值。表中延迟仅针对成功请求，拒绝比例同时列出；全部尝试、失败和超时保留在明细中。聊天并发 4 的成功吞吐前后均约 **0.104 次/秒**，主要收益是将超额排队变为明确拒绝，并非模型生成提速；串行聊天和入库存在额外开销。上节的 26.29 ms 是历史检索组件指标，不能与这里的完整聊天耗时混比。

| 验证项 | 已有证据 |
| --- | --- |
| 总时限与取消 | 真实本地模型验证 504 超时、429 容量拒绝、断连后名额回收；原生计算退出有额外等待，未提前释放名额 |
| worker 故障恢复 | 实际 kill 后接管；两次依赖错误后第三次成功；ACK 失败重放未重复发布；死信重投权限/幂等及 requires_review 语义通过 |
| 调用与成本核对 | 生成 token 与账本一致，明细/汇总一致，重启后可读，跨租户查询为空；本地货币成本为 `null` |
| 数据备份恢复 | 新库/新目录恢复 22 份文档、44 个对象目录文件、67 个索引目录文件；表数量、文件哈希、源库引用及租户隔离核对通过 |
| 恢复时间与数据损失 | 实测 RTO **22.47 秒**；静默源库下观测 RPO **0**，不代表在线 PITR 或生产 SLA |

本轮模型进程 RSS/CPU 峰值采样口径无效，已明确标为不可用；连接池事件可按实际 PID 离线核对。旧基线也未覆盖完整入库逐阶段耗时。相关边界和失败记录见 [5.4 实施与结果报告](docs/STABILITY_COST_RESULTS_2026-09-27.md)，统一数据见 [实测汇总 JSON](reports/stability_cost/summary.json)。

## 评测与质量门禁

**最近一次已执行的完整回归（2026-09-27）**：`1202 passed`、`7 subtests passed`，失败、错误、跳过均为 0，包含真实 PostgreSQL 分支；耗时 114.85 秒。JUnit 的 1209 个用例包含这 7 个子测试，不是另一组测试。此后少量观测、过滤、报告及补充边界测试改动未再次执行完整回归，新补充测试不计为已通过；以下命令供复现，不表示文档更新时重新运行了评测。

常用命令：

```powershell
.venv/Scripts/python.exe scripts/run_formal_rag_evaluation.py --tenant-id tenant-dev --top-k 10
.venv/Scripts/python.exe scripts/audit_manifest_load_cost.py --label perf_20260925 --repeat 15 --query-limit 30
.venv/Scripts/python.exe scripts/evaluate_chat_grounding.py --legacy-seed  # 仅兼容 seed 评测
.venv/Scripts/python.exe scripts/check_repo_data_size.py
.venv/Scripts/python.exe scripts/check_doc_render.py README.md
.venv/Scripts/python.exe scripts/verify_acceptance_spec.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q
```

测试覆盖认证、租户隔离、ACL、文档入库、检索、回答规则、工具降级、人工转接和发布回滚。详细评测口径见：

- `docs/EVALUATION.md`
- `docs/USAGE_AND_FIELDS.md`：启动、评测和字段解释
- `docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`
- `docs/RAG_DEV_PITFALLS.md`

## 项目结构

```text
main.py                 FastAPI 应用入口
routers/                HTTP 路由与权限声明
services/               业务编排、入库、知识运营、工具和治理
utils/                  检索、重排、证据和数据处理
schemas/                请求/响应模型
scripts/                建库、评测、检查和运维脚本
data/                   样例数据与本地运行数据，不提交生产数据
tests/                  单元、接口、隔离和发布门禁测试
docs/                   API、评测、设计和运维文档
```

## 生产注意事项

- 生产环境必须使用独立 JWT 密钥、真实身份提供方和持久化数据库/对象存储。
- `RAG_CHAT_RETRIEVAL_MODE` 可用于显式诊断模式；正式默认值为 `hybrid`，缺少稀疏索引时应显式失败，不静默降级。
- 需要人工处理的请求不要只依赖前端标记；服务端会创建 handoff ticket，并在响应和会话状态中记录结果。
- 评测报告必须注明数据来源、索引版本和 `retrieval_path`。正式 chunk 语料使用 `scripts/evaluate_hybrid_retrieval.py`；seed FAQ 只能通过 `--legacy-seed` 显式运行，不能替代生产指标。

## 相关文档

- `docs/API_INTEGRATION.md`：接口调用和身份认证
- [运行与就绪检查](docs/P1_RUNTIME_OPERATIONS_2026-09-27.md)
- [PostgreSQL 持久化迁移](docs/RUNTIME_DATA_MIGRATION_2026-09-27.md)
- [解析质量门禁与真实入库验收](docs/PARSE_QUALITY_AND_LIVE_ACCEPTANCE_2026-09-27.md)
- [检索质量、指标口径与标注缺口](docs/RETRIEVAL_QUALITY_2026-09-27.md)
- [稳定性、成本、容量对照与恢复实测](docs/STABILITY_COST_RESULTS_2026-09-27.md)
- [稳定性与成本执行计划及当前状态](docs/STABILITY_COST_EXECUTION_PLAN_2026-09-27.md)
- `docs/ENTERPRISE_AI_CUSTOMER_SERVICE_PRD.md`：产品边界和业务流程
- `docs/RAG_DOCUMENT_CORPUS_PLAN.md`：文档语料和入库计划
- `HANDOFF.md`：开发交接和历史决策

## License

MIT
