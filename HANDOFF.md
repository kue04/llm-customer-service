# LLM Customer Service Project Handoff

## 2026-06-07 Current Handoff Update: Knowledge Publish Loop + Frontend Light Split

### Completed

Knowledge ops now supports manual activation after review:

```text
POST /knowledge/publish-approved
GET /knowledge/publish-history
POST /knowledge/rollback-latest
```

Publishing appends `approved` items into `data/takeout_customer_service_seed.jsonl`, creates a backup under `data/knowledge_backups/`, rebuilds FAISS through `save_real_vector_store()`, clears runtime vector caches, records publish history, and marks merged items as `published`. Rolling back restores the latest successful publish backup, rebuilds FAISS, records a rollback history row, and moves those item statuses back to `approved`.

Frontend has been lightly split:

```text
D:\llm\front\src\features\support\SupportView.tsx
D:\llm\front\src\features\knowledge\KnowledgeOpsView.tsx
D:\llm\front\src\components\EmptyState.tsx
D:\llm\front\src\components\Score.tsx
```

No React Router or global state library was introduced. Orders/storefront behavior remains in `App.tsx`.

### Notes

Review approval still does not auto-publish. After publishing real knowledge, run a small retrieval/chat smoke test before any fixed/blind eval.

## 2026-06-06 Current Handoff Update: Knowledge Ops V1

### Completed

Added a knowledge-ops draft/review layer:

```text
GET /knowledge/items
POST /knowledge/items
PUT /knowledge/items/{id}
POST /knowledge/items/{id}/archive
POST /knowledge/items/{id}/review
GET /knowledge/export-approved
```

Knowledge changes are stored in SQLite at `data/knowledge_ops.db`. Review approval alone does not modify `data/takeout_customer_service_seed.jsonl`, does not rebuild FAISS, and does not affect the current RAG retrieval chain. Manual publish is now available in the newer 2026-06-07 update above.

Frontend now has a lightweight “知识运营” view for creating drafts, editing into a new version, approving/rejecting, archiving, filtering, and copying approved JSONL.

## 2026-06-06 Current Handoff Update: Landing Loop MVP

### Completed

Added the interview-grade landing loop:

```text
/chat/prompt session trace
-> SQLite feedback storage
-> recent bad case API
-> eval case draft export
-> in-memory ops metrics
-> basic log masking
```

New endpoints:

```text
POST /feedback
GET /feedback/recent
POST /feedback/export-eval-case
GET /ops/metrics
```

Frontend support view can submit helpful/unhelpful feedback, show recent bad cases, copy eval case drafts, and display lightweight ops metrics.

### Explicitly Out Of Scope

Enterprise-system layer is intentionally not included in this pass: no login, RBAC, admin console, real order-system integration, grey release, alerting platform, or SLA disaster recovery.

## 2026-06-06 Current Handoff Update: Lightweight Enterprise Observability

### Completed

Added lightweight observability for interview-grade enterprise presentation:

```text
/chat/prompt trace
-> request_id
-> top1_intent
-> latency_ms
-> degraded / failure_stage / answer_source
```

Backend now logs one structured `chat_request` record per chat request using standard-library logging. The frontend diagnostics panel displays the new trace fields and copied debug reports include them through the existing trace JSON block.

### Recommended Next Step

Run the full fixed evaluation and blind eval outside this implementation pass, then update README/HANDOFF with the new post-fix metrics.

## 2026-06-02 Current Handoff Update: Blind Eval Reality Check

### What Changed

Added a blind grounding evaluation path so the project no longer only optimizes the fixed 90-case set:

```text
scripts/evaluate_chat_grounding.py --blind
-> data/chat_grounding_blind_cases.jsonl
-> real /chat RAG chain
-> local judge
-> saved report
-> analyze_grounding_report.py attribution
```

The blind set contains 30 cases with oral phrasing, typos, strong emotion, mixed intents, privacy/security requests, and unsafe refund/compensation guarantees.

### Latest Blind Eval Snapshot

Latest blind report:

```text
reports/chat_grounding/2026-06-02_01-04-51.json
```

Metrics:

```text
total_cases = 30
top1_intent_hit_count = 18/30
top1_intent_hit_rate = 0.6
judge_pass_count = 13/30
judge_pass_rate = 0.4333
evidence_keyword_coverage = 0.6667
forbidden_hit_count = 0
direct_answer yes = 13
grounded yes = 13
useful yes = 13
```

Analyzer attribution:

```text
failure_attribution_counts:
  pass: 13
  retrieval_failure: 9
  generation_not_using_evidence: 7
  evidence_insufficient: 1

suggested_layer_counts:
  pass: 13
  generation_or_reply_rules: 11
  context_builder_or_reply_rules: 1
  judge: 5
```

### Important Conclusion

The fixed 90-case set is still useful as a regression guard, but it is no longer a quality target:

```text
fixed set: 90/90
blind set: 13/30
```

This gap is healthy signal. It shows the system has learned the fixed set but still needs generalization work, especially around typo normalization, query rewrite / intent hints, answer_composer hard-coded wording, and judge calibration.

### Recommended Next Step

Do not tune all 17 blind failures directly. First classify and fix by layer:

1. Retrieval/query rewrite/intent hint: fix the 9 retrieval failures.
2. Answer composer/reply rules: remove brittle hard-coded wording and add missing required steps.
3. Knowledge evidence: add only the one clearly insufficient evidence case if needed.
4. Judge/label calibration: review the 5 cases where the reply appears safer or more correct than the judge result.

## 2026-06-01 Current Handoff Update: 90-Case Grounding Closure + Engineering Baseline

### Current Stage

The project has moved from a 30-case quality loop to a 90-case fixed grounding evaluation set:

```text
user query
-> hybrid retrieval + intent hint supplement
-> bge reranker
-> primary evidence context
-> local Qwen generation
-> answer_composer boundary-aware rendering
-> reply_rules high-risk fallback
-> grounding diagnostics / local judge / bad-case analysis
```

### Latest Evaluation Snapshot

Latest report:

```text
reports/chat_grounding/2026-06-01_23-22-29.json
```

Metrics:

```text
total_cases = 90
top1_intent_hit_count = 87/90
top1_intent_hit_rate = 0.9667
judge_pass_count = 90/90
judge_pass_rate = 1.0
evidence_keyword_coverage = 0.9475
forbidden_hit_count = 0
direct_answer yes = 90
grounded yes = 90
useful yes = 90
```

Important caveat:

```text
90/90 means the current fixed evaluation set has converged.
It does not prove real-world generalization.
The next quality step should be blind eval, not more tuning on this fixed set.
```

### Completed In This Round

