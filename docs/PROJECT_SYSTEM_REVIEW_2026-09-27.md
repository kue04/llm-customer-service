# 项目系统完整性审查与改造建议

审查日期：2026-09-27  
审查对象：`llm-customer-service` 当前代码、配置、文档、测试和部署编排  
审查结论：**工程化 RAG 后端基线已形成，但尚未达到生产级完整性。**

> **整改进度更新（2026-09-27）**：本文保留修复前的审查发现，不能将下文全部问题视为当前仍未修复。两个 P0、非 Docker P1 及 5.1 数据层的代码改造和集成验证已完成；完整回归 `1154 passed`、`4 subtests passed`，无跳过。旧数据正式导入及实际 Redis/模型的端到端验收待完成；Docker/Compose 按要求暂缓；5.2、5.3、5.4 本轮未实施。最新明细见 `RAG_EXECUTION_PROGRESS.md` 文末，以及 `P1_RUNTIME_OPERATIONS_2026-09-27.md`、`RUNTIME_DATA_MIGRATION_2026-09-27.md`。

## 1. 结论摘要

当前项目已经覆盖了大部分 RAG 后端组件：身份认证、文档上传、解析、结构化切分、异步 worker、FAISS/FTS5 混合检索、证据门禁、模型生成、规则安全、会话、反馈、审计、发布和回滚。

但是，系统仍存在影响生产上线的关键问题：

1. 聊天历史存在按 `session_id` 绕过用户归属校验的风险。
2. 订单状态接口没有强制绑定 JWT 用户和租户，写入还信任请求体中的 `user_id`。
3. 演示检索接口仍暴露无 tenant/ACL 过滤的种子链路。
4. Docker Compose 使用 PostgreSQL，但镜像依赖中没有启用 `psycopg` 驱动。
5. `/health` 只代表进程存活，不代表数据库、Redis、worker、索引或模型可用。
6. 会话、订单、反馈等数据仍分散在本地 SQLite 文件中，不适合多实例部署。
7. 口语化检索 Recall@5 只有 `0.5000`，固定标题集的 `0.9630` 不能代表真实用户效果。

因此当前更准确的定位是：**可运行的工程化原型/后端基线，不是可以直接承接真实多租户生产流量的系统。**

## 2. 当前完整链路

### 2.1 在线问答链路

```text
HTTP 请求
  -> JWT 校验与角色 scope
  -> 会话恢复、意图/风险分析
  -> 查询解析与改写
  -> 订单/退款工具查询
  -> 正式 chunk-index 检索
       -> tenant/status/document ACL 过滤
       -> FAISS dense + SQLite FTS5 sparse
       -> weighted RRF + 去重 + rerank
  -> evidence gate
       -> prompt 构造
       -> 本地或在线模型生成
       -> answer composer / reply rules / safety guard
  -> 保存消息、trace、指标、反馈上下文
  -> 返回答案、引用、工具结果和完整 trace
```

正式 chunk 检索链路已经接入租户和文档 ACL。旧的 seed FAQ 链路仍保留在 `/retrieval/search-demo` 和 `/retrieval/prompt-preview`，这两条路径没有正式的 tenant/ACL 过滤。

### 2.2 文档入库链路

```text
上传
  -> 对象存储
  -> ingestion job
  -> Redis Stream
  -> worker
  -> 解析
  -> 规范化与 chunk
  -> document_chunks
  -> dense/sparse 索引
  -> manifest
  -> publish / rollback
```

这条链路的代码结构已经存在，Docker Compose 也编排了 `api`、`worker`、`postgres` 和 `redis`。但裸机不配置 Redis 时，API 仍能启动，上传任务无法跨进程到达 worker，会长期停留在 `pending/received`。

## 3. 已经具备的能力

### 3.1 代码和模块结构

