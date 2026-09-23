# RAG 改造执行进度台账

> 本文件是 `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 的执行进度记录。
> 每完成一个任务/批次就追加一条记录，不覆盖历史内容。
> 记录格式遵循计划文档第 8 节的固定输出格式。
>
> **配套文档（2026-09-23 新增，横向视图，与按批次的时间线互补）**
> - `docs/RAG_GAP_ANALYSIS_AND_ROADMAP.md` —— 距离 `docs/goal.md` 目标的差距清单、
>   按验收四档 A/B/C/D 的完成度对表、代码结构改造建议与路线图。
> - `docs/RAG_DEV_PITFALLS.md` —— 按主题汇总的踩坑与决策台账（面试复盘用），
>   A~E 节是已解决的坑（含本文件第 5 节环境坑与各条 `[D-n]` 决策记录），
>   **F 节是 2026-09-23 复核发现的 5 个尚未修复的缺口**。

## 0. 执行环境基线（开始前实测）

| 项目 | 实测结果 | 影响 |
| --- | --- | --- |
| OS / Shell | win32 / Git Bash（Windows） | 计划里的 PowerShell 命令改写为等价 bash 命令执行 |
| Python | `venv\Scripts\python.exe` = 3.12.10（满足 3.11+） | 使用项目自带 venv，不污染全局环境 |
| 已装关键依赖 | fastapi 0.136.0、pydantic 2.13.3、faiss-cpu 1.13.2、pytest 9.1.1、ruff 0.14.5、numpy 2.4.4 | 基础可用 |
| **缺失依赖** | SQLAlchemy、Alembic、PyJWT、pymupdf、python-docx、beautifulsoup4、trafilatura、redis、psycopg | 需在对应批次按需安装 |
| **Docker** | ❌ 本机无 docker 命令 | 无法真实启动 PostgreSQL 容器 |
| **PostgreSQL / psql** | ❌ 本机无 psql | 同上 |
| 现有持久化 | SQLite：`data/knowledge_ops.db`、`data/ops_feedback.db`、`data/prompt_versions.db`；检索后端 `data/faiss_store/` | 迁移不得破坏这些数据 |
| git 工作区 | 干净，仅 `docs/RAG_EXECUTION_PLAN_...md` 未跟踪 | 无用户未提交修改会被覆盖 |

### 环境偏差与落地调整（重要）

计划第 1 节要求「开发 PostgreSQL 使用 docker-compose 服务」。本机既无 Docker 也无 psql，
因此做如下等价落地，**不降低任何安全/结构要求**：

1. 数据层统一用 **SQLAlchemy 2.x + Alembic**，连接串**只从环境变量** `RAG_DATABASE_URL` 读取；
2. 默认值指向本地 SQLite 文件（`data/rag_metadata.db`，新建，不触碰既有 db）；
3. `docker-compose.yml` 中**补上 postgres + redis 服务定义**，配 `.env.example` 示例连接串，
   换到有 Docker 的机器只需改环境变量即可切到 PostgreSQL；
4. 迁移脚本按方言无关写法编写（不使用 PG 专有类型），SQLite 与 PostgreSQL 均可 `upgrade head`；
5. 测试全部跑在 SQLite（`sqlite:///:memory:` 或临时文件），保证 CI 可复现；
6. Redis Stream 队列在无 Redis 时提供**内存队列降级实现**，接口一致，生产切 Redis 只改环境变量。

以上偏离已在每个任务记录中标注，便于后续在有 Docker 的环境复验。

### ⚠️ 破坏性变更通告（阶段 1 引入，调用方必读）

**从阶段 1 起，所有受保护接口的身份只能来自 `Authorization: Bearer <JWT>`。**

- 旧的 `X-User-Role` / `X-Operator-Id` 请求头**已彻底失效**：
  只发这两个头不带令牌一律 **401**；带了令牌再发这两个头也不会提权（仍按令牌判定，403）。
  兼容期内这些头会被读取并打印 `legacy identity headers` 告警日志，仅用于排查，不参与授权。
- 令牌需包含六个 claim：`sub`（用户 id）、`tenant_id`、`roles`（数组或字符串）、
  `iss`、`aud`、`exp`。签发密钥由 `RAG_JWT_SECRET` 环境变量提供，
  缺失时受保护接口返回 **500**（服务端故障，fail closed，不会静默放行）。
- 请求体里自报的 `operator_id` / `operator_role` / `tenant_id` 一律被忽略并覆盖。
- `/docs` 已声明 `bearerAuth`，可以直接在 Swagger UI 里贴令牌联调。
- 本地起服务前请设置 `RAG_JWT_SECRET`，否则受保护接口全部 500。

## 1. 批次划分（每批次结束后写进度）

| 批次 | 覆盖任务 | 交付物 | 状态 |
| --- | --- | --- | --- |
| B1 | 阶段 0 基线 + 1.1 数据模型与迁移 | `reports/execution_baseline/*`、`alembic/*`、`services/ingestion/models.py`、`repository.py`、`tests/test_ingestion_models.py` | ✅ 完成 |
| B2 | 1.2 身份上下文（JWT）+ 阶段 1 审查 | `services/auth_context.py`、改造 `auth_service.py`/`main.py`/11 个 router、`tests/test_auth_context.py`、`tests/test_tenant_isolation.py`、`tests/conftest.py`、`tests/auth_helpers.py` | ✅ 完成 |
| B3 | 2.1 解析契约 + 2.2 五种解析器与注册表 | `services/ingestion/parsers/*`、`parser_registry.py`、fixtures、`tests/test_document_parsers.py` | ✅ 完成 |
| B4 | 2.3 上传与异步任务 API | `routers/documents.py`、`services/ingestion/{object_store,content_sniff,queue}.py`、`schemas/document_schema.py`、`tests/test_document_upload_api.py` | ✅ 完成 |
| B5 | 3.1 Chunk 配置 + 3.2 结构化切分器 | `config/chunking_config.py`、`services/ingestion/chunkers/*`、`tests/test_chunking.py` | ✅ 完成（2026-09-23） |
| B6 | 3.3 幂等流水线 + 3.4 FAISS manifest | `services/ingestion/{pipeline,worker,index_builder,index_manifest}.py`、改造 `queue.py` / `repository.py` / `utils/vector_retriever.py`、`tests/test_ingestion_pipeline.py` | ✅ 完成（2026-09-23） |
| B7 | 4.1 权限规则 + 4.2 路由改造 + 4.3 检索 API | `services/retrieval_access.py`、整体重写 `routers/retrieval.py`、改 `routers/documents.py`（+索引重建端点）/`routers/knowledge.py`/`services/auth_context.py`/`services/auth_service.py`/`schemas/retrieval_schema.py`、修 `services/ingestion/{repository,index_builder,index_manifest}.py`（B14）、`tests/{test_retrieval_isolation,test_retrieval_api,test_tenant_isolation,test_auth_context,retrieval_fixtures}.py`、`README.md` | ✅ 完成（2026-09-23） |
| B8 | 阶段 7 总审查与发布门禁 | `reports/rag_ingestion_auth_review/*`（review.txt 结论） | ⏳ 未开始 |

分批原则：一批 = 一个可独立验证、可回滚的闭环；每批结束跑一次该阶段的检查命令，
把结果写进第 3 节。**阶段审查为 NEEDS_WORK 时不进入下一批。**

## 2. 阶段审查结论汇总

| 阶段 | 结论 | 证据位置 | 时间 |
| --- | --- | --- | --- |
| 阶段 0 保存基线 | PASS | `reports/execution_baseline/review.txt` | 2026-09-23 |
| 阶段 1 数据库与身份 | **PASS** | `reports/rag_ingestion_auth_review/stage1_review.txt` | 2026-09-23 |
| 阶段 2 解析与接入 | **PASS**（2.1 / 2.2 / 2.3 全部完成） | `reports/rag_ingestion_auth_review/stage2_review.txt` | 2026-09-23 |
| 阶段 3 切分与索引 | **PASS**（3.1 / 3.2 / 3.3 / 3.4 全部完成，阶段级结论已回填） | `reports/rag_ingestion_auth_review/stage3_review.txt`（第五节回填 + `B5_task_level_review.txt` + `B6_task_level_review.txt`） | 2026-09-23 |
| 阶段 4 权限与隔离 | **PASS**（4.1 / 4.2 / 4.3 全部完成，阶段级结论见 § 五）★ **不含发布门禁** | `reports/rag_ingestion_auth_review/stage4_review.txt`（+ `B7_task_level_review.txt`） | 2026-09-23 |
| 阶段 7 发布门禁 | 待审（**已知 2 条阻断**：7.2 四格式端到端未做、7.4 索引回滚无测试） | — | — |

## 3. 任务执行记录（按时间追加）

<!-- 每次执行后在这里追加一条，不要删除旧记录 -->

### [T-0.1 / T-0.2] 阶段 0：保存基线

任务编号：0.1 + 0.2
修改文件：
- `reports/execution_baseline/backend-status.txt`（git status 快照）
- `reports/execution_baseline/backend.patch`（git diff 快照）
- `reports/execution_baseline/backend-head.txt`（HEAD 提交号）
- `reports/execution_baseline/test-output.txt`（基线 pytest 输出）
- `reports/execution_baseline/ruff-output.txt`（基线 ruff 输出）
- `reports/execution_baseline/data-size-output.txt`（基线仓库体积检查）
- `reports/execution_baseline/review.txt`（阶段 0 审查结论）

执行命令（等价 bash 改写）：
```
mkdir -p reports/execution_baseline
git status --short > reports/execution_baseline/backend-status.txt
git diff --binary > reports/execution_baseline/backend.patch
git rev-parse HEAD > reports/execution_baseline/backend-head.txt
python -m pytest -q 2>&1 | tee reports/execution_baseline/test-output.txt
python -m ruff check . 2>&1 | tee reports/execution_baseline/ruff-output.txt
python scripts/check_repo_data_size.py 2>&1 | tee reports/execution_baseline/data-size-output.txt
```

测试结果：
- pytest：`252 passed, 3 warnings, 4 subtests passed in 21.79s`，退出码 0
  （3 条 warning 均为 faiss SWIG 绑定的 DeprecationWarning，与业务无关）
- ruff：`All checks passed!`，退出码 0
- check_repo_data_size：`结果：通过，没有超标文件。`（上限 1.0 MB）
- **基线零失败**，后续任何失败都是新引入的，不得归因于历史遗留

验收结果：PASS
未解决问题：无
下一任务：1.1 建立数据模型和迁移

### [T-1.1] 阶段 1：建立数据模型和迁移

任务编号：1.1
修改文件（新增）：
- `alembic.ini` —— Alembic 配置；`sqlalchemy.url` 故意留空，连接串由 env.py 解析
- `alembic/env.py` —— 连接串解析顺序：`RAG_DATABASE_URL` 环境变量 → 配置文件 → 本机 SQLite 兜底
- `alembic/script.py.mako` —— 迁移模板
- `alembic/versions/0001_ingestion_auth.py` —— 建 13 张表 + 全部索引/约束，含 downgrade
- `services/ingestion/__init__.py`
- `services/ingestion/db.py` —— 引擎/会话工厂（**计划外新增的支撑模块**，见下方说明）
- `services/ingestion/models.py` —— 13 张表的 SQLAlchemy 2.x 模型 + 常量
- `services/ingestion/repository.py` —— 事务性读写函数
- `tests/test_ingestion_models.py` —— 19 个测试

修改文件（已存在）：
- `ruff.toml` —— 为 `alembic/*` 增加 E402 豁免（env.py 需要先插 sys.path 再 import 项目模块）
- `.env.example` —— 新增 `RAG_DATABASE_URL` / JWT / 对象存储 / Redis 配置项
- `docker-compose.yml` —— 补 postgres:16 + redis:7 服务与健康检查、volumes
- `requirements.txt` / `requirements-dev.txt` —— 新增 sqlalchemy / alembic / PyJWT

执行命令：
```
alembic upgrade head                                    # 通过 RAG_DATABASE_URL 指定目标库
python -m pytest tests/test_ingestion_models.py -q
python -m pytest -q                                     # 全量
python -m ruff check .
python -m compileall services routers schemas models alembic
python scripts/check_repo_data_size.py
```

测试结果（含 [D-1] 修订后的最终状态）：
- `tests/test_ingestion_models.py`：**20 passed in 2.61s**
- 全量：**272 passed, 3 warnings, 4 subtests passed in 10.06s**（基线 252 + 新增 20，无回归）
- `ruff check .`：All checks passed
- `compileall`：退出码 0
- `check_repo_data_size`：通过
- `alembic upgrade head` 连续执行两次：第一次建表，第二次空操作，**幂等通过**
- 迁移后 `documents` 表实测约束：
  `CONSTRAINT uq_documents_tenant_id_source_uri UNIQUE (tenant_id, source_uri)`

**迁移产物与模型定义逐项对照实测**（见 `tests/test_ingestion_models.py::test_migrated_schema_matches_model_metadata`）：
表名、列名、唯一约束名、外键名、索引名、CHECK 约束名 **全部一致**，
实测输出（开发期核对）示例：
```
[uq] document_versions db==md  ['uq_document_versions_tenant_id_content_hash',
                                'uq_document_versions_tenant_id_document_id_version',
                                'uq_document_versions_tenant_id_id']
[fk] document_chunks  db==md  ['fk_document_chunks_document_version_id_parent_chunk_id',
                               'fk_document_chunks_tenant_id',
                               'fk_document_chunks_tenant_id_document_version_id']
[ck] ingestion_jobs   db==md  ['ck_ingestion_jobs_retry_count_non_negative',
                               'ck_ingestion_jobs_stage_valid', 'ck_ingestion_jobs_status_valid']
```

验收结果：PASS
未解决问题：
1. **本机无 Docker / 无 psql**，`alembic upgrade head` 只在 SQLite 上实测过。
   PostgreSQL 路径已通过「方言无关迁移写法 + docker-compose 服务定义 + 环境变量连接串」准备好，
   但**尚未在真实 PostgreSQL 上复验**，属于待办风险项（不影响 SQLite 下的全部断言）。
2. 计划列出的文件清单里没有 `db.py`。它是引擎/会话工厂，被 repository、后续的 pipeline
   和 API 层共用，放在 repository 里会让职责混淆，因此单独成模块。已在此显式标注为计划外新增。
3. ~~`documents.source_uri` 的唯一性是 `(tenant_id, knowledge_base_id, source_uri)`，
   计划未细化粒度，待用户确认。~~ **已定论并修订，见下方 [D-1] 决策记录。**

下一任务：1.2 定义身份上下文并替换信任 Header（批次 B2）

### [T-1.2] 阶段 1：定义身份上下文并替换信任 Header

任务编号：1.2
修改文件（新增）：
- `services/auth_context.py` —— 纯逻辑层：不可变 `AuthContext`、JWT 校验、角色→scope 策略表
- `tests/conftest.py` —— 强制固定鉴权环境变量，保证测试自洽可复现
- `tests/auth_helpers.py` —— 测试自签 JWT / 直接构造 AuthContext
- `tests/test_auth_context.py` —— 45 个测试（配置解析 / 令牌校验 / AuthContext / HTTP 层 / 策略一致性）
- `tests/test_tenant_isolation.py` —— 14 个测试（跨租户不泄漏存在性、参数不能改授权范围）

修改文件（重写）：
- `services/auth_service.py` —— 从「读 header 的 dict 工厂」改为「FastAPI 依赖层」：
  `get_auth_context` / `get_request_meta` / `require_*_operation_role`（改为 scope 判定）

修改文件（改造）：
- `main.py` —— lifespan 启动自检鉴权配置 + OpenAPI 声明 bearerAuth
- 11 个受保护 router：audit / chat / example / feedback / info / knowledge / ops /
  order / prompt / release / retrieval —— 全部改为显式接收 `AuthContext`
- 8 个既有测试文件：test_audit_and_release / test_chat_api / test_feedback_ops /
  test_knowledge_ops / test_prompt_ops / test_read_auth_coverage /
  test_release_smoke_flow / test_retrieval_api —— 从 X-User-Role 迁移到 JWT
- `ruff.toml` —— `tests/conftest.py` 增加 E402 豁免（需先插 sys.path）

执行命令：
```
python -m pytest tests/test_ingestion_models.py tests/test_auth_context.py tests/test_tenant_isolation.py -q
python -m ruff check .
python -m pytest -q
python -m compileall services routers schemas models alembic main.py
python scripts/check_repo_data_size.py
```

测试结果：
- 阶段指定三项：**79 passed in 3.45s**
- 全量：**332 passed, 3 warnings, 4 subtests passed in 9.82s**
  （基线 252 → 现在 332，本阶段新增 80 个测试，**零回归**）
- ruff：All checks passed；compileall：OK；体积检查：通过
- 真实应用端到端冒烟（对 `main.app` 直打）：health 200 / 无令牌 401 /
  仅伪造旧头 401 / admin 令牌 200 / agent 令牌 403 / 过期令牌 401

验收结果：PASS
阶段结论：**PASS**（完整审查见 `reports/rag_ingestion_auth_review/stage1_review.txt`）

未解决问题：
1. **破坏性接口变更**：仍按 `X-User-Role` / `X-Operator-Id` 调用的旧客户端会全部 401，
   需要换成 Bearer 令牌。兼容期日志会打印 legacy headers 告警便于排查。
2. 令牌**签发**方尚未实现（本期只做校验）。外部签发方需提供
   `sub` / `tenant_id` / `roles` / `iss` / `aud` / `exp` 六个 claim。
3. scope 词汇表沿用既有 operation 名（`read:` / `write:` / `review:` 前缀）。
   阶段 4.1 引入 `knowledge_base:read` / `document:upload` / `index:rebuild` 时
   在 `auth_context.py` 扩展，调用方无需改动。
4. PostgreSQL 上的迁移与鉴权复验仍未做（本机无 Docker），归入阶段 7 发布门禁。

下一任务：2.1 定义解析契约（批次 B3）

### [D-2] 决策记录：scope 由服务端推导，token 自报权限一律忽略

计划 1.2 只要求「校验 JWT」，没有规定 token 里的角色与权限怎么用。本阶段定为：

* token 里的 `roles` claim **被信任**（毕竟是我们自己签发的），
  但服务端只把它当作「你是谁」，**权限（scopes）一律由服务端按 roles 推导**；
* token 里若出现 `scopes` / `permissions` claim，**完全忽略**。

理由：如果采信 token 自报的 scopes，那么任何持有合法令牌的人（或任何签发配置宽松的
OIDC Provider）都能自报权限提权，等于把授权决策外包给了令牌内容。
现在「谁签发的令牌」只决定身份，「身份对应什么权限」完全由本服务决定，
权限变更不需要重新签发令牌，也不必信任外部系统的权限声明。

已加负向测试：`test_token_supplied_scopes_are_ignored`（纯逻辑层）
与 `test_token_supplied_scopes_cannot_elevate_privileges`（HTTP 层）。

### [D-3] 决策记录：ROLE_SCOPES 由授权表推导，不手工维护

`ROLE_SCOPES`（角色 → scope 集合）由 `READ_OPERATION_ROLES` / `WRITE_OPERATION_ROLES` /
`REVIEW_ACTION_ROLES` 三张表的 inversion 自动生成，并有测试断言两者严格同构。

理由：手工维护两份（表 + 推导结果）必然漂移。现在新增接口只加表项一处，
权限视图自动同步；测试 `test_role_scopes_is_exact_inversion_of_policy_tables`
会在有人破坏这个不变量时立刻失败。

