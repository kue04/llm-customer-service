# 运行时业务数据统一（2026-09-27）

会话、消息、轮次、复核、事实、长期记忆、订单、幂等请求、反馈、审计、提示词和知识运营记录全部使用 RAG_DATABASE_URL 指向的 PostgreSQL。新表统一以 runtime_ 开头，不与 ingestion 的 users 等表混用。请求不创建或修改数据库结构。SQLite 仅在显式 RAG_ENV=test 的测试配置中可用，不作为生产/开发降级数据库。

## 部署与回滚

1. 备份当前 PostgreSQL 和所有历史 SQLite 文件；暂停写入，记录校验和和来源。
2. 使用独立 migration 身份执行 python -m alembic upgrade head。应用运行身份只需要业务表 DML 和相关 sequence 权限，不需要 DDL。
3. 按下述工具先做旧数据 dry-run，完成人工可信租户映射后显式导入。
4. API 与 worker 配置相同 RAG_DATABASE_URL，完成 readiness 和业务检查后接流量。
5. 回滚应用前先确认它兼容共享数据库。alembic downgrade 0001_ingestion_auth 会删除全部新运行时业务表，仅可在已备份且确认放弃新数据时执行；不能当无损应用回滚使用。

本次未修改 Docker 或 Compose。数据库迁移与运行时使用同一连接配置；驱动、连接失败或迁移缺失均不回退 SQLite。

## 隔离、事务与索引

- 路由在 JWT 校验后进入 runtime_scope(auth.tenant_id, auth.user_id)。后台调用必须显式传入可信身份或建立相同作用域，缺省身份直接拒绝。
- 每张表包含 tenant_id、created_by、created_at、updated_at、deleted_at。租户过滤和软删除过滤出现在查询 SQL 中。客服运营角色可按既有 scopes 读取本租户运营数据；会话、订单和记忆同时绑定用户。
- 会话 ID、订单 ID 保留全局不可重新认领约束。会话子表通过 tenant/session/creator 复合外键绑定归属。Redis 上下文 key 同样加入 tenant/user 的稳定散列命名空间。
- PostgreSQL 使用事务级 advisory lock 序列化同租户事务，并为全局会话/订单 ID 加资源锁。订单状态检查、幂等记录、变更和审计处于同一事务。无 INSERT OR REPLACE、lastrowid 或请求路径建表；插入主键用 RETURNING。
- 当前锁粒度是保守的租户级串行事务，适合现阶段一致性优先的负载。高流量租户上线前应测量 P95、锁等待和连接池饱和；如成为瓶颈，再保持相同测试收紧到资源级写锁。锁等待上限 5 秒，单条 SQL 上限 15 秒，失败不重放非幂等写入。
- 所有业务表有 tenant/updated_at 索引；另有会话 owner/session、提示词 active、反馈 helpful、知识 status、审计 object/request 索引。可通过 PostgreSQL 慢查询和锁等待指标检查性能。
- 审计是追加型数据。保留到期前不允许业务 API 修改或删除。提示词首次未配置时返回只读内置默认值，读取不会 seed 数据或建表。

## 知识发布行为变化

旧知识运营发布会写本地全局 seed JSONL 并重建无 ACL 的演示索引，多副本和多租户下不安全。新版本将审核知识状态和发布历史在 PostgreSQL 内原子保存；回滚只回滚该租户最近一批已发布记录。

publish-approved 现在表示“发布运营知识快照”，**不会直接使正式 RAG 索引生效**。正式检索的 document/version/chunk/index 发布继续走已有 ingestion/release pipeline。export-approved 提供导入该链路所需内容。历史 backup_path、faiss_index_path 字段为兼容响应保留，新的快照不再指向机器本地文件；knowledge_path 为 postgresql:runtime_knowledge_items。调用方不能再以此接口成功推断正式索引已经重建。

## 历史 SQLite 显式导入

导入工具只读原文件，不根据同名 user_id 自动推断租户，不覆盖 PostgreSQL 已有主键，不自动认领空租户。每行必须提供来自可信业务映射的身份。关系完整性、旧 tenant 冲突或主键冲突会使整个文件导入回滚。

映射文件是 JSON 数组，key 必须完整匹配旧表的实际主键（包括原表有的 tenant_id）：

    [{"table":"conversations","key":{"session_id":"historical-session"},"tenant_id":"verified-tenant","created_by":"verified-user"}]

    python scripts/runtime_data.py import-sqlite --source data/ops_feedback.db --mapping reviewed-owners.json
    python scripts/runtime_data.py import-sqlite --source data/ops_feedback.db --mapping reviewed-owners.json --apply

先审查 dry-run 的 source_sha256、各表行数、unmapped 和未使用映射数；非零不允许 apply。同一源文件内父表先于子表导入；跨文件有关联时先导入会话父记录。无人能确认归属的旧文件保持离线隔离，不进入在线数据集。离线导入应在暂停业务写入期间进行，导入 numeric id 后工具同步 PostgreSQL sequence。记录每次输出、映射审批来源和操作人，完成数量与抽样归属核对后再接流量。

## 保留与归档

保留天数需由实际业务/合同确定，本改动不擅自永久删除数据。工具使用显式 UTC 截止时间，默认 dry-run，按 tenant 归档：

    python scripts/runtime_data.py archive --tenant verified-tenant --before 2026-01-01T00:00:00+00:00 --output backups/tenant-20260101.jsonl
    python scripts/runtime_data.py archive --tenant verified-tenant --before 2026-01-01T00:00:00+00:00 --output backups/tenant-20260101.jsonl --apply

归档先完整写出并 fsync JSONL，再以一笔事务设置 soft-delete。命中后续并发更新则回滚，归档文件仍保留供核对。会话及其消息/轮次/复核/事实作为整体处理；仅归档已送达订单、废弃提示词、归档/回滚/拒绝知识以及到期反馈/审计/请求记录。活跃提示词、有效知识、未送达订单、用户/长期记忆不会被该批处理自动删除。

归档输出包含业务数据，应保存到受控加密备份存储；验证恢复前不要清除原软删除记录。JSONL 为可审查业务归档，数据库完整灾难恢复以 PostgreSQL 备份为准。到期硬删除另行按审批策略执行，避免将“归档成功”误当作永久删除授权。

## 备份与恢复策略

- 每日 pg_dump --format=custom --file=<backup>，连接凭据使用 PGSERVICE/.pgpass 或秘密管理，不写进命令输出或仓库。备份文件加密并存放到独立故障域，保留期按业务策略设定。
- 生产采用连续 WAL 归档/托管 PostgreSQL PITR；必须另外备份对象存储和索引 manifest，数据库备份不能替代文档和索引备份。
- 恢复先建隔离数据库，用 pg_restore --no-owner --dbname=<restore-db> <backup> 恢复，核对 Alembic head、关键表数量、tenant 分布、抽样会话/订单和索引版本，再切流量。
- 至少每月执行恢复演练，记录实际 RPO/RTO。备份任务成功不能替代恢复验证；本次本地测试不声称达成生产 RPO/RTO。

## 可重复验证

    $env:RAG_TEST_POSTGRES_URL = 'postgresql+psycopg://test-user@localhost/test-db'
    python -m pytest tests/test_runtime_persistence.py -q

PostgreSQL case 在测试数据库创建随机隔离 schema，结束后仅删除该 schema。覆盖 migration upgrade/downgrade/restart、真实共享事务、并发订单幂等、tenant/user/FK 隔离、知识/提示词/反馈流程、可信导入和软归档。未设置测试服务器时 PG case 会明确 skip，不能据此宣称生产 PostgreSQL 验证通过。