1. Added intent hints for address-change follow-up, delay compensation, missing item, rider complaint, merchant-cancel refund progress, unsafe refund guarantees, merchant contact induction, and food-safety evidence gaps.
2. Increased intent hint supplement strength so known high-risk intents can enter Top1 when vector similarity alone under-ranks them.
3. Added direct boundary answers for unsupported guarantees:
   - full refund / refund amount promise
   - delay compensation promise
   - immediate reship promise
   - rider punishment / compensation promise
   - food safety payout without evidence
   - private merchant call / refund request
4. Kept `forbidden_hit_count = 0`.
5. Added environment-driven RAG config with `.env.example`.
6. Added `/health`.
7. Made `/chat/prompt` and `/model/info` import `chat_service` lazily, so app import and health checks do not immediately load the local model.
8. Added pinned direct dependencies, `Dockerfile`, `docker-compose.yml`, and `.dockerignore`.

### Recommended Next Step

Do not keep optimizing the current 90 cases. Create a blind evaluation set:

```text
20-30 new cases
-> include typos, oral phrasing, angry users, mixed intents, and high-risk induction
-> run once without code changes
-> only then decide whether retrieval, answer_composer, reply_rules, or judge needs work
```

## 2026-05-20 Current Handoff Update: Composer, Judge Calibration, Model A/B

### Current Stage

The project has moved from "RAG can retrieve the right intent" to "RAG answers must be grounded, direct, and reproducibly judged":

```text
user query
-> RAG retrieval
-> local model generation
-> answer_composer structured rendering from top1 evidence
-> reply_rules high-risk fallback
-> grounding diagnostics / judge / bad-case analysis
```

### Latest Evaluation Snapshot

Latest report:

```text
reports/chat_grounding/2026-05-20_22-48-35.json
```

Metrics:

```text
total_cases = 30
top1_intent_hit_rate = 1.0
judge_pass_count = 25/30
judge_pass_rate = 0.8333
evidence_keyword_coverage = 0.9344
forbidden_hit_count = 0
direct_answer yes = 25
grounded yes = 25
useful yes = 25
```

Compared with the earlier local report `2026-05-20_22-10-44.json`:

```text
judge_pass_rate: 0.7000 -> 0.8333
judge_pass_count: 21/30 -> 25/30
generation_not_grounded: 9 -> 3
evidence_keyword_coverage: 0.9016 -> 0.9344
```

### Completed In This Round

1. Upgraded `services/answer_composer.py` from a bad-reply replacer into a structured answer composer.
2. Composer now uses top1 evidence to assemble short answers from:
   - conclusion
   - action step
   - caveat / page-based limitation
3. Added required-step safeguards for intents such as cancellation, rider delay, address change, remarks not followed, privacy, and merchant phone.
4. Calibrated judge logic for clearly unreasonable strictness:
   - refund-time cases where evidence has no fixed numeric time
   - verification-code safety wording
   - private transfer safety wording
   - privacy-phone wording
5. Added merchant-phone knowledge evidence.
6. Tried online API generation as an A/B experiment, but current result was slightly worse than local generation.
7. Kept local Qwen generation as the default path.

### Current Conclusion

Retrieval is not the current bottleneck:

```text
top1_intent_hit_rate = 1.0
```

The next quality bottleneck is answer rendering:

```text
same evidence
-> more direct first sentence
-> no generic tail
-> no duplicate action sentence
-> stable required steps
```

### Remaining Bad Cases

The latest run still has 5 non-pass cases:

```text
我取消订单后为什么只退了一部分钱
骑手一直停在一个地方不动怎么办
我的地址写错了骑手已经到原地址了怎么办
备注了不要辣结果还是很辣怎么办
商家电话在哪里看
```

Main causes:

- Composer sometimes repeats overlapping sentences.
- Some replies are supported but the first sentence is not direct enough.
- Some required steps need stronger intent-specific wording.
- Merchant phone is partly a judge wording problem, but the answer can still be more explicit.

### Recommended Next Learning Step

Continue with answer composer refinement:

```text
top1 evidence
-> extract conclusion/action/caveat
-> deduplicate overlapping sentences
-> force direct first sentence for known intents
-> render fixed short answer
```

Do this before changing models again. The A/B result showed that a stronger model does not automatically improve grounding if the answer assembly layer still allows generic or duplicated wording.

## Project Overview

This is a learning-oriented LLM customer-service project for a takeout platform scenario.

The project currently has two local parts:

- Backend: `D:\llm\llm-customer-service`
- Frontend: `D:\llm\front`

Current system goal:

- Build a FastAPI customer-service backend.
- Use a local Qwen model plus LoRA adapter for generation.
- Use RAG retrieval to provide reference materials before generation.
- Provide a frontend RAG debugging workbench for observing retrieval quality.
- Grow the project into a resume-ready LLM application engineering portfolio project.

## Current Learning Stage

Current stage:

```text
RAG configuration + reproducible evaluation + grounding observability
```

## 2026-05-11 Current Handoff Update: Config Snapshots, Final Prompt Tracing, Prompt Template Tightening

### Current Stage

The project has moved from "RAG can run" to "RAG can be inspected and reproduced":

```text
retrieval
-> rerank
-> prompt preview
-> chat evidence
-> grounding report
-> rag_config snapshot
-> final_prompt tracing
-> prompt-template iteration
```

### Completed In This Round

1. Added centralized RAG config in `config/rag_config.py`.
2. Moved these parameters into config:
   - embedding model name
   - reranker model name
   - rerank weight
   - min vector score
   - FAISS store dir
   - reply rules enabled
3. Added `GET /retrieval/config` to expose current runtime RAG config.
4. `scripts/evaluate_chat_grounding.py` now saves `run_config.rag_config` into each grounding report.
5. `scripts/evaluate_vector_retrieval.py` now supports `--save-report` and writes JSON reports to:
   - `reports/retrieval_eval/`
6. Retrieval evaluation reports now include:
   - `rag_config`
   - `rerank_weight`
   - `summary`
   - per-case TopK `results`
7. Grounding reports now save `final_prompt`.
8. `services/chat_service.py:get_answer_from_rag()` now returns the real `final_prompt`.
9. `/chat/prompt` response model now includes `final_prompt`.
10. `models/prompt.py` was rewritten into a shorter, more task-oriented customer-service prompt template to reduce instruction-echo and empty/meta replies.

### Current Backend Chat Path

```text
POST /chat/prompt
-> routers/chat.py
-> services/chat_service.py:get_answer_from_rag(query)
-> utils/vector_retriever.py:retrieve_rag_items(query)
-> models/prompt.py:create_prompt(query, documents)
-> services/chat_service.py:generate_reply(prompt)
-> services/reply_rules.py:apply_reply_rules(...)
-> return reply + final_prompt + retrieved_documents + retrieved_items
```

### Current Evaluation Artifacts

