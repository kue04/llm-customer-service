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
- `vector_score`
- `keyword_bonus`
- `direction_penalty`
- `category`
- `intent`
- `question`
- `answer`

前端展示建议：

- 把 `score` 作为主排序分。
- 同时展示 `vector_score`、`keyword_bonus`、`direction_penalty`。
- 展示 `category`、`intent`、`question`、`answer`，方便判断是否召回正确。

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
