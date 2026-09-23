# RAG 改造执行进度台账

> 本文件是 `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 的执行进度记录。
> 每完成一个任务/批次就追加一条记录，不覆盖历史内容。
> 记录格式遵循计划文档第 8 节的固定输出格式。

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
| B3 | 2.1 解析契约 + 2.2 五种解析器与注册表 | `services/ingestion/parsers/*`、`parser_registry.py`、fixtures、`tests/test_document_parsers.py` | ⏳ 未开始 |
| B4 | 2.3 上传与异步任务 API | `routers/documents.py`、对象存储、Schema、`tests/test_document_upload_api.py` | ⏳ 未开始 |
| B5 | 3.1 Chunk 配置 + 3.2 结构化切分器 | `config/chunking_config.py`、`services/ingestion/chunkers/*`、`tests/test_chunking.py` | ⏳ 未开始 |
| B6 | 3.3 幂等流水线 + 3.4 FAISS manifest | `services/ingestion/pipeline.py`、`worker.py`、改造 `utils/vector_retriever.py`、`tests/test_ingestion_pipeline.py` | ⏳ 未开始 |
| B7 | 4.1 权限规则 + 4.2 路由改造 + 4.3 检索 API | `routers/documents.py`、`routers/knowledge.py`、`routers/retrieval.py`、权限测试 | ⏳ 未开始 |
| B8 | 阶段 7 总审查与发布门禁 | `reports/rag_ingestion_auth_review/*`（review.txt 结论） | ⏳ 未开始 |

分批原则：一批 = 一个可独立验证、可回滚的闭环；每批结束跑一次该阶段的检查命令，
把结果写进第 3 节。**阶段审查为 NEEDS_WORK 时不进入下一批。**

## 2. 阶段审查结论汇总

| 阶段 | 结论 | 证据位置 | 时间 |
| --- | --- | --- | --- |
| 阶段 0 保存基线 | PASS | `reports/execution_baseline/review.txt` | 2026-09-23 |
| 阶段 1 数据库与身份 | **PASS** | `reports/rag_ingestion_auth_review/stage1_review.txt` | 2026-09-23 |
| 阶段 2 解析与接入 | 待审 | — | — |
| 阶段 3 切分与索引 | 待审 | — | — |
| 阶段 4 权限与隔离 | 待审 | — | — |
| 阶段 7 发布门禁 | 待审 | — | — |

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

## 4. 执行暂停点（下次从这里继续）

**当前停在：B2 结束（阶段 1 已判 PASS，代码已提交并推送到 GitHub），等待开始 B3（任务 2.1 + 2.2）。**

上次收尾状态：`HEAD = 0ea5834`，工作区干净，远端 `origin/optimize/interview-ready` 与本地一致，
全量测试 **332 passed**。新窗口可直接开工，无需重做 B1/B2。

下次继续时的入口动作：
0. 先读本文件第 5 节「两个环境坑」——提交/推送必须按那里写的特殊姿势来，
   否则会出现「commit 成功但分支不动」和「push 被死代理挡住」两种情况；
1. 复读本文件第 1 节分批表，确认 B3 范围 = 任务 2.1 解析契约 + 任务 2.2 五种解析器与注册表；
2. 跑一次 `python -m pytest -q` 确认起点仍是 **332 passed**；
3. 开始任务 2.1：新增 `services/ingestion/parsers/base.py`，
   定义 `DocumentParser` 协议（`supported_types` / `parser_name` / `parser_version` /
   `parse(source: bytes, filename: str) -> ParsedDocument`）与 `ParsedDocument`
   （`title` / `blocks[]` / `metadata` / `warnings[]`）；
   每个 block 含 `type` / `text` / `order` / `page` / `heading_path` / `table_json` / `image_ref`。
   **解析器不得写数据库；失败返回结构化错误，不能吞异常。**
4. 任务 2.2：实现 `plain_text.py` / `markdown.py` / `html.py` / `pdf.py` / `docx.py` /
   `ocr.py` + `parser_registry.py`；补 `tests/fixtures/` 四个样本文件；
   写 `tests/test_document_parsers.py`（块顺序、标题路径、页码、表格结构、
   OCR 触发条件、坏文件错误）。
   需先装依赖：`pymupdf`、`python-docx`、`beautifulsoup4`、`trafilatura`
   （OCR 的 `rapidocr-onnxruntime` 定义接口即可，默认不装）。
5. 阶段 2 的检查命令（计划第 7.1 节）：`python -m pytest -q` + `python -m ruff check .`；
6. 回到本文件追加 T-2.1 / T-2.2 记录；阶段 2 结束时写
   `reports/rag_ingestion_auth_review/stage2_review.txt`。

需要注意的既有约束（B3 必读）：
- 阶段 1 已把身份边界固定下来：解析器与仓储层**都不接收 tenant_id 参数**，
  租户由调用方（API 层的 `AuthContext`）传入并写入记录，解析器本身保持无状态、无数据库依赖。
  这正好满足计划对解析器的要求「解析器不得写数据库」。
- `document_versions.content_hash` 已是**租户粒度唯一**，重复文件检测靠
  `repository.find_version_by_content_hash(session, tenant_id, hash)`，
  解析器只负责算 SHA-256 并放进 `ParsedDocument.metadata`，判定逻辑放流水线（B6）。
- 新增依赖记得同步 `requirements.txt` 与 `requirements-dev.txt`；
  纯 Python 包放 requirements-dev，需要编译/体积大的放 requirements.txt。
- 测试仍跑在临时 SQLite 上，`tests/conftest.py` 已固定鉴权环境变量，
  新增测试无需再处理 JWT 密钥。
- `tests/fixtures/` 下的样本文件必须小于 1 MB（`check_repo_data_size.py` 会卡 CI）。

## 5. 提交与仓库同步记录

| 提交 | 内容 | 规模 |
| --- | --- | --- |
| `9760049` | `docs:` 加入 RAG 数据接入/切分/权限改造执行计划 | 1 文件 |
| `0ea5834` | `feat(rag):` 阶段 1 数据模型迁移 + JWT 身份上下文（B1+B2） | 57 文件，+4779 / −236 |
| `99fd798` | `docs:` 记录 B1+B2 提交结果与仓库提交/推送环境坑 | 1 文件，+53 / −1 |

- 分支：`optimize/interview-ready`；远端 `origin` = `https://github.com/kue04/llm-customer-service.git`
- 2026-09-23 推送成功，远端 `refs/heads/optimize/interview-ready` =
  `0ea5834738a031dfe0a7c6b0554c87a1fca50e11`（与本地一致，`git status -sb` 无 ahead/behind）
- `.gitignore` 已把 `reports/execution_baseline/`、`reports/rag_ingestion_auth_review/` 两个目录
  从 `reports/*` 的忽略中排除（计划明确要求这两处评审材料落盘，属交付证据，体积均在 10 KB 内）。

### ⚠️ 本仓库的两个环境坑（后续每次提交都会遇到，务必按此操作）

**坑 1：git 无法自动创建嵌套 ref 目录，导致 commit「成功」但分支指针不前进。**

分支名含 `/`（`optimize/interview-ready`），但 `.git/refs/heads/` 下**不存在** `optimize/` 子目录
（该分支只活在于 `.git/packed-refs`）。git 写 loose ref 时未能自动补建该子目录，
结果是：`git commit` 打印成功、commit 对象与 reflog 都已写入，**但分支指针不动**，
`git rev-parse HEAD` 仍返回旧提交。

识别症状：`git log` 看不到刚提交的内容，但 `git reflog` 里能看到该提交。

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

**坑 2：git 配置的代理 `127.0.0.1:7890` 已失效，直接 push 会失败。**

报错为 `Failed to connect to github.com:443 over proxy 127.0.0.1:7890`，
但 GitHub 其实**可直连**（`curl --noproxy '*' -o /dev/null -w '%{http_code}' https://github.com` → 200）。
该代理同时写在 local 与 global 配置里。推送时绕过代理：
```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  git -c http.proxy= -c https.proxy= push origin optimize/interview-ready
```
推送后远端跟踪 ref 也可能因坑 1 不更新；若 `git status -sb` 显示莫名的 ahead，
手动写 `.git/refs/remotes/origin/optimize/interview-ready` 为远端实际 SHA 即可
（用 `git ls-remote origin refs/heads/optimize/interview-ready` 查远端真实值）。

