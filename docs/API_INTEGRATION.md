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
  "user_id": "demo_user",
  "session_id": "demo_session_001",
  "order_id": "order_picked",
  "channel": "test",
  "message": "骑手已经取餐了，我现在取消能全额退款吗？"
}
```

响应：

```json
{
  "reply": "客服回答文本",
  "answer_basis": "主证据：退款金额咨询；工具结果：骑手已取餐，不应直接承诺全额退款...",
  "evidence_citations": [
    {
      "evidence_id": "kb_1_1",
      "source_type": "knowledge_base",
      "evidence_role": "primary",
      "intent": "退款金额咨询",
      "quote": "退款金额需以订单状态和平台核实结果为准"
    }
  ],
  "tool_results": [
    {
      "tool_name": "query_order_status",
      "status": "success",
      "input": {"order_id": "order_picked"},
      "output": {"status_label": "骑手已取餐"},
      "error_type": "",
      "latency_ms": 0.12,
      "retryable": false
    }
  ],
  "memory_snapshot": {
    "short_term": {"session_id": "demo_session_001", "facts": {}},
    "long_term": {"used": false, "fields": {}}
  },
  "decision_trace": {
    "request_id": "request-id",
    "primary_intent": "退款金额咨询",
    "risk_level": "medium",
    "needs_manual_review": false
  },
  "full_trace": [
    {"step": "request_received", "status": "success", "latency_ms": 0.0},
    {"step": "order_tool_called", "status": "success", "latency_ms": 0.2},
    {"step": "response_returned", "status": "success", "latency_ms": 0.0}
  ],
  "handoff_ticket": null,
  "confidence_score": 0.8,
  "trace": {"retrieval_count": 3, "request_id": "request-id"}
}
```

用途：调用 RAG 链路生成最终客服回复。旧字段仍保留，前端旧页面可以继续读取 `reply`、`confidence_score`、`final_prompt`、`retrieved_documents`、`retrieved_items`、`prompt_context_items` 和 `trace`。新增字段用于诊断面板：

- `answer_basis`：给客服回复下方展示的简短回答依据。
- `evidence_citations`：进入 Prompt 的知识证据引用，区分 `primary` 和 `supporting`。
- `tool_results`：订单状态、退款状态和人工接管工单的结构化结果。
- `memory_snapshot`：短期会话记忆和长期用户记忆快照。
- `decision_trace`：本次路由、风险、证据和人工复核结论摘要。
- `full_trace`：按时间线组织的完整后端步骤。
- `handoff_ticket`：触发人工接管时返回工单摘要。

聊天数据会长期保存到 SQLite：

- `conversation_messages` 保存 user/assistant 消息。
- `conversation_turns` 保存每轮完整响应 JSON，包括诊断字段。
- `chat_sessions` 保存轻量 request trace，供反馈和运营指标使用。

## 聊天历史接口

```http
GET /chat/history?user_id=demo_user&order_id=WM123&limit=50
```

用途：前端重新打开某个订单客服时，恢复该订单的会话消息、`session_id` 和最近一次完整诊断响应。

响应：

```json
{
  "user_id": "demo_user",
  "session_id": "session-id",
  "order_id": "WM123",
  "messages": [
    {"role": "user", "content": "我的餐怎么还没到", "risk_level": "low"},
    {"role": "assistant", "content": "我帮您查看订单状态...", "risk_level": "low"}
  ],
  "latest_response": {
    "reply": "最近一次客服回复",
    "full_trace": []
  }
}
```

查询优先级：

- 如果传 `session_id`，优先按 `session_id` 查。
- 否则按 `user_id + order_id` 查最近会话。

## 订单状态持久化接口

```http
PUT /orders/{order_id}/state
GET /orders/{order_id}/state
```

用途：保存外卖订单当前状态。客服链路中的 `query_order_status(order_id)` 会优先读取这张持久化订单状态表，找不到时才回退到本地 mock 订单。

请求：

```json
{
  "user_id": "demo_user",
  "order_id": "WM123",
  "status": "delivering",
  "status_label": "骑手已取餐，正在配送中",
  "delivery_status": "骑手已取餐，正在配送中",
  "summary": "骑手已取餐，不应直接承诺全额退款。",
  "refund_status": "pending_review",
  "store_name": "青禾轻食便当",
  "items": [{"name": "香煎鸡胸能量碗", "quantity": 1}],
  "total": 35
}
```

持久化位置：

- SQLite `order_states` 表。
- 前端模拟不同订单状态时应更新同一个 `order_id`，不要为不同状态创建多笔订单。

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

- `POST /retrieval/evaluate`：触发固定评估集，返回 Top1/Top3 统计。
- `GET /retrieval/config`：返回当前 embedding 模型、默认阈值和 hybrid 配置。
- 在前端调试台展示 `rerank_score` 和 `model_rerank_score`，观察模型 reranker 对排序的影响。

## 2026-05-06 Retrieval Debug Fields And Rerank Weight

Current `/retrieval/search` result items expose these debug fields for the frontend retrieval console:

- `rerank_score`: final rank score after base hybrid score, model rerank score, and rule bonus. Frontend labels it as `final`.
- `score`: base hybrid score before rerank, currently from `vector_score + keyword_bonus - direction_penalty`.
- `model_rerank_score`: cross-encoder score from `BAAI/bge-reranker-base`. Frontend labels it as `model`.
- `vector_score`: embedding similarity score from `BAAI/bge-small-zh-v1.5`.
- `keyword_bonus`: keyword/rule boost added during hybrid retrieval.
- `direction_penalty`: penalty for misleading business direction, such as cancellation wording pulling a refund query toward cancellation intent.

Current backend rerank formula:

```text
rerank_score = score + model_rerank_score * model_rerank_weight + rule_rerank_bonus
```

Current default:

```text
model_rerank_weight = 0.01
```

The default is intentionally conservative. In the current 12-case evaluation set, larger weights make one business-intent case worse:

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py --compare-rerank-weights 0.01 0.03 0.05
```

