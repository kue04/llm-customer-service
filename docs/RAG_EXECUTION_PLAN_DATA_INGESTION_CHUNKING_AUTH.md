# RAG 可执行改造计划：数据接入、文档解析、切分与权限

## 0. 文档用途

本文件是给编码型大模型直接执行的任务清单。每个任务规定工作目录、输入、文件、命令、完成条件和阶段审查条件。

执行规则：

1. 按编号顺序执行，不跳过阶段审查。
2. 每个任务完成后运行任务级检查，再进入下一个任务。
3. 阶段审查失败时停止后续任务，只修复当前阶段。
4. 不删除现有数据，不执行 `git reset --hard`、`git clean` 或覆盖用户未提交修改。
5. 新接口同时更新 Schema、OpenAPI、测试和文档。
6. 所有索引内容携带 `tenant_id`、`document_id`、`document_version`、`chunk_id`、`acl` 和来源信息。

## 1. 技术基线

第一期使用 Python 3.11+、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Redis、FAISS。文件解析使用可替换适配器：PDF 用 `pymupdf`，DOCX 用 `python-docx`，HTML 用 `beautifulsoup4` 和 `trafilatura`，Markdown/TXT 用标准库。OCR 定义接口，默认可选 `rapidocr-onnxruntime`。任务队列第一期使用 Redis Stream，避免引入额外队列服务。身份认证使用 JWT，后续可替换 OIDC；禁止把客户端 header 当作身份来源。

当前 FAISS 保留为第一期检索后端；索引内容改为结构化 manifest。PostgreSQL 保存文档、版本、ACL、处理任务和审计数据。对象存储通过接口抽象，开发环境可先使用本地目录，生产环境再接 S3 兼容存储。

## 2. 阶段 0：保存当前进度（必须先做）

工作目录：`D:\llm\llm-customer-service`

### 0.1 保存基线

执行：

```powershell
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force reports\execution_baseline | Out-Null
git status --short | Set-Content reports\execution_baseline\backend-status.txt -Encoding UTF8
git diff --binary | Set-Content reports\execution_baseline\backend.patch -Encoding UTF8
git rev-parse HEAD | Set-Content reports\execution_baseline\backend-head.txt -Encoding UTF8
```

### 0.2 审查并记录基线

执行：

```powershell
python -m pytest -q 2>&1 | Tee-Object reports\execution_baseline\test-output.txt
python -m ruff check . 2>&1 | Tee-Object reports\execution_baseline\ruff-output.txt
python scripts/check_repo_data_size.py 2>&1 | Tee-Object reports\execution_baseline\data-size-output.txt
git status --short
```

验收：基线提交、状态和 patch 存在；原有测试/lint 结果被保存；原有失败被原样记录，不伪装为新失败；用户未提交修改未覆盖。

### 阶段 0 审查

审查人检查以上四项并在 `reports/execution_baseline/review.txt` 写 `PASS` 或 `NEEDS_WORK` 和证据。只有 `PASS` 才进入阶段 1。

## 3. 阶段 1：数据库模型与身份上下文

### 1.1 建立数据模型和迁移

新增：

```text
alembic.ini
alembic/env.py
alembic/versions/0001_ingestion_auth.py
services/ingestion/models.py
services/ingestion/repository.py
tests/test_ingestion_models.py
```

创建表：`tenants`、`users`、`roles`、`user_roles`、`knowledge_bases`、`knowledge_base_members`、`documents`、`document_versions`、`document_acl`、`ingestion_jobs`、`document_chunks`、`index_builds`、`audit_events`。

最低字段：

- `documents`: `id`, `tenant_id`, `knowledge_base_id`, `source_uri`, `source_type`, `title`, `status`, `created_by`, `created_at`
- `document_versions`: `id`, `document_id`, `version`, `content_hash`, `parser_name`, `parser_version`, `metadata_json`, `status`, `created_at`
- `document_acl`: `document_id`, `subject_type`, `subject_id`, `permission`, `created_at`
- `document_chunks`: `id`, `document_version_id`, `chunk_id`, `parent_chunk_id`, `text`, `token_count`, `metadata_json`, `content_hash`
- `ingestion_jobs`: `id`, `tenant_id`, `document_id`, `status`, `stage`, `error_code`, `error_message`, `retry_count`, `created_at`, `updated_at`

约束：使用 Alembic，禁止运行时自动改表；业务记录必须可追溯到 tenant；唯一约束包含租户维度；原文件仅存对象 URI，不写入数据库；迁移不能清理或覆盖现有 SQLite 数据。

执行：

```powershell
python -m pytest tests/test_ingestion_models.py -q
alembic upgrade head
```

完成条件：迁移可重复执行，所有表/索引存在，测试验证跨租户唯一性和外键约束。开发 PostgreSQL 使用 docker-compose 服务，连接串必须从环境变量读取。

### 1.2 定义身份上下文并替换信任 Header

