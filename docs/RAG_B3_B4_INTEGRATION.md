# B3 → B4 对接文档（解析层 → 上传与异步任务 API）

> 适用范围：`docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` 任务 **2.3**（批次 B4）。
> 上游交付：批次 B3 = 任务 2.1（解析契约）+ 任务 2.2（五种解析器与注册表）。
> 本文只讲**接口与数据契约**，不重复解析算法细节；算法与取舍见
> `reports/rag_ingestion_auth_review/stage2_review.txt` 与台账 `docs/RAG_EXECUTION_PROGRESS.md` 的 T-2.1 / T-2.2。
>
> 一句话结论：**B4 只需要认识 `parse_document()` 和 `ParseError`，不需要认识任何具体解析器。**
> 但 B4 有三件事 B3 明确不做、必须自己补，还有三个决策点必须先拍板（见第 6 节）。

---

## 1. B3 对外稳定接口清单（可直接 import 使用）

### 1.1 注册表：`services.ingestion.parser_registry`

| 符号 | 签名 | B4 用途 |
| --- | --- | --- |
| `parse_document` | `(source: bytes, filename: str, *, source_type: str \| None = None, content_type: str \| None = None) -> ParsedDocument` | **唯一解析入口**（B4 上传接口自身不调；`worker` 里调，见 3.3） |
| `detect_source_type` | `(filename: str, content_type: str \| None = None) -> str` | 上传时的类型判定（判定失败抛 `ParserError`） |
| `get_parser` | `(source_type: str) -> DocumentParser` | 一般用不到，只有需要读 `parser_version` 时才用 |
| `ALLOWED_EXTENSIONS` | `tuple[str, ...]` | **上传扩展名白名单，直接用这个常量，不要自己写一份** |
| `EXTENSION_SOURCE_TYPES` / `SOURCE_TYPE_EXTENSIONS` / `MIME_SOURCE_TYPES` | `dict` | 需要做「扩展名 ↔ 类型 ↔ MIME」对照时用 |
| `ParserRegistry` / `default_registry()` / `reset_default_registry()` | — | 生产用 `default_registry()`；`reset_*` 仅供测试 |
| `default_parsers()` | `() -> list[DocumentParser]` | 只给测试造自定义注册表用 |

实测取值（本机实际输出，可直接当断言依据）：

```
ALLOWED_EXTENSIONS = ('.docx', '.htm', '.html', '.markdown', '.md', '.mdown', '.pdf', '.text', '.txt', '.xhtml')
available_types    = ('docx', 'html', 'md', 'pdf', 'txt')
```

> 注意：`ALLOWED_EXTENSIONS` 有 **10 项**，而来源类型只有 **5 种**。别把「10 个扩展名」误当成
> 「10 种格式」；`html` 一种类型对应 `.html/.htm/.xhtml` 三个扩展名，`md` 对应三个，`txt` 对应两个。
> 白名单与解析能力同源（都由 `SOURCE_TYPE_EXTENSIONS` 反向展开），所以**新增解析器时白名单自动跟随**，
> 不需要在 B4 里维护第二份列表。

### 1.2 契约类型：`services.ingestion.parsers.base`

| 符号 | 关键字段 / 方法 | B4 用途 |
| --- | --- | --- |
| `ParsedDocument` | `title` / `blocks: tuple[Block,...]` / `metadata: Mapping` / `warnings: tuple[ParseWarning,...]` / `source_type` / `parser_name` / `parser_version`；便捷视图 `content_hash` / `text` / `page_count` / `tables` / `blocks_of_type()`；方法 `to_dict()` / `validation_errors()` | 落库与任务查询的数据源 |
| `Block` | `type` / `text` / `order` / `page` / `heading_path` / `table_json` / `image_ref`；`to_dict()` / `heading_path_text` | B4 不直接消费（B5 切分器才消费），但**不要改它的形状** |
| `ParseWarning` | `code` / `message` / `detail`；`to_dict()` | 任务查询接口要展示的警告 |
| `ParserError` | `error_code` / `message` / `parser_name` / `filename` / `detail`；`to_dict()` | 写 `ingestion_jobs.error_code` / `error_message` |
| `compute_content_hash` | `(source: bytes) -> str`（SHA-256 hex） | 上传时若需自己算 hash 用这个，别手写 `hashlib` |
| `PARSER_CONTRACT_VERSION` | `"1.0"` | 会出现在 `metadata["parser_contract_version"]` |
| `ERROR_CODES` | `('unsupported_type','empty_document','corrupt_document','missing_dependency','ocr_unavailable','parse_failed')` | 失败码枚举，**封闭集合** |
| `WARNING_CODES` | `('encoding_fallback','no_text_layer','empty_section','degraded_extraction','image_skipped')` | 警告码枚举，**封闭集合** |
| `BLOCK_TYPES` | `('heading','paragraph','list','table','code','image','quote','page_break')` | 块类型枚举，**封闭集合** |
| `REQUIRED_METADATA_KEYS` | `('block_count','byte_size','content_hash','filename','source_type')` | 由模板保证必然存在 |