Observed comparison:

```text
weight  Top1  Top3Error  Miss  ChangedTop1  Improved  Worsened
0.01    12    0          0     0            0         0
0.03    11    1          0     1            0         1
0.05    11    1          0     1            0         1
```

Important learning conclusion:

The reranker model measures semantic relevance between the user query and a candidate answer/question pair. It does not automatically understand the project's business-intent priority. For example, `取消订单后钱多久退回来` contains cancellation wording, but the real user intent is refund arrival time. A high `model_rerank_score` can therefore pull the result toward a semantically related but business-wrong candidate if the weight is too large.

For frontend debugging, compare the fields in this order:

1. Check `vector_score` to see whether semantic retrieval found a reasonable candidate.
2. Check `keyword_bonus` and `direction_penalty` to see how hybrid business rules changed the base score.
3. Check `score` to understand the pre-rerank order.
4. Check `model_rerank_score` to see the model's pairwise relevance judgment.
5. Check `rerank_score` to see the final rank used by the API.

## 2026-05-07 Prompt Preview API

后端现在已经提供用于 RAG 调试的 prompt preview 接口：

```http
POST /retrieval/prompt-preview
```

用途：

- 返回已经完成召回和重排的参考资料。
- 返回由 `models/prompt.py:create_prompt` 拼出的最终 prompt 文本。
- 让前端在本地 Qwen 模型生成回复之前，先展示“检索证据如何变成模型输入”。

请求体：

```json
{
  "query": "refund arrival time",
  "mode": "hybrid",
  "limit": 3,
  "min_score": 0.62
}
```

请求体复用 `/retrieval/search` 的同一套结构：

- `query`：用户问题。
- `mode`：检索模式，支持 `vector` 或 `hybrid`。
- `limit`：返回的检索结果数量，范围是 1 到 20。
- `min_score`：向量分数过滤阈值，范围是 0.0 到 1.0。

响应体：

