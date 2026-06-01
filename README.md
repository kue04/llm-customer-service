# 外卖客服 RAG 智能问答系统

这是一个面向外卖客服场景的 RAG 智能问答项目。项目目标不是简单调用大模型，而是构建一条可调试、可评测、可解释的 AI 应用工程链路：从知识库检索、证据组织、Prompt 构造、本地模型生成，到安全兜底和自动化评测闭环。

后端位于 `D:\llm\llm-customer-service`，前端调试台位于 `D:\llm\front`。

## 项目功能

- 基于 FastAPI 提供客服问答、检索调试、Prompt 预览、模型信息和知识库样本接口。
- 使用本地 `Qwen2.5-1.5B-Instruct` 生成中文客服回复，并支持 LoRA adapter 检测和加载。
- 构建外卖客服 JSONL 知识库，覆盖退款售后、配送进度、售后流程、食品安全、优惠券、平台安全、隐私保护等场景。
- 支持关键词检索、向量检索、hybrid search 和 `bge-reranker-base` rerank。
- 在 Context Builder 中区分 `primary evidence` 和 `supporting evidence`，降低相似意图混用风险。
- 对高频和高风险场景提供 `reply_rules` 兜底，例如私下转账、食品安全、退款进度、商家拒绝退款等。
- 提供 grounding evaluation，可以自动评估回复是否直接回答、是否基于证据、是否有用。
- 提供 bad case 分析脚本，输出 `issue_type` 和 `suggested_layer`，帮助判断问题应该改 retrieval、context builder、generation、reply rules 还是 judge。

## RAG 链路

```text
用户问题
-> FastAPI /chat/prompt
-> vector retrieval + keyword bonus + direction penalty
-> bge-reranker-base rerank
-> Top1 标记为 primary evidence
-> 其余资料标记为 supporting evidence
-> 构造 final_prompt
-> 本地 Qwen 生成客服回复
-> reply_rules 高风险/高频场景兜底
-> 返回 reply、retrieved_items、prompt_context_items、final_prompt、trace
```

## 项目亮点

### 1. 完整 AI 应用工程闭环

项目包含后端接口、知识库、向量索引、rerank、Prompt 构造、模型生成、前端调试、自动评测和 bad case 分析，不是单次 Prompt Demo。

### 2. Hybrid Retrieval + Reranker

检索排序使用：

```text
score = vector_score + keyword_bonus - direction_penalty
rerank_score = score + model_rerank_score * 0.01 + rule_rerank_bonus
```

这样既保留 embedding 语义召回，又通过关键词和业务方向惩罚处理外卖客服里的相似意图混淆。

### 3. 证据组织型 Context Builder

不是简单搬运 TopK 文档，而是把 Top1 组织为“最相关参考资料”，其余作为“补充参考资料”。当 Top1 与补充资料接近时，会提示模型优先使用 primary evidence，减少串意图。

### 4. Grounding Evaluation

评测脚本会保存：

- `retrieved_items`
- `prompt_context_items`
- `final_prompt`
- `reply`
- `judge_status`
- `manual_judgment`
- `used_primary_evidence`
- `mixed_supporting_intent`
- `expected_intent`
- `missing_evidence_keywords`
- `forbidden_keyword_hits`

因此可以复盘每个坏案例到底坏在哪一层。

### 5. 分层诊断 suggested_layer

`scripts/analyze_grounding_report.py` 会根据报告字段输出建议修复层：

```text
retrieval
context_builder
generation_or_reply_rules
context_builder_or_reply_rules
reply_rules
judge
pass
```

这让调试从“凭感觉改 Prompt”变成“按链路定位问题”。

## 技术栈

- Python 3.11+
- FastAPI
- Pydantic
- Transformers
- PEFT / LoRA
- Qwen2.5-1.5B-Instruct
- BAAI/bge-small-zh-v1.5
- BAAI/bge-reranker-base
- FAISS
- JSONL knowledge base
- unittest

## 项目结构

