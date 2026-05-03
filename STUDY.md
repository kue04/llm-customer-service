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

## 2026-04-30 学习记录：检索调试脚本与分数解释

### 本节完成内容

- 给 `scripts/debug_prompt.py` 增加命令行参数：
  - 位置参数 `query`：用于传入要调试的用户问题。
  - `--limit`：控制返回多少条候选资料。
  - `--no-prompt`：只查看检索结果，不输出最终 prompt。
- 将 `utils/retriever.py` 中的检索结果拆成两层函数：
  - `retrieve_document_candidates(query, limit)`：返回带 `score`、`answer`、`details` 的候选资料。
  - `retrieve_documents(query, limit)`：继续只返回 `List[str]`，供正式 chat 链路使用。
- 增加相似答案去重：
  - `normalize_answer_text()`：去掉标点和空白，统一文本。
  - `text_bigrams()`：把中文文本切成两个字一组的片段。
  - `is_similar_answer()`：判断两个答案是否高度相似，避免“原答案 + 一句补充说明”重复进入候选结果。
- 增加分数解释：
  - `explain_knowledge_item_score()` 返回总分和每次加分原因。
  - `debug_prompt.py` 输出每条候选资料的字段命中情况。

### 关键理解

- `argparse` 会把命令行参数解析成 `args` 对象。
- `--no-prompt` 通过 `action="store_true"` 实现：
  - 命令中出现 `--no-prompt` 时，`args.no_prompt == True`。
  - 命令中不出现时，`args.no_prompt == False`。
- 命令行参数名中的横杠会转换成 Python 属性名中的下划线：
  - `--no-prompt` 对应 `args.no_prompt`。
- `help` 只是说明文字，不负责实现参数逻辑。
- `retrieve_documents()` 原来内部有 `score`，但返回时只返回了 answer，所以外部看不到分数。
- 新增 `retrieve_document_candidates()` 是为了调试时保留完整候选信息，同时不破坏正式接口。
- `score_knowledge_item()` 现在可以复用 `explain_knowledge_item_score()`，避免打分逻辑写两份。
- Python 缩进会决定代码是否属于循环内部。`print()` 缩进错了，会导致只打印最后一条 detail。

### 当前检索流程

```text
用户问题
-> normalize_terms() 提取关键词
-> 遍历 JSONL 知识库
-> explain_knowledge_item_score() 计算每条资料分数和加分细节
-> 完全重复 answer 跳过
-> 相似 answer 跳过
-> candidates 按 score 从高到低排序
-> retrieve_document_candidates() 返回带分数和 details 的结果
-> retrieve_documents() 只取 answer，供正式 RAG 链路使用
```

### 已验证命令

```powershell
.\venv\Scripts\python.exe -B scripts\debug_prompt.py "会员退款怎么办？" --limit 5
```

可以看到 5 条候选资料、每条资料的分数、字段命中解释，以及最终 prompt。

```powershell
.\venv\Scripts\python.exe -B scripts\debug_prompt.py "会员退款怎么办？" --limit 5 --no-prompt
```

只输出检索候选资料，不输出最终 prompt。

### 本节掌握情况

- 已理解“先打分、再排序、取前 N 条”的检索流程。
- 已理解为什么要把调试函数和正式业务函数分开。
- 已理解 `argparse` 中位置参数、带值参数、布尔开关的区别。
- 已理解 `details` 是为了观察打分过程，不是给模型最终回答使用。

### 下一步建议

- 先复习 `debug_prompt.py` 和 `retriever.py` 当前代码。
- 确认能手动解释 `score=17` 是如何由各字段加分得到的。
- 下一节再考虑是否继续优化检索排序，例如处理同分候选、调整字段权重，或加入更细的关键词命中规则。

## 2026-04-30 学习记录：候选资料多条件排序

### 本节完成内容

- 给每条 candidate 增加 `matched_term_count` 字段。
- 在 `debug_prompt.py` 中输出 `matched_terms=...`，用于观察候选资料总共命中了多少关键词。
- 将候选资料排序规则从单一分数排序：

```python
candidates.sort(key=lambda item: item["score"], reverse=True)
```

升级为多条件排序：

```python
candidates.sort(
    key=lambda item: (
        item["score"],
        item["matched_term_count"],
        -len(item["answer"]),
    ),
    reverse=True,
)
```

### 关键理解

- `matched_term_count` 表示所有 `matched_terms` 类型 detail 中命中的关键词数量总和。
- 计算方式：

```python
matched_term_count = sum(
    len(detail["terms"])
    for detail in explanation["details"]
    if detail["reason"] == "matched_terms"
)
```

