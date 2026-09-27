# P1 非 Docker 运行改造

本次范围：生产演示检索入口隔离、真实依赖就绪检查、非 Docker 启动前迁移，以及评审 5.1 的统一业务持久化。Dockerfile、Compose 和容器 CI 编排保持不变。

## 配置与启动

默认 `RAG_ENV=production`。生产和开发都必须配置 `RAG_DATABASE_URL` 为 PostgreSQL；SQLite 仅用于显式 `RAG_ENV=test`。安装 `requirements.txt`（已启用 `psycopg[binary]`）。API 与 worker 使用同一数据库、Redis Stream、消费组及共享文档/索引存储。

非容器启动入口先运行 Alembic；迁移失败不会继续启动服务：

```powershell
python -m scripts.start_runtime api --host 127.0.0.1 --port 8000
python -m scripts.start_runtime worker --consumer-name worker-1
```

正式部署建议由单独的迁移步骤执行 `python -m alembic upgrade head`，成功后再以只具有业务 DML 权限的身份启动 `uvicorn main:app` 和 `python -m services.ingestion.worker`。这些直接入口也检查数据库迁移版本，未达到当前 head 时拒绝启动。

## 演示入口

- `/retrieval/search-demo` 和 `/retrieval/prompt-preview` 使用独立 router。
- 仅 `development`/`test` 且 `RAG_ENABLE_DEMO_ENDPOINTS=true` 时注册；生产即使设置 true 也不注册，OpenAPI 中不展示。
- 已注册的处理函数仍检查 profile，并要求服务端根据 admin 角色签发的 `demo:read` scope。token 自报 scopes 不改变权限。
- 正式 `/retrieval/search` 保留租户及 document ACL 过滤。

## 健康检查

| 路径 | 用途 | 状态 |
|---|---|---|
| `/health`、`/health/live` | 仅进程存活，保持旧入口兼容 | 200 |
| `/health/ready` | 检查所有必需依赖，只返回总体状态 | 就绪 200；失败 503 |
| `/health/dependencies` | 返回各项状态、版本及探测耗时 | 需要 JWT 和 `read:ops_metrics_read`；失败 503 |

依赖包括数据库连接及 Alembic head、Redis ping、同一 Stream/消费组的 worker 心跳、active manifest 指纹及 dense/sparse 文件一致性、已加载模型或在线模型可访问性、JWT 配置。错误响应仅含固定错误码，不返回连接串、密钥、私有主机地址或原始异常。

worker 心跳每 20 秒更新，60 秒到期；独立线程在长任务处理中持续更新。进程退出后心跳自动过期，不删除其他 worker 共享的有效标记。

模型默认启动预热；失败时实例保持可诊断但 readiness 不通过。`RAG_WARM_MODELS_ON_STARTUP=false` 可关闭预热，但不会让未加载模型通过探针。在线生成服务通过带认证的 `GET <api_base>/models` 检查配置模型是否可用；该地址需由所用服务支持，探针不发起收费生成请求。

只有明确设置 `RAG_ALLOW_DEGRADED_READINESS=true` 的 development/test 可容忍 Redis、worker、索引或模型失败，响应会标记 `degraded`；数据库和 JWT 配置不可降级。生产忽略此开关。

## 数据迁移与兼容

完整表结构、历史导入、事务、归档和备份恢复说明见 `RUNTIME_DATA_MIGRATION_2026-09-27.md`。业务请求不再创建或修补表；旧固定 SQLite 文件不作为在线数据源。FAISS/FTS5 文件继续作为可重建检索索引制品，不属于业务数据库回退。

本次对本机旧文件执行了只读导入规划，没有导入或改写旧文件：

| 文件 | 需提供可信归属映射的记录数 |
|---|---:|
| `data/ops_feedback.db` | 8768 |
| `data/knowledge_ops.db` | 5 |
| `data/prompt_versions.db` | 2 |

这些是检查时的数量，后续源文件变化须重新 dry-run。需准备逐记录可信 tenant/creator 映射及实际目标 PostgreSQL，再显式执行导入；不能把历史记录默认分配给任意租户。

## 验证范围

最终全量后端回归：`1154 passed`、`4 subtests passed`，没有跳过项；仅有依赖弃用警告。改动 Python 文件的 Ruff 与 `git diff --check` 均通过。

新增测试覆盖生产 demo 禁用、额外 scope、依赖失败 503、错误脱敏、worker 过期、模型未加载、缓存不能掩盖丢失 manifest、数据库未迁移不建表以及迁移失败不启动 API/worker。

真实 PostgreSQL 集成使用从发行商获取的 Windows 便携二进制，在仅监听回环地址的临时实例上执行；未使用 Docker。验证升级/降级/重启、事务回滚、幂等竞争、独立进程共享、两个 API 实例读写与 JWT 隔离、历史映射导入和归档。此验证不等于已经部署到生产或完成生产备份恢复演练。
