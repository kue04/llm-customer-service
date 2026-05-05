# API 对接文档

本文档用于前端对接当前 FastAPI 后端。当前后端定位是外卖客服 RAG 学习项目，接口分为聊天、检索调试、模型信息和知识库样本浏览。

## 基础信息

本地开发地址：

```text
http://127.0.0.1:8000
```

启动命令：

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

## RAG 检索调试

```http
POST /retrieval/search
```

用途：前端检索调试台的核心接口。用于展示向量检索或 hybrid 检索的 TopK 结果和分数拆解。

请求体：

```json
{
  "query": "会员退款多久到账",
  "mode": "hybrid",
  "limit": 3,
  "min_score": 0.62
}
```

字段说明：

- `query`：用户问题，不能为空。
- `mode`：检索模式，支持 `vector` 和 `hybrid`。
- `limit`：返回数量，范围 1 到 20。
- `min_score`：纯向量分过滤阈值，范围 0 到 1。

模式说明：

- `vector`：只按 `vector_score` 排序。
- `hybrid`：按 `score = vector_score + keyword_bonus - direction_penalty` 排序。

响应字段：

- `rank`
- `score`
- `rerank_score`
- `model_rerank_score`
- `vector_score`
- `keyword_bonus`
- `direction_penalty`
- `category`
- `intent`
- `question`
- `answer`

前端展示建议：

- 把 `rerank_score` 作为最终排序分。
- 同时展示 `score`，用于观察 rerank 前的 vector/hybrid 分数。
- 展示 `model_rerank_score`，用于观察 bge-reranker 对 query-candidate pair 的相关性判断。
- 同时展示 `vector_score`、`keyword_bonus`、`direction_penalty`。
- 展示 `category`、`intent`、`question`、`answer`，方便判断是否召回正确。

### Rerank 对接状态

后端检索链路内部已经接入 rerank 学习版本：

```text
vector_score
-> score = vector_score + keyword_bonus - direction_penalty
-> rerank_score = score + model_rerank_score * 0.01 + rule_rerank_bonus
```

当前使用：

- embedding 模型：`BAAI/bge-small-zh-v1.5`
- reranker 模型：`BAAI/bge-reranker-base`
- 模型 rerank 权重：`model_rerank_score * 0.01`
- 规则 rerank 示例：当 query 包含 `怎么办`，且候选意图或问题包含 `追问` 时，加 `0.02`

评估脚本 `scripts/evaluate_vector_retrieval.py` 已经打印：

- `rerank`
- `model`
- `vector`
- `bonus`
- `penalty`

当前正式 `/retrieval/search` response model 已暴露 `rerank_score` 和 `model_rerank_score`，前端调试台可以直接展示模型 reranker 对排序的影响。

## 聊天接口

```http
POST /chat/prompt
```

请求体：

```json
{
  "message": "会员退款多久到账"
}
```

响应：

```json
{
  "reply": "客服回答文本",
  "confidence_score": 0.8
}
```

用途：调用 RAG 链路生成最终客服回复。

## 模型信息

```http
GET /model/info
```

响应：

```json
{
  "base_model": "qwen2.5-1.5b-instruct",
  "adapter_enabled": true,
  "adapter_name": "takeout-qwen-lora-gpu-smoke"
}
```

用途：顶部状态栏展示基础模型和 LoRA adapter 状态。

## 知识库分类

```http
GET /examples/categories
```

用途：获取知识库分类列表。

## 按分类查看样本

```http
GET /examples/by-category?category=退款售后&limit=5
```

用途：按分类浏览知识库样本。

## 搜索知识库样本

```http
POST /examples/search
```

请求：

```json
{
  "keyword": "退款",
  "limit": 5
}
```

用途：按关键词搜索知识库样本。

## 错误处理建议

- `422`：请求参数不合法，例如 `query` 为空、`limit` 超出范围。
- `500`：模型或检索链路内部错误。
- 首次请求可能较慢，因为 embedding 模型和本地生成模型可能需要加载。

## 后续 API 建议

- `POST /retrieval/prompt-preview`：返回最终拼接给模型的 prompt。
- `POST /retrieval/evaluate`：触发固定评估集，返回 Top1/Top3 统计。
- `GET /retrieval/config`：返回当前 embedding 模型、默认阈值和 hybrid 配置。
- 在前端调试台展示 `rerank_score` 和 `model_rerank_score`，观察模型 reranker 对排序的影响。