- `sum(...)` 会把每条 detail 的命中词数量加起来。
- `if detail["reason"] == "matched_terms"` 表示只统计关键词命中，不统计完整句子命中。
- Python 排序 key 可以是 tuple：
  - 先比较第 1 个元素。
  - 第 1 个元素相同，再比较第 2 个元素。
  - 第 2 个元素相同，再比较第 3 个元素。
- 当前排序优先级：
  1. `score` 越高越靠前。
  2. `matched_term_count` 越高越靠前。
  3. `answer` 越短越靠前。
- 因为 `reverse=True` 是从大到小排序，所以要用 `-len(item["answer"])` 实现“短答案优先”。

### 验证结果

使用命令：

```powershell
.\venv\Scripts\python.exe -B scripts\debug_prompt.py "外卖订单退款超时怎么办？" --limit 8 --no-prompt
```

观察到 `score=12` 且 `matched_terms=5` 的候选资料顺序发生变化，说明第三排序条件 `-len(answer)` 已经生效。

### 本节掌握情况

- 已理解候选资料不是只靠一个分数排序，也可以用多个条件分层排序。
- 已理解 tuple sort 和“加权总分”不同：tuple sort 是先后比较，不是把多个值加起来。
- 已理解 `-len(answer)` 的作用是让短答案在倒序排序中排得更靠前。

### 下一步建议

- 下一节可以学习“检索质量评估”：准备一组固定测试问题，运行调试脚本，观察每个问题的 top 结果是否合理。
- 这会让检索优化从“看单个例子”变成“用小测试集验证整体效果”。

## 2026-04-30 学习记录：检索质量评估与命中率

### 本节完成内容

- 新增 `scripts/evaluate_retrieval.py`，用于批量评估多个固定测试问题。
- 将单个问题调试升级为小型评估集：
  - 会员退款
  - 外卖超时
  - 订单取消后退款到账
  - 骑手联系不上
  - 食品异物
  - 优惠券不可用
- 给每个评估问题增加 `expected_keywords`。
- 输出 Top1 命中的期望关键词。
- 增加 Top1 命中率：

```text
Top1 命中率：2/3 = 66.7%
```

- 将初步判断从二分类升级为多档判断：
  - `OK`
  - `部分命中`
  - `弱命中`
  - `未命中`

### 关键理解

- 单条调试只能说明一个问题的效果，不能说明整体检索质量。
- 固定评估集可以帮助发现检索器的稳定问题。
- `expected_keywords` 是一种简单评估方法，不是真正语义评估。
- 字面关键词评估会有局限：
  - 例如答案语义接近，但没有出现“到账”两个字，仍可能被判为部分命中。
- `calculate_hit_rate()` 用于计算命中比例：

```python
len(matched_keywords) / len(expected_keywords)
```

- `{hit_rate:.1%}` 可以把小数格式化成百分比：

```text
0.6666 -> 66.7%
```

- `judge_hit_rate()` 将命中率转换为人工可读判断：

```text
100%        -> OK
50%~99.9%   -> 部分命中
0%~49.9%    -> 弱命中
0%          -> 未命中
```

### 当前评估结果

- 会员退款：100%，OK。
- 外卖超时：100%，OK。
- 订单取消后钱多久到账：66.7%，部分命中。
- 骑手联系不上：100%，OK。
- 食品有异物：100%，OK。
- 优惠券不能用：100%，OK。

### 本节掌握情况

- 已理解如何用小型测试集观察检索质量。
- 已理解评估脚本和调试脚本的区别：
  - `debug_prompt.py` 看单个问题的检索和 prompt。
  - `evaluate_retrieval.py` 批量看多个问题的检索质量。
- 已理解 RAG 优化顺序：
  1. 先看检索是否召回正确资料。
  2. 再看 prompt 是否组织得清楚。
  3. 最后再看模型生成效果。

### 下一步建议

- 给评估脚本增加整体汇总：
  - 总问题数
  - OK 数量
  - 部分命中数量
  - 弱命中数量
  - 未命中数量
  - 平均命中率
- 这一步会把评估从“逐条查看”升级为“整体指标”。

## 2026-05-01 学习记录：评估脚本整体汇总

### 本节完成内容

- 给 `scripts/evaluate_retrieval.py` 增加整体评估汇总。
- `print_query_results()` 不再只负责打印单条结果，还会返回结构化评估结果：

```python
{
    "query": query,
    "hit_rate": hit_rate,
    "judgement": judgement,
}
```

- 使用 `results = []` 收集所有评估 case 的结果。
- 使用 `results.append(result)` 将每个问题的评估结果保存起来。
- 新增 `print_summary(results)` 输出整体指标：
  - 总问题数
  - OK 数量
  - 部分命中数量
  - 弱命中数量
  - 未命中数量
  - 平均命中率

### 关键理解

- 单条评估只能说明某一个问题的表现，整体汇总才能观察检索器的整体质量。
- `results` 是一个列表，里面保存每个问题的结构化评估结果。
- 计数逻辑：