- `main.py` 负责应用入口和路由注册。
- `routers/` 按 chat、documents、retrieval、knowledge、release、audit、feedback 等领域拆分。
- `services/` 负责业务编排、鉴权、会话、入库、工具和治理。
- `utils/` 负责向量检索、稀疏检索、混合融合、证据上下文和数据处理。
- `schemas/` 提供 HTTP 输入输出模型。
- `scripts/` 提供索引构建、评测、检查和运维脚本。
- `alembic/` 和 SQLAlchemy 模型已经为 ingestion 元数据准备了迁移基础。

### 3.2 安全与权限

- JWT 校验包含签名、算法白名单、`exp`、`iss`、`aud` 和必需 claim。
- scope 由服务端按角色推导，不信任 token 中自报的 permissions/scopes。
- 正式检索在召回前应用 tenant、发布状态和 document ACL 过滤。
- 对象 key 会净化文件名，上传有大小和内容类型检查。
- 已有审计日志、敏感信息脱敏、规则拦截和人工转接。

### 3.3 RAG 与运营

- dense、sparse、hybrid 三种路径有明确数据来源和错误状态。
- 默认 hybrid 使用 FAISS + SQLite FTS5 + weighted RRF。
- 证据不足可以澄清，高风险请求可以转人工并生成 handoff ticket。
- 返回 `request_id`、引用、`answer_basis`、`decision_trace` 和 `full_trace`。
- 已有知识审核、发布、索引重建和索引回滚接口。

### 3.4 测试基础

当前本地测试结果：

```text
1061 passed, 3 failed
```

3 个失败均与当前环境缺少 `trafilatura` 有关，位于 HTML 解析 fallback 测试。测试覆盖面较广，但仍缺少若干真正的 API 级越权和生产环境验证。

## 4. 必须优先修复的问题

### P0：聊天历史越权

`GET /chat/history` 接收客户端提供的 `user_id` 和 `session_id`。当带有 `session_id` 时，`find_conversation()` 只按会话 ID 查询，没有校验当前 `auth.user_id` 或 `auth.tenant_id`。

证据：

- [routers/chat.py](/D:/llm/llm-customer-service/routers/chat.py:41)
- [services/conversation_store.py](/D:/llm/llm-customer-service/services/conversation_store.py:223)

整改方案：

1. `user_id` 不再作为可信查询参数，统一使用 `auth.user_id`。
2. 查询条件必须包含 `session_id + user_id + tenant_id`。
3. 不存在和无权访问统一返回 404，避免泄漏资源存在性。
4. 增加“猜到其他 session_id”“伪造 user_id”“跨租户访问”三个负向测试。

### P0：订单状态越权与归属伪造

订单读取只按 `order_id` 查询，没有验证当前用户或租户。写入时直接使用请求体里的 `user_id`，调用方可以伪造订单归属。

证据：

- [routers/order.py](/D:/llm/llm-customer-service/routers/order.py:18)
- [services/order_state_store.py](/D:/llm/llm-customer-service/services/order_state_store.py:43)

整改方案：

1. `order_states` 增加 `tenant_id`。
2. 读取条件改为 `order_id + tenant_id + owner/user binding`。
3. 写入时忽略请求体中的 `user_id`，由 JWT 或可信订单系统注入。
4. 对订单写入增加幂等键、状态迁移校验和审计前后快照。
5. 增加跨用户读取、跨租户读取、伪造 `user_id` 写入测试。

### P1：演示检索路径可被生产误用

`/retrieval/search-demo` 和 `/retrieval/prompt-preview` 明确使用无 tenant/ACL 过滤的种子数据，但仍挂在正式 FastAPI 应用中。

证据：[routers/retrieval.py](/D:/llm/llm-customer-service/routers/retrieval.py:274)

推荐方案：

- 最优：开发环境单独挂载 demo router，生产构建不注册。
- 次优：增加 `RAG_ENABLE_DEMO_ENDPOINTS=false`，并要求额外的 `demo:read` scope。
- 同时为生产 profile 增加端点禁用测试，防止发布时重新暴露。