Grounding report:

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_chat_grounding.py --use-local-judge --save-report
```

Saved under:

```text
reports/chat_grounding/
```

Each case now includes:

- `query`
- `retrieved_documents`
- `retrieved_items`
- `final_prompt`
- `reply`
- `manual_judgment`
- `raw_judge_response`
- `judge_status`
- `judge_error`

Retrieval evaluation report:

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py --save-report
```

Saved under:

```text
reports/retrieval_eval/
```

Top-level fields include:

- `run_id`
- `created_at`
- `script`
- `rerank_weight`
- `rag_config`
- `summary`
- `cases`

Each retrieval case includes:

- `query`
- `judgement`
- `top_intents`
- `error_type`
- `notes`
- `rerank_impact`
- `original_top_intent`
- `reranked_top_intent`
- `rerank_changed_count`
- `results` (TopK structured candidates)

### Current Important Files

- `config/rag_config.py`: single source of truth for RAG runtime defaults
- `utils/vector_retriever.py`: retrieval, rerank, FAISS persistence, config usage
- `services/chat_service.py`: chat orchestration, final prompt tracing, reply rules switch
- `models/prompt.py`: current compact customer-service RAG prompt template
- `routers/retrieval.py`: `/retrieval/search`, `/retrieval/prompt-preview`, `/retrieval/config`
- `scripts/evaluate_vector_retrieval.py`: retrieval evaluation + JSON report export
- `scripts/evaluate_chat_grounding.py`: grounding evaluation + final prompt + config snapshot
- `scripts/analyze_grounding_report.py`: grounding bad-case analysis

### Current Test Baseline

```powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -B -m unittest discover tests
```

Expected result:

```text
Ran 47 tests
OK
```

### What The User Is Learning Now

The current focus is no longer "add more RAG features quickly". It is:

```text
How to run enterprise-style LLM experiments with:
- controlled config
- saved evaluation artifacts
- prompt visibility
- bad-case analysis
- single-variable iteration
```

### Recommended Next Learning Step

Next recommended task:

```text
Add a retrieval-eval analysis script, similar to analyze_grounding_report.py
```

Goal:

- read `reports/retrieval_eval/*.json`
- print config snapshot and rerank weight
- print Top1 / Top3 / miss summary
- list bad cases
- show each bad case's TopK candidates and score breakdown

Why this is the right next step:

- grounding evaluation already has an analysis script
- retrieval evaluation can already save reports
- what is still missing is a clean way to read and compare those retrieval reports
- this closes the loop for retrieval-side experimentation

### Suggested Next Conversation Start

Use this prompt in a new window:

```text
请先阅读 D:\llm\llm-customer-service\HANDOFF.md、STUDY.md 和 docs\API_INTEGRATION.md，然后继续带我学习当前外卖客服 RAG 项目。

当前后端在 D:\llm\llm-customer-service，前端在 D:\llm\front。

当前项目已经完成：
1. keyword retrieval、vector retrieval、hybrid search、bge reranker、FAISS 持久化
2. /retrieval/search、/retrieval/prompt-preview、/retrieval/config
3. /chat/prompt 返回 reply、final_prompt、retrieved_documents、retrieved_items
4. grounding report 会保存 run_config.rag_config、final_prompt、reply、judge 结果
5. retrieval evaluation 支持 --save-report，并把 rag_config、rerank_weight、summary、每个 case 的 TopK results 落盘
6. 已有 analyze_grounding_report.py 用于 bad case 分析
7. 当前 prompt 模板已经收紧为更短、更任务化的客服模板

我这阶段的学习目标不是继续堆功能，而是学习企业 RAG 的实验方法：如何保存参数、读取报告、比较 bad case，并区分 retrieval、prompt、generation、judge 哪一层出了问题。

请继续下一步：带我实现 retrieval evaluation 的分析脚本。目标是新增一个类似 analyze_grounding_report.py 的脚本，读取 reports/retrieval_eval/*.json，输出配置快照、rerank_weight、Top1/Top3/miss 汇总，并列出 bad cases 及其 TopK 候选和分数拆解。先解释为什么 retrieval report 也需要独立分析脚本，再小步实现。
```

## 2026-05-09 当前交接更新：本地 LLM-as-Judge 初版跑通

### 当前学习阶段

当前已经从“RAG 检索是否命中”推进到“RAG 最终回答是否可评估”。重点是把一次真实客服回复拆成：

```text
真实 RAG 回复
-> build_grounding_report
-> build_judge_prompt
-> local_judge_provider
-> 本地 Qwen 输出 JSON 文本
-> parse_judge_response
-> apply_judge_result
-> 打印带 judge 结果的 report
```

### 本阶段已完成

1. `scripts/evaluate_chat_grounding.py` 支持 4 条固定评估问题，从真实 RAG 链路生成回复。
2. 新增 `build_judge_prompt(report)`，把用户问题、参考资料和客服回复整理成 judge prompt。
3. 新增 `local_judge_provider(prompt)`，复用本地 `services.chat_service.generate_reply()` 调用本地 Qwen 作为 judge。
4. 新增 `parse_judge_response(text)`，解析 judge 返回的 JSON。
5. 新增 `apply_judge_result(report, judge_result)`，把 judge 结果写回 `manual_judgment`。
6. CLI 支持 `--show-judge-prompt`、`--use-local-judge`、`--show-judge-response`。
7. 优化 judge prompt：`risk_notes` 只能指出客服回复里的问题，不能把参考资料里未被提到的内容当成风险。
8. 优化 judge prompt：用户问“可以吗/能不能”时，回复必须明确回答“可以/不建议/需要平台核实”，否则 `direct_answer` 应降级。
9. 新增非空校验：`parse_judge_response()` 会拒绝空的 `reason`，避免 judge 给出没有解释的评分。

### 当前验证命令

```powershell
.\venv\Scripts\python.exe -B -m unittest tests.test_evaluate_chat_grounding
.\venv\Scripts\python.exe -B -m unittest discover tests
```

当前结果：

```text
Ran 18 tests in tests.test_evaluate_chat_grounding
OK

Ran 30 tests in unittest discover tests
OK
```

### 当前重点理解

- `Qwen` 可以作为本地学习阶段的 judge，因为它成本低、链路可控、方便观察 prompt 和 JSON 输出。
- 评分不稳定是正常的。LLM-as-judge 本质上仍然是模型判断，不是绝对标准答案。
- judge prompt 决定评分边界，`parse_judge_response()` 决定输出格式底线。
- 提示词里的“必须填写 reason”只是软约束，代码里的非空校验才是硬约束。
- 当前 judge 还不能完全替代人工评估，它更像一个“初筛评分助手”。

### 推荐下一步

下一步建议做“judge 失败处理”，不要直接让脚本因为一次坏 JSON 或空 reason 崩掉。可以把失败写进 report，例如：

