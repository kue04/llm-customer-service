# LLM Customer Service Project Handoff

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
Vector retrieval RAG + hybrid search + frontend debugging workbench
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