新增 `services/auth_context.py`，定义不可变 `AuthContext(user_id, tenant_id, roles, scopes, request_id)`。

实现要求：

1. 从 `Authorization: Bearer <JWT>` 读取身份。
2. 校验签名、发行方、受众、过期时间和 `tenant_id` claim。
3. 测试环境使用专用测试密钥；生产密钥只从环境变量读取。
4. `X-User-Role` 不参与鉴权；兼容期仅可记录，不可授权。
5. 缺失/过期 token 返回 401，租户或资源不匹配返回 403 或不泄漏存在性的 404。

修改 `services/auth_service.py`、`main.py`、受保护的 routers。新增 `tests/test_auth_context.py`、`tests/test_tenant_isolation.py`，覆盖伪造角色 header、过期 token、越权角色、跨租户资源和合法用户。

阶段任务检查：

```powershell
python -m pytest tests/test_ingestion_models.py tests/test_auth_context.py tests/test_tenant_isolation.py -q
python -m ruff check .
```

## 4. 阶段 2：文件接入与文档解析

### 2.1 定义解析契约

新增 `services/ingestion/parsers/base.py`。定义 `DocumentParser` 协议：`supported_types`、`parser_name`、`parser_version`、`parse(source: bytes, filename: str) -> ParsedDocument`。

`ParsedDocument` 字段：`title`、`blocks[]`、`metadata`、`warnings[]`。每个 block 含 `type`、`text`、`order`、`page`、`heading_path`、`table_json`、`image_ref`。解析器不得写数据库；失败返回结构化错误，不能吞异常。

### 2.2 实现格式解析器和注册表

新增：

```text
services/ingestion/parsers/plain_text.py
services/ingestion/parsers/markdown.py
services/ingestion/parsers/html.py
services/ingestion/parsers/pdf.py
services/ingestion/parsers/docx.py
services/ingestion/parsers/ocr.py
services/ingestion/parser_registry.py
```

要求：PDF 保留页码；DOCX 保留标题层级和表格；HTML 清理 script/style/nav/footer 等无关内容；表格同时保留稳定 JSON 和可检索文本；OCR 仅在可提取文本为空或低于配置阈值时调用；每份原文件计算 SHA-256；重复文件标记 `duplicate`，不能创建第二份有效文档版本。解析警告必须可由任务查询接口读取。

新增 fixture：`tests/fixtures/sample.pdf`、`sample.docx`、`sample.html`、`sample.md`。新增 `tests/test_document_parsers.py`，断言块顺序、标题路径、页码、表格结构、OCR 触发条件和坏文件错误。

### 2.3 增加上传和异步任务 API

新增路由：

```text
POST /knowledge-bases/{knowledge_base_id}/documents
GET  /documents/{document_id}
GET  /documents/{document_id}/versions
POST /documents/{document_id}/reprocess
GET  /ingestion-jobs/{job_id}
```

上传仅执行鉴权、类型/大小检查、对象存储、创建任务；解析在 Redis Stream worker 异步运行。默认最大文件 50 MB，仅接受 PDF、DOCX、HTML、MD、TXT。文件名只作展示，不能拼接 shell 命令。对象 key 使用随机文档 ID 并包含 tenant 路径。

开发环境提供 `LocalObjectStore`；代码同时定义 `ObjectStore` 接口供 S3 实现替换。新增 Schema、OpenAPI 导出更新和 `tests/test_document_upload_api.py`。覆盖拒绝不支持扩展名、MIME 与文件内容不一致、超大文件、无权限、成功创建任务、任务失败可查询和可重试。

## 5. 阶段 3：Chunk 切分、持久化与索引

### 3.1 定义 Chunk 配置

新增 `config/chunking_config.py`，初始默认值：

```text
target_tokens = 450
min_tokens = 120
max_tokens = 700
overlap_tokens = 80
preserve_heading_path = true
parent_chunk_enabled = true
```

允许知识库配置覆盖；每个文档版本保存实际生效配置与 tokenizer 标识。配置越界时启动失败并给出字段错误。

### 3.2 实现结构化切分器

新增目录 `services/ingestion/chunkers/`，实现结构感知切分：

1. 先按标题层级和页码分组，再按段落切分。
2. 超过上限的段落依次按句号、问号、分号、换行切分。
3. 表格按行分块并重复表头；代码块不从内部截断。
4. 列表项尽量保持完整。
5. 相邻 child chunk 重叠 80 tokens，chunk 不超过 700 tokens。
6. 每组 child chunk 指向一个 parent chunk。
7. 标题路径可作为上下文前缀，但不得重复原文主体。
8. 去除空白、重复页眉页脚和完全重复段落。
9. 每块保存 token/字符数、页码、标题、来源、ACL 和租户信息。

Chunk 输出至少包含：`chunk_id`、`parent_chunk_id`、`text`、`token_count`、`tenant_id`、`document_id`、`document_version`、`page_start`、`page_end`、`heading_path`、`acl`、`content_hash`。