```text
raw_judge_response: 原始模型输出
judge_error: 解析失败原因
manual_judgment: 保持为空或标记待复核
```

这样可以学习真实评估系统里很重要的一点：模型输出不稳定时，系统应该可观测、可恢复，而不是直接中断。

### 新窗口继续提示词

```text
请先阅读 D:\llm\llm-customer-service\HANDOFF.md、STUDY.md 和 docs\API_INTEGRATION.md，然后继续带我学习当前外卖客服 RAG 项目。

当前后端在 D:\llm\llm-customer-service，前端在 D:\llm\front。

目前已经完成 keyword retrieval、vector retrieval、hybrid search、bge reranker、prompt preview、chat retrieved_documents、grounding report、build_judge_prompt、本地 Qwen LLM-as-judge、judge JSON 解析、judge 结果写回 report，以及 reason 非空校验。

请继续下一步：带我给 scripts/evaluate_chat_grounding.py 增加 judge 失败处理。目标是当本地 Qwen 输出坏 JSON、缺字段、非法 yes/partial/no 或空 reason 时，脚本不要直接崩溃，而是在 report 中保存 raw_judge_response 和 judge_error。请先解释为什么真实 LLM-as-judge 系统必须处理模型输出不稳定，再用 5-10 行的小步改动带我做。
```

The project has moved beyond keyword retrieval. The current focus is:

- Embedding and vector retrieval.
- Vector index caching.
- Hybrid scoring.
- Retrieval evaluation.
- Frontend/backend integration for RAG debugging.

## Current Backend Architecture

Main chat request path:

```text
POST /chat/prompt
-> routers/chat.py
-> services/chat_service.py:get_answer_from_rag(query)
-> utils/retriever.py:retrieve_documents(query)
-> models/prompt.py:create_prompt(query, documents)
-> services/chat_service.py:generate_reply(prompt)
-> local Qwen model generates final reply
```

Retrieval debugging path:

```text
POST /retrieval/search
-> routers/retrieval.py
-> utils/vector_retriever.py:retrieve_by_real_vector(...)
-> embedding model + cached vector index
-> vector/hybrid TopK results
```

Important backend files:

- `main.py`: FastAPI app entry, router registration, CORS config.
- `routers/chat.py`: chat endpoint.
- `routers/retrieval.py`: RAG retrieval debugging endpoint.
- `routers/example.py`: knowledge sample browsing endpoints.
- `routers/info.py`: model info endpoint.
- `schemas/retrieval_schema.py`: request/response schema for retrieval API.
- `services/chat_service.py`: RAG orchestration and local model generation.
- `models/prompt.py`: Chinese customer-service prompt template.
- `utils/retriever.py`: keyword retrieval, scoring, dedupe, query expansion.
- `utils/vector_retriever.py`: toy embedding, real embedding, vector index, hybrid retrieval.
- `scripts/evaluate_vector_retrieval.py`: vector retrieval evaluation script.
- `scripts/evaluate_retrieval.py`: keyword retrieval evaluation script.
- `docs/API_INTEGRATION.md`: frontend/backend API integration guide.
- `docs/FRONTEND_DESIGN.md`: frontend design guide.
- `STUDY.md`: detailed learning record.

## Current Frontend Architecture

Frontend path:

```text
D:\llm\front
```

Current frontend stack:

- Vite
- React
- TypeScript
- Tailwind CSS
- lucide-react

Current frontend role:

```text
RAG debugging workbench
```

Main frontend modules:

- `ModelInfoBar`: shows base model, adapter status, retrieval controls.
- `ChatPanel`: sends questions to `/chat/prompt` and displays model replies.
- `RetrievalPanel`: calls `/retrieval/search` and displays score breakdown.
- `KnowledgeBrowser`: browses categories and searches knowledge examples.

Important frontend files:

- `src/App.tsx`
- `src/types/api.ts`
- `src/api/client.ts`
- `src/api/retrieval.ts`
- `src/api/chat.ts`
- `src/api/examples.ts`
- `src/api/model.ts`
- `src/components/AppShell.tsx`
- `src/components/ModelInfoBar.tsx`
- `src/components/ChatPanel.tsx`
- `src/components/RetrievalPanel.tsx`
- `src/components/KnowledgeBrowser.tsx`
- `src/components/ScoreBadge.tsx`

Current frontend status:

- Frontend can call backend successfully.
- CORS issue was fixed in backend `main.py`.
- `/retrieval/search` returns correctly in browser.
- UI shows:
  - `score`
  - `vector_score`
  - `keyword_bonus`
  - `direction_penalty`
  - `category`
  - `intent`
  - `question`
  - `answer`
- The right-side knowledge browser now uses internal scrolling.
- Retrieval cards now highlight `intent`.

## Backend API Summary

### RAG Retrieval Debugging

```http
POST /retrieval/search
```

Request:

```json
{
  "query": "会员退款多久到账",
  "mode": "hybrid",
  "limit": 3,
  "min_score": 0.62
}
```

Supported modes:

- `vector`
- `hybrid`

Response fields:

- `rank`
- `score`
- `vector_score`
- `keyword_bonus`
- `direction_penalty`
- `category`
- `intent`
- `question`
- `answer`

Hybrid formula:

```text
score = vector_score + keyword_bonus - direction_penalty
```

### Chat

```http
POST /chat/prompt
```

Request:

```json
{
  "message": "会员退款多久到账"
}
```

Response:

```json
{
  "reply": "...",
  "confidence_score": 0.95
}
```

### Model Info

```http
GET /model/info
```

### Knowledge Samples

```http
GET /examples/categories
GET /examples/by-category?category=会员服务&limit=5
POST /examples/search
```

## Current Vector Retrieval Features

`utils/vector_retriever.py` currently includes:

- `build_document_text(item)`: builds embedding text from category, intent, question, answer.
- `load_vector_documents()`: loads JSONL knowledge data into vector-document format.
- `build_toy_embedding(text)`: toy embedding for learning vector concepts.
- `cosine_similarity(vector_a, vector_b)`: vector similarity calculation.
- `build_toy_vector_index()` and `get_toy_vector_index()`: toy index and cache.
- `get_embedding_model()`: loads and caches `BAAI/bge-small-zh-v1.5`.
- `build_embedding(text)`: creates real 512-dimensional embedding.
- `build_real_vector_index()` and `get_real_vector_index()`: real vector index and cache.
- `retrieve_by_real_vector(...)`: vector/hybrid retrieval.
- `calculate_keyword_bonus(query, source)`: lightweight business keyword correction.
- `calculate_direction_penalty(query, source)`: lightweight directionality penalty.
- Exact answer dedupe and similar answer dedupe using `is_similar_answer()` from `utils/retriever.py`.

## Current Evaluation

