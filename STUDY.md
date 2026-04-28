# 学习进度记录

本文档用于记录这个项目中的学习进度。目标不是只看懂代码，而是逐步做到：能读懂、能改动、能自己写小模块。

## 当前学习阶段

当前阶段：FastAPI 基础接口开发与项目结构整理。

路由拆分已经完成第一轮练习：examples 接口和 model info 接口都已经从 `main.py` 拆到独立 router。当前正在学习模型主线：`/chat/prompt` 如何检索资料、拼 prompt，并交给本地模型生成回复。

## 已完成练习

### 1. GET 健康检查接口

练习内容：

- 新增 `/health`。
- 理解 `@app.get(...)`。
- 理解函数返回 dict 会自动变成 JSON。

掌握情况：已了解基础形式，但接口本身较简单，不作为主要能力判断。

### 2. 模型信息接口

练习内容：

- 新增 `/model/info`。
- 在 `services/chat_service.py` 中编写 `get_model_info()`。
- 使用 `MODEL_PATH.name`、`ADAPTER_PATH.name`，避免向 API 调用方暴露本机绝对路径。
- 使用 `adapter_config.json` 判断 adapter 是否可用。
- 新增 `schemas/info_schema.py` 和 `ModelInfoResponse`。

关键收获：

- API 返回值要考虑安全性和稳定性。
- 接口层调用 service 层函数。
- `response_model` 可以让接口返回结构更清楚。

掌握情况：可以理解并完成。

### 3. 分类列表接口

练习内容：

- 新增 `/examples/categories`。
- 新增 `services/example_service.py`。
- 从 `data/takeout_customer_service_seed.jsonl` 读取数据。
- 使用 `json.loads(line)` 把 JSON 字符串转成 Python dict。
- 使用 `set()` 对分类去重。
- 使用 `sorted(...)` 排序返回。
- 新增 `CategoriesResponse`。

关键收获：

- `Path(...)` 是构造文件路径，不是读取文件内容。
- `DATA_PATH.open(...)` 才是真正打开文件。
- `set` 适合去重，列表 `[]` 不会自动去重。
- JSONL 是“一行一个 JSON”。

掌握情况：已理解核心流程。

### 4. 按分类查询样本接口

练习内容：

- 新增 `/examples/by-category`。
- 使用查询参数 `category` 和 `limit`。
- 根据分类过滤 JSONL 数据。
- 返回 `question` 和 `answer` 对象列表。
- 新增嵌套 response model：
  - `ExampleItem`
  - `ExamplesByCategoryResponse`
- 使用 `Query(default=5, ge=1, le=20)` 限制 `limit`。
- 使用 `HTTPException(status_code=404)` 处理分类不存在。

关键收获：

- 查询参数来自 URL。
- `Query` 用于 GET 查询参数校验。
- `ge` 是 greater than or equal，表示大于等于。
- `le` 是 less than or equal，表示小于等于。
- `HTTPException` 是 FastAPI 用来返回 HTTP 错误的异常。

掌握情况：已能测试 200、404、422 三类结果。

### 5. POST 搜索接口

练习内容：

- 新增 `/examples/search`。
- 使用 POST 请求体。
- 新增请求模型 `SearchExamplesRequest`。
- 新增响应模型：
  - `SearchResultItem`
  - `SearchExamplesResponse`
- 按 `keyword` 搜索问题和答案。
- 使用 `Field(min_length=1)` 限制关键词不能为空。
- 使用 `Field(default=5, ge=1, le=20)` 限制请求体中的 `limit`。

关键收获：

- GET 常用于查询，POST 常用于提交请求体。
- `request: SearchExamplesRequest` 表示 FastAPI 会把 JSON 请求体转成 Pydantic 对象。
- `request.keyword` 和 `request.limit` 可以读取请求体字段。
- `Field` 用于 Pydantic 模型字段校验。
- `Query` 用于 URL 查询参数校验。

掌握情况：已能完成 schema、service、接口调用和测试。

### 6. 路由拆分

练习内容：

- 新建 `routers/example.py`。
- 把 `/examples/categories`、`/examples/by-category`、`/examples/search` 从 `main.py` 拆到 `routers/example.py`。
- 在 `main.py` 中使用 `app.include_router(example.router, prefix="/examples", tags=["examples"])` 注册。
- 新建 `routers/info.py`。
- 把 `/model/info` 从 `main.py` 拆到 `routers/info.py`。
- 在 `main.py` 中使用 `app.include_router(info.router, prefix="/model", tags=["info"])` 注册。
- 理解 router 内部路径和 `prefix` 会拼成最终访问路径。

