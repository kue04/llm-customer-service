# 学习进度记录

本文档用于记录这个项目中的学习进度。目标不是只看懂代码，而是逐步做到：能读懂、能改动、能自己写小模块。

## 当前学习阶段

当前阶段：FastAPI 基础接口开发。

路由拆分还没有正式开始学习。下一阶段会从 `APIRouter` 的最基础概念重新开始，不默认已经掌握。

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
| router 拆分 | 未开始 | 下一阶段从零开始 |
| Git 提交流程 | 待加强 | 需要继续练习 status、add、commit、push |
| LoRA 训练代码 | 看过/跑过 | 还没有作为代码能力重点练习 |
| 前端 | 未开始 | 后续阶段学习 |
| RAG 向量检索 | 未开始 | 后续阶段学习 |

## 仍然容易混淆的点

- `Path` 构造的是路径，`open` 才是打开文件。
- `json.loads(line)` 是把 JSON 字符串转成 Python dict。
- `set()` 自动去重，`list` 不会自动去重。
- `Query` 放在接口函数参数里，属于 HTTP 查询参数校验。
- `Field` 放在 Pydantic 模型字段里，属于请求体或模型字段校验。
- `HTTPException` 应该用于真实错误，不要用普通 200 响应伪装错误。
- `response_model` 不是业务逻辑，而是接口返回结构声明和校验。

## 下一阶段学习路线

### 第 1 步：重新学习路由拆分

目标：

- 理解 `APIRouter` 是什么。
- 新建 `routers/example.py`。
- 把 examples 相关接口从 `main.py` 移过去。
- 在 `main.py` 用 `app.include_router(...)` 注册。

暂定练习：

```text
main.py 只负责创建 app 和注册 router
routers/example.py 负责 /examples/categories、/examples/by-category、/examples/search
```

### 第 2 步：整理 service 层

目标：

- 把重复的 `DATA_PATH` 提到 `services/example_service.py` 顶部。
- 抽一个 `iter_examples()` 或 `load_examples()` 小函数。
- 理解“避免重复代码”不是炫技，而是减少未来改错地方。

### 第 3 步：补充接口错误文档

目标：

- 让 `/docs` 里不再显示 `404 Undocumented`。
- 学习 FastAPI 的 `responses={...}`。

### 第 4 步：学习 Git 整理

目标：

- 理解 `git status`。
- 区分源码、数据、模型产物、缓存。
- 学习哪些文件应该提交，哪些文件应该忽略。

### 第 5 步：回到模型主线

目标：

- 理解当前 `/chat/prompt` 如何调用模型。
- 理解 LoRA adapter 是怎样被加载的。
- 后续增加配置开关，例如是否启用 adapter。

### 第 6 步：后续扩展

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

路由拆分部分从下一次开始重新学，不跳过概念。