```text
llm-customer-service/
├── main.py
├── config/
│   └── rag_config.py
├── routers/
│   ├── chat.py
│   ├── retrieval.py
│   ├── example.py
│   └── info.py
├── services/
│   ├── chat_service.py
│   ├── reply_rules.py
│   └── example_service.py
├── models/
│   └── prompt.py
├── schemas/
├── utils/
│   ├── vector_retriever.py
│   └── rag_context.py
├── scripts/
│   ├── evaluate_chat_grounding.py
│   ├── analyze_grounding_report.py
│   ├── evaluate_vector_retrieval.py
│   ├── analyze_retrieval_report.py
│   ├── debug_prompt.py
│   └── debug_answer_plan.py
├── data/
├── reports/
├── tests/
└── local_models/
```

## 本地启动

安装依赖：

```powershell
cd D:\llm\llm-customer-service
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

启动后端：

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

访问接口文档：

```text
http://127.0.0.1:8000/docs
```

## 本地模型

默认模型路径：

```text
local_models/qwen2.5-1.5b-instruct
```

LoRA adapter 检测路径：

```text
models/takeout-qwen-lora-minimal
```

如果 adapter 存在并包含 `adapter_config.json`，服务启动时会自动加载。

## 核心 API

### Chat

```http
POST /chat/prompt
```

请求示例：

```json
{
  "message": "退款多久到账"
}
```

响应包含：

```json
{
  "reply": "退款到账时间取决于支付渠道...",
  "confidence_score": 0.95,
  "retrieved_documents": [],
  "retrieved_items": [],
  "prompt_context_items": [],
  "final_prompt": "...",
  "trace": {
    "retrieval_count": 3,
    "answer_source": "rag",
    "reply_rules_applied": true
  }
}
```

### Retrieval Search

```http
POST /retrieval/search
```

用于调试 TopK 检索、hybrid score 和 rerank score。

### Prompt Preview

```http
POST /retrieval/prompt-preview
```

用于查看用户问题最终会被组装成什么 Prompt。

### Model Info

```http
GET /model/info
```

返回当前基础模型和 LoRA adapter 状态。

## 评测与诊断

生成 chat grounding 报告：

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_chat_grounding.py --use-local-judge --save-report
```

分析报告：

```powershell
.\venv\Scripts\python.exe -B scripts\analyze_grounding_report.py reports\chat_grounding\<report>.json --show-all
```

向量检索评测：

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py --save-report
```

运行单元测试：

```powershell
.\venv\Scripts\python.exe -B -m unittest discover tests
```

## 当前评测能力

当前 10 条 chat grounding 回归集已经加入：

- `expected_intent`
- `expected_evidence_keywords`
- `forbidden_keywords`
- `matched_evidence_keywords`
- `missing_evidence_keywords`
- `forbidden_keyword_hits`

这使评测不完全依赖 LLM-as-judge，也能做一部分确定性检查。

## 当前评测快照

最近一次 10 条 chat grounding 回归集主要结果：

```text
judge_status_counts:
  succeeded: 10
  failed: 0

judge_failure_type_counts:
  empty_response: 0
  not_json: 0
  missing_field: 0
  invalid_enum: 0
  empty_reason: 0
  other: 0

suggested_layer_counts:
  pass: 8
  judge: 2