### 1.3 B4 **不应**依赖的东西（内部实现）

- 具体解析器类（`PdfParser` / `DocxParser` / `HtmlParser` / `MarkdownParser` / `PlainTextParser`）
  —— 一切经由 `parse_document()`；在 router 或 service 里 `from ...parsers.pdf import PdfParser` 属于越界。
- `services/ingestion/parsers/_text.py` —— 下划线前缀即「实现细节，不进入契约面」。
- `parsers/ocr.py` 的内部实现 —— OCR 是 PDF/DOCX 解析器的内部增强，不是独立来源类型，
  `default_parsers()` 里也**没有** ocr 条目，不要试图 `get_parser("ocr")`（必然抛 `unsupported_type`）。

---

## 2. 数据契约映射表（B3 产物 → 数据库列）

| B3 产物 | 目标列 | 说明 |
| --- | --- | --- |
| `ParsedDocument.metadata["content_hash"]` | `document_versions.content_hash` | 租户内唯一（`uq_document_versions(tenant_id, content_hash)`） |
| `ParsedDocument.parser_name` | `document_versions.parser_name` | 自描述字段，[D-4] 加这个字段就是为了这列 |
| `ParsedDocument.parser_version` | `document_versions.parser_version` | 同上；解析行为变化时靠它识别「旧解析结果」 |
| `ParsedDocument.metadata`（含 `warnings`） | `document_versions.metadata_json` | JSON 列，可直接 `json.dumps`；**warnings 不要新建列** |
| `ParsedDocument.title` | `documents.title`（首次写入；空则回退文件名主干） | 模板已做 title 兜底，不会是空串 |
| `ParsedDocument.source_type` | `documents.source_type` | 取值 ∈ `('pdf','docx','html','md','txt')` |
| `ParsedDocument.warnings[*].to_dict()` | 任务查询接口的响应体 | 已可直接 `json.dumps`，无需二次转换 |
| `ParserError.error_code` | `ingestion_jobs.error_code` | 直接落库，不要解析异常字符串 |
| `ParserError.message` | `ingestion_jobs.error_message` | 同上 |

**关于 warnings 放哪：** `document_versions` 没有 warnings 列，且计划不要求加列。
推荐把 `to_dict()` 结果塞进 `metadata_json` 的一个固定键（例如 `"warnings"`），
任务查询接口从 `metadata_json` 里取。若确实要独立列，**必须走 Alembic 新增迁移**
（计划明令「禁止运行时自动改表」），且需要同步更新 `tests/test_ingestion_models.py`
里「迁移产物与模型定义逐项对照」的断言。

**关于 `status` 初值：** `documents.status` 合法值含 `received / stored / parsed / chunked /
indexed / published / archived / failed / duplicate`；`ingestion_jobs.status` 合法值为
`pending / running / succeeded / failed / retrying / cancelled`，`stage` 合法值即
`INGESTION_STAGES`（`received → stored → parsed → normalized → chunked → persisted → indexed → published`）。
上传接口建的是 `documents.status="received"` + `jobs.status="pending"/stage="received"`，
**不要**在上传期就把状态写成 `stored`（`stored` 是对象存储确实写完之后的阶段，由流水线推进）。