```json
{
  "query": "refund arrival time",
  "mode": "hybrid",
  "count": 2,
  "prompt": "...final prompt text...",
  "results": [
    {
      "rank": 1,
      "score": 0.88,
      "rerank_score": 0.891,
      "model_rerank_score": 0.91,
      "vector_score": 0.82,
      "keyword_bonus": 0.06,
      "direction_penalty": 0.0,
      "category": "refund",
      "intent": "refund_progress",
      "question": "When will the refund arrive?",
      "answer": "Refunds return to the original payment method."
    }
  ]
}
```

前端展示建议：

1. 保留 `/retrieval/search` 作为轻量级检索分数调试接口。
2. 当用户需要查看最终 RAG prompt 拼装结果时，调用 `/retrieval/prompt-preview`。
3. 将 `results` 和 `prompt` 放在一起展示，这样可以清楚看到哪些检索答案进入了 prompt。

实现说明：

`/retrieval/prompt-preview` 和 `/retrieval/search` 使用同一条 vector/hybrid/rerank 检索链路。接口拿到候选结果后，会把候选答案转换成 `documents`，再传给 `create_prompt(query, documents)` 生成最终 prompt。
## 2026-05-08 `/chat/prompt` RAG evidence 返回字段

当前聊天接口已经返回本次 RAG 使用的参考资料，前端可以把它展示在客服回复下方，方便调试回答是否有证据支撑。

```http
POST /chat/prompt
```

请求体：

```json
{
  "message": "会员退款多久到账"
}
```

响应体：

```json
{
  "reply": "客服回答文本",
  "confidence_score": 0.95,
  "retrieved_documents": [
    "退款到账时间取决于支付渠道。平台审核通过后通常会原路退回，银行卡或部分第三方支付可能存在处理延迟。"
  ]
}
```

字段说明：

- `reply`：模型生成的客服回复。
- `confidence_score`：当前接口返回的置信度字段。
- `retrieved_documents`：本次生成回复时使用的 RAG 参考资料列表。

前端展示建议：

1. 在客服回复正文下方展示 `retrieved_documents`。
2. 只展示前 3 条即可，避免调试台过长。
3. 把该区域命名为“本次参考资料”，用于人工判断回答是否被资料支撑。

调试时建议按这个顺序排查：

```text
/retrieval/search
-> 看召回和 rerank 是否合理

/retrieval/prompt-preview
-> 看参考资料如何进入最终 prompt

/chat/prompt
-> 看模型回复是否和 retrieved_documents 一致
```

## 2026-05-10 `/chat/prompt` Retrieval Metadata 返回字段

当前聊天接口已经进一步返回结构化检索 metadata，方便前端和评估报告定位 RAG 问题。

```http
POST /chat/prompt
```

请求体：

```json
{
  "message": "餐洒了怎么申请售后"
}
```

响应体：

```json
{
  "reply": "很抱歉影响您的用餐。餐品撒漏可以在订单详情页申请售后...",
  "confidence_score": 0.95,
  "retrieved_documents": [
    "很抱歉影响您的用餐。餐品撒漏可以在订单详情页申请售后..."
  ],
  "retrieved_items": [
    {
      "rank": 1,
      "answer": "很抱歉影响您的用餐。餐品撒漏可以在订单详情页申请售后...",
      "category": "售后流程",
      "intent": "餐品撒漏售后",
      "question": "餐洒了怎么申请售后？",
      "score": 0.9283,
      "rerank_score": 0.9362,
      "model_rerank_score": 0.7901,
      "vector_score": 0.9283,
      "keyword_bonus": 0.0,
      "direction_penalty": 0.0
    }
  ]
}
```

字段关系：

- `retrieved_documents`：只包含进入 prompt 的答案文本，适合直接展示给用户或调试人员看。
- `retrieved_items`：包含检索结果的完整调试 metadata，适合前端调试台和 grounding 报告使用。

前端展示建议：

1. 普通聊天面板默认展示 `reply` 和简化版 `retrieved_documents`。
2. 调试模式下展开 `retrieved_items`，显示 `category`、`intent`、`question` 和分数拆解。
3. 如果某次回复质量差，优先检查 `retrieved_items[0].intent` 是否符合用户真实意图。

## 2026-05-10 Grounding Report And Bad Case Analysis