Vector evaluation script:

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py
```

Current result:

```text
总问题数：3
Top1 命中：2
Top3 召回但 Top1 错误：1
未命中：0

错误类型分布：
意图粒度相近：1
```

Meaning:

- Recall is acceptable for the small test set.
- Ranking still needs work.
- The remaining known issue is mainly intent granularity: for `外卖超时怎么办`, vector retrieval ranks cancellation-related results above delivery-delay/call-order results.

## Current Known Limitations

- Current vector store is a Python list with a for-loop similarity scan. This is fine for 500 examples, not for large production datasets.
- No FAISS, Chroma, or Milvus yet.
- `keyword_bonus` and `direction_penalty` are lightweight rules, not a general reranker.
- Evaluation set is still small.
- `/chat/prompt` does not return retrieval details. The frontend uses `/retrieval/search` separately for retrieval evidence.
- No `/retrieval/prompt-preview` API yet.
- Frontend and backend are in separate folders. This is currently preferable for learning because Python/model dependencies and Node/Vite dependencies stay separate.

## Current Startup Commands

Backend:

```powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Backend docs:

```text
http://127.0.0.1:8000/docs
```

Frontend:

```powershell
cd D:\llm\front
npm run dev
```

Frontend page:

```text
http://127.0.0.1:5173
```

Build frontend:

```powershell
cd D:\llm\front
npm run build
```

## Recently Completed Work

- Added vector retrieval API:
  - `routers/retrieval.py`
  - `schemas/retrieval_schema.py`
  - `main.py` router registration
- Added CORS for local frontend development in `main.py`.
- Added vector retrieval evaluation script.
- Added frontend API integration document.
- Added frontend design document.
- Updated `requirements.txt` with `sentence-transformers`.
- Updated frontend to match actual backend API:
  - removed unsupported `keyword` retrieval mode
  - removed unsupported `prompt-preview` call
  - fixed garbled Chinese UI text
  - added `direction_penalty` display
  - highlighted `intent` in retrieval cards
  - made knowledge panel internally scrollable

## Recommended Next Learning Topics

Suggested next stage:

```text
Frontend debugging workbench refinement + larger retrieval evaluation set
```

Recommended next tasks:

1. Expand `scripts/evaluate_vector_retrieval.py` from 3 cases to 8-12 cases:
   - 食品有异物
   - 优惠券不能用
   - 订单取消后退款
   - 配送超时
   - 骑手联系不上
   - 会员退款
   - 商家状态
   - 账号/隐私问题
2. Add evaluation summary metrics:
   - Top1 hit rate
   - Top3 recall-but-ranking-error rate
   - Miss rate
   - Error type distribution
3. Add frontend evaluation view or a backend `/retrieval/evaluate` endpoint.
4. Add prompt preview API:
   - `POST /retrieval/prompt-preview`
   - return retrieved documents and final prompt.
5. Learn rerank:
   - why vector retrieval is not enough
   - how rerank differs from keyword bonus
   - when to use cross-encoder reranker or LLM reranker
6. Later learn FAISS or Chroma:
   - persist vector index
   - avoid recomputing all embeddings
   - support larger knowledge bases

## Suggested Next Conversation Start

Use this prompt in a new window:

```text
请阅读 HANDOFF.md 和 STUDY.md，继续带我学习下一阶段。当前项目已经完成向量检索 RAG、hybrid search、/retrieval/search API 和 React 前端调试台。请先帮我扩展向量检索评估集，从 3 条扩展到 8-12 条，并解释每个 case 的 expected_intents、error_type 和 notes 应该怎么设计。
```

## 2026-05-10 当前交接更新：FAISS 持久化、Grounding 可观测性与 Bad Case 收口

### 当前阶段

当前项目已经从“检索调试”推进到：

```text
FAISS 持久化向量库
-> chat grounding 自动评估
-> retrieval metadata 可观测性
-> bad case 分类
-> 高频问题业务规则兜底
```

### 本阶段已完成

1. `utils/vector_retriever.py` 已接入 FAISS `IndexFlatIP`，并将向量索引持久化到：
   - `data/faiss_store/real_vector.index`
   - `data/faiss_store/real_vector_docs.json`
2. FAISS 持久化增加兼容性检查：
   - 索引数量和 docs 数量不一致会重建。
   - 向量维度不一致会重建。
   - 当前知识库内容和磁盘 docs 不一致会重建。
3. `/chat/prompt` 现在返回：
   - `reply`
   - `confidence_score`
   - `retrieved_documents`
   - `retrieved_items`
4. `retrieved_items` 包含真实检索 metadata：
   - `rank`
   - `category`
   - `intent`
   - `question`
   - `score`
   - `vector_score`
   - `rerank_score`
   - `model_rerank_score`
   - `keyword_bonus`
   - `direction_penalty`
5. `scripts/evaluate_chat_grounding.py` 已扩展为 10 条固定评估问题，并保存 `retrieved_items`。
6. 新增 `scripts/analyze_grounding_report.py`，默认只展示 bad case。
7. 分析脚本会优先读取真实 `retrieved_items`，旧报告没有 metadata 时才 fallback 到关键词猜测。
8. 新增 `services/reply_rules.py`，对高频高风险场景做最小业务规则后处理：
   - 私下转账：必须包含“不建议私下转账 / 平台订单结算页 / 官方渠道”。
   - 优惠券不可用：必须包含“使用门槛 / 有效期 / 适用品类 / 适用商家 / 支付方式”。
   - 骑手联系不上：必须包含“订单详情页 / 配送异常 / 未收到餐反馈”。
   - 餐洒申请售后：必须包含“订单详情页 / 餐品问题 / 包装破损 / 撒漏 / 照片凭证”。
9. 知识库新增明确 QA：
   - `餐洒了怎么申请售后？`
   - `外卖汤洒了，包装也破了，我要怎么申请售后？`
10. `models/prompt.py` 已补充更明确的回答要求，减少只说“根据页面提示操作”的泛化回复。

### 当前关键文件

- `utils/vector_retriever.py`：FAISS 持久化、向量检索、hybrid 分数、rerank、retrieved metadata。
- `services/chat_service.py`：RAG 编排、生成回复、应用业务规则后处理。
- `services/reply_rules.py`：高频意图的回答骨架兜底。
- `models/prompt.py`：RAG 生成 prompt。
- `scripts/evaluate_chat_grounding.py`：生成 grounding 报告和本地 judge 评分。
- `scripts/analyze_grounding_report.py`：分析 bad case，输出检索 metadata 和 judge reason。
- `data/takeout_customer_service_seed.jsonl`：知识库。

### 当前验证命令

运行后端测试：

```powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -B -m unittest discover tests
```

当前预期：

```text
Ran 41 tests
OK
```