```python
sum(1 for result in results if result["judgement"] == "OK")
```

含义是：遍历所有结果，遇到一个 `OK` 就贡献 `1`，最后求和。

- 平均命中率计算方式：

```python
sum(result["hit_rate"] for result in results) / total
```

- 当前评估方式仍然是关键词级别评估，不是真正语义评估；它适合作为早期轻量评估工具。

### 当前评估汇总

```text
总问题数：6
OK：5
部分命中：1
弱命中：0
未命中：0
平均命中率：94.4%
```

### 本节掌握情况

- 已理解如何从逐条评估升级为整体指标。
- 已理解为什么工程项目需要可重复的评估脚本。
- 已理解 RAG 项目不能只靠“看起来回答不错”，而要有固定测试集和指标。

### 下一步建议

- 保存当前阶段改动。
- 下一阶段进入向量检索 RAG：
  - embedding 是什么
  - 文本如何变成向量
  - FAISS / Chroma 如何保存和检索向量
  - 关键词检索和向量检索的区别
  - hybrid search 和 rerank 为什么重要
## 2026-05-03 学习记录：向量检索 RAG 与检索调试 API

### 本节完成内容

- 新增 `utils/vector_retriever.py`，从关键词检索升级到向量检索学习版本。
- 先用 `build_toy_embedding()` 理解文本如何变成低维向量。
- 学习 `cosine_similarity()`，理解向量检索按语义方向相似度排序。
- 新增 toy 向量索引缓存：`build_toy_vector_index()`、`get_toy_vector_index()`。
- 安装并使用 `sentence-transformers`。
- 使用 `BAAI/bge-small-zh-v1.5` 将中文文本编码为 512 维真实 embedding。
- 新增真实向量索引缓存：`get_embedding_model()`、`build_embedding()`、`build_real_vector_index()`、`get_real_vector_index()`。
- 新增真实向量检索 `retrieve_by_real_vector()`，支持 `min_score`、答案去重和相似答案去重。
- 新增 hybrid 排序信号：`vector_score`、`keyword_bonus`、`direction_penalty`。
- 当前 hybrid 分数公式：`score = vector_score + keyword_bonus - direction_penalty`。
- 新增 `scripts/evaluate_vector_retrieval.py`，用于批量评估向量检索质量。
- 评估脚本支持 `Top1 命中`、`Top3 召回但 Top1 错误`、`未命中`、`error_type`、`notes` 和错误类型分布统计。
- 新增 `/retrieval/search` API，供前端和 `/docs` 调试检索结果。
- 新增 `docs/API_INTEGRATION.md` 和 `docs/FRONTEND_DESIGN.md`，为前端阶段做准备。

### 关键理解

- embedding 是把文本转换成数字向量，向量空间里方向相近通常代表语义相近。
- toy embedding 适合理解机制，但不能作为真实检索方案。
- 真实 embedding 不应在每次请求时重新给知识库计算向量，而是应该构建索引并缓存。
- 纯向量检索能召回语义接近内容，但可能出现意图粒度相近、角色方向相反、答案重复等问题。
- hybrid search 的目标不是替代向量检索，而是用业务可解释信号轻微纠偏。
- RAG 优化不能只看单条 query，需要固定评估集和可重复评估脚本。
- `Top3 召回但 Top1 错误` 表示召回到了可用资料，但排序仍需优化。

### 当前接口能力

新增接口：

```text
POST /retrieval/search
```

请求示例：

```json
{
  "query": "会员退款多久到账",
  "mode": "hybrid",
  "limit": 3,
  "min_score": 0.62
}
```

返回字段包括：

- `rank`
- `score`
- `vector_score`
- `keyword_bonus`
- `direction_penalty`
- `category`
- `intent`
- `question`
- `answer`

### 当前评估结果

当前小型向量检索评估集结果：

```text
总问题数：3
Top1 命中：2
Top3 召回但 Top1 错误：1
未命中：0
错误类型分布：
意图粒度相近：1
```

### 当前限制

- `keyword_bonus` 和 `direction_penalty` 仍是轻量规则，不是通用 reranker。
- 当前评估集只有 3 条，不能代表整体检索质量。
- 仍然使用 Python list + for 循环做向量相似度比较，适合 500 条知识库学习，不适合大规模生产。
- 后续如果知识库变大，应学习 FAISS、Chroma 或 Milvus。

### 下一步建议

- 先做前端 RAG 检索调试台，对接 `/retrieval/search`。
- 前端优先展示 `score`、`vector_score`、`keyword_bonus`、`direction_penalty`，帮助观察排序原因。
- 再扩展评估集，增加食品安全、优惠券、取消退款、配送异常等更多 case。
- 后续再学习 rerank，用更强模型对 TopK 候选重新排序。