### 关键设计取舍（供后续批次参考）

| 取舍 | 决定 | 理由 |
| --- | --- | --- |
| 主键 | 32 位 uuid hex 字符串 | 对象存储 key 也用同一 ID，避免暴露自增序号；SQLite/PG 行为一致 |
| 时间 | naive UTC（`DateTime` 无时区） | SQLite 不存时区，带 tzinfo 会导致两种后端比较结果不同 |
| 跨租户隔离 | 复合外键 `(tenant_id, parent_id)` | 写错租户时由数据库报错，而不是靠业务代码自觉 |
| SQLite 外键 | 连接级 `PRAGMA foreign_keys=ON` | SQLite 默认关闭外键，不打开则跨租户外键测试形同虚设 |
| 重复文件 | `uq_document_versions(tenant_id, content_hash)` | 数据库级保证「重复文件不产生第二份有效版本」 |
| 枚举字段 | `String` + CHECK 约束 | 可移植；PG 原生 enum 改动成本高、迁移不可逆 |

### [D-1] 决策记录：`documents.source_uri` 唯一性粒度

**决策：`uq_documents(tenant_id, source_uri)`（租户粒度），修订原三列设计。**

背景：B1 初版写成 `(tenant_id, knowledge_base_id, source_uri)`，看似更灵活，
实际**与既有的重复文件抑制逻辑自相矛盾**：

- `document_versions` 上有 `uq(tenant_id, content_hash)`，即租户内相同内容只允许一份版本；
- 同一文件 F 上传到 KB1 后，再上传到 KB2 时三列约束会放行新建文档 D2，
  但 D2 建版本时必然撞上 content_hash 唯一约束；
- 结果是两条坏路：要么 `IntegrityError`，要么按「重复文件标记 duplicate」生成一个
  **永远没有内容的僵尸文档**。

可逆性对比（决定性判据）：

| 方案 | 以后要变更时的代价 |
| --- | --- |
| 两列（现选） | 要支持跨库共用，加 `knowledge_base_documents` 关联表 —— 纯增量，不动现有数据 |
| 三列（原设计） | 要合并重复副本时，chunk / embedding / ACL / 审计已经各自分叉，**无法判定该留哪份** |

附带收益：两列方案给出正确语义 —— `source_uri` 唯一标识「这份文件这个逻辑实体」，
重传同一 URI 应**追加版本**而非新建文档，正好对上 `documents`/`document_versions` 的分层本意。
三列方案下同一份政策文件会形成两条独立版本链，早晚漂移出互相矛盾的答案。

落地内容：
- `services/ingestion/models.py`、`alembic/versions/0001_ingestion_auth.py` 同步改为两列约束
- `repository.get_document_by_source_uri` 去掉 `knowledge_base_id` 参数
- 新增契约测试 `test_reupload_same_source_uri_appends_version_not_new_document`，
  锁定「重传 → 追加版本」这一行为，供阶段 2.3 上传接口与阶段 3.3 流水线实现时对齐

修订 0001 而非追加 0002 的依据：`data/rag_metadata.db` 不存在，0001 从未落到任何正式库，
只在临时 SQLite 上验证过；且尚未提交。**若 0001 已被部署，则必须改为追加新迁移。**

后续待办：若真的出现「同一份文件需被两个知识库共用」的需求，
新增 `knowledge_base_documents(document_id, knowledge_base_id)` 关联表，
并把 `documents.knowledge_base_id` 降级为「归属库」概念，不要回退到多副本方案。

验证结果：`tests/test_ingestion_models.py` **20 passed**、全量 **272 passed**、ruff 通过。

### [T-2.1] 阶段 2：定义解析契约

任务编号：2.1
修改文件（新增）：
- `services/ingestion/parsers/__init__.py`
- `services/ingestion/parsers/base.py` —— 解析契约本尊
- `services/ingestion/parsers/_text.py` —— 私有文本工具（**计划外新增**，见下方说明）

新增契约内容：

| 名称 | 内容 |
| --- | --- |
| `DocumentParser` | `runtime_checkable` 协议：`supported_types` / `parser_name` / `parser_version` / `parse(source, filename)` |
| `ParsedDocument` | `title` / `blocks[]` / `metadata` / `warnings[]` + 自描述三字段 |
| `Block` | `type` / `text` / `order` / `page` / `heading_path` / `table_json` / `image_ref` |
| `ParseWarning` | `code` / `message` / `detail`，可 JSON 直存 |
| `ParserError` | `error_code` / `message` / `parser_name` / `filename` / `detail`，带 `to_dict()` |
| `TemplateDocumentParser` | 模板基类：统一算 hash、包错误、重编号、title 兜底、产物自检 |

执行命令：
```
python -m pytest tests/test_document_parsers.py -q
python -m pytest -q
python -m ruff check .
python scripts/check_repo_data_size.py
```

测试结果：见 [T-2.2]（同一批交付，测试文件合并统计）

验收结果：PASS

关键实现决策（都已写进 `base.py` 的 docstring，并在测试里锁定）：

1. **失败用抛异常而不是返回错误对象**。计划写的是「失败返回结构化错误，不能吞异常」。
   做成返回值会留出「调用方忘记检查」的口子，而忘记检查的后果是把空文档当成功结果
   送进索引 —— 这是**静默的数据损坏**，比一次显式失败严重得多。
   因此定为「抛 `ParserError`（结构化、带稳定 error_code）」。
2. **未预期异常必须保留 `__cause__`**。模板用 `raise ParserError(...) from exc` 包装，
   测试 `test_unexpected_exception_is_wrapped_without_being_swallowed` 断言
   `excinfo.value.__cause__` 是原始异常。只包不链等于吞掉 traceback。
3. **`heading_path` 的语义固定下来**：标题块**包含自己**，正文块是当前标题栈。
   这样切分器（3.2）只看 block 就能分组，不必重建层级。
4. **`content_hash` 由模板统一写入 `metadata`**（与第 4 节的既有约定一致），
   解析器不各自算，避免同一文件在不同解析路径下算出不同 hash 让重复抑制失效。
5. **产物自检前置**：模板返回前跑 `validation_errors()`（表格块必须有 `table_json`、
   图片块必须有 `image_ref`、`order` 必须从 0 连续、metadata 必须可 JSON 序列化），
   违反即抛 `parse_failed`。测试 `test_invalid_produced_document_is_rejected` 锁定。

计划外新增 `_text.py` 的说明：编码回退链与分段逻辑被 plain_text / markdown / html
三个解析器共用。若各写一份，同一个 GBK 文件在不同格式下可能得到不同乱码结果，
而这种不一致表现为「检索不到那份文档」，极难排查。放私有模块（下划线前缀）
表明它是实现细节，不进入契约面。

### [T-2.2] 阶段 2：实现格式解析器和注册表

任务编号：2.2
修改文件（新增）：
- `services/ingestion/parsers/plain_text.py`
- `services/ingestion/parsers/markdown.py`
- `services/ingestion/parsers/html.py`
- `services/ingestion/parsers/pdf.py`
- `services/ingestion/parsers/docx.py`
- `services/ingestion/parsers/ocr.py`
- `services/ingestion/parser_registry.py`
- `scripts/make_parser_fixtures.py` —— 样本生成器（**计划外新增**，见下方说明）
- `tests/fixtures/sample.md` / `sample.html` / `sample.docx` / `sample.pdf`
- `tests/test_document_parsers.py` —— 116 个测试
- `.gitattributes` —— 把两个文本样本锁成 LF（**计划外新增**，见下方说明）

修改文件（已存在）：
- `services/ingestion/__init__.py` —— 导出 `parsers` / `parser_registry`
- `requirements.txt` / `requirements-dev.txt` —— 新增 pymupdf / python-docx / beautifulsoup4 / trafilatura

各解析器的关键行为：

| 解析器 | 结构还原要点 |
| --- | --- |
| `plain_text` | 按空行分段、**保留段内换行**；识别 ASCII 换页符 `\f` 产生页码；不猜标题（猜错标题会污染标题路径分组） |
| `markdown` | ATX + Setext 标题、围栏代码块、GFM 表格、有序/无序列表、引用、独立图片；行内标记清洗但代码块原样保留 |
| `html` | 先删 script/style/nav/footer/header/aside/form/iframe/svg 等噪声，再按文档顺序遍历；`pre`→代码块、`img`→图片引用；结构提取不足时降级 trafilatura |
| `pdf` | 按 PyMuPDF block（≈段落）成块并带页码；用字号分布推断正文字号再识别标题层级；`find_tables()` 的结果从正文块剔除；文本层不足才整页渲染送 OCR |
| `docx` | 按 `w:body` 子元素顺序遍历（只读 `document.paragraphs` **看不到表格**），并下钻 `w:sdt`；标题层级三路兜底（style.name → style_id → `w:outlineLvl`）；列表靠 `w:numPr` / 样式名；图片记 `docx:<rId>` 引用 |

OCR 触发条件（用注入的假引擎测试，不需要真装 rapidocr）：

| 场景 | 行为 |
| --- | --- |
| 文本层充足 | **不调用** OCR（对已能取到文字的页面做 OCR 只会引入识别错误），`pdf_ocr_used=False`，无警告 |
| 文本层低于阈值（默认 32 字符）且引擎可用 | 整页渲染 2 倍 PNG → OCR，产出正文块，`pdf_ocr_used=True`，无警告 |
| 文本层低于阈值且引擎不可用 | **不报错**，写 `no_text_layer` 警告（`detail.ocr_engine=None`） |
| 引擎可用但识别为空 | 同样写 `no_text_layer` 警告，`detail.ocr_engine` 记引擎名 |

「引擎不可用时不报错」是有意的：文件已经存进对象存储了，让任务失败只会让用户
反复重传一个必然失败的文件；正确做法是入库成功 + 一条可查询的警告。

执行命令：
```
python -m pytest tests/test_document_parsers.py -q
python -m pytest -q
python -m ruff check .
python -m compileall services routers schemas models alembic main.py scripts
python scripts/check_repo_data_size.py
```

测试结果：
- `tests/test_document_parsers.py`：**116 passed in 1.06s**
- 全量：**448 passed, 5 warnings, 4 subtests passed in 6.67s**
  （B2 结束时 332 → 现在 448，本批新增 116 个测试，**零回归**）
- `ruff check .`：All checks passed
- `compileall`：退出码 0
- `check_repo_data_size`：通过（四个样本 767 B / 1,357 B / 37,210 B / 4,472 B，均远低于 1 MB）
- warning 由 3 条增至 5 条：新增两条来自 `fastapi.testclient` / `starlette.testclient`
  的 DeprecationWarning（本机依赖升级后出现），与本次改动无关，无 `-W error` 门槛。

样本文件实测解析结果（开发期核对用，摘录）：

```
sample.md  title=客户服务知识库测试文档 blocks=12
  [5] table  hp=('客户服务知识库测试文档','退款政策')
      columns=['订单状态','处理时限','责任人'] rows=[['待发货','1 个工作日','客服组'],['已发货','3 个工作日','物流组']]
  [11] image hp=(...,'抬头修改') img=images/invoice-sample.png
sample.pdf title=年度服务报告 pages=2
  [0] heading p=1 hp=('年度服务报告',)                 # 字号 20 / 正文 11
  [2] heading p=1 hp=('年度服务报告','营收概览')        # 字号 15 / 正文 11
  [4] table   p=1 hp=('年度服务报告','营收概览')        # 表格文字未在正文块里重复出现
  [5] heading p=2 hp=('年度服务报告','下半年计划')
sample.docx title=员工手册（DOCX 样本） blocks=11
  [9] heading hp=('员工手册（DOCX 样本）','休假制度','年假天数')   # 三级嵌套正确
sample.html title=客户服务手册（HTML 样本） blocks=10
      站点页眉 / 导航 / 广告位 / 版权 / script / style 均未进入结果
```

验收结果：PASS

计划外新增 `scripts/make_parser_fixtures.py` 的说明：计划只要求「新增 fixture 四个文件」。
但样本一旦提交为二进制，就没人知道里面是什么、也没法重新生成（PDF 的字号、DOCX 的
样式名都是断言依据）。改为「内容写死在脚本里 + 脚本生成样本」，好处是样本可复现、
断言依据人可读、并且脚本末尾会校验体积不超过 1 MB（与 CI 门槛同源）。
测试 `test_text_fixtures_match_the_generator_script` 断言文本样本与脚本常量一致，
防止有人手工改样本后被重新生成覆盖、让断言悄悄失效。

计划外新增 `.gitattributes` 的说明：本机 `core.autocrlf=true`，样本文件在别人机器上
会被检出成 CRLF，届时「与生成脚本逐字节一致」的断言会以一个和它想守的东西
（内容是否被改过）毫无关系的原因失败。因此把两个文本样本的 `eol` 锁成 LF。
二进制样本（docx / pdf）不受影响。

未解决问题：
1. **PDF 标题识别是启发式的**（字号倍数 + 长度 + 句末标点）。会误判，但只影响
   `heading_path` 的丰富程度，不丢内容。若后续发现有价值的 PDF 标题层级还原不准，
   可考虑引入 `pymupdf_layout`（PyMuPDF 运行时会打印推荐提示）。
2. **扫描件 OCR 未在真实引擎上跑过**：本机默认不装 `rapidocr-onnxruntime`，
   所有 OCR 触发逻辑都用注入的假引擎覆盖。真实引擎的接入效果归入阶段 7 门禁复验。
3. `html` 的列表项每项单独成块，而 `markdown` 的连续列表合成一个块。两者语义不同
   （`<li>` 是显式结构，Markdown 列表是纯文本推断），因此未强行统一；
   切分器（3.2）需要同时处理这两种形态。
4. PDF 表格识别依赖 PyMuPDF `find_tables()` 的 lines 策略，只靠空格对齐的
   「表格」不会被识别（这符合预期）；识别失败时不中断解析。

下一任务：2.3 增加上传和异步任务 API（批次 B4）

### [D-4] 决策记录：解析产物自带 `parser_name` / `parser_version` / `source_type`

计划 2.1 只列出 `ParsedDocument` 的四个字段（`title` / `blocks` / `metadata` / `warnings`），
本批额外加了三个自描述字段。

理由：`document_versions` 表需要落 `parser_name` 与 `parser_version`，而流水线（3.3）
在「解析成功」与「写库」之间可能失败重试、可能换 worker 执行。
如果这几个值只挂在解析器对象上，重启后就必须靠 registry 再猜一次
（而 registry 是可被覆盖的，测试里就换过）。让产物自带身份，
一条历史版本记录就能脱离运行环境被解释。

### [D-5] 决策记录：解析器模块禁止依赖数据库，用测试而不是纪律来保证

计划 2.1 要求「解析器不得写数据库」。这条要求如果只写在文档里，
下一次「顺手在解析器里查一下文档是否已存在」就会破功。

因此落成两条可执行断言：
- `test_parser_modules_do_not_depend_on_database`：扫描 `services/ingestion/parsers/*.py`
  源码，出现 `sqlalchemy` / `services.ingestion.repository` / `services.ingestion.db` 即失败；
- `test_parsers_do_not_accept_tenant_id`：断言每个解析器的 `parse` 签名恰好是
  `(source, filename)`，从接口层面杜绝「顺手把 tenant_id 传进来」。

第二条同时也是对阶段 1「解析器与仓储层都不接收 tenant_id」的延续确认。

### 阶段 2 补充的关键设计取舍

| 取舍 | 决定 | 理由 |
| --- | --- | --- |
| 解析失败 | 抛 `ParserError`（结构化） | 返回错误对象会留出「忘记检查」的口子，后果是空文档静默进索引 |
| 表格表示 | 只有一种形状 `{columns, rows}`，跑齐每行长度 | 三个解析器各自发明一种 dict 形状，下游就得写三套读取逻辑 |
| 表格文本 | 同时产出可检索文本（` \| ` 连接，首行表头） | 只存 JSON 则向量检索永远匹配不到单元格内容 |
| 编码回退链 | 集中在 `_text.py`，BOM 优先，最后 `latin-1` 兜底 | 三个解析器各写一份会导致同一文件在不同格式下解出不同乱码 |
| OCR 引擎缺失 | 降级为警告，不中断入库 | 文件已在对象存储，让任务失败只会让用户反复重传必然失败的文件 |
| OCR 触发 | 「低于阈值」而非「为空」 | 只识别出页眉页脚的扫描件文本量很少但非零，只判空会永远不触发 |
| 扩展名 vs MIME | 扩展名永远优先，MIME 仅兜底 | 反过来会让「报表.pdf + Content-Type: text/html」绕过基于扩展名的准入规则 |
| 解析器版本 | 每个解析器自带 `parser_version` | 解析行为变化时能识别「同一文件的旧解析结果」，为重建索引提供依据 |

### [T-2.3] 阶段 2：增加上传和异步任务 API

任务编号：2.3
修改文件（新增）：
- `routers/documents.py` —— 五个端点（上传 / 详情 / 版本 / 重处理 / 任务查询）
- `services/ingestion/object_store.py` —— `ObjectStore` 接口 + `LocalObjectStore` +
  对象 key 与 `source_uri` 寻址 + 文件名净化
- `services/ingestion/content_sniff.py` —— magic bytes 内容嗅探与一致性判定
- `services/ingestion/queue.py` —— 任务投递（Redis Stream + 内存降级）
- `schemas/document_schema.py` —— 上传 / 详情 / 版本 / 任务响应模型
- `scripts/export_openapi.py` —— OpenAPI 导出（**计划外新增**，见下方说明）
- `tests/test_document_upload_api.py` —— **107 个测试**
- `reports/rag_ingestion_auth_review/{B4_task2_3_test-output.txt, B4_stage2-check.txt,
  B4_full_test-output.txt, B4_followup_review.txt, openapi.json}` —— 本批证据
  （`B4_followup_review.txt` 为回头审查补测后的门禁重跑记录，见 [D-8]）

修改文件（已存在）：
- `main.py` —— 注册 `documents.router`（路由内含两个前缀，故 `include_router` 不加 prefix）
- `services/ingestion/__init__.py` —— 导出三个新模块
- `requirements.txt` —— 新增 `python-multipart==0.0.32`、`redis==6.4.0`
- `requirements-dev.txt` —— 新增 `python-multipart==0.0.32`（**redis 刻意不入 dev，见 [D-7]**）
- `tests/test_ingestion_models.py` —— 按 [D-6] 修正契约测试 docstring 的责任划分
  （**只改注释，断言与语义未动**）
- `.env.example` —— **无需改动**：B1 已预留 `RAG_OBJECT_STORE_DRIVER` /
  `RAG_OBJECT_STORE_LOCAL_ROOT` / `RAG_UPLOAD_MAX_MB` / `RAG_REDIS_STREAM_URL` /
  `RAG_INGESTION_STREAM_KEY` / `RAG_INGESTION_CONSUMER_GROUP` 全部键名，本批直接沿用

**没有新增 Alembic 迁移**：B4 不改表结构（版本仍由流水线创建、解析警告仍放
`document_versions.metadata_json`），因此 0001 之后没有 0002。

执行命令：
```
python -m pytest tests/test_document_upload_api.py -q
python -m pytest -q
python -m ruff check .
python -m compileall services routers schemas models alembic main.py scripts
python scripts/check_repo_data_size.py
python scripts/export_openapi.py
```

