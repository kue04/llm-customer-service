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

健康检查：

```text
http://127.0.0.1:8000/health
```

访问接口文档：

```text
http://127.0.0.1:8000/docs
```

环境变量配置参考 `.env.example`。默认走本地模型；如果要切到在线模型，将 `RAG_GENERATION_PROVIDER` 设为 `online` 并配置对应 API key。

## 落地闭环最小版

项目现在补充了面试落地版的业务闭环：

```text
用户提问
-> RAG 回复并生成 trace
-> 后端保存会话 request_id / query / reply / trace
-> 前端提交有帮助或没帮助反馈
-> 后端保存 bad case
-> 前端查看最近 bad case
-> 导出 eval case 草稿
```

新增接口：

```text
POST /feedback
GET /feedback/recent
POST /feedback/export-eval-case
GET /ops/metrics
```

默认 SQLite 存储位置：

```text
data/ops_feedback.db
```

轻量运维指标包括请求量、失败数、平均延迟、P95 延迟、检索为空次数、reply rules 命中次数和 fallback 次数。日志会对手机号、验证码和疑似订单号做基础脱敏。当前不包含登录权限、真实订单系统、灰度发布和生产 SLA，这些属于生产上线版能力。

## 知识库管理 v1

新增知识库运营入口，采用“草稿审核 + 手动发布”模式：

```text
新增/修改/下架/审核知识
-> 保存到 SQLite
-> 审核通过后仍不自动影响 RAG
-> 手动发布 approved 知识
-> 备份正式 JSONL
-> 合并到 data/takeout_customer_service_seed.jsonl
-> 重建 FAISS
-> 记录发布历史，可回滚最近发布
```

新增接口：

```text
GET /knowledge/items
POST /knowledge/items
PUT /knowledge/items/{id}
POST /knowledge/items/{id}/archive
POST /knowledge/items/{id}/review
GET /knowledge/export-approved
POST /knowledge/publish-approved
GET /knowledge/publish-history
POST /knowledge/rollback-latest
```

默认 SQLite 存储位置：

```text
data/knowledge_ops.db
```

`approved` 条目可以继续导出为 JSONL 草稿，也可以手动发布到正式知识库。发布会修改正式 JSONL 并重建 FAISS；发布前会备份到 `data/knowledge_backups/`，发布成功后条目状态变为 `published`，避免重复合并。前端已轻拆为业务模块：客服/RAG 位于 `D:\llm\front\src\features\support`，知识运营位于 `D:\llm\front\src\features\knowledge`。

Docker 本地冒烟：

```powershell
docker compose up --build
```

容器不会打包 `local_models/` 和 LoRA 权重，运行时通过 volume 挂载本地目录。

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

生成 blind eval 报告：

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_chat_grounding.py --blind --use-local-judge --save-report
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

chat grounding 回归集和 blind eval 都会记录：

- `expected_intent`
- `expected_evidence_keywords`
- `forbidden_keywords`
- `matched_evidence_keywords`
- `missing_evidence_keywords`
- `forbidden_keyword_hits`
- `retrieved_items`
- `prompt_context_items`
- `final_prompt`
- `trace`

这使评测不完全依赖 LLM-as-judge，也能做一部分确定性检查。

## 当前评测快照

固定 90 条 grounding 集已经收敛：

```text
report = reports/chat_grounding/2026-06-01_23-22-29.json
total_cases = 90
top1_intent_hit_rate = 0.9667
judge_pass_count = 90/90
judge_pass_rate = 1.0
evidence_keyword_coverage = 0.9475
forbidden_hit_count = 0
```

为了避免只刷固定集，新增了 30 条 blind eval，覆盖口语、错别字、强情绪、多意图和高风险诱导。第一次真实链路盲测结果：

```text
report = reports/chat_grounding/2026-06-02_01-04-51.json
total_cases = 30
top1_intent_hit_count = 18/30
top1_intent_hit_rate = 0.6
judge_pass_count = 13/30
judge_pass_rate = 0.4333
evidence_keyword_coverage = 0.6667
forbidden_hit_count = 0

suggested_layer_counts:
  pass: 13
  generation_or_reply_rules: 11
  context_builder_or_reply_rules: 1
  judge: 5
```

结论：固定集已经稳定，但盲测暴露了真实泛化缺口。主要问题不是模型输出格式，而是口语/错别字下的意图召回、answer_composer 的固定措辞、部分安全边界回复不够直接，以及少量 judge/标注口径偏严。

示例分析输出：

```text
failure_attribution_counts:
  pass: 13
  retrieval_failure: 9
  generation_not_using_evidence: 7
  evidence_insufficient: 1

generation_sub_attribution_counts:
  reply_not_direct_enough: 4
  reply_missing_required_step: 2
  evidence_wording_mismatch: 1
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

- 基于 blind eval 分层修复，不再继续只刷当前 90 条固定评估集。
- 优先修复 9 个 retrieval/query rewrite/intent hint 问题。
- 然后修复 answer_composer 的固定措辞和必要步骤缺失。
- 校准少量 judge/标注口径，避免把正确安全回复误判为失败。
- 将 `suggested_layer`、`missing_evidence_keywords` 展示到前端调试台。
- 增加结构化日志、请求 trace id、限流和基础鉴权。
- 补充生产环境部署说明和模型/索引版本管理。

## 企业级能力现状

当前项目已具备面试作品级企业雏形：

- 可观测：`/chat/prompt` 返回 `trace`，包含 `request_id`、`top1_intent`、`latency_ms`、降级状态和失败阶段。
- 可解释：接口返回检索证据、prompt context、final prompt 和 grounding 诊断字段。
- 可评测：支持固定 grounding 集、blind eval、bad case 分层分析。
- 可部署雏形：提供 `.env.example`、`/health`、Dockerfile 和 docker-compose。

轻量观测字段说明：

```text
request_id: 单次请求追踪 ID
top1_intent: 当前 Top1 检索意图
latency_ms: /chat/prompt 端到端处理耗时
degraded: 是否发生降级
failure_stage: none / retrieval / generation / answer_composer / reply_rules
answer_source: rag / fallback
```

## Current Snapshot: 2026-06-02

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

Latest fixed grounding report:

```text
reports/chat_grounding/2026-06-01_23-22-29.json
```

Fixed-set metrics:

```text
total_cases = 90
top1_intent_hit_rate = 0.9667
judge_pass_count = 90/90
judge_pass_rate = 1.0
evidence_keyword_coverage = 0.9475
forbidden_hit_count = 0
```

Latest blind eval report:

```text
reports/chat_grounding/2026-06-02_01-04-51.json
```

Blind metrics:

```text
total_cases = 30
top1_intent_hit_rate = 0.6
judge_pass_count = 13/30
judge_pass_rate = 0.4333
forbidden_hit_count = 0
```

Recent learning conclusion:

- 当前固定 90 条 grounding 集已经收敛，但这不代表真实泛化完美。
- blind eval 已经跑过真实链路，暴露出检索泛化、口语错别字、模板硬编码和 judge 口径问题。
- `forbidden_hit_count = 0` 说明目前没有生成明显禁用承诺。
- 下一步应该先按 blind eval 归因修复 retrieval/intent hint，再处理 answer_composer 和 judge。

Engineering notes:

```text
- Runtime config can be overridden through environment variables. See .env.example.
- /health is a lightweight readiness endpoint and does not require model generation.
- Dockerfile and docker-compose.yml are provided for local container smoke runs.
- /chat/prompt and /model/info load chat_service lazily to avoid model loading on app import.
```