### P1：Compose 与依赖不一致

Compose 使用 `postgresql+psycopg://...`，但 `requirements.txt` 中 `psycopg[binary]` 被注释。容器可能通过健康检查，但数据库访问和 worker 事务会在运行时失败。

证据：

- [docker-compose.yml](/D:/llm/llm-customer-service/docker-compose.yml:15)
- [requirements.txt](/D:/llm/llm-customer-service/requirements.txt:38)

推荐方案：

1. 将 `psycopg[binary]` 放入生产 requirements 或单独的 `requirements-postgres.txt`。
2. Docker build 阶段执行一次 `python -c "import psycopg"`。
3. 启动前执行 `alembic upgrade head`，失败则阻止 API 和 worker 启动。
4. CI 增加 PostgreSQL service job，至少验证迁移和关键 repository 操作。

### P1：健康检查不是真正的 readiness

当前 `/health` 固定返回 OK。它无法反映数据库未迁移、Redis 不可用、索引不存在、模型未加载等情况。

证据：[main.py](/D:/llm/llm-customer-service/main.py:147)

推荐方案：

- `/health/live`：只检查进程是否存活。
- `/health/ready`：检查数据库连接、迁移版本、Redis、active manifest 和必要模型。
- `/health/dependencies`：返回各依赖状态、版本和耗时，不暴露敏感连接信息。
- 生产配置下 Redis/数据库不满足要求时，ready 返回非 2xx；本地开发才允许显式降级。

## 5. 重要但可分阶段处理的问题

### 5.1 数据层统一

会话、订单、反馈、提示词和部分知识运营数据仍使用独立 SQLite 文件，例如 `ops_feedback.db`、`knowledge_ops.db` 和固定本地路径。该方案适合单实例开发，不适合多 worker、多副本、备份和租户级数据治理。

推荐迁移路径：

1. 统一使用 PostgreSQL 作为运行时数据源。
2. 把运行时 `ensure_schema()` 改为 Alembic migration。
3. 所有业务表统一包含 `tenant_id`、创建者、更新时间和软删除/保留策略。
4. 为会话、订单、反馈、知识和审计统一定义索引、归档和备份策略。
5. SQLite 仅保留为测试后端，不作为生产降级路径。

### 5.2 文档入库质量

目前支持 PDF、DOCX、HTML、Markdown、TXT，但以下能力还不完整：

- 真实 OCR 引擎未纳入默认运行环境。
- 未支持 XLSX/CSV 和图表视觉理解。
- 没有系统化的解析质量门禁。
- 没有病毒/恶意文件扫描。
- 跨页段落、表格连续性和多栏文档仍需独立验证。

推荐增加 `parse_quality` 结果，至少检查：空文本、乱码率、文本长度异常、重复段、页码连续性、OCR 置信度和结构异常。低质量文档应进入 `requires_review`，不能直接进入 active index。

### 5.3 检索质量

当前标题式 gold 集 Recall@5 为 `0.9630`，口语化数据集 Recall@5 为 `0.5000`。后者更接近真实用户表达，是当前主要质量短板。

推荐顺序：

1. 扩充独立人工标注的口语、错别字、多轮指代和多意图数据。
2. 将 query rewrite 与 intent classification 分开评测。
3. 增加 query-level、chunk-level、intent-level 三种指标，避免指标混用。
4. 对标题集、口语集、盲测集分别设门槛。
5. 不把固定集结果直接解释为线上 SLA。

### 5.4 稳定性和成本

目前尚未完成真实并发压测、QPS/P95/P99 目标、总 deadline、背压、取消、死信队列、成本账本和恢复演练。

推荐补齐：

- API、Redis worker、索引构建和模型生成分别压测。
- 为单请求设置总 deadline，并为外部模型/工具设置独立 timeout。
- Redis Stream 增加积压监控、失败重投和死信处理。
- 记录每次模型调用的 provider、模型、token、耗时、失败和重试成本。
- 演练数据库恢复、对象存储恢复、索引回滚和 worker 重启。