测试结果：
- `tests/test_document_upload_api.py`：**107 passed**（实测 7.4 ~ 7.6 s）
- 全量：**555 passed, 5 warnings, 4 subtests passed**（实测 12.9 ~ 13.5 s）
  （B3 收尾基线 448 → 现在 555，本批新增 107 个测试，**零回归**）
  注：本批首轮收尾为 100 / 548，回头看时补了 7 条成员角色矩阵用例后重跑为
  107 / 555，重跑证据见 `B4_followup_review.txt`，补记见下方 [D-8]。
- `ruff check .`：All checks passed
- `compileall`：退出码 0
- `check_repo_data_size`：通过（`openapi.json` 181 KB，低于 1 MB 上限）
- warning 仍为 **5 条**，与 B3 收尾完全一致（未引入新的 DeprecationWarning；
  实现期发现 `HTTP_413_REQUEST_ENTITY_TOO_LARGE` 已被 starlette 标记废弃，
  改用 `HTTP_413_CONTENT_TOO_LARGE`）

验收结果：PASS

对接文档第 8 节验收清单逐条核对：

- [√] 五个端点全部实现，且每个受保护 handler 显式接收 `AuthContext`
      （`Depends(get_auth_context)`，业务层不从 header 自行读身份）
- [√] 上传扩展名白名单直接取 `parser_registry.ALLOWED_EXTENSIONS`（10 项全部有参数化用例）
- [√] 上传 50 MB 上限，且为**流式累计**：分块读取、超限立即中止（测试覆盖
  「超一字节即 413」「恰好等于上限放行」「上限配置写错报 500」）
- [√] `filename` 只作展示，对象 key = `rag/<tenant>/<document_id>/<净化文件名>`，无 shell 拼接
- [√] 内容嗅探已实现，且「不确定即放行」（强特征格式才校验内容，见 [D-7]）
- [√] `tenant_id` 全程只来自 `AuthContext`；跨租户访问返回 404 且与「不存在」
      **逐字节相同**（知识库 / 文档 / 版本 / 任务四类资源各自一条断言）
- [√] 上传不解析、不切分、不做内容 hash 重复判定（AST 级守卫测试同时断言
      「不 import 具体解析器」「不调用 `parse_document`」「不 import `hashlib`」）
- [√] `ingestion_jobs` 初值 `pending` / `received`（不是 `stored`）
- [√] 写操作全部写 `audit_events`（上传与重处理各一条断言，含 actor / tenant /
      action / resource / request_id / 脱敏摘要）
- [√] 覆盖：不支持扩展名 415 / 内容与扩展名不一致 415 / 超大文件 413 / 无权限 401·403 /
      成功创建任务 202 / 任务失败可查询 / 可重试（另补：跨租户不可区分、审计脱敏、
      拒绝时不产生孤儿对象、OpenAPI 端点齐全、对象存储与队列的单元测试、
      **成员角色矩阵真值表**——owner/editor 放行、reviewer/viewer 拒绝，见 [D-8]）
- [√] 新增依赖同步：`python-multipart` 两侧都有；`redis` 只进 `requirements.txt`
      并已说明理由（见 [D-7]）
- [√] 起点 448 passed，本批结束后 **555 passed，零回归**
- [√] ruff / compileall / 体积检查全部通过
- [√] 台账追加 `[T-2.3]` + `[D-6]` + `[D-7]`；`stage2_review.txt` 已回填**阶段级结论**
      （含对接文档 6.2 要求的「duplicate 标记实现位置」说明段）

计划外新增 `scripts/export_openapi.py` 的说明：计划 2.3 要求「OpenAPI 导出更新」，
但仓库里原本没有任何导出产物，也不存在导出入口，只有 `main.py` 里运行时构造的
`_custom_openapi()`。补一个脚本让这件事**可复现**：接口增删在评审材料里是一段 diff，
而不是「口头说明新增了哪些端点」。端点齐全性本身由测试断言，
脚本只负责导出，不做判定。

未解决问题：
1. **worker 尚未实现**（阶段 3.3）。上传后 job 会停在 `pending`，
   队列只完成「投递」，没有消费端；`xreadgroup` / `xack` 与消费者组创建
   一并留给 worker，以免现在就把「消息被谁读走」的语义固定下来。
2. **Redis 分支没有连过真实 Redis**：本机无 Redis 服务，Redis Stream 实现用
   注入的假客户端断言 `XADD` 参数与 `XLEN`；「配了 URL 却没装客户端」这条路径
   用 `sys.modules["redis"] = None` 模拟。真实 Redis 上的行为归入阶段 7 门禁复验。
3. **同名重传会覆盖同一个对象 key**（同一份文档的新修订）。取舍与理由见 [D-7]，
   流水线必须在拿到 job 后尽快读取字节；若将来要求保留历史修订的原始字节，
   需要给「每次上传」一个独立的 key，那要动 job 表结构（新增列走迁移）。
4. 计划 7.2 的「四种文件端到端链路」要求 chunk / manifest / 检索结果都能由
   `document_id` 串联，这依赖阶段 3 的切分与索引，本阶段只能覆盖其中的**上传环节**；
   完整链路验收归入阶段 3 与阶段 7。

下一任务：3.1 定义 Chunk 配置（批次 B5）

### [D-6] 决策记录：上传接口的三项契约（对接文档第 6 节）

**6.1 ★ `document_version` 由谁创建 → 由流水线创建，上传接口不建版本。**

上传只建 `documents`（按需）+ `ingestion_jobs`，**不建 `document_versions`**，理由：

1. 内容 hash 的重复**判定**本来就归流水线（台账第 4 节原文），上传不建版本 =
   上传期不需要做这个判定，两条要求自洽；
2. `parser_name` / `parser_version` 只有解析之后才知道（[D-4] 让产物自带这两个字段
   就是为了这一刻），上传期建版本只能先写空串再回填，等于制造一次无意义的 UPDATE；
3. `ingestion_jobs.document_version_id` 是**可空列** —— 现有 schema 已经为
   「建 job 时版本还不存在」留好了位置，说明这个顺序是被预期的；
4. 上传期建版本会撞 `uq_document_versions(tenant_id, content_hash)`：
   「内容相同、文件名不同」的两次上传会各自新建文档，第二个版本必然 `IntegrityError`
   （或产生一个永远没有内容的僵尸文档）。

**与 `test_reupload_same_source_uri_appends_version_not_new_document` 的对应关系：**
该契约测试的**语义不变**（同一 URI 只对应一份文档、版本号在文档内连续递增），
但它原来的 docstring 把「命中就加版本」写成了上传接口的行为 —— 那与本次决策冲突。
因此**只改 docstring**，明确「上传负责复用文档、流水线负责建版本」，
断言与语义一字未动。这样它同时对上传接口（2.3）与流水线（3.3）成立。

**6.2 「重复文件标记 `duplicate`」的实现位置 → 机制在 2.2，判定在 3.3。**
（该说明已写进 `stage2_review.txt` 的阶段级结论，避免按计划字面把 2.2 判成缺口。）

**6.3 上传 / 重处理用哪个 scope 与 operation 名 → 复用授权表已有键。**

- 上传 → `knowledge_create`（写）：语义是「在知识库里建内容」；
- 读（详情 / 版本 / 任务）→ `knowledge_read`；
- 重处理 → `knowledge_rollback`：重跑会重放解析与索引，**必须比上传更严**。
  授权表里角色集合严格窄于 `knowledge_create` 的唯一写操作就是它
  （`{supervisor, admin}` ⊂ `{supervisor, knowledge_ops, admin}`），
  并有测试断言 `reprocess_roles < upload_roles`。
  `document:upload` / `index:rebuild` 这类资源级枚举属于阶段 4.1，现在还不存在，
  自己编名字会命中「未登记的 operation 不授予任何人」而直接 403。

### [D-7] 决策记录：B4 的四个实现取舍

**1. `source_uri` 与对象 key 是两个概念，必须分开（否则「重传追加版本」会坏掉）。**

> **与对接文档的关系（回头审查时补的显式标注）**：对接文档 3.1 第 7 步只说
> 「用 `get_document_by_source_uri` 命中则复用」，**没有规定 `source_uri` 从哪来**。
> 本处决定由服务端从净化文件名派生、忽略客户端传入 —— 属**比文档更严**的加固
> （若允许自报，KB-A 成员可传入 KB-B 文档的 URI 给别人的库追加版本）。
> 详见 `stage2_review.txt` 3.7 [C]。

- `documents.source_uri = upload://<净化后的文件名>`：**稳定的逻辑标识**。
  同一租户内同名 = 同一份文档，重传即追加版本（[D-1] 的语义落地）。
- 对象 key = `rag/<tenant_id>/<document_id>/<净化后的文件名>`：**物理位置**，
  含 tenant 路径与随机文档 ID（计划原文要求）。
- 为什么不接收客户端自报的 `source_uri`：那会让「KB-A 的成员」通过传入 KB-B
  文档的 URI 给别人的库追加版本，形成越权写入口。身份由文件名唯一决定，
  跨库复用则要求对**归属库**也有写权限（403）。
- 代价：同一租户内两个不相关但同名的文件会进同一条版本链。这是 [D-1]
  「一个源文件 = 一份文档」的直接推论；跨库共用应走后续的关联表（纯增量）。
- 流水线如何拿到字节：`filename_from_source_uri(source_uri)` + `job.document_id`
  + tenant 就能重建 key，这条往返关系有测试闭合。因此**没有**给 job 表加列
  （加列要走迁移），也没有把对应关系只留在内存里。

**2. 内容一致性检查只对强特征格式生效（宁可放过，不错杀）。**

> **与对接文档的关系（回头审查时补的显式标注）**：对接文档 5.2 只给了原则
> 「只在嗅探结果明确且与扩展名不一致时拒绝（415）、`None` 一律放行」，
> **没有定义「明确」的边界**，也没说「html 前缀命中到底拒不拒」。
> 本处把它物化成「强特征 / 弱特征」两级枚举，属实现补的取舍：
> 相对文档**更宽**（html 前缀命中也不拒），同时**更可测**（把「明确」变成可断言的枚举）。
> 详见 `reports/rag_ingestion_auth_review/stage2_review.txt` 3.7 [B]。

- `%PDF-` / zip 内含 `word/` 是强特征，扩展名指向 pdf / docx 时**必须**匹配，
  否则 415 —— 这两类文件冒充成本最低，且解析失败发生在异步 worker 里，
  上传期拦住能给用户明确的 415 而不是一个事后失败的 job；
- html / md / txt **一律放行**：Markdown 内嵌 HTML 是常见写法，
  按内容拒收会造成「正常文档传不上去」这种更难接受的错误；
- 实测：`report.pdf` + HTML 内容 → 415；`说明.md` + 纯中文 → 202；
  `note.md` 以 `<!DOCTYPE html>` 开头 → 202；
- B3 的「扩展名优先于 MIME」继续成立：`report.pdf` + `Content-Type: text/html`
  + 真实 PDF 内容 → 202 且按 pdf 路径处理。

**3. 审计写 `audit_events`（新表），不是旧的 `audit_logs`。**

对接文档 3.1 第 9 步写的是 `services.audit_service.record_audit_log(...)`（旧 sqlite 表，
无 tenant 列），但计划 2.3 的验收清单与台账第 4 节都明确写「写入 `audit_events`
（actor、tenant、action、resource、request_id、脱敏摘要）」。按**计划**执行：
走 `repository.record_audit_event`，与业务同事务，并额外把 `actor_role` / `ip` /
`user_agent` 放进取证摘要（`audit_events` 没有这三列）。

摘要的脱敏范围也收窄过：**只对自由文本脱敏**（filename / user_agent / error_message）。
一开始对所有字符串统一脱敏，测试立刻发现 `knowledge_base_id` / `job_id`（32 位 hex）
里的连续数字串会被通用「订单号」规则打码，审计摘要里的 ID 变成不可关联的乱码 ——
结构化 ID 由服务端生成、不含个人信息，必须保持原样。

**4. 写操作要求知识库写成员（owner / editor），`redis` 只进生产侧依赖。**

> **与对接文档的关系（回头审查时补的显式标注）**：对接文档 3.1 第 2 步只要求
> 「校验成员身份（无成员关系 403）」，**未区分角色**。本处额外收紧了：
> 只有 owner / editor 能写，reviewer / viewer 一律 403 —— 属**比文档更严**的加固。
> 依据：文档口径下 viewer 也能上传并覆盖同名文档的对象字节，成员分级形同虚设。
> 代价：联调方若严格按文档写客户端，会以为 viewer 能上传，故此处显式记录。
> 详见 `stage2_review.txt` 3.7 [A]。

- 「有成员关系」≠「有写权限」：viewer / reviewer 一律 403；
- JWT 的 `sub` 是身份提供方的 `external_id`，**不等于** `users.id`。
  `knowledge_base_members.user_id` 与 `documents.created_by` 都是指向 `users.id`
  的外键，因此统一经 `_resolve_principal_user_id` 映射一次。
  实现期正是这里先写错了（拿 sub 直接比对成员关系），测试报 403 才发现 ——
  否则线上表现会是「加了成员也永远没写权限」，且不会报错；
- `python-multipart` 必须**两侧都加**（`UploadFile` 是路由定义期的硬依赖，
  缺包 CI 直接红）；`redis` 只进 `requirements.txt`：队列在未配 URL 时走内存降级，
  Redis 分支用注入的假客户端测试，既不连真实 Redis 也不 import redis，
  CI 不需要它；配了 URL 却没装客户端时队列明确报不可用（503），不会静默降级。

### [D-8] 回头审查补记：三处「超出对接文档原文」的取舍 + 一处覆盖盲区

B4 交付完成后对**对接文档字面要求**做了一轮逐条回头核对，产出如下。
结论：**实现方向都成立，没有需要回滚的改动**；但有三处「我做的比文档更多 / 更宽 / 更严」
的地方此前只写在决策记录里、没有点明「这是超出文档的加固」，以及一处测试盲区。

**三处超出对接文档原文的取舍（已在 [D-7] 对应条目补显式标注）：**

| # | 对接文档的说法 | 本实现的取舍 | 方向 |
| --- | --- | --- | --- |
| 1 | 3.1 第 2 步：校验「成员身份」，无成员关系 403（**未区分角色**） | 写操作要求 owner / editor，reviewer / viewer 一律 403 | **更严** |
| 2 | 5.2：「只在嗅探结果明确且与扩展名不一致时拒绝」，**未定义「明确」** | 物化为强特征（必拒）/ 弱特征（html 前缀命中不拒）/ 无魔数（放行）两级 | **更宽**且更可测 |
| 3 | 3.1 第 7 步：用 `get_document_by_source_uri` 复用，**未规定 `source_uri` 来源** | 服务端从净化文件名派生，忽略客户端传入（否则是越权写入口） | **更严** |

标注之所以必要：这些取舍若不写明，下一位执行者会误以为它们是文档要求，
从而在「放宽写权限」「让前端传 source_uri」这类改动上以为无需决策记录。

**覆盖盲区（已补，属「实现正确但没测到」）：**

`KB_MEMBER_ROLES` 有四个合法角色（owner / editor / reviewer / viewer），
但成员权限测试原本只采样三点：editor（放行）、viewer（403）、无成员关系（403）。
**owner（写白名单的另一半）与 reviewer 都没有用例** ——
若实现把 `KB_WRITE_MEMBER_ROLES` 误写成 `{"editor"}`，测试会全绿，
而「库主传不上自己的文件」必然在线上发生。已补 4 条：

- `test_upload_allows_knowledge_base_owner`（补上白名单另一半）
- `test_upload_rejects_reviewer_member`（reviewer 带「审核」语义，最易被误认为可写）
- `test_upload_respects_knowledge_base_role_matrix[owner|editor|reviewer|viewer]`
  —— 由 `KB_MEMBER_ROLES` 与 `KB_WRITE_MEMBER_ROLES` **两张真值表驱动**而非抽样；
  以后往角色枚举加值，要么被接纳、要么被拒，不会出现「既没被授权、也没有测试提醒」的静默缺口；
- `test_write_member_roles_are_a_strict_subset_of_all_member_roles`
  —— 纯集合断言，同时守「拼错角色名 → 永远 403」与「白名单等于全集 → 分级失效」两个方向。

测试文件 **100 → 107**，全量 **548 → 555**（ruff / compileall / 体积检查已重跑）。
阶段级审查的对应记录见 `reports/rag_ingestion_auth_review/stage2_review.txt` 3.7 节。

### 阶段 2 的接口现状（供后续批次参考）

| 后续批次要用到的点 | 现状 |
| --- | --- |
| 解析入口 | `parser_registry.parse_document(source, filename)`（B3 提供，B4 未改） |
| 字节来源 | `object_store.build_object_key(tenant_id, document_id, filename)`；文件名从 `source_uri` 反解 |
| 队列消费 | `queue.IngestionQueue.publish` 已就绪；`xreadgroup` / 消费组留给 worker（3.3） |
| 版本落库 | `repository.create_document_version`，`metadata_json["warnings"]` 是警告的固定键 |
| 重复文件判定 | `repository.find_version_by_content_hash`（3.3 调用） |
| 任务状态推进 | `repository.update_ingestion_job` / `fail_ingestion_job`（支持只改 stage，便于幂等重试） |
| 测试基建 | `tests/auth_helpers.py` 自签令牌；临时 SQLite；`models.Base.metadata.create_all` 可快速建表 |

### [T-3.1] 阶段 3：定义 Chunk 配置（2026-09-23）

任务编号：3.1
修改文件（新增）：
- `config/chunking_config.py` —— `ChunkConfig`（frozen dataclass，8 个字段）+
  `ConfigFieldError` / `ChunkConfigError` + 环境变量解析 + 生效配置落库载荷
修改文件（已存在）：
- `services/ingestion/__init__.py` —— `__all__` 与模块文档登记 `chunkers`（1 处）

执行命令：
```
./venv/Scripts/python.exe -m pytest tests/test_chunking.py -q
./venv/Scripts/python.exe -m pytest -q
./venv/Scripts/python.exe -m ruff check .
```

测试结果：见 [T-3.2]（3.1 与 3.2 同批交付，测试文件与门禁合并统计）

验收结果：PASS

实现要点（逐条对应计划 3.1）：
1. 新增 `config/chunking_config.py`，初始默认值与计划**逐值一致**：
   `target_tokens=450` / `min_tokens=120` / `max_tokens=700` / `overlap_tokens=80` /
   `preserve_heading_path=True` / `parent_chunk_enabled=True`；
2. 允许知识库覆盖：`replace_overrides(mapping)` / `build_config(overrides)`，
   以 base 为底只改传入项 —— **真正从 `knowledge_bases.chunking_config_json`
   读出来的动作归 B6 流水线**（本批只交付机制，已登记为遗留问题）；
3. 每个文档版本保存「实际生效配置 + tokenizer 标识」：
   `storage_payload()` = `{config_version, 8 个字段, tokenizer_id}`，
   直接可落 `document_versions.metadata_json["chunking"]`；
   每个 chunk 的 `metadata_json` 里另冗余记 `tokenizer_id` / `chunker_version` /
   `chunking_config_version`，便于脱离版本记录解释单个 chunk；
4. **配置越界时启动失败并给出字段错误（不静默夹取）**：
   `__post_init__` 抛 `ChunkConfigError`，错误对象含逐字段 (field, message, value)；
   模块级 `CHUNK_CONFIG` 在 **import 期**构造并校验 →
   非法配置让进程启动失败。有子进程用例证明「进程起不来且 stderr 指出字段」；
5. 比计划**更严**的两处（方向已标注）：未知配置键报错（防 `max_token` 少一个 s
   被静默忽略）、布尔字段拒绝字符串 `"false"`（JSON 写错类型时 `"false"` 是真值）。

