# LLM Customer Service Project Handoff

## Project Overview

This project is a learning-oriented LLM customer-service backend for a takeout platform scenario.

Current system goal:

- Build a FastAPI service for customer-service question answering.
- Use a local Qwen model plus LoRA adapter for generation.
- Use a RAG-style retrieval layer to provide relevant reference materials before generation.
- Grow the project into a resume-ready LLM application engineering portfolio project.

Current focus:

- RAG retrieval quality.
- Debugging and evaluation tooling.
- Learning the engineering workflow behind LLM applications, not only copying code.

## Current Architecture

Main request path:

```text
POST /chat/prompt
-> routers/chat.py
-> services/chat_service.py:get_answer_from_rag(query)
-> utils/retriever.py:retrieve_documents(query)
-> models/prompt.py:create_prompt(query, documents)
-> services/chat_service.py:generate_reply(prompt)
-> local Qwen model generates final reply
```

Important files:

- `main.py`: FastAPI app entry.
- `routers/chat.py`: chat endpoint.
- `services/chat_service.py`: RAG orchestration and local model generation.
- `models/prompt.py`: Chinese customer-service prompt template.
- `utils/retriever.py`: keyword retrieval, scoring, dedupe, query expansion.
- `scripts/debug_prompt.py`: single-query retrieval and prompt debugging.
- `scripts/evaluate_retrieval.py`: batch retrieval quality evaluation.
- `data/takeout_customer_service_seed.jsonl`: customer-service knowledge data.
- `STUDY.md`: detailed learning progress log.

## Current Retrieval Features

`utils/retriever.py` currently includes:

- JSONL reading through `iter_knowledge_items()`.
- Keyword extraction through `normalize_terms()`.
- Domain keywords through `DOMAIN_KEYWORDS`.
- Query expansion through `QUERY_EXPANSIONS`.
- Weighted field scoring through `FIELD_WEIGHTS`:
  - `intent`: 5
  - `category`: 3
  - `question`: 2
  - `answer`: 1
- Score explanation through `explain_knowledge_item_score()`.
- Exact answer dedupe with `seen_answers`.
- Similar answer dedupe through:
  - `normalize_answer_text()`
  - `text_bigrams()`
  - `is_similar_answer()`
- Candidate output through `retrieve_document_candidates()`.
- Formal answer-only output through `retrieve_documents()`.
- Multi-condition sorting:

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

Sorting priority:

1. Higher `score`.
2. Higher `matched_term_count`.
3. Shorter `answer`.

## Current Debug Tool

`scripts/debug_prompt.py` supports:

```powershell
.\venv\Scripts\python.exe -B scripts\debug_prompt.py "会员退款怎么办？"
```

Useful options:

```powershell
.\venv\Scripts\python.exe -B scripts\debug_prompt.py "会员退款怎么办？" --limit 5
.\venv\Scripts\python.exe -B scripts\debug_prompt.py "会员退款怎么办？" --limit 5 --no-prompt
```

It prints:

- Retrieval candidates.
- `score`.
- `matched_terms`.
- Field-level scoring details.
- Final prompt unless `--no-prompt` is provided.

## Current Evaluation Tool

`scripts/evaluate_retrieval.py` evaluates a small fixed test set.

Run:

```powershell
.\venv\Scripts\python.exe -B scripts\evaluate_retrieval.py
```

It currently evaluates:

- 会员退款怎么办？
- 外卖超时怎么办？
- 订单取消后钱多久到账？
- 骑手联系不上怎么办？
- 食品有异物怎么办？
- 优惠券不能用怎么办？

Current summary:

```text
总问题数：6
OK：5
部分命中：1
弱命中：0
未命中：0
平均命中率：94.4%
```

Known limitation:

- This is keyword-based evaluation, not semantic evaluation.
- A semantically correct answer may be marked as partial if it does not contain an expected keyword literally.

## Learning Progress

Completed learning areas:

- FastAPI basics: GET, POST, request body, response models.
- Pydantic schemas: `BaseModel`, `Field`, `Query`.
- Router split with `APIRouter`.
- Service-layer extraction.
- JSONL reading and `json.loads(line)`.
- `set` dedupe.
- Chat RAG call chain.
- Chinese customer-service prompt template.
- Keyword retrieval and weighted scoring.
- Similar answer dedupe.
- `argparse` command-line parameters.
- Debug script for single-query inspection.
- Batch evaluation script for retrieval quality.
- Query expansion and phrase expansion.
- Top1 hit-rate evaluation.
- Overall retrieval evaluation summary.

Current learning stage:

```text
LLM application engineering foundation, focused on RAG retrieval quality and evaluation.
```

## Learning Method

Preferred workflow:

1. Explain the goal and the files involved.
2. Let the learner write 10-30 lines of key code.
3. Run the command and inspect output.
4. Review errors and explain the concepts.
5. Update `STUDY.md`.

Do not simply paste full implementations unless the learner asks for it.

Focus on what must be remembered:

- Why the code exists.
- What data structure is flowing through the code.
- What changes before and after running a command.
- What the engineering tradeoff is.

## Current Technical Direction

Short-term direction:

1. Finish and commit current retrieval/evaluation improvements.
2. Start vector RAG.

Next stage: vector retrieval RAG.

Topics to learn next:

- Embedding: how text becomes vectors.
- Vector similarity: cosine similarity / inner product.
- Vector stores: FAISS or Chroma.
- Document chunking.
- Keyword retrieval vs vector retrieval.
- Hybrid search.
- Rerank.
- Retrieval evaluation beyond simple keyword hit rate.

Longer-term LLM engineer roadmap:

- RAG engineering.
- Prompt and structured output.
- Tool calling and Agent workflows.
- LangChain / LangGraph or LlamaIndex.
- Hugging Face Transformers.
- PyTorch basics.
- LoRA / QLoRA fine-tuning.
- Model serving and deployment.
- Docker, monitoring, latency and cost optimization.

## Current Git State To Expect

Current learning unit changed or added:

- `utils/retriever.py`
- `scripts/debug_prompt.py`
- `scripts/evaluate_retrieval.py`
- `STUDY.md`
- `HANDOFF.md`

Recommended commit message:

```text
Add retrieval evaluation and query expansion tooling
```

## Suggested Next Conversation Start

Use this prompt in a new window:

```text
请阅读 HANDOFF.md 和 STUDY.md，继续带我学习下一阶段：向量检索 RAG。先解释 embedding、向量检索和当前关键词检索的区别，然后引导我一步步实现，不要一次性贴完整代码。
```