当前后端提供离线 grounding 评估脚本：

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_chat_grounding.py --use-local-judge --save-report
```

报告输出目录：

```text
reports/chat_grounding/
```

报告中每个 case 会保存：

- `query`
- `retrieved_documents`
- `retrieved_items`
- `reply`
- `manual_judgment`
- `raw_judge_response`
- `judge_status`
- `judge_error`

分析 bad case：

```powershell
.\venv\Scripts\python.exe -B scripts\analyze_grounding_report.py reports\chat_grounding\新报告.json
```

默认只展示 bad case。展示全部 case：

```powershell
.\venv\Scripts\python.exe -B scripts\analyze_grounding_report.py reports\chat_grounding\新报告.json --show-all
```

分析时按这个顺序判断：

```text
retrieved_items 是否命中正确 intent
-> reply 是否利用检索资料
-> judge_reason 是否合理
```

这能区分三类问题：

- 检索问题：`retrieved_items` 本身不相关。
- 生成问题：检索正确，但 `reply` 没有用好资料。
- 评估问题：`reply` 合理，但 judge 判断边界不合理。

## 2026-05-10 FAISS 持久化说明

当前向量检索使用 FAISS 持久化索引：

```text
data/faiss_store/real_vector.index
data/faiss_store/real_vector_docs.json
```

FAISS 保存的是知识库向量索引，不是用户问题缓存，也不是模型回复缓存。

运行时流程：

```text
用户问题
-> 生成 query embedding
-> FAISS 搜索相似知识库向量
-> 返回 TopK 知识库资料
-> hybrid/rerank
-> prompt
-> 模型生成 reply
```

当知识库内容变化、索引数量不匹配或向量维度不匹配时，后端会自动重建 FAISS store。
## 2026-05-11 Current Integration Notes

Current backend debug endpoints:

- `POST /retrieval/search`
- `POST /retrieval/prompt-preview`
- `GET /retrieval/config`
- `POST /chat/prompt`

`/chat/prompt` currently returns:

- `reply`
- `confidence_score`
- `final_prompt`
- `retrieved_documents`
- `retrieved_items`

Current evaluation artifact directories:

- `reports/chat_grounding/`
- `reports/retrieval_eval/`

Grounding report payloads now include:

- `run_config.rag_config`
- `retrieved_items`
- `final_prompt`
- `reply`
- `judge_status`
- `manual_judgment`

Retrieval evaluation report payloads now include:

- `rag_config`
- `rerank_weight`
- `summary`
- `cases`
- per-case TopK `results`
## 2026-05-13 `/chat/prompt` Trace 字段与降级说明

当前 `/chat/prompt` 的响应包含：

- `reply`
- `confidence_score`
- `final_prompt`
- `retrieved_documents`
- `retrieved_items`
- `trace`

示例 `trace`：

```json
{
  "retrieval_count": 3,
  "used_fallback_prompt": false,
  "reply_rules_applied": false,
  "answer_source": "rag",
  "degraded": false,
  "failure_stage": "none",
  "fallback_reason": ""
}
```

字段含义：

- `retrieval_count`：结构化 retrieval item 的返回条数
- `used_fallback_prompt`：这次是否使用了 fallback prompt，而不是正常 RAG prompt
- `reply_rules_applied`：reply rules 是否真的改动了最终 reply
- `answer_source`：`rag` 或 `fallback`
- `degraded`：这次请求是否进入了降级路径
- `failure_stage`：`none`、`retrieval`、`generation` 或 `reply_rules`
- `fallback_reason`：触发 degrade/fallback 的简短原因

当前 `services/chat_service.py` 中的 degrade 设计：

1. retrieval failure
   - 降级到 fallback prompt
2. generation failure
   - 返回固定安全兜底回复
3. reply-rules failure
   - 保留原始模型回复

这意味着 `/chat/prompt` 现在更适合理解为：

```text
request
-> retrieval
-> prompt selection
-> generation
-> reply rules
-> response + trace
```

对前端来说，`trace` 更适合作为调试面板或折叠开发者区域内容，而不是普通用户可见字段。