运行 chat grounding：

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_chat_grounding.py --use-local-judge --save-report
```

分析 bad case：

```powershell
.\venv\Scripts\python.exe -B scripts\analyze_grounding_report.py reports\chat_grounding\新报告.json
```

查看全部 case：

```powershell
.\venv\Scripts\python.exe -B scripts\analyze_grounding_report.py reports\chat_grounding\新报告.json --show-all
```

运行向量检索评估：

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py
```

### 当前重要理解

- FAISS 不是保存“用户问题和答案缓存”，而是保存知识库文本的向量索引。
- 用户 query 不会提前建索引；运行时只对 query 做一次 embedding，然后去 FAISS 中搜索相似知识库向量。
- FAISS 主要加速向量近邻搜索，不负责保证检索质量。
- 当前数据量只有 500 条，速度瓶颈主要不是 FAISS，而是 embedding 模型、reranker、Qwen 生成和本地 judge。
- RAG 效果排查应按层定位：

```text
知识库是否覆盖
-> 检索是否命中
-> rerank 是否排序正确
-> prompt 是否约束到位
-> 生成是否利用资料
-> judge 是否评分合理
```

### 推荐下一步学习

建议下一阶段学习：

```text
RAG 生产化 API 与配置化
```

优先做两件事：

1. 配置化检索和模型参数：
   - FAISS 路径
   - embedding 模型名
   - reranker 模型名
   - rerank weight
   - min_score
   - 是否启用业务规则后处理
2. 增加 `/retrieval/config` 或 `/debug/config`：
   - 返回当前 RAG 配置。
   - 让前端和报告能记录“这次评估使用了什么参数”。

这一步比继续调 prompt 更适合学习企业开发，因为它把当前实验型代码推进到“可复现、可配置、可排查”的工程状态。

### 新窗口继续提示词

```text
请先阅读 D:\llm\llm-customer-service\HANDOFF.md、STUDY.md 和 docs\API_INTEGRATION.md，然后继续带我学习当前外卖客服 RAG 项目。

当前后端在 D:\llm\llm-customer-service，前端在 D:\llm\front。

目前已经完成：keyword retrieval、vector retrieval、hybrid search、bge reranker、FAISS 持久化、prompt preview、chat retrieved_documents、chat retrieved_items、grounding report、本地 Qwen LLM-as-judge、judge 失败处理、bad case 分析脚本、retrieval metadata 可观测性，以及高频问题业务规则后处理。

请继续下一步：带我学习 RAG 配置化和可复现实验。目标是把 embedding 模型、reranker 模型、rerank weight、min_score、FAISS 存储路径、是否启用 reply rules 等参数集中到一个配置模块，并增加一个调试接口或脚本输出当前配置。请先解释为什么企业 RAG 系统必须记录配置和实验参数，再小步实现。
```
## 2026-05-06 Current Handoff Update

### Current Stage

Current stage:

```text
Vector RAG evaluation + bge reranker calibration + frontend retrieval debugging
```

The project has completed the first serious rerank learning loop:

```text
vector recall
-> hybrid score
-> bge-reranker model score
-> weighted rerank_score
-> API response
-> frontend debug panel
-> offline evaluation comparison
```

### Current Retrieval Score Fields

The retrieval debug API and frontend now use these fields:

- `vector_score`: embedding similarity from `BAAI/bge-small-zh-v1.5`.
- `keyword_bonus`: lightweight business keyword correction.
- `direction_penalty`: business-direction penalty for cases such as timeout vs timeout-cancel.
- `score`: pre-rerank vector/hybrid score.
- `model_rerank_score`: `BAAI/bge-reranker-base` cross-encoder score for query-candidate relevance.
- `rerank_score`: final sorting score.

Current formula:

```text
rerank_score = score + model_rerank_score * model_rerank_weight + rule_rerank_bonus
```

Current default:

```text
DEFAULT_MODEL_RERANK_WEIGHT = 0.01
```

### Current Evaluation Commands

Single detailed run:

```powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py --rerank-weight 0.01
```

Multi-weight comparison:

```powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -B scripts\evaluate_vector_retrieval.py --compare-rerank-weights 0.01 0.03 0.05
```

Current comparison result:

```text
weight  Top1  Top3Error  Miss  ChangedTop1  Improved  Worsened
0.01    12    0          0     0            0         0
0.03    11    1          0     1            0         1
0.05    11    1          0     1            0         1
```

Interpretation:

- `0.01` is currently the best default for the 12-case evaluation set.
- `0.03` and `0.05` both worsen the same refund/cancellation case.
- The risky case is `取消订单后钱多久退回来`.
- The model gives `支付超时取消` a higher semantic relevance score than `退款进度`, but business intent should prefer `退款进度`.

Key lesson:

```text
model semantic relevance is not always the same as business intent correctness
```

### Current Tests

Run backend tests:

```powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -B -m unittest discover tests
```

Current expected result:

```text
Ran 8 tests
OK
```

### Current Frontend State

Frontend path:

```text
D:\llm\front
```

The retrieval panel now displays:

- `final`: final `rerank_score`
- `score`: pre-rerank score
- `model`: `model_rerank_score`
- `vector`
- `bonus`
- `penalty`

Build command:

```powershell
cd D:\llm\front
npm run build
```

### Learning Style Requirement

Important user preference:

```text
Do not silently implement a full patch first.
Explain the next function or change before editing.
Let the user write small pieces when feasible.
After each step, explain the output and why it matters for RAG/LLM application engineering.
```

The user explicitly wants to learn LLM application development through this repo, not just receive completed code.

### Recommended Next Step

Suggested next stage:

```text
Prompt preview API for RAG
```

Why:

- Retrieval and rerank observability are now good enough for the current learning stage.
- The next missing visibility point is how retrieved documents become the final prompt.
- This connects retrieval quality to model-generation behavior.

Recommended next task:

```text
Add POST /retrieval/prompt-preview
```

It should return:

- user query
- retrieved documents
- final prompt text from `models/prompt.py:create_prompt`
- score fields for each retrieved document

Keep it small:

1. First inspect `models/prompt.py:create_prompt`.
2. Explain its inputs and output.
3. Add a schema.
4. Add router function.
5. Verify with one API call.

### Suggested Next Conversation Start

Use this prompt in a new window:

```text
请阅读 D:\llm\llm-customer-service\HANDOFF.md、STUDY.md 和 docs\API_INTEGRATION.md，然后继续带我学习当前外卖客服 RAG 项目。

当前后端在 D:\llm\llm-customer-service，前端在 D:\llm\front。

目前已经完成：
1. keyword retrieval baseline
2. vector retrieval with BAAI/bge-small-zh-v1.5
3. hybrid search：score = vector_score + keyword_bonus - direction_penalty
4. 12 条向量检索评估集
5. bge-reranker-base 接入
6. 当前默认 rerank 公式：rerank_score = score + model_rerank_score * 0.01 + rule_rerank_bonus
7. /retrieval/search 已正式返回 rerank_score 和 model_rerank_score
8. 前端调试台已显示 final / score / model / vector / bonus / penalty
9. evaluate_vector_retrieval.py 支持 --rerank-weight 和 --compare-rerank-weights
10. 当前多权重评估结论：0.01 是默认最稳权重；0.03/0.05 会带偏“取消订单后钱多久退回来”这个 case

请继续下一步：带我学习并实现 RAG prompt preview。先不要直接写完整代码，先解释 models/prompt.py:create_prompt 的输入、输出、调用位置，以及为什么 prompt preview 对 RAG 调试重要。继续用小步学习方式，让我参与 5-10 行的小改动。
```
## 2026-05-07 Current Handoff Update

### Prompt Preview API 已完成

后端现在已经包含：

```http
POST /retrieval/prompt-preview
```

请求结构：

- 复用 `RetrievalSearchRequest`。
- 字段包括：`query`、`mode`、`limit`、`min_score`。

响应结构：

- `PromptPreviewResponse`
- 字段包括：`query`、`mode`、`count`、`prompt`、`results`。
- `results` 使用和 `/retrieval/search` 相同的 `RetrievalResultItem` 结构。

当前数据流：

```text
query
-> retrieve_by_real_vector(...)
-> candidates
-> build_retrieval_result_item(rank, item)
-> results
-> documents 来自候选结果中的 answer
-> create_prompt(query, documents)
-> prompt preview 响应
```

### 已新增测试

`tests/test_retrieval_api.py` 现在覆盖：

- `/retrieval/search` 的 rerank 调试字段。
- 使用 `patch` 后直接调用 `preview_prompt(request)` 的行为。
- 通过 `TestClient` 验证 HTTP 层的 `/retrieval/prompt-preview` 行为。

已验证命令：

```powershell
.\venv\Scripts\python.exe -B -m unittest tests.test_retrieval_api
.\venv\Scripts\python.exe -B -m unittest discover tests
```

预期结果：

```text
Ran 10 tests
OK
```

### 学习记录

你现在已经理解：

- 后端内部 `candidates` 和对外 API `results` 的区别。
- 一个 `RetrievalResultItem` 和 `list[RetrievalResultItem]` 的区别。
- 为什么要在 `build_retrieval_result_item()` 中提取嵌套的 `source` 字段。
- 为什么单元测试中要用 `patch()` 避免加载真实 embedding/reranker 模型。
- 直接函数测试和 FastAPI `TestClient` HTTP 接口测试的区别。

### 推荐下一步

把 `/retrieval/prompt-preview` 接到 `D:\llm\front` 的前端调试台中。

建议的下一步学习顺序：

1. 查看前端 API client 文件。
2. 为 prompt preview 响应新增 TypeScript 类型。
3. 新增调用 `POST /retrieval/prompt-preview` 的前端 API 函数。
4. 在检索调试结果附近展示接口返回的 `prompt`。
## 2026-05-08 当前交接更新：RAG evidence 与 grounding 初步评估

### 当前项目位置

- 后端路径：`D:\llm\llm-customer-service`
- 前端路径：`D:\llm\front`
- 当前学习阶段：已经从检索调试、rerank、prompt preview，推进到“最终回答是否可追溯、是否被参考资料支撑”。

### 当前已经完成

1. keyword retrieval baseline。
2. vector retrieval with `BAAI/bge-small-zh-v1.5`。
3. hybrid search：`score = vector_score + keyword_bonus - direction_penalty`。
4. 12 条向量检索评估集。
5. `BAAI/bge-reranker-base` 接入。
6. 默认 rerank 公式：`rerank_score = score + model_rerank_score * 0.01 + rule_rerank_bonus`。
7. `/retrieval/search` 返回 `rerank_score`、`model_rerank_score` 和分数拆解字段。
8. 前端调试台显示 final、score、model、vector、bonus、penalty。
9. `/retrieval/prompt-preview` 返回最终 prompt 和进入 prompt 的检索结果。
10. 前端调试台已经接入 prompt preview，并用结构化方式展示最终提示词。
11. `/chat/prompt` 返回 `reply`、`confidence_score`、`retrieved_documents`。
12. 前端客服回复下方可以显示“本次参考资料”。
13. 新增 `scripts/evaluate_chat_grounding.py`，开始学习回答可追溯和 grounding 初步评估。

### 关键代码位置

- 检索调试接口：`routers/retrieval.py`
- 聊天生成接口：`routers/chat.py`
- RAG 生成服务：`services/chat_service.py`
- 向量检索工具：`utils/vector_retriever.py`
- prompt 拼装函数：`models/prompt.py`
- grounding 学习脚本：`scripts/evaluate_chat_grounding.py`
- 检索接口测试：`tests/test_retrieval_api.py`
- 聊天接口测试：`tests/test_chat_api.py`
- grounding 脚本测试：`tests/test_evaluate_chat_grounding.py`

### 当前要重点掌握

- `candidates` 是检索器内部候选结果。
- `results` 是整理后返回给前端的结构化检索结果。
- `documents` 是进入 prompt 的参考资料文本。
- `retrieved_documents` 是聊天接口返回给前端的本次 RAG 证据。
- prompt preview 用来观察“检索资料如何变成模型输入”。
- RAG evidence 用来观察“最终回答参考了哪些资料”。
- grounding 用来判断“回答是否被资料支撑”。
- 当前 grounding 脚本只是规则版学习工具，不是最终自动评测系统。

### 已验证命令

后端测试：

```powershell
.\venv\Scripts\python.exe -B -m unittest discover tests
```

当前结果：

```text
Ran 15 tests
OK
```

前端构建在 RAG evidence 展示阶段已通过：

```powershell
npm run build
```

### 推荐下一步

继续学习 `scripts/evaluate_chat_grounding.py`，把它从单条固定样例升级成 3 到 5 条固定评估集。目标不是立刻做复杂自动评分，而是先学会把一次 RAG 回答拆成：

1. 用户问题。
2. 检索证据。
3. 模型回复。
4. 高风险承诺词。
5. 是否需要人工复核。

### 新窗口继续提示词