关键收获：

- `APIRouter` 可以理解成“小型路由盒子”。
- `main.py` 应该尽量只负责创建 `app` 和注册 router。
- 子路由文件负责具体业务接口。
- `prefix="/examples"` 加上 `@router.get("/categories")`，最终路径是 `/examples/categories`。
- `tags` 主要影响 `/docs` 里的接口分组。

掌握情况：已完成第一轮拆分，能理解 `prefix` 的作用。

### 7. Service 层小重构

练习内容：

- 把重复的 `DATA_PATH` 提到 `services/example_service.py` 顶部。
- 新增 `iter_examples()`，统一读取 JSONL 文件。
- 使用 `yield json.loads(line)` 一条一条返回样本。
- 让 `get_categories()`、`get_examples_by_category()`、`search_examples()` 复用 `iter_examples()`。

关键收获：

- 重构的目标是外部行为不变，内部结构更清楚。
- `yield` 适合一条一条读取文件数据。
- 公共读取逻辑抽出来后，后续修改数据来源只需要改一个地方。

掌握情况：已完成，并通过接口测试确认行为不变。

### 8. OpenAPI 错误响应说明

练习内容：

- 给 `/examples/by-category` 增加 `responses={404: ...}`。
- 理解 `HTTPException` 和 `responses` 的区别。

关键收获：

- `HTTPException` 负责真实运行时返回 404。
- `responses` 负责让 `/docs` 文档展示这个接口可能返回 404。

掌握情况：已理解并完成。

### 9. Chat 主链路理解

练习内容：

- 阅读 `routers/chat.py`，理解 `request.message` 来自 POST 请求体。
- 阅读 `services/chat_service.py`，理解 `get_answer_from_rag(query)` 的两条路径。
- 理解 `query` 只是函数内部参数名，本质上是用户输入的 message。
- 理解 `retrieve_documents(query)` 是项目自定义检索函数，不是外部 API。
- 理解 `documents` 非空时才会调用 `create_prompt(query, documents)`。
- 理解 `documents=[]` 时会走兜底 prompt，并把 `confidence_score` 设为 `0.5`。

关键收获：

- POST 请求体字段由 Pydantic 模型决定，例如 `ChatRequest.message`。
- `request.message` 传入函数后可以换名为 `query`。
- `create_prompt()` 负责生成提示词，不负责生成最终回答。
- 最终回答由 `generate_reply(prompt)` 调用本地 Qwen 模型生成。

掌握情况：已能复述 `/chat/prompt` 的核心调用链。

### 10. 中文关键词检索器

练习内容：

- 将 `utils/retriever.py` 从英文硬编码 `knowledge_base` 改为读取 `data/takeout_customer_service_seed.jsonl`。
- 新增 `iter_knowledge_items()` 逐行读取 JSONL。
- 给 `retrieve_documents(query, limit=3)` 增加返回数量限制。
- 新增 `DOMAIN_KEYWORDS`，从中文用户问题中提取外卖领域关键词。
- 将检索字段从只看 `question` 扩展为 `question`、`answer`、`category`、`intent`。
- 从“先匹配先返回”改成“候选结果打分排序后返回前 N 条”。

关键收获：

- 当前检索器是关键词检索，不是向量检索。
- `query_terms.intersection(search_terms)` 用于找用户关键词和数据关键词的交集。
- `score = len(matched_terms)` 可以作为最简单的相关性分数。
- 同分结果仍可能受数据顺序影响，后续可继续做字段加权排序。

掌握情况：已能让“会员退款怎么办？”检索到会员退款相关中文资料。

### 11. 中文 Prompt 模板与 Debug 脚本

练习内容：

- 将 `models/prompt.py:create_prompt()` 从英文模板改成中文客服模板。
- 理解 `create_prompt()` 返回的是普通字符串，不是 JSON，也不是最终客服回答。
- 新增 `scripts/debug_prompt.py`，用于调试“用户问题 -> 检索资料 -> prompt”。
- 解决脚本从 `scripts/` 目录运行时找不到 `models` 模块的问题，使用 `sys.path.insert(...)` 加入项目根目录。