测试 `tests/test_chunking.py` 覆盖长段落、表格、代码、列表、重叠、边界长度、空文档、确定性 ID 和权限元数据继承。提供固定 tokenizer mock，测试不依赖下载模型。

### 3.3 实现幂等处理流水线

固定状态流转：

```text
received -> stored -> parsed -> normalized -> chunked -> persisted -> indexed -> published
```

每一步更新 `ingestion_jobs.stage` 并写结构化日志。失败写 `error_code` 和可诊断消息，支持从失败步骤幂等重试。以内容 hash 和版本号防重复。仅 `published`、有效期内、ACL 完整的版本可以检索。归档/未发布/旧版本默认排除。

新增 `services/ingestion/pipeline.py`、`worker.py`、`tests/test_ingestion_pipeline.py`。测试每阶段故障、重试、重复投递、重复文件、发布失败和旧版本排除。

### 3.4 改造 FAISS 索引 manifest

修改 `utils/vector_retriever.py`，增加 `index_manifest.json`，记录索引版本、embedding 模型/维度、构建时间、过滤规则和 Chunk ID 顺序。向量序号不得再隐式等于 JSONL 行号。命中必须返回 chunk、document、version、tenant、ACL、来源元数据。

构建到临时目录，验证向量数、manifest 数和向量维度一致后原子切换。失败不得覆盖当前索引。增加索引版本回滚接口/函数和测试。检索前强制传入服务端构造的 tenant/ACL filter，缺失 filter 必须拒绝而非默认全库查询。

## 6. 阶段 4：资源级权限和检索隔离

### 4.1 固定权限判定规则

权限枚举：

```text
knowledge_base:read
knowledge_base:write
document:upload
document:read
document:review
document:publish
document:delete
index:rebuild
audit:read
```

每次访问按顺序校验：JWT 有效 → tenant 匹配 → scope 满足操作 → knowledge base 成员关系 → document ACL → 检索 filter 注入 `tenant_id` 与 allowed subjects。`tenant_id` 只从 AuthContext 读取，忽略 body/query 中同名字段。

### 4.2 改造知识和文档路由

修改 `routers/knowledge.py`，新增 `routers/documents.py`。每个受保护 handler 显式接收 `AuthContext`，不允许业务层自行从 header 读用户。发布和索引重建分开授权。写操作写入 `audit_events`，记录 actor、tenant、action、resource、request_id 和脱敏摘要。

新增/扩展 `tests/test_tenant_isolation.py`：

1. 用户 A 不可读取租户 B 文档。
2. 同租户但无 ACL 的用户不能检索受限文档。
3. 只有 `document:publish` 可发布。
4. 只有 `index:rebuild` 可重建。
5. 被过滤文档的标题、分数、数量、引用和 trace 均不泄漏。
6. 修改请求 body 的 `tenant_id` 不改变授权范围。

### 4.3 改造检索 API

修改 `routers/retrieval.py` 和 `utils/vector_retriever.py`。在 API 层从 AuthContext 生成不可由客户端控制的授权过滤器，再传递给 retriever。禁止客户端传 SQL、FAISS ID 或任意 filter 表达式。过滤必须发生在候选暴露前，并由负向测试证明检索结果不泄漏。

## 7. 阶段审查与发布门禁

### 7.1 自动检查

执行：

```powershell
python -m pytest -q
python -m ruff check .
python -m compileall services routers schemas models
python scripts/check_repo_data_size.py
```

### 7.2 文件链路验收

对 PDF、DOCX、HTML、Markdown 各执行一次端到端上传。确认文档版本、解析块、Chunk、manifest 和检索结果可以通过 document_id 串联；长文档至少生成 2 个 chunk；每个 chunk 有来源、租户、版本、页码/标题路径和 ACL；重复上传不生成重复有效版本；失败任务可查错和重试。

### 7.3 安全审查

逐项验证：伪造 `X-User-Role` 不提升权限；body 的 tenant_id 不影响范围；跨租户资源返回 404/403 且不泄漏存在性；无 ACL 文档不出现在结果、引用、trace 和错误信息；未发布/归档/过期/旧版本不进入索引；索引构建失败不破坏旧版本。

### 7.4 阶段完成条件

全部自动测试通过；四种文件端到端通过；权限负向测试全部通过；索引原子切换/回滚通过；审查材料保存在 `reports/rag_ingestion_auth_review/`；结论写 `PASS` 或 `NEEDS_WORK` 并引用测试结果。`NEEDS_WORK` 时不得进入后续阶段。

## 8. 给执行大模型的固定输出格式

每任务结束输出：

```text
任务编号：
修改文件：
执行命令：
测试结果：
验收结果：PASS / NEEDS_WORK
未解决问题：
下一任务：
```

每阶段结束输出：

```text
阶段编号：
阶段目标：
已完成任务：
自动检查结果：
人工审查结果：
风险与回滚点：
阶段结论：PASS / NEEDS_WORK
```

`NEEDS_WORK` 时停止，不得开始下一阶段。