```text
请先阅读 D:\llm\llm-customer-service\HANDOFF.md、STUDY.md 和 docs\API_INTEGRATION.md，然后继续带我学习当前外卖客服 RAG 项目。

当前后端在 D:\llm\llm-customer-service，前端在 D:\llm\front。

目前已经完成：
1. keyword retrieval baseline
2. vector retrieval with BAAI/bge-small-zh-v1.5
3. hybrid search：score = vector_score + keyword_bonus - direction_penalty
4. 12 条向量检索评估集
5. bge-reranker-base 接入
6. 默认 rerank 公式：rerank_score = score + model_rerank_score * 0.01 + rule_rerank_bonus
7. /retrieval/search 返回 rerank_score、model_rerank_score 和分数拆解字段
8. 前端调试台显示 final / score / model / vector / bonus / penalty
9. /retrieval/prompt-preview 已接入前后端，可以显示最终 prompt
10. /chat/prompt 已返回 retrieved_documents，前端客服回复下方能显示本次参考资料
11. scripts/evaluate_chat_grounding.py 已完成第一版单样例 grounding report

请继续下一步：带我把 scripts/evaluate_chat_grounding.py 从单条固定样例升级为 3 到 5 条固定评估集。先解释为什么 RAG 评估要区分 retrieval relevance、groundedness 和 usefulness，再用小步方式让我参与 5-10 行的小改动。不要一次性写完整复杂系统。
```
## 2026-05-13 当前交接更新：Retrieval 对比、Answer Plan 学习链路、Chat Trace/降级

### 当前阶段

项目已经从“RAG 可以被观察”推进到“RAG 可以被拆成 retrieval / plan / generation / degrade 分层”：

```text
retrieval report
-> grounding report
-> issue typing
-> structured answer plan
-> debug render vs user render
-> chat trace
-> degrade strategy
```

### 本轮已完成

1. 新增 `scripts/analyze_retrieval_report.py`。
2. retrieval report 分析现已支持：
   - 单报告摘要
   - `ranking_error_count`
   - `miss_count`
   - bad case 的 TopK 分数拆解
3. retrieval report 分析现已支持 `--compare-to`：
   - base/target 摘要
   - 配置 diff
   - improved / worsened / unchanged case 对比
4. 在 `scripts/analyze_grounding_report.py` 中新增 grounding bad case 类型划分：
   - `retrieval_bad`
   - `generation_not_direct`
   - `generation_not_grounded`
   - `safety_overclaim`
   - `judge_or_other`
5. 新增 `scripts/debug_answer_plan.py`，作为结构化中间状态学习链路。
6. 将 planner 生成与正常 reply 生成拆开：
   - `generate_reply(prompt)`
   - `generate_answer_plan(prompt)`
7. 将 answer plan 的 schema 重构为更偏中间层的结构：
   - `user_intent`
   - `answer_type`
   - `direct_answer_brief`
   - `key_evidence`
   - `action_suggestion`
   - `needs_caution`
   - `caution_reason`
8. 为 answer plan JSON 增加 parser / validator / normalizer / repair 流程。
9. 将 answer plan 的渲染拆成：
   - `render_plan_debug(plan)`
   - `render_user_reply(plan)`
10. `/chat/prompt` 现在会返回轻量 `trace`：
    - `retrieval_count`
    - `used_fallback_prompt`
    - `reply_rules_applied`
    - `answer_source`
    - `degraded`
    - `failure_stage`
    - `fallback_reason`
11. 在 `services/chat_service.py` 中加入降级策略：
    - retrieval failure -> fallback prompt
    - generation failure -> 固定安全兜底回复
    - reply-rules failure -> 保留原始模型回复

### 这一阶段教会了什么

这一轮新增了两个重要认识：

```text
JSON 既可以是：
- 评估产物
- 运行时中间状态
```

以及：

```text
服务链路不能只建模成 success-or-crash。
它应该支持：
- trace
- degrade
- fallback reason
```

### 当前关键文件

- `scripts/analyze_retrieval_report.py`
- `scripts/analyze_grounding_report.py`
- `scripts/debug_answer_plan.py`
- `services/chat_service.py`
- `schemas/chat_schema.py`
- `tests/test_analyze_retrieval_report.py`
- `tests/test_analyze_grounding_report.py`
- `tests/test_debug_answer_plan.py`
- `tests/test_chat_service_degrade.py`

### 当前推荐调试顺序

在线 `/chat/prompt` 请求：

```text
trace
-> retrieved_items
-> final_prompt
-> reply
```

结构化 answer-plan 学习链路：

```text
retrieval
-> raw answer_plan JSON
-> parsed/normalized plan
-> debug render
-> user render
```

### 推荐下一学习阶段

下一阶段建议进入：

```text
把项目分层成：
- formal service capability
- debug capability
- offline evaluation capability
- learning-script capability
```

## 2026-05-19 新窗口交接记录

### 当前状态

项目路径：`D:\llm\llm-customer-service`

最近两天完成的主线是：把外卖客服 RAG 从“能回答”推进到“能评估、能归因、能知道该改哪一层”。

已完成：

1. 30 条 grounding 专业 case 评估集已建立。
2. `scripts/analyze_grounding_report.py` 已输出 `issue_type_counts`、`suggested_layer_counts`、`failure_attribution_counts`、`failure_attribution_table`。
3. 三类失败归因已实现：`retrieval_failure`、`evidence_insufficient`、`generation_not_using_evidence`。
4. forbidden 误伤已修复：否定语境如“不支持直接取消”不会再算违规。
5. 少量知识库证据已补强：验证码、地址写错且骑手到原地址、支付超时退款。
6. 最新报告为 `reports/chat_grounding/2026-05-19_21-27-59.json`。

最新指标：

```text
top1_intent_hit_rate = 1.0
evidence_keyword_coverage = 0.9508
forbidden_hit_count = 0
judge_pass_rate = 0.7667
failure_attribution_counts = {'pass': 23, 'generation_not_using_evidence': 7}
```

### 重要判断

当前不要优先改 retrieval，因为 Top1 intent 已经 100%。

当前不要继续堆 reply rules，因为规则堆叠会变成维护负担。

当前不要大规模补知识库，因为归因表没有显示 `evidence_insufficient`。

下一步应该分析剩余 7 条 `generation_not_using_evidence`，再细分：

```text
judge_too_strict
reply_not_direct_enough
reply_missing_required_step
evidence_wording_mismatch
```

### 建议下一步命令

```powershell
.\venv\Scripts\python.exe -B scripts\analyze_grounding_report.py reports\chat_grounding\2026-05-19_21-27-59.json --show-all
```

重点看这 7 条：

- 订单取消后钱多久退回来
- 骑手已经取餐了我还能取消吗
- 骑手一直停在一个地方不动怎么办
- 我的地址写错了骑手已经到原地址了怎么办
- 商家电话在哪里看
- 骑手让我私下转配送费可以吗
- 骑手让我发验证码给他可以吗

### 测试记录

已跑过：

```powershell
.\venv\Scripts\python.exe -B -m unittest tests.test_analyze_grounding_report
.\venv\Scripts\python.exe -B -m unittest tests.test_evaluate_chat_grounding
.\venv\Scripts\python.exe -B -m unittest tests.test_reply_rules
```

结果均通过。