---

## 3. 五个端点各自的对接点

### 3.1 `POST /knowledge-bases/{knowledge_base_id}/documents`（上传）

推荐处理顺序（每一步都有明确的拒绝理由，不要合并）：

1. **鉴权**：`AuthContext` 走依赖注入，`tenant_id` **只从 `auth.tenant_id` 取**，忽略 body/query 里的同名字段。
2. **知识库存在性 + 成员关系**：`repository.get_knowledge_base(session, auth.tenant_id, kb_id)`
   → `None` 返回 **404**（不泄漏存在性）；再 `repository.get_knowledge_base_member(...)` 校验成员身份（无成员关系 **403**）。
3. **扩展名白名单**：`Path(filename).suffix.lower() in parser_registry.ALLOWED_EXTENSIONS`
   → 不通过返回 **415**。这一步只做「允许清单」，不解析。
4. **大小检查**：默认上限 **50 MB**（计划 2.3 明确）。建议做成模块级常量 + 环境变量可覆盖，
   并在读取 `UploadFile` 时用**流式累计**而不是 `await file.read()` 全量进内存（50 MB × 并发会打爆内存）。
5. **内容嗅探（magic bytes）与声明类型一致性**：见第 5 节，这是 B4 必须新写的部分。
6. **对象存储**：key 必须**包含 tenant 路径 + 随机文档 ID**（计划原文），例如
   `rag/{tenant_id}/{document_id}/{sanitized_filename}`；文件名只作展示，
   **绝不可拼接 shell 命令**，也**不要**用客户端传来的文件名当路径。
7. **`repository.get_document_by_source_uri(session, tenant_id, source_uri)`**
   —— 命中则复用文档（`source_uri` 唯一性在租户粒度，[D-1]），未命中则 `create_document(...)`。
8. **建任务**：`repository.create_ingestion_job(session, tenant_id=..., document_id=..., status="pending", stage="received")`。
9. **审计**：`services.audit_service.record_audit_log(...)`，记录 actor / tenant / action / resource / request_id 与脱敏摘要。
10. **投递队列**（Redis Stream；无 Redis 用内存降级），把 `job_id` 发出去。
11. 返回 **202 Accepted** + `document_id` / `job_id`。

**本端点明确不做的事：** 不解析、不切分、不算 chunk、不做内容 hash 的重复判定、不建 `document_version`（见 6.1）。

### 3.2 `GET /documents/{document_id}`

- 鉴权 + 租户过滤：`repository.get_document(session, tenant_id, document_id)`，`None` → **404**。
- 附加最新版本摘要：`repository.latest_document_version(session, tenant_id, document_id)`。
- 返回 `documents` 字段 + 最新版本的 `version` / `status` / `parser_name` / `parser_version`。

### 3.3 `GET /documents/{document_id}/versions`

- 先校验文档属于本租户，再 `repository.list_document_versions(session, tenant_id, document_id)`（已按 `version` 升序）。
- 每项返回 `version` / `content_hash` / `parser_name` / `parser_version` / `status` / `created_at`。
- **`metadata_json` 默认不整体返回**（可能很大且含内部字段），只返回 `warnings` 摘要。

### 3.4 `POST /documents/{document_id}/reprocess`

- 权限应比普通上传更严（重跑会消耗索引资源），建议用独立的 operation 名并写审计。
- 行为：校验文档属于本租户 → **新建一个 job**（不要复用旧 job 改状态；
  `retry_count` 是「同一 job 的失败重试次数」，语义不同于「新建一次处理」）→ 投递队列 → 202。
- 若计划要求「从失败阶段幂等重试」由 3.3 的流水线负责（`update_ingestion_job` 支持只改 `stage`），
  B4 这里只需要把新 job 的 `stage` 设为要重跑的起点（默认 `received`）。

### 3.5 `GET /ingestion-jobs/{job_id}`