**计划外新增第 7 个配置项 `parent_max_tokens`（默认 2800 = 4 × max_tokens）**：
给超大 section 的 parent 设上界 —— 否则一整章会被装进一条几十万字符的记录，
它既不参与 embedding 也不被任何检索路径引用，只是把库撑大。
校验规则 `parent_max_tokens >= max_tokens`。

未解决问题：
1. 知识库级配置（`knowledge_bases.chunking_config_json`）与生效配置的**落库动作**
   属 B6 流水线，本批只交付机制与载荷；
2. 本批**无新增依赖**（纯 stdlib），故 `requirements*.txt` 未改动。

下一任务：3.2 实现结构化切分器（同批交付）

### [T-3.2] 阶段 3：实现结构化切分器（2026-09-23）

任务编号：3.2
修改文件（新增）：
- `services/ingestion/chunkers/__init__.py` —— 包导出 + 守卫约定说明
- `services/ingestion/chunkers/errors.py` —— `ChunkingError` + 3 个稳定 error_code
- `services/ingestion/chunkers/tokenizer.py` —— `TokenCounter` 协议 +
  3 个确定性实现（`heuristic-zh-v1` 默认 / `mock-word-v1` / `mock-char-v1`）+ 注册表
- `services/ingestion/chunkers/models.py` —— `Chunk` / `ChunkContext` / `AclEntry` /
  `ChunkingStats` / `ChunkingResult` + 确定性 `chunk_id` 生成
- `services/ingestion/chunkers/cleaner.py` —— 块级清洗（空白 / 页码行 /
  重复页眉页脚 / 完全重复段落）+ 删除计数
- `services/ingestion/chunkers/chunker.py` —— 主流程（分组 → 成单元 → 装箱 →
  parent/child → 元数据）
- `tests/test_chunking.py` —— **235 个测试**
- `reports/rag_ingestion_auth_review/{B5_full_test-output.txt, B5_task3_2_test-output.txt,
  B5_ruff-output.txt, B5_compileall-output.txt, B5_data-size-output.txt,
  B5_task_level_review.txt, stage3_review.txt}` —— 本批证据
修改文件（已存在）：
- `services/ingestion/__init__.py` —— 登记 `chunkers`（与 [T-3.1] 同一处改动）
- `config/chunking_config.py` —— 本批新增文件（见 [T-3.1]）

**没有新增 Alembic 迁移**：本批不写库、不改表结构（0001 仍是最新迁移）。

执行命令：
```
./venv/Scripts/python.exe -m pytest tests/test_chunking.py -q
./venv/Scripts/python.exe -m pytest -q
./venv/Scripts/python.exe -m ruff check .
./venv/Scripts/python.exe -m compileall services routers schemas models alembic main.py scripts
./venv/Scripts/python.exe scripts/check_repo_data_size.py
```

测试结果：
- `tests/test_chunking.py`：**235 passed**（1.4 s）
- 全量：**790 passed, 5 warnings, 4 subtests passed**（14.1 s）
  （B4 收尾基线 555 → 现在 790，本批新增 235，**零回归**）
- `ruff check .`：All checks passed（过程中真实拦下一次 F401：
  chunker.py 遗留了未使用的 `render_table_text` 导入）
- `compileall`：退出码 0；`check_repo_data_size`：通过
- warning 仍为 **5 条**，与 B4 收尾逐条一致（fastapi/starlette 的 httpx 弃用 ×2、
  faiss SWIG 绑定 ×3），**未新增**

验收结果：PASS

计划 3.2 九条要求逐条核对（完整逐条见 `B5_task_level_review.txt`）：
1. [√] 按 `(heading_path, page)` 分组；标题块 heading_path 含自己 →
   标题与正文天然同组；同名标题出现在两处**不合并**（否则 parent 跨无关内容）
2. [√] 超长段落四级拆分：句末标点 → 分号 → 换行 → **按 token 硬切**（计划未定义的第 4 级，
   实现补全并计入 `hard_split_units`）
3. [√] 表格按行分块 + **每组重复表头**；代码块**整块保留**不内部截断
4. [√] 列表按项边界装箱，仅单项超限才项内拆分（计入 `list_items_split`）；
   HTML「每项一块」与 Markdown「连续列表合成一块」两种形态都处理
5. [√] 相邻 child 重叠 ≥ `overlap_tokens`（按单元边界取整）、每块 ≤ `max_tokens`
6. [√] 每个 child 指向同组 parent；parent 先于 child 出现（B6 落库的前置条件）
7. [√] 标题路径作文本前缀（`" > "` 拼接），三重判据防重复原文主体；
   前缀计入 token 预算，挤爆上限时丢弃并计数
8. [√] 四道清洗且全部计数：空白 / 纯页码行（计划外新增规则）/
   重复页眉页脚（≥3 个不同页码）/ 完全重复段落
9. [√] 12 个字段齐备（`chunk_id` / `parent_chunk_id` / `text` / `token_count` /
   `tenant_id` / `document_id` / `document_version` / `page_start` / `page_end` /
   `heading_path` / `acl` / `content_hash`）+ `char_count` / `chunk_type` / `ordinal` /
   `document_version_id`；归属信息整体进 `metadata_json`

计划点名的测试覆盖九项全部命中：长段落 / 表格 / 代码 / 列表 / 重叠 / 边界长度 /
空文档 / 确定性 ID / 权限元数据继承。**tokenizer 用固定 mock，测试不下载模型**
（另有 `TestProductionDefaults` 用生产默认配置 + 默认启发式 tokenizer 复跑）。

**枚举型契约用真值表而非抽样**（踩坑 D2 的教训）：
`BLOCK_TYPE_TRUTH_TABLE` 覆盖 8 种块类型全集并断言「表 == 枚举」；
ACL 权限（5）/ 主体类型（4）逐值参数化；`ChunkingStats` 字段全集（17）显式锁住。

**用真库验证 B6 要依赖的落库约束**（本批生产代码零调用 `insert_chunks`，
只在测试里调用以证明产物兼容）：临时 SQLite + `alembic upgrade head` +
`db.create_db_engine`（打开 `PRAGMA foreign_keys=ON`）→
父先于子真实生效、重复 `chunk_id` 被数据库拒绝、跨版本父 chunk 被拒、
`metadata_json` 原样读回。

未解决问题：
1. **chunker 尚无生产调用点**：B6 的 pipeline 才是第一个调用方。
   已按纪律把它登记进第 4 节「已建未启用」清单 —— 有意的分批结果，不是漏接线；
2. 图片块（`image_ref`）不进 chunk（无图检索）；图文混合检索需单独设计；
3. PDF 表格「跨页重复表头」未覆盖：本批的重复表头只在**同一个表格块内**成立，
   跨页拆分的表格需要解析器侧配合（属 B3 范围）；
4. **发现 B4 的读回顺序问题**：`repository.list_chunks` 排序键为
   `(created_at, chunk_id)`，同批写入的 `created_at` 可能同值 → 读回顺序 ≠ 写入顺序。
   写入侧不受影响，但 **B6 需要文档顺序时必须读 `metadata_json["ordinal"]`**；
   不修 B4 代码（不属本批范围）；
5. token 数为确定性启发式估计（与真实 BPE 有偏差，是刻意选择：测试可复现）。
   换真实 tokenizer 时 `tokenizer_id` 变化会改变 `chunk_id` → 需重建索引，
   这是**设计预期**（否则无法识别「同一份文档的旧切分结果」）；
6. `preserve_heading_path=False` 时标题文本以正文首行保留 —— 否则关掉前缀等于
   让标题从 chunk 消失、按标题检索整类失效。该语义边界已在代码与测试中固定。

下一任务：3.3 实现幂等处理流水线 + 3.4 FAISS 索引 manifest（批次 B6，**闭环关键**）

### [D-9] 决策记录：切分器的归属元数据怎么传（`ChunkContext`）

**背景**：计划 3.2 的两条要求字面上冲突 ——
约束说「切分器不得接收 `tenant_id`」，但 chunk 输出**必须包含**
`tenant_id` / `document_id` / `document_version` / `acl`。

**决策：用一个不可变的 `ChunkContext` 承载归属信息，切分入口签名为**

```python
chunk_document(document: ParsedDocument, *, context: ChunkContext, config: ChunkConfig) -> ChunkingResult
```

`ChunkContext` 的字段：`tenant_id` / `document_id` / `document_version_id` /
`document_version` / `acl`（`tuple[AclEntry, ...]`）/ `source_uri` / `filename` /
`source_type` / `document_title`。

理由（三条，与交接文档给的推荐解法一致）：
1. 保持「切分器是纯函数」：无环境依赖、无数据库会话、可离线测试；
2. 守卫测试断言的是**不出现裸 `tenant_id` 参数**（与解析器守卫 `[D-5]` 同源），
   而不是禁止一切元数据传入 —— 否则 B7 的检索过滤就没有数据来源
   （`document_chunks` 表**没有** tenant / acl / 页码 / heading_path 的独立列，
   这些只能进 `metadata_json`，而那是 B7 过滤的唯一来源）；
3. 上下文对象可以**整体** JSON 序列化进 `metadata_json`，正好对上「没有独立列」这条约束。

**被否决的方案**：
- **直接把 `tenant_id` / `acl` 加成参数**：解析器守卫守的正是这个 ——
  一旦参数化，`tenant_id` 就变成「可以随手传」的散装参数，
  将来会出现「切分时传 A 租户、落库时写 B 租户」这种静默错配。
- **在切分器内部读环境 / 全局上下文**：切分器会变成有状态的半成品，
  同一份文档换个进程切出不同结果，确定性 id 与幂等重跑同时失效。
- **把归属信息交给调用方在切分后自行补到 chunk 上**：等于让下游做字符串拼接式
  后处理，一旦漏掉某个字段，chunk 就会带着空租户进索引 —— 而这类缺失
  不会有任何报错，只会在检索时表现为「查不到」。
  现在的做法是**让非法上下文在构造期就抛错**（空 tenant / 版本号 < 1 都是硬错误）。

**同批一并拍板的两个字段歧义**（计划只写了 `document_version`）：
- 同时保留 `document_version`（int 版本序号，供人看）与 `document_version_id`
  （str，`document_chunks.document_version_id` 外键指向 `document_versions.id`），
  因为 `insert_chunks` 需要的是后者，而前者有查询价值；
- `acl` 的形状定为 `tuple[AclEntry, ...]`（`subject_type` / `subject_id` /
  `permission`，对齐 `document_acl` 表），既能 JSON 序列化又能直接被 B7 消费。
  枚举在这里**镜像**了一份（不 import `services.ingestion.models`，避免把
  SQLAlchemy 拉进纯函数层），并由 `test_acl_enums_mirror_models` 断言两份逐值一致。

**落地的守卫**：`tests/test_chunking.py::TestChunkerGuards` 共 9 条 ——
禁止依赖数据库（4 个模块名参数化）、禁止依赖上层模块、禁止动态 import、
禁止任一函数接收裸 `tenant_id` / `acl` 参数、`chunk_document` 签名逐参数冻结、
禁止文件读取。

验证结果：`tests/test_chunking.py` **235 passed**、全量 **790 passed**、ruff / compileall / 体积检查全绿。

### [T-3.2-补] 记录后门禁复跑：那 1 个 failed 经查为环境噪声，不是回归（2026-09-23）

规程要求「文档改动后再跑一次门禁确认无回归」。复跑结果
`1 failed, 789 passed, 5 warnings, 4 subtests passed`，唯一失败用例
`tests/test_document_upload_api.py::test_local_object_store_round_trip`。

**判定为非回归，依据三条**：

1. 异常类型是 `SystemExit` 而非 `AssertionError` ——
   用例前四条断言（`exists False` / `put` / `exists True` / `get` / 无 `.tmp` 残留）**全部通过**，
   只有最后的 `store.delete(key)` 被打断；
2. 抛出点在 `...\cli\vendor\shim\sitecustomize.py`
   （运行环境注入的批量删除守卫），**不在项目代码里**；
   项目侧只是正常调用 `Path.unlink()`（`object_store.py:234`）；