## 6. 推荐目标架构

```text
API Gateway
  -> AuthContext / tenant context
  -> Rate limit / request deadline
  -> Chat Orchestrator
       -> Session Store (PostgreSQL)
       -> Intent / Query Planner
       -> Authorized Tools
       -> Retrieval Gateway
            -> active manifest
            -> tenant + ACL + publication filter
            -> dense / sparse / rerank
       -> Evidence Gate
       -> Generation Provider
       -> Safety / Claim Verification
       -> Human Handoff
  -> Audit / Metrics / Cost Ledger

Document Pipeline
  -> Object Storage
  -> Job Metadata (PostgreSQL)
  -> Redis Stream
  -> Worker
  -> Parse Quality Gate
  -> Chunk + Persist
  -> Build Versioned Index
  -> Atomic Publish / Rollback
```

核心原则：所有业务数据统一由服务端身份产生租户边界；所有检索入口统一经过 Retrieval Gateway；demo、legacy 和正式链路必须在部署层明确隔离。

## 7. 分阶段改造计划

### 阶段一：安全阻断项

- 修复聊天历史查询绑定。
- 修复订单读写的用户/租户绑定。
- 增加跨租户、跨用户负向测试。
- 生产默认关闭 demo 检索端点。

验收标准：任何合法 JWT 都不能读取或修改其他用户、其他租户的会话和订单状态。

### 阶段二：部署可用性

- 补齐 PostgreSQL 驱动。
- 启动时执行迁移并失败即退出。
- 增加 live/ready 探针。
- 增加 Redis、数据库、worker 和 active manifest 检查。
- 在 CI 中加入 PostgreSQL/Redis 集成 job。

验收标准：依赖缺失时实例不会被编排系统标记为可接流量；上传任务能在 API 与 worker 之间完成闭环。

### 阶段三：数据层统一

- 会话、订单、反馈、知识、提示词和审计统一迁移到 PostgreSQL。
- 删除请求路径内的建表/补列逻辑。
- 增加数据保留、备份和恢复策略。

验收标准：两个 API 副本共享同一份会话、订单、反馈和审计数据，重启不丢失业务状态。

### 阶段四：质量与运维

- 增加解析质量门禁和恶意文件扫描。
- 扩充口语/盲测集并固定评测口径。
- 增加限流、deadline、背压、死信和成本账本。
- 完成并发压测、故障注入和恢复演练。

验收标准：质量、延迟、成本、恢复和安全均有可重复的报告，而不是只依赖单元测试。

## 8. 必须补充的测试清单

- `session_id` 指定时读取其他用户会话应返回 404。
- 请求体 `user_id` 与 JWT 用户不一致时，订单写入必须拒绝或忽略请求体字段。
- 跨租户订单读取和更新必须失败。
- demo 检索端点在生产 profile 下不可访问。
- PostgreSQL `alembic upgrade head` 和回滚/重启测试。
- Redis worker 重启、重复消息、积压、ack 失败和重投测试。
- 对象存储写入成功但数据库提交失败时的补偿测试。
- active manifest 切换失败时旧索引仍可服务。
- 多进程/多副本共享会话、订单和反馈数据测试。
- JWT 密钥轮换、过期、issuer/audience 不匹配测试。
- ready 探针在数据库、Redis、索引、模型不可用时返回非就绪。

## 9. 最终判断

项目的 RAG 主干、文档入库骨架、检索隔离、回答降级和治理能力已经具备较好的工程基础；当前主要问题不是“缺少一个模型或一个接口”，而是安全边界和生产运行条件还没有在所有链路统一落实。

在完成阶段一和阶段二之前，不建议接入真实多租户生产流量；完成阶段三和阶段四后，才具备进一步做灰度发布、SLA 评估和规模化压测的条件。