- `repository.get_ingestion_job(session, tenant_id, job_id)`，`None` → **404**。
- 返回 `status` / `stage` / `error_code` / `error_message` / `retry_count` / `document_id` / `document_version_id` / 时间戳。
- **解析警告从这里读**：`document_version.metadata_json["warnings"]`（形状见第 2 节），
  响应里建议展开成 `[{code, message, detail}, ...]`，与 `ParseWarning.to_dict()` 一一对应，
  这样前端可以按 `code` 分类展示（例如 `no_text_layer` → 「疑似扫描件」）。

---

## 4. 必须由 B4 承担的职责（B3 明确不做）

| 职责 | 为什么不在 B3 | B3 已有的兜底 |
| --- | --- | --- |
| 租户归属 | 解析器签名被测试锁死为 `(source, filename)`，**不接受 `tenant_id`** | `test_parsers_do_not_accept_tenant_id` |
| 写数据库 | 解析器模块源码扫描禁止出现 `sqlalchemy` / `repository` / `db` | `test_parser_modules_do_not_depend_on_database` |
| 内容 hash 的**判定** | 解析器只**产出** `content_hash`，判定属于流水线 | `find_version_by_content_hash()` 在仓储层待调 |
| 类型 / 大小准入 | 契约层不管准入策略 | 提供 `ALLOWED_EXTENSIONS` |
| 对象存储 | 解析器只吃 `bytes` | 无 |
| 异步投递 | 解析器是同步纯函数 | 无 |

---

## 5. B4 需要新定义的东西（B3 未提供，容易误以为已有）

### 5.1 `ObjectStore` 接口 + `LocalObjectStore`

计划原文要求「开发环境提供 `LocalObjectStore`；代码同时定义 `ObjectStore` 接口供 S3 实现替换」。
建议最小接口（四个方法即可，够用且好替换）：

```
put(key: str, data: BinaryIO | bytes, *, content_type: str | None = None) -> str
get(key: str) -> bytes
exists(key: str) -> bool
delete(key: str) -> None
```

**回读路径必须与写路径同源**：`worker` 从对象存储取文件字节后交给 `parse_document(source, filename)`，
所以 `get()` 返回的必须是**原始字节**（不是 base64、不是解压后的文本）。

### 5.2 内容嗅探（magic bytes）—— 这是最容易漏掉的一条

**B3 的「扩展名优先于 MIME」不等于「MIME 与文件内容一致性校验」。** 两者是不同的事：

- B3 的规则解决的是：**解析路径**不能被伪造的 `Content-Type` 带偏
  （否则「报表.pdf + `Content-Type: text/html`」会被 HTML 解析器处理）。
- 计划 2.3 要求测试覆盖「MIME 与文件内容不一致」，这要求**读文件头字节**判断真实类型：
  - `%PDF-` → pdf；`PK\x03\x04` → docx（zip 容器，需再看 `[Content_Types].xml` 或 `word/` 条目）；
  - HTML 没有魔数，靠 `<!DOCTYPE html` / `<html` 前缀启发式判断，**允许不确定**；
  - md / txt 没有魔数，**不做近似判断**（否则正常中文文本会被误判）。

所以 B4 需要新增 `sniff_content_type(data: bytes) -> str | None`，语义是
**「能确定就返回类型，不能确定就返回 `None`，由调用方决定是否放行」**。
判定策略建议：**只在「嗅探结果明确且与扩展名判定不一致」时拒绝（415）**，
`None` 一律放行 —— 因为「宁可放过一个 md/txt，也不错杀一个正常中文文档」。

### 5.3 队列与 Schema

- `services/ingestion/queue.py`（或等价）：Redis Stream + **内存降级实现**，接口一致，切生产只改环境变量。
- `schemas/document_schema.py`：Pydantic 模型，风格参照 `schemas/knowledge_schema.py`
  （`BaseModel` + `Field`，响应模型显式声明 `response_model=`）。
- `main.py` 注册：`app.include_router(documents.router, prefix="/documents", tags=["documents"])`
  —— 注意上传路由的路径是 `/knowledge-bases/{kb_id}/documents`，与 `/documents/{id}` 是**两个前缀**，
  要么在同一个 router 上写全路径，要么拆成两个 router（推荐前者，避免文件碎片）。

---

## 6. 三个必须先拍板的决策点（附推荐 + 理由）

### 6.1 ★最高优先级★ `document_version` 到底由谁创建