3. 守卫自己的负载写着 `{"scope":"turn","count":550,"threshold":50}` ——
   这是**本轮累计删除量**超阈值，与本批文档改动无因果关系
   （本批改动为 docs/*.md + .workbuddy/memory/*.md，零 Python 改动）。

**根因（我方操作所致）**：为取回被守卫吞掉的 pytest 汇总行，
在同一轮里连跑 5 次全量测试，每次都在临时目录创建/删除数百个文件 → 越过每轮阈值 →
此后本轮所有删除被拦。已登记为踩坑记录 **A5**（`docs/RAG_DEV_PITFALLS.md`），
完整堆栈与判定过程见 `reports/rag_ingestion_auth_review/B5_post_record_gate_note.txt`。

**B5 验收结论不变**：全量 **790 passed** / 5 warnings / 4 subtests，
门禁四件套（pytest + ruff + compileall + 体积检查）全绿。

### [T-3.3] 幂等处理流水线 + worker（2026-09-23）

**交付物**：`services/ingestion/pipeline.py`（894 行）、`services/ingestion/worker.py`（332 行）、
改造 `services/ingestion/queue.py`（消费端 `xreadgroup` / `xack`）、
`tests/test_ingestion_pipeline.py`（1003 行 / 49 条用例）。

**状态流转**：`received → stored → parsed → normalized → chunked → persisted → indexed → published`，
实现为 `pipeline.STAGE_ORDER`，并有守卫测试断言它与 `ingestion_jobs.stage` 的**数据库 CHECK
约束同源**（不是两处各写一遍）。每个阶段结束写 `job.stage` 并 commit（进度可现场查询）。

**幂等三支点**（均有专门重跑用例）：
① 判重走 `find_version_by_content_hash()`（`content_hash` 租户粒度唯一）；
② `chunk_id` 是确定性哈希 → 同输入重跑得到同一批 id；
③ 落库**先删后写**（`_stage_persisted` → `delete_chunks` + `insert_chunks`）。

**幂等的实现方式**（本批核心设计）：不把 `ParsedDocument` 落盘做断点续传，而是
`persist = position >= start` —— 失败重试时，失败阶段**之前**的阶段**重算但不重写**
（解析 / 切分是纯函数、确定性），从失败阶段起正常执行并落库。
代价是重试多花一次解析 + 切分的 CPU，收益是少一套「中间产物持久格式 + 生命周期 + 清理策略」。

**失败不抛异常**：`process_job` 返回 `PipelineOutcome`（`status='failed'` + 稳定 `error_code`），
不向上抛业务异常 —— worker 要连续处理一批任务，一个坏文件抛异常会打断整轮消费。
8 个阶段**逐一注入故障**，验证「失败阶段 + error_code + retry_count 累加 + 文档状态」四件事。

**`[D-9]` 落地**：流水线是构造 `ChunkContext` 的唯一地方（`tenant_id` 取自 `documents` 表、
`acl` 取自 `document_acl` 表、页码 / 来源 / 文件名取自 `ParsedDocument`）；
切分入口签名仍冻结为 `chunk_document(document, *, context, config)`，
B5 的 AST 守卫测试**仍绿**。

**`[D-6]` 落地**：上传接口不建版本；版本由流水线在 `parsed` 阶段创建，
且只在 `job.document_version_id` 为空时创建。

**知识库覆盖**：从 `knowledge_bases.chunking_config_json` 读出 → `build_config()` 校验 →
**越界即任务失败（`invalid_chunking_config`），不静默夹取**。
生效配置 + tokenizer 标识 + 切分统计**直接取** `ChunkConfig.storage_payload()` 落进
`metadata_json["chunking"]`（`stats` 挂在 `["chunking"]["stats"]`），**没有第二份序列化**。
警告固定写 `metadata_json["warnings"]`（与解析警告**共用键名**，形状 `{code, message, detail}`）。

**worker**（12 条用例）：消费 → 推进 → ack 时机；空队列不算错误；未知 job 跳过并 ack；
一个坏任务不阻塞后续；不可预期异常被兜住并 ack；`run_forever` 迭代上限；
**队列不支持消费时明确报错而不是静默空转**；Redis 分支的 `XGROUP` / `XREADGROUP` / `XACK`
参数与消费者组 id 选择理由。

### [T-3.4] FAISS 索引 manifest 与原子切换（2026-09-23）

**交付物**：`services/ingestion/index_builder.py`（537 行）、
`services/ingestion/index_manifest.py`（684 行）、
改造 `utils/vector_retriever.py`（**+341 行**）。

- **原子切换**：新索引**先构建到临时路径** → 写 manifest → **最后一步才改指针**；
  任一步失败可回滚到旧 manifest（指针未动 = 旧索引仍生效）。
  「先构建后切换」而不是「原地覆盖」，是这一条的全部要点。
- **manifest 内容**：`chunk_id` 列表、`tokenizer_id`、切分配置版本、embedding 模型标识、
  `chunk_count`。`index_builds.manifest_uri` 从「从未读写」变成**有读写**。
- **顺序来源**：`index_builder._assemble_entries` 的排序键是
  `(document_version_id, ordinal)`，并在 manifest 里留痕
  `"ordered_by": "metadata_json.ordinal"` —— B5 的发现（读回顺序 ≠ 写入顺序）**被严格遵守**，
  `list_chunks` 的 `(created_at, chunk_id)` 排序**从未**被用作索引顺序。
- **检索侧（本批主动多做的一块）**：`utils/vector_retriever.py` 新增
  `load_chunk_index()` / `search_chunk_index()` / `ChunkAccessFilter` / `ChunkHit`，
  用 FAISS **原生预过滤**（`SearchParameters(sel=IDSelectorBatch(...))`）而不是后过滤；
  `access` 是**必填关键字参数**，显式传 `None` 也抛错。
  理由：索引建好了但检索端读不到，等于「已建未启用」再犯一次。
  **API 层接线（4.3）与端到端隔离测试仍归 B7。**

**本批后「已建未启用」清单的变化**：`chunk_document()` / `insert_chunks()` /
`delete_chunks()` / queue 消费端 / `index_builds.manifest_uri` **五行全部补上调用点**，
并有 AST 守卫测试（`TestProductionCallPoints`）守着 —— 详见第 4 节表格。

### [T-3.3-补] 基线里那 2 个 failed 是**真缺陷**不是环境噪声：`delete_chunks` 已修（2026-09-23）

**开工基线**：`2 failed, 837 passed`，两条都挂在 `TestIdempotency`
（`test_redelivering_a_finished_job_changes_nothing` / `test_chunk_ids_are_stable_across_reruns`），
共同点是「任务**已经 succeeded 之后**再跑一次」，失败阶段固定在 `persisted`。

**真因**：`repository.delete_chunks` 当时用 `for row in rows: session.delete(row)` **逐行删**，
ORM 把它编译成 `DELETE ... WHERE id = ?` 的 **executemany（每行一条独立语句）**；
而 `document_chunks` 有一组**指向自己的复合外键**
（`(document_version_id, parent_chunk_id) → (document_version_id, chunk_id)`），
**SQLite 对即时外键约束的检查发生在「每条语句结束时」** →
删父 chunk 那条语句结束时子 chunk 仍指向它：

    sqlalchemy.exc.IntegrityError: FOREIGN KEY constraint failed
    [SQL: DELETE FROM document_chunks WHERE document_chunks.id = ?]

首次执行之所以全绿：**首次执行时表里没有旧行可删**（`rows` 为空，循环根本没进），
这个缺陷只在重跑路径上暴露 —— 而幂等测试是唯一会走重跑的用例。
**值得单独记一笔**：该函数自己的 docstring 写的正是正确做法
（「只要一次全量 delete 即可……逐条删反而会中途撞外键」），**实现却与注释相反**。

**修复**：改**一条** `DELETE ... WHERE tenant_id = ? AND document_version_id = ?`
（批量 delete + `synchronize_session="fetch"`）。整个版本的行在同一语句内一起消失，
约束在语句结束时看到的已是"全部删完"的一致状态 ——
既不需要「先删子再删父」的拓扑排序，也不需要 `ON DELETE CASCADE` 或
`PRAGMA defer_foreign_keys`。

**验证**：`pytest tests/test_ingestion_pipeline.py -q` → 49 passed；
全量 → **839 passed**；ruff / compileall / 体积检查全绿。
现场证据双份保留：`B6_baseline_test.txt`（红）+ `B6_full_test-output.txt`（修复后）。
已登记踩坑 **B13**；方法论（环境噪声 vs 真缺陷的判据）登记为踩坑 **D12**。

### 阶段 3 收尾：阶段级结论 **PASS**（2026-09-23）

阶段 3 的四个任务（3.1 / 3.2 / 3.3 / 3.4）**全部完成**，
阶段级结论已在 `reports/rag_ingestion_auth_review/stage3_review.txt` **第五节回填为 PASS**
（该文件原文写明「本文件将在阶段 3 的最后一个批次（B6）完成后回填阶段级结论」，
回填时**保留了「待回填」原文**以便对照）。

当初拒绝提前给 PASS 的**唯一理由**（「chunk 还没有任何生产调用点」）现已消失 ——
「上传 → 解析 → 切分 → 落库 → 建索引 → 可检索」已是一条**真实可跑的链路**。
事后来看当初不给 PASS 是对的：B6 恰好在这条**接线路径**上炸出了全程唯一一个真缺陷。

**本批完成后的门禁基线**：全量 **839 passed** / 5 warnings / 4 subtests passed，
`ruff check .` 干净、`compileall` 退出码 0、`check_repo_data_size.py` 通过。
阶段 3 起点 555（B4 收尾）→ 790（B5 收尾）→ **839（B6 收尾）**，零回归。
`B5_post_record_gate.txt` 保留原始输出以便复核，解释文件即上述 `..._note.txt`。

### [T-4.1] 阶段 4：固定权限判定规则（2026-09-23）

任务编号：4.1

修改文件（新增）：
- `services/retrieval_access.py`（254 行）—— 资源级权限判定模块，**`ChunkAccessFilter` 的唯一生产构造点**。
  导出：`acl_allows_read()` / `acl_entries_by_document()` / `visible_chunk_ids()` /
  `build_chunk_access_filter()` / `document_is_visible()` / `knowledge_base_is_readable()` /
  `visibility_summary()`；常量 `READ_PERMISSION` / `SUPPORTED_SUBJECT_TYPES`。
  **只给计数不给明细**（`visibility_summary`）是有意的：明细会在日志/响应里泄漏受控资源 id。

修改文件（已存在）：
- `services/auth_context.py` —— 新增 `RESOURCE_SCOPE_ROLES`（计划 4.1 的**九项枚举**，
  键名即完整 scope 字符串）、`RESOURCE_SCOPE_PREFIX = ""` 与 `resource_scope(permission)`；
  `_invert_grants()` 增加一张表 → 两张策略表**并存**倒排进 `ROLE_SCOPES`；
  模块 docstring 改为「操作维度 + 资源维度并存」。
- `services/auth_service.py` —— 新增 `require_resource_scope(permission, context)`，
  `__all__` 与 import 同步。
- `tests/test_auth_context.py`（45 → 47 条）—— `test_role_scopes_is_exact_inversion_of_policy_tables`
  扩成**四张表**参与校验；新增 `test_resource_scopes_follow_the_plan_enumeration`（九项逐个存在、
  键名即 scope）与 `test_publish_and_index_rebuild_are_granted_separately`；
  `test_admin_holds_every_scope_and_agent_does_not` 增加资源维度。

落地内容：
- 判定顺序六段全部实现：JWT 有效（复用 B2 的 `get_auth_context`，**未改**）→ 租户匹配
  （所有查询带 `AuthContext.tenant_id`，本模块**没有任何接收 tenant 的参数**）→
  scope 满足操作（`require_resource_scope`）→ 知识库成员（`knowledge_base_is_readable`）→
  document ACL（`document_is_visible` / `visible_chunk_ids`）→ 检索 filter 注入
  （`build_chunk_access_filter` 总是给**显式** `allowed_chunk_ids`）。
- ACL 语义（本批拍板，登记 **[D-13]**）：无 ACL 记录 = **租户内可见**；
  有记录则须命中 `permission='read'` 且 `subject_type ∈ {tenant, user, role}`；
  **`write` 不隐含 `read`**；`group` **fail closed**（无组表）；未建档不影响判定。
- 生效期维度：**本批不适用，显式说明** —— `documents` / `document_versions` **没有**
  `effective_from` / `effective_to` / `expires_at` 列（`models.py:281` + 迁移 0001 均无）。
  但 `published` 之外的状态确实被排除。要加生效期需新开迁移 + 同步 schema 对照断言，
  且只需改 `visible_chunk_ids` 一处（入口已收敛）。

执行命令：
```
python -m pytest tests/test_auth_context.py tests/test_retrieval_isolation.py -q
python -m pytest -q
python -m ruff check .
python -m compileall services routers schemas models alembic main.py scripts utils config
python scripts/check_repo_data_size.py
```

测试结果：
- `tests/test_auth_context.py`：**47 passed**
- `tests/test_retrieval_isolation.py`：**23 passed**（含 `TestFilterIsMandatory` 5 条：
  `access=None` 与假对象 → `chunk_filter_required`；空 tenant → `chunk_filter_tenant_missing`；
  空集合 → **零命中**；无可见 chunk 的租户 → 零命中）
- 全量：**884**（见 [T-4.3] 的门禁汇总）
- `ruff check .`：All checks passed；`compileall`：退出码 0；体积检查：通过

验收结果：PASS

未解决问题：
1. `STATUS_BY_ERROR_CODE` 里只有 `chunk_index_unavailable` 一条有端到端用例，
   两条 `chunk_filter_*` 与 `embedding_model_mismatch` 的**状态码映射本身无测试**。
2. 生效期维度不适用（已显式说明，非漏做）。

下一任务：4.2 改造知识和文档路由（同批次 B7）

### [D-12] 决策记录：「无权限」对查询者 = 零命中，对调用方 = 报错（2026-09-23）

**这是 B7 规程点名要求「由执行者拍板」的一点，也是本批唯一需要先做判断的点。**

两个方向，**不矛盾**，因为它们回答的是不同的问题：

| 场景 | 结论 | 回答的问题 | 理由 |
| --- | --- | --- | --- |
| **查询者**（用户）无权访问某文档 | **零命中**（不是 403） | 「我能看到什么」= **授权结果** | 返回 403 会泄漏「这条文档存在但你没权限」——与踩坑 C3「跨租户与不存在必须不可区分」是同一条原则。受控文档在结果里与"不存在"完全一样：**既不出现，也不报错**。 |
| **调用方**（程序）忘了构造 filter | **报错**（`chunk_filter_required`，HTTP 500） | 「你的调用方式对不对」= **契约违例** | 这是编程错误，必须大声。默认全库 = 「少传一个参数就把所有租户的数据都检索出来」，而写代码的人**根本不会注意**。 |

方向标注（沿用 [D-8] 的写法）：**更严**。计划 4.1 原文只说「检索 filter 注入 `tenant_id`
与 allowed subjects」，没有规定"无权时怎么办"，因此这是本批的补充决定，不是照抄。

落地位置：
- 零命中 → `services/retrieval_access.py`（`build_chunk_access_filter` 总是给显式
  `allowed_chunk_ids`，**空集合就表示零命中**，不退回"不限租户"）+
  `routers/retrieval.py:143` 的注释；
- 报错 → `utils/vector_retriever.py::search_chunk_index`（B6 交付，`access` 必填关键字、
  显式传 `None` 也抛）——**本批未放宽该契约**；
- 方向说明写在 `services/retrieval_access.py` 模块 docstring 的「两个方向性判断」一段。

配套可观测性：响应里的 `visible_chunk_count` 让"零命中"与"索引坏了"可区分 ——
没有这个数，线上只会看到 0 条结果，无法归因。

### [D-13] 决策记录：ACL 的三个方向性判断 + 生效期维度不适用（2026-09-23）

计划 4.1 只列了「document ACL」这一项判定，**没有规定语义细节**。本批拍板三条：

1. **无 ACL 记录 = 租户内可见**（【更宽】）。依据是**与实际使用方式一致**：
   上传接口创建的文档在 `document_acl` 里**没有行**；若默认拒绝，
   则"刚上传的文档谁都检索不到"，与产品的实际用法相反。
   即 `document_acl` 是**额外收紧**，不是**默认拒绝**。
2. **`write` 不隐含 `read`**（【更严】）。`permission` 必须是 `read`；
   `write` / `review` / `publish` / `delete` **都不隐含读权限** ——
   否则「能发布」会顺带把文档正文暴露给检索。
3. **`subject_type='group'` 一律不匹配**（【更严 / fail closed】）。
   本服务**没有组成员关系表**。与其猜一个"可能是同组"的语义，
   不如不匹配，并在 docstring 写明「等有组表时在这里补」。

另附一条**显式说明**（不是决策，是事实澄清）：
**生效期维度本批不适用** —— `documents` / `document_versions` 里没有
`effective_from` / `effective_to` / `expires_at` 之类的列，没有可判定的字段。
这不是"漏做"。真要加：新开一次 Alembic 迁移 + 同步 `tests/test_ingestion_models.py`
的 schema 对照断言 + 在 `visible_chunk_ids` 里加一个条件。

三条判断的验证：`tests/test_retrieval_isolation.py::TestAclIsolation` 5 条
（含 `test_write_permission_does_not_grant_read`、`test_role_grant_works_and_unknown_group_does_not`、
`test_tenant_subject_grant_covers_the_whole_tenant`）。

### [T-4.2] 阶段 4：改造知识和文档路由（2026-09-23）

任务编号：4.2

修改文件（已存在）：
- `routers/documents.py` —— 新增 `_load_readable_document()`（租户匹配 **且** document ACL 允许；
  被 ACL 拦下时返回**与"不存在"逐字相同**的 404：同一 detail、同一状态码）；
  `GET /documents/{id}`、`GET /documents/{id}/versions`、`GET /ingestion-jobs/{job_id}`
  加 `require_resource_scope(DOCUMENT_READ_PERMISSION, auth)` + 可见性判定；
  上传端点加 `require_resource_scope(DOCUMENT_UPLOAD_PERMISSION, auth)`；
  **新增端点** `POST /ingestion/indexes/rebuild`（见下）。
- `routers/knowledge.py` —— `publish_approved` 增加 `require_resource_scope("document:publish", auth)`
  （**保留**原有的 `require_write_operation_role("knowledge_publish", auth)`）。
- `schemas/document_schema.py` —— 新增 `IndexRebuildResponse`
  （`index_name` / `index_version` / `chunk_count` / `embedding_model` / `embedding_dimension` /
  `manifest_uri` / `switched` / `skipped` / `skip_reason` / **`tenant_count`**），`__all__` 同步。
- `tests/test_tenant_isolation.py`（14 → 21 条）—— 新增 `class TestAclOnRealRoutes`（7 条，
  走**真实** `documents` + `knowledge` + `retrieval` 三路由，见 [T-4.3] 的 fixture 说明）。

新增端点 `POST /ingestion/indexes/rebuild`：
- 权限：`index:rebuild` = {supervisor, admin}（**与 `document:publish` 分开授权**，
  且集合更窄：`knowledge_ops` 能发布但**不能**重建）。
  理由：一次重建影响**所有租户**的检索结果，不该由发布者顺手触发。
- 调用 `rebuild_index(..., index_name=DEFAULT_INDEX_NAME)`（该函数默认 `all_tenants=True`）。
- 失败处理：`IndexBuildError` / `IndexManifestError` → **503** + 稳定 `error_code`。
  理由：这类失败是"环境没准备好"（模型缺失、磁盘只读…）而非请求不合法，
  重试有意义，且**旧索引仍然可用** —— 这正是 3.4「失败不得覆盖当前索引」在 API 层的表现。
- 审计：`_record_audit(action="index_rebuild", resource_type="index", ...)`
  （复用 B4 的原语，**未另写一套**），summary 含 `tenant_count`。
- **`tenant_count` 从"已生效的 manifest"读回**，不从构建过程的中间对象取
  （否则可能报一份没生效的版本）。它同时是"这份索引是全局的"的**外部可证明观测量**：
  退化成按租户分片时它会变成 1，测试立刻红。

执行命令：
```
python -m pytest tests/test_tenant_isolation.py -q
python -m pytest -q
python -m ruff check .
```

测试结果：
- `tests/test_tenant_isolation.py`：**21 passed**（其中 `TestAclOnRealRoutes` 7 条：
  同租户无 ACL 零命中且有正向对照 / 授权者可检索 / 受控文档在 detail + versions + job
  **三个端点**都不泄漏（404 逐字相同）/ 跨租户 404 / **`index:rebuild` 仅 supervisor|admin**
  （agent、knowledge_ops → 403；成功后 `tenant_count == 2`）/ `document:publish` 负向
  （agent → 403，只验负向以免污染真实 seed JSONL）/ 同一请求体换身份结果不同）
- 全量：**884**（见 [T-4.3]）
- `ruff check .`：All checks passed

验收结果：PASS

计划 4.2 六条隔离要求的对表（逐条有对应用例）：

| 计划 4.2 第 N 条 | 覆盖用例 |
| --- | --- |
| 1. 用户 A 不可读取租户 B 文档 | `TestAclOnRealRoutes::test_cross_tenant_document_is_invisible_on_real_routes` + 既有 `test_cannot_read_other_tenants_document`（数据层） |
| 2. 同租户但无 ACL 不能检索受限文档 | `TestAclOnRealRoutes::test_same_tenant_without_acl_cannot_retrieve_controlled_document` |
| 3. 只有 `document:publish` 可发布 | `TestAclOnRealRoutes::test_publish_requires_document_publish_permission` + `test_role_without_publish_scope_gets_403_even_for_own_document` |
| 4. 只有 `index:rebuild` 可重建 | `TestAclOnRealRoutes::test_index_rebuild_requires_its_own_permission` |
| 5. 被过滤文档的标题/分数/数量/引用/trace 均不泄漏 | `TestAclOnRealRoutes::test_controlled_document_does_not_leak_through_job_or_detail_endpoints`（三端点）+ `test_retrieval_isolation.py` 全 23 条 |
| 6. 改 body 的 `tenant_id` 不改变授权范围 | 既有 `test_body_tenant_id_does_not_widen_scope` + 新增 `test_retrieval_filter_is_per_identity_not_per_request` |

下一任务：4.3 改造检索 API + 隔离测试（同批次 B7）

### [T-4.3] 阶段 4：改造检索 API + 检索层隔离测试（2026-09-23）

任务编号：4.3

修改文件（重写）：
- `routers/retrieval.py`（313 行，**整体重写**）—— 三条端点，**入口即见分野**：

  | 端点 | 走哪条轨 | 权限过滤 | `retrieval_path` |
  | --- | --- | --- | --- |
  | `POST /retrieval/search` | **B 轨**（chunk 级索引） | ✅ 服务端构造的 tenant / ACL / 发布状态过滤 | `chunk-index` |
  | `POST /retrieval/search-demo` | A 轨（781 条种子 FAQ） | ❌ 无（数据源本身没有这两维） | `seed-faq-demo` |
  | `POST /retrieval/prompt-preview` | A 轨 | ❌ 无 | — |

  原 `/search` 的 A 轨实现**一行未删**，改名迁到 `/search-demo`
  （规程明确要求 A 轨不删、但必须标注）。
  `STATUS_BY_ERROR_CODE` 把稳定 `error_code` 翻译成状态码（**刻意不一把梭 500**）：

  | error_code | HTTP | 语义 |
  | --- | --- | --- |
  | `chunk_index_unavailable` | **503** | 服务可用性问题（索引还没建），调用方可稍后重试 |
  | `embedding_model_mismatch` | **500** | 部署配置问题（重试无用），要告警 |
  | `chunk_filter_required` / `chunk_filter_tenant_missing` | **500** | **编程错误**，设计上不该在生产出现 |
  | 未知 | 500 | 兜底 |

修改文件（已存在）：
- `utils/vector_retriever.py` —— 新增 `describe_chunk_index()`：**只读 manifest、不 `read_index`**
  （不加载 FAISS 索引、不触发 embedding），因此可以在响应里安全调用。
- `schemas/retrieval_schema.py` —— 新增 `RetrievalPath = Literal["chunk-index","seed-faq-demo"]`、
  `ChunkRetrievalRequest`（**刻意无 tenant / acl / filter / row_id 字段**）、
  `ChunkRetrievalItem` / `ChunkIndexInfo` / `ChunkRetrievalResponse`；
  `RetrievalSearchResponse` 增加 `retrieval_path: RetrievalPath = "seed-faq-demo"`。

新增文件：
- `tests/retrieval_fixtures.py`（244 行）—— 共用测试基建：确定性假 embedder（zlib.crc32 词袋哈希）、
  `RetrievalEnv`（建租户 / 入库跑流水线 / 重建索引 / 改版本状态 / 授权 / 造 AuthContext）、
  `build_retrieval_app()`。
  ★ `markdown_body(tag, ..., salt="")` 的 `salt` **必传**：同一 tag 生成完全相同的内容会撞
  `uq_document_versions(tenant_id, content_hash)` 被判重，`latest_document_version` 返回 None。
- `tests/test_retrieval_isolation.py`（406 行，**23 条**，规程硬要求）—— 6 个测试类：

  | 测试类 | 条数 | 守什么 |
  | --- | --- | --- |
  | `TestCrossTenantIsolation` | 5 | 两租户互查零命中；manifest 含两租户 + `scope == "all_tenants"` |
  | `TestAclIsolation` | 5 | 受控文档零命中 / 授权后可检索 / role 与 group / write 不隐含 read / tenant 主体 |
  | `TestFilterIsMandatory` | 5 | `access=None` 与假对象报错；空 tenant 报错；空集合零命中；无可见 chunk 租户零命中 |
  | `TestVersionVisibility` | 3 | 真值表：received/stored/parsed/chunked/indexed → 0；published → N |
  | `TestPositiveControl` | 3 | **正向对照**：B 入库后 A 仍能检索到自己的 chunk；命中自证来源；ACL 快照随 chunk 传递 |
  | `TestPreFiltering` | 2 | **长尾租户在 top-k 被 B 语料占满时仍能检索到**；ACL 变化不需重建索引 |

修改文件（已存在）：
- `tests/test_retrieval_api.py`（5 → 14 条）—— 既有 `RetrievalSearchApiTest` 改走演示端点并断言
  `retrieval_path == "seed-faq-demo"`；新增 `TestChunkRetrievalApi` 9 条
  （401 / 403 / 仅本租户 chunk / 响应带索引来源与 `visible_chunk_count` /
  **客户端塞 tenant_id、allowed_chunk_ids、row_ids、filter、acl 全部被忽略** /
  B 只看到自己的 / 无索引 → 503 + `chunk_index_unavailable` / demo 显式标注 / 未知字段不报错）。

★ 测试编写过程中修掉的**自己的错误**（不是产品缺陷，但值得记）：
1. 用 `min_score=0.99` 对短词袋向量**不可靠**（16 维词袋下最窄 chunk 自相似度 1.0、
   更宽的 ≈0.5~0.9）→ 改成"不加阈值 + 断言目标 document_id ∈ / ∉ 结果集合"。
2. `TestPreFiltering` 长尾用例最初只断言 `all(hit.tenant_id == TENANT_A)` ——
   **空列表也满足，守不住任何东西** → 改为断言 `hits` 非空。
3. ACL 用例最初对**已发布的非受控文档**断言"没授权就不可见" → 与 ACL 语义矛盾
   （无记录 = 租户内可见）→ 改为**先 grant 给别人**（变成受控）再断言不可见。
4. `class ChunkRetrievalApiTest` pytest **不收集**（无 `Test` 前缀）→ 改名 `TestChunkRetrievalApi`。
5. `routed` fixture 只 monkeypatch 了 `retrieval_router`，而索引重建端点走 `documents_router`
   的默认值 → 真的去加载 sentence-transformers → 503 `embedding_unavailable`。
   改为**对两个模块都替换** `default_index_root` / `default_embedder` / `default_embedding_model`。

执行命令：
```
python -m pytest tests/test_retrieval_isolation.py tests/test_retrieval_api.py -q
python -m pytest -q                                   # 见下方门禁
python -m pytest --collect-only -q                    # 取 warning 汇总（A6 的规避手段）
python -m ruff check .
python -m compileall services routers schemas models alembic main.py scripts utils config
python scripts/check_repo_data_size.py
```

测试结果（门禁汇总，本批收尾状态）：
- 全量：**884 tests / 0 failures / 0 errors**（JUnit XML 口径 `B7_full_test_junit.xml`）
  = 880 passed + 4 subtests；起点 843（= 839 + 4 subtests）→ **新增 41 条，零回归**。
  逐文件拆解：`test_retrieval_isolation.py` +23（新文件）、`test_tenant_isolation.py` +7、
  `test_retrieval_api.py` +9、`test_auth_context.py` +2。
- warning：**5 条**（`B7_collectonly_warnings.txt`），逐条为既有噪声（fastapi/httpx、
  starlette/anyio、faiss SWIG ×3），**未新增**。
- `ruff check .`：All checks passed
- `compileall`：退出码 0
- `check_repo_data_size.py`：通过，没有超标文件

★ **门禁取数口径变更（本批新踩环境坑 A6）**：pytest 在 sessionfinish 阶段被环境的批量删除
守卫 `SystemExit(1)` 打断（`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":1542,...}`），
**stdout 的标准汇总行没有打印、退出码为 1**，但测试**全部跑绿**。
改读 `--junitxml`（在测试执行期写入，不经 sessionfinish），warning 用 `--collect-only -q` 探针取回。
**判据是「输出里有没有 F／E」＋ JUnit XML，不是退出码。**（对照踩坑 A5：那次是反过来把绿说成红。）

★ **诚实标注**：warning「5 条且未新增」属**间接验证**（collect-only 覆盖导入期 warning，
不等于执行期完备视图）；本批未引入新依赖、无新触发源，故判定持平。不写成"已验证"。

验收结果：PASS

未解决问题：
1. **本批接线没有 AST 守卫**（B6 的 `TestProductionCallPoints` 只扫 `pipeline.py` / `queue.py`）。
   生产调用点靠**手工 grep** 复核（结果见任务级审查第二节）。
   建议 B8 补三条断言：router 引用 `retrieve_chunk_items` / `build_chunk_access_filter`；
   `access` 无默认值；路由层不 import `jwt`。
2. **索引回滚一组六个导出符号零调用点、零测试**（本批新发现，详见下方补充记录）。
3. F2 / F4 / F5 / 真实引擎复验 / 7.2 四格式端到端 仍未做（见任务级审查第十节）。

### ★ B7 沿接线路径炸出的真缺陷：多租户索引互相覆盖（登记踩坑 B14，2026-09-23）

判定依据照 A5 / D12：**异常抛出点在项目代码、单独复跑稳定复现、用户视角是静默零命中**
（最危险的一类）—— 不是环境噪声。红/绿双份证据保留。

现象（`B7_index_multitenant_RED.txt`）：
1. A 租户上传并跑流水线 → 生效索引 `v1`，6 条（全 A）；
2. B 租户上传并跑流水线 → **生效索引仍是 `v1`、仍是 6 条，但条目全变成 B 的**，
   A 的 6 条**从生效索引里消失**；
3. 此时 A 租户检索 → **0 条命中，且没有任何报错**。

根因（三处自相矛盾；单看都合理，放在一起才炸）：

| # | 位置 | 症状 |
| --- | --- | --- |
| ① | `index_builder.rebuild_index` | 只取**触发构建的那一个租户**的可索引 chunk |
| ② | `repository.create_index_build` | 版本号按 `(tenant_id, index_name)` 计 → 两租户都拿 **v1**，切换时 `rmtree` 掉对方目录 |
| ③ | `IndexManifest.verify()` | 要求 `entry.tenant_id == manifest.tenant_id` → 全局索引**永远无法通过校验** |

★ 结论：这不是编码错误，而是**缺失决策** —— 「按租户分片」还是「全局共用一份」
从头到尾**没被显式拍板过**，于是三个子系统各自选了一个。

修复（4 处代码 + manifest 留痕）：
1. `repository.list_indexable_chunks(..., all_tenants=False)` —— `all_tenants=True` 时去掉租户过滤；
2. `repository.create_index_build(...)` —— 版本号查询去掉 `tenant_id` 条件
   （docstring 写明：版本号必须是**全局**的，否则两租户都拿 v1 会互删目录）；
3. `repository.list_index_builds(..., all_tenants=False)` —— 跨租户降级 superseded 用；
4. `index_builder.rebuild_index(..., all_tenants=True)` —— 取数用 `all_tenants`；
   manifest `extra` 增加 `"scope": "all_tenants"|"tenant"` 与 `"tenants": sorted({...})` 留痕；
5. `IndexManifest.verify()` —— 租户不变量放宽为「条目租户 ∈ **声明集合**」
   （缺省退回旧的单租户语义 → **B6 老 manifest 仍能通过**，有意的向后兼容）。

另新增两个只读仓储函数供检索层用：
`list_tenant_document_acl(session, tenant_id)`（一次查询取整租户 ACL）与
`list_published_chunk_refs(session, tenant_id) -> Sequence[(document_id, chunk_id)]`。

验证（`B7_index_multitenant_GREEN.txt`）：A 入库 → `v1` 6 条（A）；
B 入库 → **`v2` 含 12 条（B 6 + A 6）**；两租户各只命中自己的 6 条。
回归守卫：`TestCrossTenantIsolation::test_manifest_contains_both_tenants`
+ `TestPositiveControl::test_own_tenant_can_still_retrieve_after_another_tenant_ingests`。

★ `search_chunk_index` 的契约（B6 交付）**一行未改** —— 修的是根因，不是在调用侧绕过。

### ★ B7 复核新发现：索引「回滚 / 版本枚举」一组导出资产零调用点、零测试（2026-09-23）

计划 3.4 原文要求「增加索引版本**回滚**接口/函数**和测试**」。
本批复核（逐符号全仓 grep，排除 venv 与定义文件）实测：

| 符号 | 定义 | 生产调用点 | 测试调用点 |
| --- | --- | --- | --- |
| `rollback_index()` | `index_builder.py:416` | ❌ 无（也没有 API 出口） | ❌ **无** |
| `available_versions()` | `index_builder.py` | ❌ 无 | ❌ 无 |
| `active_index_version()` | `index_builder.py` | ❌ 无 | ❌ 无 |
| `index_root_for()` | `index_builder.py` | ❌ 无 | ❌ 无 |
| `build_entries()` | `index_builder.py` | ❌ 无 | ❌ 无 |
| `ERROR_INDEX_ROLLBACK_FAILED` | `index_builder.py:87` | ❌ 无 | ❌ 无 |

对照：同模块 `rebuild_index` 有 4 处调用点（`pipeline.py:253` 默认 runner、
`routers/documents.py:680` 新端点、2 处测试），说明**不是**"整个模块没接线"，
而是**接线时只挑了 `rebuild_index` 这一个入口**。

**性质判定**：这不是缺陷（代码本身能跑），而是**「已交付」与「已验证」之间的差距** ——
与 F1 / F3 同一类问题，因此按纪律登记，不放过。

**影响**：
- 对阶段 4：**不阻断**（回滚属阶段 3 的 3.4，不属 4.1/4.2/4.3）；
- 对阶段 7（B8 发布门禁）：**阻断** —— 计划 7.4 明写「索引原子切换 / **回滚**通过」，
  而回滚**没有任何测试**，该判据现在**无法判定为通过**。

**处置建议（B8 三选一，且必须登记）**：① 补测试（成本最低，`rollback_index` 只改指针，
现有 fixture 就能测）；② 补 API 出口；③ 明确声明为"预留能力，不在本次验收范围"并说明理由。
**不能既不测、不用、也不声明** —— 那等于让"回滚已实现"继续误导下一个人。

**本批不做处置的理由**（诚实说明）：不在 4.1/4.2/4.3 范围内（不扩大批次范围）；
且本轮环境删除配额已耗尽（见踩坑 A6），此时新增测试并重跑全量有制造"假红"的实际风险。

### 阶段 4 收尾：阶段级结论 **PASS**（2026-09-23）

阶段 4 的三个任务（4.1 / 4.2 / 4.3）**全部完成**，阶段级结论见
`reports/rag_ingestion_auth_review/stage4_review.txt` 第五节（**PASS**，四条判据同时成立）。
任务级审查见 `B7_task_level_review.txt`。

★ **本阶段最实质的成果 = F3 消除的实证**（`docs/RAG_DEV_PITFALLS.md` F3 条目已回填「修复于 B7」）：

- 「已建未启用」清单里 **`search_chunk_index()` / `ChunkAccessFilter` /
  「检索层 tenant / ACL 过滤的 API 出口」三行全部消失**；
- `grep -n "tenant\|acl" routers/retrieval.py` 从 B6 时的**零命中** → 13 处；
- 检索层隔离测试从**不存在** → **23 条**（数据层那 14 条仍在，**两套并存**）。

⚠️ **阶段 4 PASS ≠ 发布门禁（阶段 7 / B8）PASS**。7.3 / 7.4 里仍有判据不成立，
其中最明确的两条阻断项：**7.2 四种文件端到端未做**、**7.4 索引回滚无测试**。

**本批完成后的门禁基线**：全量 **884 tests / 0 failures / 0 errors**（JUnit XML 口径）
= 880 passed + 4 subtests；`ruff check .` 干净、`compileall` 退出码 0、
`check_repo_data_size.py` 通过、warning 5 条持平。
阶段 3 收尾 843 → 本阶段收尾 **884**，新增 41 条，零回归。

### [规范] 企业级验收规范 v2.1 落盘 + 与项目现状对齐（2026-09-23）

**性质**：**非批次任务**，是「规范落盘」，不改变 B8 排期、不涉及任何功能代码。

- 源 `C:\Users\kk\Downloads\RAG系统方案与企业级验收规范.md`（v2.0，**未改动**，留作对照）
  → **`docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`（v2.1，仓库内稳定引用路径）**。
- **门槛数字与指标定义零改动**（§10.3 的 28 个指标、§10.5 首轮门槛原文照旧）；
  共改 16 处：A 类 3 处（事实过期）、B 类 7 处（把「目标」与「现状」拆成两列）、
  C 类新增 §17 六节（执行层规程）、D 类 1 处 + 配套 2 处。
- 主要产出：**§1.0「本项目当前状态总览」13 行** —— 13 项能力的
  `已实现 / 部分实现 / 未实现 / 已建未启用 / 未就绪 / 未演练` 与证据一次列清。
  其中 `已实现` 3 项（重排、索引 manifest 原子发布、失败阶段恢复与幂等键）。
- 新增 §17《验收作业规程》：门禁取数口径（JUnit XML 优先，退出码/汇总行不可作判据）、
  结论状态机（阶段未完成不写 PASS、阶段 PASS ≠ 门禁 PASS、不可测项写「无法判定」）、
  「已建未启用」判据（定义在、测试在、无人调用 = 未完成）、
  证据落盘与 append-only、**规范编号 ↔ 项目批次映射表**、与 §10.3/§10.5 的关系。
- 审查：`reports/rag_ingestion_auth_review/spec_v2.1_review.txt` —— 任务级 **PASS**。
- 交付前机械校验 **8 项全过**（脚本 **`scripts/verify_acceptance_spec.py`**（本次新建并入库，
  定位是「规范每次改动后的回归门禁」）：737 行、0 悬空引用、
  围栏 4/代码块 2、表格 24=24、顶层章节 1~17 连续）。
- 自检发现并修复 **4 个问题**，其中 1 个是真渲染缺陷：§17.3 列表内缩进代码块
  被渲染成行内代码（源文件肉眼正常，仅渲染暴露）。

> **与 B8 的关系**：B8 引用验收规范**以 v2.1 为准**（路径见上）。
> §10.5 标「不可测」的 4 类指标（输入 / 检索 / 性能 / 成本）属 B8 **之后**的路线，
> **不得据此给 B8 记 NEEDS_WORK**（拿不到数 ≠ 未达标）。

### [踩坑] 规范落盘批次的踩坑登记与文档缺陷修复（2026-09-23）

承接上一条（规范 v2.1 落盘）。**上一条的记录位置不全**：
问题只落进了审查文件与工作记忆，**没进 `docs/RAG_DEV_PITFALLS.md`**（三份 append-only 文件之一）。
已补齐，按「四段式 + 分类编号」登记：

| 编号 | 类 | 主题 |
| --- | --- | --- |
| **B15** | 契约边界 | 验收规范把「目标」与「现状」写在同一格 → 参考与基准两边都不能用 |
| **D13** | 测试方法论 | 列表项内缩进的代码围栏会被吞成行内代码（源文件与围栏总数都「正常」） |
| **D14** | 测试方法论 | 交付前校验器会假报警 → 先校准口径，再判定被校验对象 |
| **E2** | 工程协作 | 并行会话共享工作区 → `git add` 必须用精确路径 |
| **E3** | 工程协作 | 两套编号体系并存且撞车 → 引用必须带前缀 |

另 **回填 A1**（嵌套 ref 坑第 3 次复现，强化「只要分支名含 `/` 就必然发生」）。
**条数：39 → 44**（A6 + B15 + C6 + D14 + E3；F 节 5 条另计）。

**顺带修掉踩坑文件自身两处旧缺陷**：B11、F2 的示例代码写在列表项内、
用缩进围栏，被渲染成**行内代码**（示例根本没显示成代码块）——
即**踩坑记录自己踩了记在 D13 里的同一个坑**。已改为「围栏 + 取消缩进」。
校验：58 个标题 0 缺失、表格 9 = 9、`<code>python` 残留 0、两个代码块均正常渲染。

### [B8-1] 回滚资产做成真能力：回滚 API + 8 条测试（2026-09-23）

**性质**：B8 的第一步（B8 共四步：回滚资产 / AST 守卫 / 四格式端到端 / 门禁与总审查）。
**用户决策**：回滚资产三选一 → 选「**补测试 + 接 ops 回滚入口**」。

#### 交付物

| 文件 | 改动 |
| --- | --- |
| `services/auth_context.py` | 新增资源级权限 **`index:rollback`**（第 10 个）；注释改为「三个刻意的授权划分」 |
| `schemas/document_schema.py` | 新增 `IndexRollbackRequest` / `IndexRollbackResponse`（含 from→to 版本 + 可选版本列表） |
| `routers/documents.py` | 新增 **`POST /ingestion/indexes/rollback`**；常量 `INDEX_ROLLBACK_PERMISSION`；`MASKED_SUMMARY_KEYS` + `reason`；模块 docstring 第 6 条 |
| `tests/test_index_rollback.py` | **新文件**，8 条 |
| `tests/test_auth_context.py` | 权限枚举断言更新（**保留 `==`**）+ 新增「回滚与重建分开授权」 |
| `services/ingestion/index_builder.py` | **删除 `index_root_for`**（零调用零测试的 1 行别名，同时清掉随之无用的 import 与 `__all__` 条目） |

#### 接线登记（规范 §17.3）

`rollback_index` / `active_index_version` / `available_versions` /
`ERROR_INDEX_ROLLBACK_FAILED` / `ERROR_INDEX_BUILD_NOT_FOUND`
→ 生产调用点 `routers/documents.py::rollback_chunk_index`；
`build_entries` → `rebuild_index` 内部（B6 起）。
**`index_root_for` 已删除** → **回滚一族 6 个符号全部有归属，「已建未启用」状态解除。**

> 关键判断：**只补测试不够。** 按 §17.3，测试在但无人调用仍记未完成 ——
> 所以真正的交付物是**端点**，测试只是它的证据。
> 没有归属的东西（`index_root_for`）正确处置是**删掉**，而不是补个测试让它"看起来完成"。

#### 门禁（证据在 `reports/rag_ingestion_auth_review/B8_*`）

| 项 | 结果 |
| --- | --- |
| 全量 | **893 / 0 failures / 0 errors / 0 skipped**（`B8_full_test_junit.xml`，29.5s） |
| 基线对比 | 884 → 893，**新增 9 条**（8 回滚 + 1 权限独立性），**零回归** |
| stdout | `889 passed, 5 warnings, 4 subtests passed`，**退出码 0**（本轮未触发 A6 守卫） |
| warning | **5 条，与基线持平** |
| ruff / compileall / 体积检查 | 全部通过（`B8_ruff-output.txt` 等） |

#### 中途红了一条（判定与处置，详见审查文件第四节）

`tests/test_auth_context.py::test_resource_scopes_follow_the_plan_enumeration` ——
它断言「权限枚举与计划**完全相等**」，而本批加了第 10 个权限。
判据（D12）：AssertionError + 抛出点在项目测试 + 稳定复现 → **真信号，不是环境噪声**；
但它守的是**有意的设计决策** → 改的是**测试期望**，且**保留 `==`**（不放宽成 `>=`），
让「加一道门」必须在这个测试里显式登记（B10）。

#### 口径修正（记入踩坑 D15）

`pytest --collect-only -q` **取不到 warning 条数**（它不执行测试）。
B7 的 `B7_collectonly_warnings.txt` 文件名有歧义 —— 内容只有测试清单，
B7 记的"warning 5 条"其实来自全量 stdout。本批证据文件改名 **`B8_collectonly_tests.txt`**
（名副其实：取测试数），warning 改从**全量 stdout** 取。已同步修正 MEMORY.md 第 8 节。

#### 审查

`reports/rag_ingestion_auth_review/B8_step1_rollback_review.txt` —— 任务级 **PASS**。
声明范围：**不等于 7.4 判据通过**（7.4 是整组，回滚只是其中一条，另有 7.2 未做），
**不等于 B8 PASS**，更不等于发布门禁 PASS（§17.2 两级分别出具）。

#### 下一步

B8 剩余三步：① 接线 AST 守卫；② 四格式端到端（7.2 判据）；③ 7.3 逐项证据 + 总审查 + 发布门禁。

### [B8-2] 接线 AST 守卫：把 B7 的接线从「人工核对」变成「测试锁定」（2026-09-23）

**性质**：B8 第二步（作业规程的「前置项 2」）。

#### 背景：为什么需要

B7 把检索从 A 轨（种子 FAQ，无 tenant / ACL）切到 B 轨（chunk 级 + 服务端权限过滤），
但这次切换**此前只有人工核对**。谁把 `routers/retrieval.py` 里那两行改回去，
或给 `search_chunk_index` 的 `access` 加个默认值，**测试仍然会全绿** ——
要等线上检索越权才暴露。这正是 F1「已建未启用」的复现方式，所以用 AST 钉死（踩坑 D1）。

#### 交付物

`tests/test_wiring_guards.py`（**新文件**，18 条）：

| 守卫 | 断言 |
| --- | --- |
| 1 | `routers/retrieval.py` 必须**引用并调用** `retrieve_chunk_items` + `build_chunk_access_filter`（**只 import 不算**） |
| 2 | `utils/vector_retriever.py::search_chunk_index` 的 `access` **无默认值**（位置参数与 keyword-only 两侧都查） |
| 2b | `access` 注解必须存在，且**不含 `None` / `Optional`**（堵"先软化注解、再给默认值"的写法） |
| 3 | `routers/*.py` 一律不得 import `jwt`（12 个 router parametrize） |

**外加两条「守卫自身的健全性检查」**：目标文件必须存在；扫到的 router 文件数不得少于 10
（否则 parametrize 拿到空列表会让测试**静默变绿**）。
**再加一条反向验证**：用临时文件构造违规样例，确认守卫**真的会红** ——
否则前面所有断言都可能是装饰（记入踩坑 **D16**）。

#### 关键判断：守卫自己也要有测试

守卫最坏的失效方式是**静默变成空** —— 目标被改名或挪走，断言再无对象可执行，测试照样绿。
因此三件事一起做：① `_function_def` 取不到函数时**直接报错**（不返回 None）；
② 文件数健全性检查；③ 反向验证。

#### 门禁（证据 `reports/rag_ingestion_auth_review/B8_step2_*`）

| 项 | 结果 |
| --- | --- |
| 全量 | **911 / 0 failures / 0 errors / 0 skipped**（`B8_step2_junit.xml`，28.2s） |
| 基线对比 | 893 → 911，**新增 18 条**（守卫），**零回归** |
| stdout | `907 passed, 5 warnings, 4 subtests passed` |
| warning | **5 条持平** |
| ruff / compileall / 体积检查 | 全部通过 |

#### 位置选择说明

规程允许加在 `test_ingestion_pipeline.py` **或新文件**，本批选**新文件**：
接线守卫是一类**跨模块的架构约束**（不是流水线内部行为），
且"随时能加一条守卫"不该每次去动那个已经很大的既有测试文件。
AST 辅助函数**有意未抽公共模块**（抽取需要改动既有测试），理由写在文件 docstring 里。

#### 下一步

③ 四格式端到端（7.2 判据，交付 `tests/test_release_gate.py` + 证据 `B8_e2e_four_formats.txt`）
→ 7.3 逐项证据 → 阶段 7 总审查与发布门禁。

### [B8-3] 四格式端到端：7.2 判据通了（2026-09-23）

**性质**：B8 第三步（作业规程「前置 3 件事」里的四格式端到端）。

#### 交付物

`tests/test_release_gate.py`（**新文件**，7 条 = 4 格式 parametrize + 3 条专项）
+ 证据 `reports/rag_ingestion_auth_review/B8_e2e_four_formats.txt`。

覆盖 7.2 的八条：

| # | 要求 | 实现 |
| --- | --- | --- |
| 1 | 走**真实路由**上传 | `POST /knowledge-bases/{id}/documents`（multipart） |
| 2 | 消费任务跑完流水线 | 测试内 `IngestionPipeline.process_job`（规程允许） |
| 3 | 重建索引 | `POST /ingestion/indexes/rebuild`（真实端点） |
| 4 | 检索**命中本文档 chunk** | `POST /retrieval/search`；断言 `retrieval_path == "chunk-index"` 且命中集合与本文档 chunk 有交集 |
| 5 | 链路串联 | 命中的 `document_id` / `document_version_id` / `document_version` / `tenant_id` / `source_type` 一致，且 `text` 与库内 chunk **逐字相同** |
| 6 | 长文档 ≥ 2 chunk | Markdown 样本断言 |
| 7 | 重复上传不产生重复有效版本 | 两次上传同一文件 → `list_document_versions` 仍为 1 |
| 8 | 失败可查错 + 可重试 | 构造"过准入但解析失败"的 docx → `error_code` 非空 + `/reprocess` 新建 job |

#### 为什么这条测试非有不可

其它测试都是**分段**的：上传测到"建了 job"、流水线测到"跑完 8 阶段"、
检索测到"能按权限过滤"。**没有一条把它们串起来** —— 而 7.2 要的正是"串起来能跑通"。
F1 双轨制与 B14 的静默零命中，都是"两段各自全绿、接口对上才暴露"的产物。

夹具同时挂**上传与检索两个 router**，并把**两组**生产默认值（上传一组、检索一组）
都换成**同一个** `fake_embedding` —— 否则查询向量与索引向量不在同一空间，分数没有意义。

#### 一个刻意的判断：query 取自文档自己的 chunk

测试 embedder 是词袋哈希，query 必须与 chunk 共享 token 才可能有分。
用文档自己的词构造 query ＝"用户问文档里写过的内容"，**验证的是链路通不通**，
而不是检索质量 —— 后者属 M10 Recall@k，其口径修正尚未做（验收规范 §10.3 已标 N/A）。
**不拿"链路测试"冒充"检索质量评测"**，这是本批的一条表述纪律。

#### 中途走了一次弯路（记入踩坑 D17）

第一版用 `b"PK\x03\x04" + 零字节` 当"损坏文件"，被**准入**以 415 拦下 ——
说明「准入校验」与「解析校验」是**两层**，"坏文件"要针对**目标那一层**构造：
准入只要求「合法 zip + 存在 `word/` 条目」（`content_sniff._is_docx_zip`）。
改用 `zipfile` 造一个含 `word/document.xml`（内容为非法 XML）的**合法 zip**
→ 过准入、解析失败。**副产品**：那次 415 是"准入确实在工作"的正面证据。

#### 门禁（证据 `reports/rag_ingestion_auth_review/B8_step3_*`）

| 项 | 结果 |
| --- | --- |
| 全量 | **918 / 0 failures / 0 errors / 0 skipped**（`B8_step3_junit.xml`，41.3s） |
| 基线对比 | 911 → 918，**新增 7 条**，**零回归** |
| stdout | `914 passed, 5 warnings, 4 subtests passed` |
| warning | **5 条持平** |
| ruff / compileall / 体积检查 | 全部通过 |

#### 对阶段 7 门禁的影响

7.4 的两条已知阻断项**至此全部消除**（索引回滚 → B8-1；四格式端到端 → 本批）。
下一步只剩：**7.3 六条逐项证据 + `stage7_review.txt` + 发布门禁结论**。

## 4. 执行暂停点（下次从这里继续）

**当前停在：B7 结束（任务 4.1 + 4.2 + 4.3 全部完成，阶段 4 已 PASS），
等待开始 B8（阶段 7 总审查与发布门禁）。**

> **暂停点更新时间：2026-09-23**
> 下一批的**完整作业规程**（进度快照 + 五步闭环 + 记录格式模板）见
> **`docs/RAG_NEXT_WINDOW_PROMPT.md`** —— 新开窗口**整份粘贴**即可，不要改写。
> 该规程要求的五步闭环：核对基线 → 读进度与约束 → 干活 → 门禁自检 → 审查 → 记录
> （四处记录**全部必须带日期**）。

上次收尾状态（B7）：**~~工作区包含 B5 + B6 + B7 全部改动（尚未提交）~~**
→ **2026-09-23 校正：B5 + B6 + B7 已由用户提交并推送**
（`HEAD = 624d4968…`，提交信息「B7: 检索 API 正式接线（chunk 级 + 权限过滤），阶段 4 收尾 PASS」），
工作区干净、与 `origin/optimize/interview-ready` **同 SHA**。

> ⚠️ 随之失效的一处陈述（**不要照旧做**）：`stage4_review.txt` 第三节写的
> 「回滚点：本阶段全部改动尚未提交，`git checkout --` / `git stash` 可回到 B6 状态」
> **已不成立**。现在要回到 B6 状态只能靠 `git revert` / `git reset`，属**改写历史**的操作 ——
> **提交/推送/历史一律归用户，不要动。**

本批结束后全量 **884 tests / 0 failures / 0 errors**（JUnit XML 口径 = 880 passed + 4 subtests，
起点 843 = 839 + 4 subtests，新增 41 条，零回归）、`ruff check .` 干净、
`compileall` 退出码 0、`check_repo_data_size.py` 通过、warning **5 条**（未新增）。
新窗口开工前先 `git status -sb` + `git rev-parse HEAD` 复核实际状态
（B7 收尾当时 HEAD 是 `a670373185cd074b4ceae0c4008e55135276f341`；
**2026-09-23 校正**：B7 提交后为 `624d4968366f095b6ffcb32d27dee41ffe08d382`）。

**2026-09-23 开 B8 前的实测复核（本次校正的依据）**：

- 全量测试 **884 / 0 failures / 0 errors / 0 skipped**（JUnit XML：`tmp/b8_baseline_junit.xml`，43.8s）；
- ⚠️ 跑的过程 **A6 那个环境守卫再次出现**（`SAFE_DELETE_BULK_CONFIRM_REQUIRED {"count":108,"threshold":50,"scope":"turn"}`）
  → 进度条全绿到 `[100%]`、**无汇总行**、退出码 1 —— **测试实际全绿**，按 JUnit XML 取数；
- 回滚一族 6 个符号**仍零调用点零测试**（前置项 1 未做，`rebuild_index` 仍是唯一接线的入口）；
- B7 的检索接线**仍在**：`routers/retrieval.py:46/55/151/153` 引用
  `build_chunk_access_filter` / `retrieve_chunk_items`，`tenant|acl` 命中 **10 处**（B6 时是 0）；
- **F4 的范围比登记的更大**：README 的 `332 passed` 出现在 **3 处**
  （第 8 行 badge、第 286 行「测试文件数 30」表、第 427 行目录树注释），
  不是"一行的事"；三处都要改，测试文件数也要重数。

> **⚠️ 门禁取数口径在 B7 变了（踩坑 A6）—— B8 必读，否则会把绿读成红**
>
> | | B5 收尾那次（A5） | B7 收尾起（A6） |
> | --- | --- | --- |
> | 现象 | `1 failed, 789 passed` | 进度条全绿到 `[100%]`，但**没有汇总行**、退出码 1 |
> | 异常 | `SystemExit`（环境删除守卫） | 同左：`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":1542,...}` |
> | 真伪 | **假红**（测试其实绿） | **假红**（测试其实绿），但这次连"绿"的证据都被吃掉 |
> | 处置 | 不改代码 | **改读 `--junitxml`** |
>
> **B7 起的固定做法**（照做）：
> 1. `pytest -q --junitxml=reports/rag_ingestion_auth_review/<批次>_full_test_junit.xml`
>    → `tests / failures / errors` 三个属性就是权威计数；
> 2. warning 条数用 `pytest --collect-only -q` 的 warnings summary 取（只收集不执行，
>    不产生批量删除）；
> 3. **判据是「输出里有没有 F／E」＋ JUnit XML，不是退出码、也不是汇总行**；
> 4. **一轮只跑一次全量**（A5）——逐符号/逐文件的核实一律用 `--collect-only` 或定向跑单文件。
>
> `--basetemp` 与 `PYTEST_DEBUG_TEMPROOT` 都试过了：前者无效，后者直接卡死。

> **⚠️ 「门禁红了」的判据（A5 + D12 + A6 三条合并）**
> ① 先看**异常类型** —— `AssertionError` / 业务异常 = 逻辑信号；
> `SystemExit` / `PermissionError` / 断连 = 环境信号；
> ② 再看**抛出点** —— 在项目代码里就是真的；
> ③ 最后**单独复跑那一条**（不要重跑全量）；
> ④ **新增（A6）**：如果输出里**没有** `F` / `E`，先怀疑是守卫把汇总行吃了，
> 去读 JUnit XML 再下结论。
>
> **证据文件的红/绿双份约定**：B6（`B6_baseline_test.txt`）、B7
> （`B7_index_multitenant_RED.txt` ↔ `_GREEN.txt`）都保留了"问题现场"。
> **问题现场的证据不要删** —— 只留绿色那份，"这里红过、为什么红、怎么修的"整段历史就消失了。

### B8 本批要做什么（阶段 7：总审查与发布门禁）

**B8 与前 6 批性质不同：前面都是"写代码"，B8 主要是「出证据 + 做终审」。**
计划第 7 节的四小节（7.1 自动检查 / 7.2 文件链路验收 / 7.3 安全审查 / 7.4 阶段完成条件）
就是 B8 的作业清单，逐条出证据，最后写结论。

**B8 要交付什么**：

1. **7.1 自动检查**（四件套）—— 重跑取最终数字，落 `B8_*` 证据。
   注意用 A6 的取数口径（JUnit XML）。
2. **7.2 四种文件端到端链路**（**B7 遗留，未做**）——
   PDF / DOCX / HTML / Markdown **各跑一次**：上传 → 解析 → 切分 → 落库 → 进索引 → 检索，
   确认文档版本 / 解析块 / Chunk / manifest / 检索结果都能由 `document_id` 串联；
   长文档至少 2 个 chunk；每个 chunk 有来源、租户、版本、页码或标题路径、ACL；
   重复上传不产生重复有效版本；失败任务可查错和重试。
   `tests/fixtures/` 下已有四种格式的可复现样本（B3 的 `scripts/make_parser_fixtures.py` 生成）。
3. **7.3 安全审查逐项**（六条，逐条出证据）——
   伪造 `X-User-Role` 不提升权限 / body 的 `tenant_id` 不影响范围 / 跨租户资源返回 404
   且不泄漏存在性 / 无 ACL 文档不出现在结果、引用、trace 和错误信息 /
   未发布、归档、过期、旧版本不进入索引 / 索引构建失败不破坏旧版本。
   现状：B7 时已声称"大部分已覆盖"（见 `stage4_review.txt` 第六节 C 表），
   B8 要把它们从"已覆盖"变成**逐条可指认的证据文件**。
4. **7.4 阶段完成条件** —— 全部自动测试通过 / 四种文件端到端通过 /
   权限负向测试全部通过 / **索引原子切换与回滚通过** / 审查材料落
   `reports/rag_ingestion_auth_review/` / 结论写 `PASS` 或 `NEEDS_WORK`。
   ★ **「回滚通过」目前无法判定** —— 见下方"必须先处置的 3 件事"第 1 条。
5. **结论文件** `reports/rag_ingestion_auth_review/release_gate_review.txt`
   （或按批次命名 `B8_task_level_review.txt` + `stage7_review.txt`），
   明确 PASS / NEEDS_WORK。**NEEDS_WORK 时不得进入后续阶段。**

### ★ 必须先处置的 3 件事（B8 开工第一小时就做）

按优先级排列。前两条是 B7 明确挂出来、**不处置就没有资格写发布门禁 PASS** 的：

**1. 索引「回滚 / 版本枚举」一组导出资产（阻断 7.4）**

   `services/ingestion/index_builder.py` 里这 **6 个导出符号全部零调用点、零测试**：
   `rollback_index` / `available_versions` / `active_index_version` / `index_root_for` /
   `build_entries` / `ERROR_INDEX_ROLLBACK_FAILED`。
   计划 3.4 原文要求「增加索引版本**回滚**接口/函数**和测试**」—— 函数有、测试没有。
   **三选一并登记**：① 补测试（推荐，成本最低：`rollback_index` 只改指针、不重建不删文件，
   现有 fixture 就能测）；② 补 API 出口；③ 明确声明为"预留能力，不在本次验收范围"并说明理由。
   **不能既不测、不用、也不声明。**
   （复核命令：`for fn in rollback_index available_versions active_index_version index_root_for
   build_entries ERROR_INDEX_ROLLBACK_FAILED; do grep -rn "\b$fn\b" --include=*.py . |
   grep -v venv | grep -v index_builder.py; done` → 全部无输出）

**2. 给 B7 的新接线补 AST 守卫**

   B6 有 `TestProductionCallPoints`（AST 逐函数查形参，沿用 D1 的教训），
   但它只扫 `services/ingestion/pipeline.py` 与 `queue.py`。
   B7 的「API → 检索层」接线**只有手工 grep 复核**，没有断言守着。
   建议补三条：
   · `routers/retrieval.py` 必须引用 `retrieve_chunk_items` 与 `build_chunk_access_filter`；
   · `search_chunk_index` 的 `access` 参数**必须没有默认值**（防"顺手加个默认全库"）；
   · 路由层不得 import `jwt`（防在 router 里重建身份解析）。
   B7 未补的理由已记录在 `B7_task_level_review.txt` 遗留 1：本轮环境删除配额已耗尽（A6），
   此时加测试并重跑有制造假红的实际风险。

**3. 7.2 的四种文件端到端链路**（见上）

### ⚠️ 「已建未启用」资产清单（2026-09-23 B7 收尾后复核）

**背景**：复核发现整仓存在**双轨制** —— ingestion 子系统与线上检索链路长期没接上。
B5 / B6 接通了数据侧，**B7 接通了检索侧**。下表是复核后剩下的状态。
（划掉的行 = 已被某批补上调用点，**保留在表里**是为了让"曾经欠过账"这件事可见。）

| 资产 | 定义位置 | 生产调用点 | 测试调用点 |
| --- | --- | --- | --- |
| ~~`chunk_document()`~~ | `services/ingestion/chunkers/chunker.py` | ✅ B6 接上（`pipeline.py:252`） | 有（B5，235 例） |
| ~~`insert_chunks()` / `delete_chunks()`~~ | `repository.py:545` / `:597` | ✅ B6 接上（`pipeline.py:576-577`） | 有 |
| ~~queue 消费端（`xreadgroup` / `xack`）~~ | `services/ingestion/queue.py` | ✅ B6 接上（`worker.py`） | 有 |
| ~~`index_builds.manifest_uri`~~ | `models.py:430` | ✅ B6 接上（`index_builder.py`） | 有 |
| ~~`search_chunk_index()`~~ | `utils/vector_retriever.py:1083` | ✅ **B7 接上**（`utils/vector_retriever.py:1205`，`retrieve_chunk_items` 内） | 有（B6 入口守卫 + **B7 端到端 23 例**） |
| ~~`ChunkAccessFilter`~~ | `utils/vector_retriever.py:969` | ✅ **B7 接上**（构造：`services/retrieval_access.py:162`；消费：`routers/retrieval.py:151`） | 有 |
| ~~检索层 tenant / ACL 过滤的 API 出口~~ | `routers/retrieval.py` | ✅ **B7 接上**（`POST /retrieval/search`） | 有（`TestChunkRetrievalApi` 9 例） |
| `queue.publish()` | `queue.py` | ✅ `routers/documents.py` | 有 |
| **`rollback_index()`** | `index_builder.py:416` | ❌ **无（也没有 API 出口）** | ❌ **无** ← B8 处置 |
| **`available_versions()` / `active_index_version()` / `index_root_for()` / `build_entries()` / `ERROR_INDEX_ROLLBACK_FAILED`** | `index_builder.py` | ❌ 无 | ❌ 无 ← B8 一并处置 |

**读法**：B7 之后，**「检索侧」的欠账已清空**；剩下的欠账集中在
`index_builder.py` 的**回滚 / 版本枚举**这一族，且它直接卡住 7.4 的一条判据。

B7 复核的其余结论（详见 `B7_task_level_review.txt` 与 `stage4_review.txt`）：

1. **`data/rag_metadata.db` 不存在** → alembic 0001 从未落到开发库（与 B1~B6 相同）；
2. **检索评测口径失真**（F2，**仍未修**）：`scripts/evaluate_retrieval_metrics.py:85` 按 intent
   匹配判相关，报出的 recall@1 **不是文档 / Chunk 级指标** → 该数字不得作为检索能力证据；
3. **~~`tests/test_tenant_isolation.py` 只测数据层~~** → ✅ **F3 已消除**（B7）：
   新增 `tests/test_retrieval_isolation.py` 23 条检索层用例 + 真实路由层 `TestAclOnRealRoutes` 7 条，
   数据层 14 条**保留**，两套并存。`docs/RAG_DEV_PITFALLS.md` 的 F3 条目已回填「修复于 B7」；
4. **README badge 数字**（F4，**部分推进**）：B7 已加第 0 节「两条检索路径：哪条是真的」表格，
   但 badge 里的测试数是否同步到 **884** 未核对（文档维护项，不在代码门禁内）；
5. **worker 有 CLI 入口但没有进程编排**（F5，**仍未修**）：
   `python -m services.ingestion.worker` 可用且 `main(argv)` 有测试，
   但 `main.py` 的 lifespan 不启动它，`docker-compose.yml` 只有 `api` / `postgres` / `redis`
   → **容器化部署后队列仍然没人消费**；
6. **7.2 四种文件端到端链路至今未做**（B4 时归入"阶段 3 与阶段 7"，B7 仍未做）→ B8；
7. **真实引擎全部未复验**：PostgreSQL / Redis Stream / 真实 tokenizer + embedding / OCR
   （本机无 Docker、无 psql、无 Redis）→ B8 门禁（或明确声明为已知限制）；
8. **索引重建的并发竞态未测**（两个请求同时重建的版本号竞态），单机 SQLite 难以构造。

**执行纪律补充（B6 立、B7 沿用，B8 仍适用）**：
每个新模块必须登记**生产调用点** —— 定义在、测试在、但没人调用等于未完成；
权限隔离测试必须**数据层与检索层各写一套**，不允许用前者代替后者；
指标命名必须精确（`intent_hit_rate` 不能叫 `recall`）；
**"问题现场"的证据文件要保留**（见本节开头的红/绿双份约定）；
**新增（B7）**：门禁取数用 JUnit XML（A6），一轮只跑一次全量（A5）。

**B8 开工前必读：**

- 本文件第 5 节「四个环境坑」—— **commit/push 前必读**，尤其坑 1（嵌套 ref）每次 commit 必踩、
  坑 2 的结论是「代理与直连都间歇抽风，只能交替重试并以远端 SHA 为准」、
  坑 3 的 venv 是轻量版（pip 必须显式加 `--trusted-host mirrors.aliyun.com -i https://...`）；
  另有第五条环境坑（pytest 删除守卫）见踩坑记录 **A6**，它只影响**跑测试**、不影响 commit；
- **`reports/rag_ingestion_auth_review/stage4_review.txt`** —— 阶段 4 的阶段级审查，
  第六节 C 表列了 **7.1 / 7.2 / 7.3 / 7.4 每一条的"当前状态 + 处置建议"**，
  这是 B8 的工作清单来源；
- **`reports/rag_ingestion_auth_review/B7_task_level_review.txt`** —— B7 的任务级审查，
  含 9 处**偏离计划原文的方向标注** + 第八节**测试盲区 7 条** + 第十节 **8 条遗留问题**；
- **`docs/RAG_DEV_PITFALLS.md`（踩坑记录）**：每批结束后**必须**把新踩的坑追加进去
  （格式：现象 → 根因 → 解决 → 面试怎么讲）。这是 `docs/goal.md` 明确要求的面试复盘素材。
  B7 新增 **A6**（环境删除守卫吃掉 pytest 汇总行 → 改用 JUnit XML）、
  **B14**（多租户索引互相覆盖：缺失决策，不是编码错误）共 2 条，
  并**回填 F3**（修复于 B7）。**禁止断更**，无新坑也要写「本批未新增坑」；
- `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 第 7 节
  （7.1~7.4 的计划原文 = B8 的范围来源）。

B8 继续时的入口动作：

0. 先读第 5 节环境坑 + 踩坑 A6，再 `git status -sb` + `git rev-parse HEAD` 复核仓库状态；
1. 复读第 1 节分批表，确认 B8 范围 = 阶段 7（7.1 + 7.2 + 7.3 + 7.4）；
2. 跑一次全量，用 A6 的口径取数（`--junitxml`），确认起点是 **884 / 0 failures / 0 errors**；
3. **先处置「必须先做的 3 件事」**（回滚资产 → 接线 AST 守卫 → 四格式端到端），
   再走 7.3 的逐项安全审查；
4. 必须沿用的既有约定（**一条都不要顺手放宽**）：
   * `search_chunk_index()` 的 `access` 是**必填关键字参数**，显式传 `None` 也抛错 ——
     **不要给它加默认值全库**；
   * `ChunkAccessFilter` **只允许服务端构造**（唯一构造点 `services/retrieval_access.py:162`），
     不由客户端传入；`routers/retrieval.py` 的请求模型里**不得**新增 tenant/acl/filter/row_id 字段；
   * 过滤用 FAISS **原生预过滤**（`SearchParameters(sel=IDSelectorBatch(...))`），
     **不要改成"先全局 top-k 再后筛"**（后过滤在长尾租户上会静默返回 0 条）；
   * **无权限对查询者 = 零命中**（不是 403）、**缺 filter 对调用方 = 报错** ——
     拍板记录见 **[D-12]**；
   * ACL 语义：无记录 = 租户内可见；`write` 不隐含 `read`；`group` fail closed ——
     见 **[D-13]**（改这三条必须补新的决策记录）；
   * 切分入口签名冻结：`chunk_document(document, *, context: ChunkContext, config: ChunkConfig)`，
     `tenant_id` 只能经 `ChunkContext` 传；改回去会挂 B5 的 AST 守卫；
   * 版本创建走 `repository.create_document_version()`，判重走 `find_version_by_content_hash()`；
   * 任务状态推进只走 `repository.fail_ingestion_job` / `update_ingestion_job`；
   * 警告固定在 `document_versions.metadata_json["warnings"]`（形状 `{code, message, detail}`）；
   * 删除某版本全部 chunk **必须是一条 DELETE 语句**（B13 的教训）；
   * 读回 chunk 顺序**只能信 `metadata_json["ordinal"]`**；
   * **索引是全局一份**（B14 的教训）：`rebuild_index` 默认 `all_tenants=True`，
     版本号必须**全局**递增，manifest `extra["scope"]` / `extra["tenants"]` 必须留痕；
     **不要退回"按租户分片"**（B7 之前的状态会让先入库的租户静默消失）；
   * 鉴权走 `services/auth_context.py`，**不要在 router 里重建身份解析**；
   * 发布（`document:publish`）与索引重建（`index:rebuild`）**分开授权且集合不同**，
     `index:rebuild` = {supervisor, admin} 的收窄是有意的，不要放宽；
5. 每个新模块/新函数必须登记**生产调用点**（B7 刚在 `rollback_index` 上吃过这个亏）；
6. 门禁跑完后回到本文件**追加**一条记录（不覆盖历史），并把证据落到
   `reports/rag_ingestion_auth_review/`（**B8 的证据文件统一用 `B8_` 前缀**）。

需要注意的既有约束（仍适用，B8 必读）：

- 解析器**不接收 tenant_id、不写数据库**，两条已有测试兜底；
  切分器同理（B5 的 9 条 AST 守卫，含「签名逐参数冻结」）；
  B6 又补了 5 条「生产调用点」AST 守卫（`TestProductionCallPoints`，**只覆盖 pipeline/queue**）
  与 2 条契约守卫。
- `document_versions.content_hash` 是**租户粒度唯一**；解析器只负责把 SHA-256 放进
  `ParsedDocument.metadata["content_hash"]`，**判定在流水线**。
- **上传接口不建版本**（[D-6]）：`document_versions` 由流水线在 `parsed` 阶段创建。
- **切分器不接收裸 tenant_id**（[D-9]）：归属信息一律经 `ChunkContext` 传入；
  **流水线是构造 `ChunkContext` 的唯一地方**。
- 解析警告放 `document_versions.metadata_json` 的固定键 `"warnings"`；
  若要独立列**必须**走 Alembic 新迁移并同步 `tests/test_ingestion_models.py` 的对照断言。
- 切分配置留痕放 `document_versions.metadata_json["chunking"]`；
  配置**越界即失败**（`invalid_chunking_config`），**不静默夹取**。
- 新增依赖同步 `requirements.txt` 与 `requirements-dev.txt`：**测试会 import 的必须两侧都加**。
- 测试跑在临时 SQLite 上，`tests/conftest.py` 已固定鉴权环境变量；
  可用 `tests/auth_helpers.py` 自签令牌。
  **B7 起还有一套现成的检索侧基建：`tests/retrieval_fixtures.py`**
  （确定性假 embedder + `RetrievalEnv`，两租户入库/重建/授权一条龙）—— B8 做端到端优先复用它。
- `tests/fixtures/` 下的样本必须 < 1 MB；`.gitattributes` 已把两个文本样本锁成 LF。
## 5. 提交与仓库同步记录

| 提交 | 内容 | 规模 |
| --- | --- | --- |
| `9760049` | `docs:` 加入 RAG 数据接入/切分/权限改造执行计划 | 1 文件 |
| `0ea5834` | `feat(rag):` 阶段 1 数据模型迁移 + JWT 身份上下文（B1+B2） | 57 文件，+4779 / −236 |
| `99fd798` | `docs:` 记录 B1+B2 提交结果与仓库提交/推送环境坑 | 1 文件，+53 / −1 |
| `df60946` | `docs:` 补全环境坑实测细节 | 1 文件，+14 / −4 |
| `1e3e53a` | `feat(rag):` 阶段 2 解析契约 + 五种格式解析器与注册表（B3） | 27 文件，+5095 / −46 |

> 上表只列到 `1e3e53a`；**本文件自身的后续文档提交不会再回填**（否则永远差一条）。
> 需要最新提交号时直接 `git log --oneline -n 10`。

- 分支：`optimize/interview-ready`；远端 `origin` = `https://github.com/kue04/llm-customer-service.git`
- 2026-09-23（B1+B2）推送成功，远端 `refs/heads/optimize/interview-ready` =
  `0ea5834738a031dfe0a7c6b0554c87a1fca50e11`
- 2026-09-23（B3）推送成功，远端 `refs/heads/optimize/interview-ready` =
  `1e3e53a1ef8ce2743222898aee7f4de54fb49a37`
  验证方式：`git push` 输出 `69ddd0e..1e3e53a`，并用 GitHub API 交叉核对
  `GET /repos/kue04/llm-customer-service/branches/optimize%2Finterview-ready`
  返回的 `commit.sha` 与本地 `git rev-parse HEAD` 完全一致。
  **推送实际重试了 5 次才成功**（前 4 次直连与代理都被拒），详见坑 2。
- `.gitignore` 已把 `reports/execution_baseline/`、`reports/rag_ingestion_auth_review/` 两个目录
  从 `reports/*` 的忽略中排除（计划明确要求这两处评审材料落盘，属交付证据，体积均在 10 KB 内）。

### ⚠️ 本仓库的环境坑（后续每次提交都会遇到，务必按此操作）

> B1/B2 期间只有两条（坑 1、坑 2）；B3 期间坑 1 再次复现，坑 2 的结论被推翻重写为
> 「间歇性、需交替重试」，并新增坑 3（venv 重建）与坑 4（自动化执行环境的通道差异）。
> 四条都按最新实测更新过。

**坑 1：git 无法自动创建嵌套 ref 目录，导致 commit「成功」但分支指针不前进。**

分支名含 `/`（`optimize/interview-ready`），但 `.git/refs/heads/` 下**不存在** `optimize/` 子目录
（该分支只活在于 `.git/packed-refs`）。git 写 loose ref 时未能自动补建该子目录，
结果是：`git commit` 打印成功、commit 对象与 reflog 都已写入，**但分支指针不动**，
`git rev-parse HEAD` 仍返回旧提交。

识别症状：`git log` 看不到刚提交的内容，但 `git reflog` 里能看到该提交。

**B3 提交实测再次完整复现**（说明这不是偶发，而是只要分支名含 `/` 就必然发生）：
`git commit` 成功 → `git reflog` 里有 `1e3e53a`，但 `git rev-parse HEAD` 返回的是
`e5d5bc3`（`packed-refs` 里的旧值），`.git/refs/heads/optimize/interview-ready`
这个文件**根本不存在**（`optimize/` 目录被 git 的 ref 更新动作清掉了）。
按下面的处理姿势 mkdir + 写 40 位 SHA 之后立即恢复正常。
**推论：提交后绝不能只看 `git commit` 的输出就认为成功，必须 `git rev-parse HEAD` 复核。**

实测结论（2026-09-23 逐条验证过）：
- `git status` / `git log` / `git rev-parse` 都**不会**动 `.git/refs/**`，只有 `git commit` 会踩坑；
- 目录不存在时 `git update-ref refs/heads/optimize/<新名字> <sha>` 能成功写入；
  但对**当前分支名** `interview-ready` 调用 `git update-ref`，会返回 0 却静默不落盘；
- `git update-ref -d <嵌套 ref>` 会顺手把因此变空的父目录 `optimize/` 一起删掉，
  连目录里的 ref 文件一并带走 —— **不要用它**。

处理（每次 commit 后都要做）：
```bash
mkdir -p .git/refs/heads/optimize .git/refs/remotes/origin/optimize
git rev-parse <刚提交的短SHA>     # 取 40 位完整 SHA
printf '%s\n' <完整SHA> > .git/refs/heads/optimize/interview-ready
git rev-parse HEAD                # 必须输出上面那个完整 SHA
```
> 注意 1：ref 文件里**必须写 40 位完整 SHA**。写 7 位缩写会让 git 报
> `fatal: ambiguous argument 'HEAD'`，把仓库搞得读不出 HEAD。
> 注意 2：`mkdir` 与写入务必放在**同一条命令**里执行，因为 `optimize/` 目录
> 随时可能被 git 的 ref 更新动作清掉。

**坑 2：代理与直连**都会间歇性抽风**，唯一可靠的做法是「探测 + 交替重试」。（2026-09-23 B3 期间实测修订）**

原记录写的是「代理 `127.0.0.1:7890` 已失效，GitHub 可直连，push 时要绕过代理」。
B3 期间的实测把这个结论推翻了两次，最终结论是：**两条路径都不是恒定的，取决于当下**。

实测时间线（同一天、同一台机器）：

| 时刻 | 直连 github.com:443 | 经代理 127.0.0.1:7890 | 实际用哪条 |
| --- | --- | --- | --- |
| B1/B2 期间 | 可用（curl 200） | 失效（连接被拒） | 直连 |
| B3 开头（推 `69ddd0e`） | 不可用（`Empty reply from server` / 21 秒超时） | **可用** | 代理 |
| B3 收尾（推 `1e3e53a`） | 时好时坏：`ls-remote` 通、`push` 前 4 次被拒、**第 5 次成功** | 全程不可用（`Failed to connect ... over proxy`） | **直连** |

所以正确做法不是「永远绕过代理」或「永远走代理」，而是：

```bash
# 1) 先各探一次，确认当下哪条能通（返回 SHA 才算通）
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  git -c http.proxy= -c https.proxy= ls-remote origin refs/heads/optimize/interview-ready
git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 \
  ls-remote origin refs/heads/optimize/interview-ready

# 2) 推送时两条交替重试。B3 收尾那次直连推到第 5 次才成功，
#    「第一次失败就换策略」会误判成两条都不通。
for i in 1 2 3 4 5; do
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    git -c http.proxy= -c https.proxy= push origin optimize/interview-ready && break
  git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 \
    push origin optimize/interview-ready && break
  sleep 3
done
```

判据不要看 `push` 的返回码（实测失败时退出码仍可能是 0），要看远端 SHA：

```bash
git -c http.proxy= -c https.proxy= ls-remote origin refs/heads/optimize/interview-ready
# 或绕开 git 用 GitHub API 交叉核对（Bash 通道网络抖动时这条更稳）：
#   GET https://api.github.com/repos/kue04/llm-customer-service/branches/optimize%2Finterview-ready
```

推送后远端跟踪 ref 也可能因坑 1 不更新；若 `git status -sb` 显示莫名的 ahead/behind，
手动写 `.git/refs/remotes/origin/optimize/interview-ready` 为远端实际 SHA 即可。

**坑 3（新增）：本机 venv 在 B3 期间被破坏过一次，已重建为轻量版。**

现象：`venv/Lib/site-packages` 整个目录消失，venv 从会话开始时的 20473 个文件
变成只剩 46 个（`Scripts/` 下的 `pip.exe` / `pytest.exe` 等包装器还在，但没有包体），
`./venv/Scripts/python.exe -m pytest` 直接报 `No module named pytest`。
已确认不是 git 造成（`venv/` 在 `.gitignore` 里），回收站里也没有痕迹，
**无法原样恢复**，坏掉的 venv 已移到
`%TEMP%\venv-broken-20260923`（未删除，可自行清理）。

重建方式（按 requirements-dev.txt 的轻量口径，用户确认）：

```bash
# 注意：本机 pip 默认指向 mirrors.aliyun.com 的 http 源，会因「非可信主机」被忽略，
# 必须显式指定 HTTPS 源 + trusted-host：
./venv/Scripts/python.exe -m pip install --no-cache-dir \
  --trusted-host mirrors.aliyun.com -i https://mirrors.aliyun.com/pypi/simple/ \
  -r requirements-dev.txt
./venv/Scripts/python.exe -m pip install --no-cache-dir \
  --trusted-host mirrors.aliyun.com -i https://mirrors.aliyun.com/pypi/simple/ \
  pymupdf python-docx beautifulsoup4 trafilatura
```

重建后 `venv` 里**没有** torch / transformers / datasets / trl / peft /
sentence-transformers（原 venv 装过完整 requirements.txt）。
这不影响任何测试与 lint 门槛：`tests/` 下零处导入这些包，
`requirements-dev.txt` 的注释也写明「本项目在缺失这些包时会走降级分支」。
需要跑真实模型推理时，按 `requirements.txt` 补装即可。

**坑 4（自动化执行环境补充）：通过 AI 代理跑命令时，Bash 与 PowerShell 两个通道的能力不互补。**

- **Bash（Git Bash）通道**：能跑 `git`，但沙箱会拦掉出网请求
  （表现为 `Connection reset` / 超时；同为直连，PowerShell 里能通）。
  需要出网的命令（`git push`、`pip install`）要显式申请放行；
- **PowerShell 通道**：能出网（`Invoke-WebRequest` 正常），但沙箱会**静默**拦掉
  启动外部程序 —— `git.exe` 存在（`Test-Path` 为真）却没有任何输出、也没有退出码，
  且 `git` 不在 PowerShell 的 PATH 上（机器上的 git 是
  `%USERPROFILE%\.workbuddy\binaries\PortableGit\...\mingw64\bin\git.exe`）；
- 另外 `cmd.exe` 在 PowerShell 通道被安全策略禁止，不要用它做桥接。

结论：**提交/推送要放在 Bash 通道并申请出网放行**；网络可用性探测可以用
PowerShell 的 `Invoke-WebRequest` 做交叉验证。

