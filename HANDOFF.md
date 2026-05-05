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