**冲突点：**

- `tests/test_ingestion_models.py::test_reupload_same_source_uri_appends_version_not_new_document`
  的 docstring 写的是「先查 `get_document_by_source_uri`，**命中就加版本**，未命中才建文档」
  —— 字面读起来像「上传接口创建 version」。
- 但台账第 4 节又明确写「上传接口**不要**在这里做重复判定」，
  而 `document_versions` 上有 `uq(tenant_id, content_hash)`。

**这两条放在一起会炸：** 用户先传 `a.pdf`（hash `H1`）→ 文档 A、版本 1。再传一个**内容相同**
但文件名不同的 `b.pdf`（hash 同为 `H1`）→ `source_uri` 不同 → 新建文档 B → 上传期创建版本时
**必然撞 `uq(tenant_id, content_hash)` 抛 `IntegrityError`**。

**推荐方案（B4 采用）：上传只建 `documents`（按需）+ `ingestion_jobs`，不建 `document_versions`。**
版本由流水线（3.3）在 `parsed` 阶段创建。理由：

1. 内容 hash 的重复**判定**本来就归流水线，上传不建版本 = 不需要在上传期做这个判定，
   与台账第 4 节的要求自洽；
2. `parser_name` / `parser_version` 只有在**解析之后**才知道（这正是 [D-4] 让产物自带这两个字段的原因），
   上传期建版本只能先写空串、之后回填，等于制造一次无意义的 UPDATE；
3. `ingestion_jobs.document_version_id` 是**可空列** —— 现有 schema 已经为「建 job 时版本还不存在」
   留好了位置，说明这个顺序是被预期的；
4. 重复文件因此能在流水线里被**优雅**处理（查 `find_version_by_content_hash` → 命中则把
   `documents.status` 标 `duplicate`、不建新版本、job 正常结束），而不是在上传期抛 500。

**如果 B4 选择「上传期建版本」**，则必须显式 `except IntegrityError` 并转成
「`duplicate` 文档状态 + 正常返回 202」，同时**必须新增一条决策记录**说明
「为什么上传期可以做重复判定」，否则与台账第 4 节矛盾会被后续审查判为不一致。

> 无论选哪条，**都必须新增 `[D-6]` 决策记录**写进台账第 3 节，并说明与
> `test_reupload_same_source_uri_appends_version_not_new_document` 的对应关系。
> 若最终改动了那个契约测试的语义，**测试也要同步改**（它是契约测试，不是普通用例）。

### 6.2 计划 2.2 的「重复文件标记 `duplicate`」到底落在哪个任务

- 计划**字面**把它写在 2.2（解析器那一节的末尾）。
- B3 的实际拆法是：**机制在 2.2**（每份原文件算 SHA-256，写进 `metadata["content_hash"]`），
  **判定在 3.3**（流水线查 `find_version_by_content_hash` 决定是否标 `duplicate`）。
- 这个拆分已经写在台账第 4 节与 `stage2_review.txt`，但**风险仍在**：
  阶段 2 审查如果不是由本文读者做，很可能会按计划字面判「2.2 没实现 duplicate 标记 = 缺口」，
  进而给出 `NEEDS_WORK` —— 而按纪律 `NEEDS_WORK` 不得进入下一批。

**因此：2.3 完成、回填 `stage2_review.txt` 的阶段级结论时，必须显式写一段
「「重复文件标记 duplicate」的实现位置说明」**，讲清「机制在 2.2、判定在 3.3」
以及为什么这样拆（`document_versions` 的 `uq(tenant_id, content_hash)` 是数据库级保证，
判定需要流水线上下文）。这是把阶段 2 从 PENDING 收成 PASS 的**必要条件**，不是可选项。

### 6.3 上传用哪个 scope / operation 名

- 现在 `services/auth_service.py` 只有 `require_read_operation_role(operation, ctx)` /
  `require_write_operation_role(operation, ctx)` / `require_review_action_role(action, ctx)`，
  scope 词汇沿用既有 `read:` / `write:` / `review:` 前缀，**operation 名必须是授权表里已有的键**。