```

这说明当前系统在固定回归集上已经具备比较稳定的 RAG 链路和 judge 输出格式。剩余问题主要集中在 judge 对“没有具体时间/金额但资料本身也未提供具体值”的评判偏严，而不是 retrieval 或 generation 链路失效。

示例分析输出：

```text
issue_type_counts: {'generation_not_grounded': 2}
suggested_layer_counts: {'pass': 8, 'judge': 2}
judge_failure_type_counts: {
  'empty_response': 0,
  'not_json': 0,
  'missing_field': 0,
  'invalid_enum': 0,
  'empty_reason': 0,
  'other': 0
}
```

## 前端调试台

项目配套前端位于：

```text
D:\llm\front
```

前端调试台用于观察 RAG 链路中的关键中间结果，适合在面试或项目展示时演示“系统为什么这样回答”：

- 展示 `/retrieval/search` 返回的 TopK 检索结果。
- 展示 `final / score / model / vector / bonus / penalty` 等分数拆解。
- 展示 `category`、`intent`、`question`、`answer`，用于判断 Top1 是否命中正确业务意图。
- 调用 `/retrieval/prompt-preview` 查看最终 prompt。
- 调用 `/chat/prompt` 查看最终回复、`retrieved_items`、`prompt_context_items`、`final_prompt` 和 `trace`。

推荐展示流程：

```text
1. 输入“退款多久到账”
2. 查看 Top1 是否为“退款进度”
3. 查看 final_prompt 中 primary evidence
4. 查看最终 reply 是否复述支付渠道、原路退回、可能延迟、订单详情页查看进度
5. 对照 grounding report 看该 case 的 expected_intent 和 missing_evidence_keywords
```

## 典型 Case

### 退款多久到账

系统会召回“退款进度”意图，并回复：

```text
退款到账时间取决于支付渠道。平台审核通过后通常会原路退回，
银行卡或部分第三方支付可能存在处理延迟。您可以在订单详情页查看退款进度。
```

### 骑手让我私下转配送费可以吗

系统会识别平台安全风险，并回复：

```text
不建议私下转账配送费。配送费应以平台订单结算页为准，
任何额外费用都应通过官方渠道确认和处理。
```

### 餐品有异物可以赔吗

系统会识别食品安全投诉，并回复：

```text
建议先停止食用，拍照保留异物、餐品和包装等凭证，
通过订单售后提交食品安全投诉，是否赔付以平台核实结果为准。
```

## 面试介绍版本

可以这样介绍本项目：

```text
我做了一个外卖客服场景的 RAG 智能问答系统。它不是简单调用大模型，
而是包含知识库构建、hybrid retrieval、bge reranker、证据组织、
本地 Qwen 生成、安全规则兜底和 grounding evaluation 的完整 AI 应用链路。

项目重点是可解释和可评测：接口会返回 retrieved_items、final_prompt 和 trace，
评测脚本会判断回复是否直接回答、是否基于证据，并通过 suggested_layer
定位问题应该改 retrieval、context builder、generation、reply rules 还是 judge。
```

## 后续规划

- 将 10 条 grounding 回归集扩展到 30-50 条。
- 增加 `intent_hit_rate`、`evidence_keyword_coverage`、`forbidden_hit_count` 等汇总指标。
- 将 `suggested_layer`、`missing_evidence_keywords` 展示到前端调试台。
- 优化知识库答案结构，使 answer 更适合直接进入生成。
- 尝试 answer planner：先生成结构化回答计划，再渲染成客服话术。
- 完善 Docker、配置文件、日志和部署说明。
## Current Snapshot: 2026-05-20

This project is now a learning-oriented Chinese takeout customer-service RAG backend. The current quality work focuses on grounded answer rendering, not just retrieval.

Current chat chain:

```text
user query
-> FastAPI /chat/prompt
-> hybrid retrieval + bge reranker
-> top1 primary evidence
-> local Qwen generation
-> answer_composer structured short answer
-> reply_rules high-risk fallback
-> reply + retrieved_items + final_prompt + trace
```

Latest grounding report:

```text
reports/chat_grounding/2026-05-20_22-48-35.json
```

Latest metrics:

```text
total_cases = 30
top1_intent_hit_rate = 1.0
judge_pass_count = 25/30
judge_pass_rate = 0.8333
evidence_keyword_coverage = 0.9344
forbidden_hit_count = 0
```

Recent learning conclusion:

- Retrieval is currently strong enough for the 30-case evaluation set.
- The main bottleneck is answer composition: directness, deduplication, and stable required steps.
- Online API generation was tested, but did not outperform the local model in this chain.
- Local model remains the default generator.

Recommended next task:

```text
Improve answer_composer:
1. remove duplicate / overlapping sentences
2. make first sentence more direct
3. strengthen required steps for remaining bad cases
4. rerun grounding evaluation
```