关键收获：

- prompt 是模型输入，不是模型输出。
- debug prompt 可以在不加载大模型的情况下检查 RAG 上游是否正常。
- 如果 `参考资料` 为空，说明检索器没有命中，不应该先怀疑模型。

掌握情况：已能使用 `scripts/debug_prompt.py` 查看模型将收到的 prompt。

## 当前代码能力统计

| 能力点 | 当前状态 | 说明 |
| --- | --- | --- |
| FastAPI 基础 GET | 已入门 | 能理解并添加简单 GET 接口 |
| FastAPI POST 请求体 | 已入门 | 能使用 Pydantic request model |
| response_model | 已入门 | 能为接口声明返回结构 |
| Pydantic BaseModel | 已入门 | 能定义简单字段和嵌套字段 |
| Query 参数校验 | 已入门 | 理解 `default`、`ge`、`le` |
| Field 请求体校验 | 已入门 | 理解 `min_length`、`ge`、`le` |
| JSONL 读取 | 已入门 | 能按行读取并解析 JSON |
| set 去重 | 已理解 | 知道和 list 的区别 |
| HTTPException | 已入门 | 知道用它返回 404 等错误 |
| service 层函数 | 已入门 | 能把业务逻辑放到 service |
| router 拆分 | 已入门 | 已拆分 examples 和 info router |
| Git 提交流程 | 已入门 | 已完成 status、分批 add、cached diff、commit、push |
| Chat 调用链路 | 已入门 | 理解 request、query、retriever、prompt、generate_reply |
| 中文关键词检索 | 已入门 | 已能读取 JSONL 并按简单分数排序 |
| Prompt 调试 | 已入门 | 已新增 debug prompt 脚本 |
| LoRA 训练代码 | 看过/跑过 | 还没有作为代码能力重点练习 |
| 前端 | 未开始 | 后续阶段学习 |
| RAG 向量检索 | 未开始 | 当前只有关键词检索，后续升级 |

## 仍然容易混淆的点

- `Path` 构造的是路径，`open` 才是打开文件。
- `json.loads(line)` 是把 JSON 字符串转成 Python dict。
- `set()` 自动去重，`list` 不会自动去重。
- `Query` 放在接口函数参数里，属于 HTTP 查询参数校验。
- `Field` 放在 Pydantic 模型字段里，属于请求体或模型字段校验。
- `HTTPException` 应该用于真实错误，不要用普通 200 响应伪装错误。
- `response_model` 不是业务逻辑，而是接口返回结构声明和校验。
- `prefix` 会和 router 内部路径拼接，不要重复写 `/examples/examples/...`。
- `HTTPException` 是运行时行为，`responses={...}` 是文档说明。
- `create_prompt()` 生成的是模型输入，不是客服回答。
- `documents=[]` 时不会调用 `create_prompt()`，而是走兜底 prompt。
- 当前中文检索是关键词匹配，不是真正语义理解。

## 下一阶段学习路线

### 第 1 步：继续优化中文检索排序

目标：

- 给 `intent`、`category`、`question`、`answer` 不同字段设置不同权重。
- 让“会员退款怎么办？”更稳定地优先返回“会员退款”样本。
- 学习为什么检索排序会影响模型回答质量。

### 第 2 步：配置化模型与 adapter

目标：

- 学习把硬编码路径改成配置项。
- 例如通过环境变量控制是否启用 adapter。
- 让 `/model/info` 返回更完整但不暴露本机绝对路径的信息。

### 第 3 步：继续练习 Git 小提交

目标：

- 每完成一个小功能就查看 `git status`。
- 学会用小 commit 保存阶段性成果。
- 保持 GitHub 远程同步。

### 第 4 步：后续扩展

候选方向：

- 简单前端页面。
- 更完整的评估报告。
- 关键词检索升级为向量检索 RAG。
- 更系统的 LoRA/QLoRA 训练参数实验。

## 学习方式约定

为了真正训练代码能力，后续采用这个节奏：

1. Codex 先解释目标和相关文件。
2. 你先写 10 到 30 行关键代码。
3. 你贴代码和测试结果。
4. Codex 做代码审查、解释错误、必要时帮你补丁。
5. 每完成一小节，更新本文件。

每完成一小节，及时更新本文件。