- `document:upload` / `index:rebuild` 这类**资源级权限枚举属于阶段 4.1**，现在还不存在。
- 所以 B4 只能先复用现有 operation 名（例如上传走 `knowledge_write`，读走 `knowledge_read`）。
  **自己编一个 operation 名会导致授权判定异常**（表里没有 → 直接 403 或不匹配），务必先看表再加。

---

## 7. 不要擅自改动 B3 的东西

以下属于**已冻结的契约**，B4 不要「顺手优化」：

- `base.py` 的字段集合与语义（`Block` 七个字段、`ParsedDocument` 四个 + 三个自描述字段）；
- `ERROR_CODES` / `WARNING_CODES` / `BLOCK_TYPES` 三个**封闭集合**（新增值需要同时改测试）；
- `heading_path` 的语义（标题块包含自己，正文块是当前标题栈）；
- `REQUIRED_METADATA_KEYS` 五个必需键；
- 解析器 `parse(self, source: bytes, filename: str)` 的签名（不含额外参数）。

**如果 B4 确实发现契约不够用**：改动必须同时
(a) 更新 `tests/test_document_parsers.py` 的对应断言；
(b) **提升相关解析器的 `parser_version`**（解析行为变化要靠它识别旧结果）；
(c) 在台账里补一条决策记录。三者缺一不可。

---

## 8. B4 完成的定义（验收清单）

- [ ] 五个端点全部实现，且每个受保护 handler 显式接收 `AuthContext`（不从 header 自行读身份）
- [ ] 上传：扩展名白名单取自 `parser_registry.ALLOWED_EXTENSIONS`（未自建第二份列表）
- [ ] 上传：50 MB 上限，且为**流式累计**而非全量读入内存
- [ ] 上传：`filename` 只作展示，对象 key 含 tenant 路径 + 随机文档 ID，无 shell 拼接
- [ ] 上传：内容嗅探（magic bytes）已实现，且「不确定即放行」
- [ ] `tenant_id` 全程只来自 `AuthContext`；跨租户访问返回 404/403 且不泄漏存在性
- [ ] 上传**不**解析、**不**切分、**不**做内容 hash 重复判定
- [ ] `ingestion_jobs` 状态初值为 `pending`/`received`（不是 `stored`）
- [ ] 写操作全部写 `audit_events`
- [ ] `tests/test_document_upload_api.py` 覆盖：不支持扩展名 / MIME 与内容不一致 /
      超大文件 / 无权限 / 成功创建任务 / 任务失败可查询 / 可重试
- [ ] 新增依赖同步到 `requirements.txt` **与** `requirements-dev.txt` 两侧
- [ ] `python -m pytest -q` 起点 448 passed，B4 结束后**零回归**
- [ ] `python -m ruff check .` 干净、`python scripts/check_repo_data_size.py` 通过
- [ ] 台账追加 `[T-2.3]` + `[D-6]`；`stage2_review.txt` 回填**阶段级结论**（含 6.2 要求的说明段）

---

## 9. 相关文件索引

| 文件 | 作用 |
| --- | --- |
| `docs/RAG_EXECUTION_PLAN_DATA_INGESTION_CHUNKING_AUTH.md` §4（2.3） | 任务 2.3 的原始要求 |
| `docs/RAG_EXECUTION_PROGRESS.md` §4 | 执行暂停点（B4 入口动作） |
| `docs/RAG_EXECUTION_PROGRESS.md` §5 | 4 条环境坑（提交/推送/venv/通道差异），**动手前必读** |
| `docs/RAG_NEXT_WINDOW_PROMPT.md` | 下一批的**交接与作业规程**（进度快照 + 五步闭环 + 记录格式模板），新开窗口整份粘贴 |
| `reports/rag_ingestion_auth_review/stage2_review.txt` | 2.1 / 2.2 的任务级审查（阶段级待回填） |
| `services/ingestion/parsers/base.py` | 契约本尊（docstring 里有全部语义约定） |
| `services/ingestion/parser_registry.py` | 注册表与白名单 |
| `services/ingestion/repository.py` | 落库函数（B4 只用这一层写库） |
| `tests/test_ingestion_models.py` | 迁移/约束/重传契约测试 |
