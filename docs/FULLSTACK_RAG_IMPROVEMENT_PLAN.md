# 外卖客服 RAG 前后端同步改进实施计划

## 1. 项目路径与执行边界

按实际目录结构识别：

- 后端：D:\llm\llm-customer-service
  - Python、FastAPI、Pydantic、Transformers、FAISS、SQLite。
- 前端：D:\llm\front
  - React 19、TypeScript、Vite、Tailwind。

本计划要求每个后端契约变更必须在同一里程碑同步完成：

1. Pydantic Schema。
2. OpenAPI 快照。
3. TypeScript 类型。
4. API Client。
5. 页面展示与交互。
6. 后端目标测试。
7. 前端类型检查和构建。
8. 接口联调 Smoke。

禁止只改后端字段、把前端留到最后集中修。

---

## 2. 最终目标

把当前项目升级为以下形态：

~~~text
用户原始问题
→ 会话上下文和订单状态
→ History-aware Query Rewrite
→ Dense + BM25 双路召回
→ RRF 融合
→ CrossEncoder 精排
→ Primary / Supporting Evidence
→ 本地或在线异步生成
→ Answer Plan 和 Claim-Evidence 校验
→ Reply Rules / Safety Guard
→ 风险感知 SSE
→ 人工审核
→ 反馈候选集
→ 带版本指纹的发布门禁
~~~

最终必须满足：

- 前端发送原始用户问题，不再把订单详情拼进 message。
- 聊天请求不再并行重复调用 chat、retrieval、prompt-preview 三次链路。
- 合格模型回复不再被 Answer Composer 无条件覆盖。
- 同一进程内 FAISS 只加载一次。
- 过期、未生效、归档和旧版本知识不能进入索引。
- Hybrid 必须真实包含 BM25 与 Dense 两路候选。
- Reranker 失败仍返回召回结果。
- 多轮追问可显示 original_query 和 rewritten_query。
- Streaming 对高风险请求不发送未经校验的原始 token。
- 前端能够展示 Dense/BM25/RRF/Reranker 分数拆解。
- 前端能够展示 Agent 节点、工具计划、实际工具结果和重试次数。
- 发布报告必须绑定代码、Prompt、知识库、索引和模型版本。

---

## 3. 当前必须先处理的契约问题

### 3.1 前端重复执行三条 RAG 链路

当前 D:\llm\front\src\App.tsx 的 sendSupportMessage 同时调用：

~~~text
POST /chat/prompt
POST /retrieval/search
POST /retrieval/prompt-preview
~~~

问题：

- 同一个用户动作执行三次 embedding 和 reranker。
- chat 使用带订单上下文的 message，retrieval 使用原始 query，结果可能不一致。
- 三个接口的 min_score 和上下文不同，诊断面板可能展示的不是生成回答真正使用的证据。

改造后：

- 正常聊天只调用 POST /chat/prompt 或 POST /chat/prompt-stream。
- 检索结果、Prompt、trace 全部使用 ChatResponse 中的权威数据。
- /retrieval/search 和 /retrieval/prompt-preview 仅保留给独立“检索实验室”页面手动调用。

### 3.2 前端重复拼接订单上下文

当前前端把订单号、店铺、商品、状态、金额拼入 message，同时又向后端传 order_id。

改造后：

~~~json
{
  "message": "订单显示已送达但我没收到",
  "user_id": "demo_user",
  "session_id": "session_demo_001",
  "order_id": "WM-DEMO-ORDER"
}
~~~

订单事实只通过 PUT /orders/{id}/state 保存，再由后端订单工具读取。

### 3.3 前后端 RetrievalMode 不统一

当前前端使用 vector | hybrid。

改造后使用：

~~~text
dense
hybrid
~~~

后端暂时接受 vector，并规范化为 dense；前端不再发送 vector。

---

# 里程碑 0：保存两个仓库的当前基线

## Task 0.1：保存后端工作区

执行目录：D:\llm\llm-customer-service

- [ ] 设置 UTF-8：

~~~powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
~~~

- [ ] 保存状态：

~~~powershell
New-Item -ItemType Directory -Force reports\fullstack_baseline | Out-Null
git status --short | Set-Content reports\fullstack_baseline\backend-status.txt -Encoding UTF8
git diff --binary | Set-Content reports\fullstack_baseline\backend.patch -Encoding UTF8
~~~

- [ ] 记录当前提交：

~~~powershell
git rev-parse HEAD | Set-Content reports\fullstack_baseline\backend-head.txt -Encoding UTF8
~~~

- [ ] 不得执行 reset、clean、checkout -- 或 stash drop。

## Task 0.2：保存前端工作区

执行目录：D:\llm\front

- [ ] 保存状态：

~~~powershell
New-Item -ItemType Directory -Force reports\fullstack_baseline | Out-Null
git status --short | Set-Content reports\fullstack_baseline\frontend-status.txt -Encoding UTF8
git diff --binary | Set-Content reports\fullstack_baseline\frontend.patch -Encoding UTF8
git rev-parse HEAD | Set-Content reports\fullstack_baseline\frontend-head.txt -Encoding UTF8
~~~

- [ ] 运行现有构建：

~~~powershell
npm run build
~~~

预期：TypeScript 和 Vite 构建成功。

## Task 0.3：建立双仓分支

在两个仓库分别创建：

~~~text
codex/rag-fullstack-v2
~~~

只切分支，不自动提交当前用户修改。

---

# 里程碑 1：建立前后端契约单一来源

## Task 1.1：后端导出 OpenAPI 快照

后端新增：

- D:\llm\llm-customer-service\scripts\export_openapi.py
- D:\llm\llm-customer-service\tests\test_export_openapi.py

脚本接口：

~~~python
from pathlib import Path
import json

from main import app


def export_openapi(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
~~~

原子步骤：

- [ ] 先写测试，断言导出 JSON 包含 /chat/prompt、/retrieval/search、/knowledge/items。
- [ ] 运行：

~~~powershell
.\venv\Scripts\python.exe -m pytest tests\test_export_openapi.py -q
~~~

- [ ] 确认测试先失败。
- [ ] 实现 export_openapi。
- [ ] 再运行目标测试并确认 PASS。
- [ ] 生成：

~~~powershell
.\venv\Scripts\python.exe scripts\export_openapi.py --output docs\openapi.json
~~~

- [ ] 确认 docs\openapi.json 是 UTF-8 且可被 json.loads 读取。

## Task 1.2：前端使用 OpenAPI 生成类型

前端新增依赖：

~~~powershell
npm install -D openapi-typescript vitest jsdom @testing-library/react @testing-library/jest-dom
~~~

package.json 增加：

~~~json
{
  "scripts": {
    "types:api": "openapi-typescript ../llm-customer-service/docs/openapi.json -o src/types/openapi.generated.ts",
    "test": "vitest run",
    "test:watch": "vitest",
    "check": "npm run types:api && npm run test && npm run build"
  }
}
~~~

原子步骤：

- [ ] 生成 D:\llm\front\src\types\openapi.generated.ts。
- [ ] 在 src\types\api.ts 中把 ChatResponse、RetrievalSearchResponse、KnowledgeOpsItem 等类型改为 generated components 的别名。
- [ ] 页面专用类型继续留在 api.ts，不得手写复制后端响应字段。
- [ ] 运行：

~~~powershell
npm run types:api
npm run build
~~~

- [ ] 修复所有类型不一致，直到 build 成功。

## Task 1.3：集中管理身份 Header 和 AbortSignal

修改前端：

- src\api\client.ts
- src\api\chat.ts
- src\api\retrieval.ts
- src\api\knowledge.ts
- src\api\prompt.ts
- src\api\release.ts
- src\api\audit.ts

apiRequest 参数改为：

~~~typescript
type ApiRequestOptions<TBody> = {
  method?: "GET" | "POST" | "PUT";
  body?: TBody;
  role?: OperatorRole;
  operatorId?: string;
  signal?: AbortSignal;
};
~~~

client.ts 提供：

~~~typescript
export function buildOperatorHeaders(
  role: OperatorRole,
  operatorId: string,
): Record<string, string>;
~~~

验收：

- [ ] chat.ts 不再自己声明 chatAgentHeaders。
- [ ] 所有 API 模块从 client.ts 获取 Header。
- [ ] apiRequest 把 signal 传入 fetch。
- [ ] Vitest 验证 agent 和 admin 生成不同 Header。
- [ ] npm run test 和 npm run build 均通过。

## Task 1.4：修复聊天请求契约

修改前端：

- src\App.tsx
- 新建 src\features\support\buildChatRequest.ts
- 新建 src\features\support\buildChatRequest.test.ts

纯函数：

~~~typescript
export function buildChatRequest(input: {
  question: string;
  userId: string;
  sessionId: string | null;
  orderId: string;
}): ChatRequest {
  return {
    message: input.question.trim(),
    user_id: input.userId,
    session_id: input.sessionId,
    order_id: input.orderId,
    channel: "support_console",
  };
}
~~~

原子步骤：

- [ ] 测试 message 只等于原始问题。
- [ ] 测试 message 不包含订单号、金额、店铺或商品。
- [ ] App.tsx 删除聊天发送路径中的 buildOrderContextMessage。
- [ ] buildOrderContextMessage 只保留给导出诊断报告使用。
- [ ] 正常聊天删除 Promise.allSettled 中的 searchRetrieval 和 previewRetrievalPrompt。
- [ ] 聊天成功后只用 chatResult.retrieved_items 更新检索结果。
- [ ] 聊天成功后使用 chatResult.final_prompt 更新诊断，不再请求 preview。
- [ ] 独立 Retrieval 页面继续允许手动调用 search 和 preview。
- [ ] npm run test。
- [ ] npm run build。

后端同步验收：

- [ ] ChatRequest.channel 已存在并进入 full_trace。
- [ ] 后端测试确认 order_id 工具结果进入 Prompt。
- [ ] 后端测试确认原始 message 不需要携带“订单上下文”文本。

---

# 里程碑 2：修复 Composer、知识有效性和索引生命周期

## Task 2.1：Answer Composer 只修复低质量回复

后端修改：

- services\answer_composer.py
- services\chat_service.py
- schemas\chat_schema.py
- tests\test_answer_composer.py
- tests\test_chat_api.py

固定行为：

~~~python
cleaned_reply = remove_generic_tails(reply)
low_quality = reply_needs_composer(query, cleaned_reply, primary_item)

if not low_quality:
    return cleaned_reply, {
        "applied": False,
        "reason": "model_reply_accepted",
    }

composed_reply, parts = compose_from_primary_evidence(query, primary_item)
return composed_reply, {
    "applied": True,
    "reason": "low_quality_model_reply",
    "answer_parts": {
        "conclusion": parts.conclusion,
        "action": parts.action,
        "caveat": parts.caveat,
    },
}
~~~

新增响应字段：

~~~text
answer_strategy = model_reply | composer_repair | safety_fallback
~~~

前端同步：

- src\types\api.ts 或生成类型。
- src\components\DiagnosticsPanel.tsx。
- src\features\support\SupportView.tsx。

展示规则：

- model_reply：绿色“模型回答被保留”。
- composer_repair：黄色“主证据规则修复”。
- safety_fallback：红色“安全兜底替换”。

验收：

- [ ] 后端测试：合格回复保持原文。
- [ ] 后端测试：泛化套话被 composer 替换。
- [ ] 后端测试：Safety Guard 替换时 strategy 为 safety_fallback。
- [ ] 前端测试：三种 strategy 显示正确文案。
- [ ] 前后端 build/test 通过。

## Task 2.2：知识有效期和最新版本过滤

后端新增：

- utils\knowledge_filter.py
- tests\test_knowledge_filter.py

固定规则：

1. status 为空的历史 seed 视为 active。
2. published、approved 可进入索引。
3. draft、pending_review、rejected、archived、rollback 不进入。
4. effective_at 晚于当前 UTC 时间时不进入。
5. expired_at 小于等于当前 UTC 时间时不进入。
6. 同一 base_id 只保留最大 version。
7. 缺少 base_id 时使用 id。

必须按以下逻辑实现：

~~~python
from datetime import datetime, timezone
from typing import Iterable


INACTIVE_STATUSES = {
    "draft",
    "pending_review",
    "rejected",
    "archived",
    "rollback",
}


def parse_optional_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def version_number(item: dict) -> int:
    raw = str(item.get("version") or "0").lower().lstrip("v")
    return int(raw) if raw.isdigit() else 0


def is_active_knowledge_item(item: dict, now: datetime | None = None) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = str(item.get("status") or "").strip().lower()
    if status in INACTIVE_STATUSES:
        return False

    effective_at = parse_optional_datetime(item.get("effective_at"))
    if effective_at is not None and effective_at > current:
        return False

    expired_at = parse_optional_datetime(item.get("expired_at"))
    if expired_at is not None and expired_at <= current:
        return False

    return True


def select_latest_active_items(
    items: Iterable[dict],
    now: datetime | None = None,
) -> list[dict]:
    latest_by_key: dict[str, dict] = {}
    for item in items:
        if not is_active_knowledge_item(item, now=now):
            continue
        key = str(item.get("base_id") or item.get("id") or "").strip()
        if not key:
            continue
        existing = latest_by_key.get(key)
        if existing is None or version_number(item) > version_number(existing):
            latest_by_key[key] = item
    return list(latest_by_key.values())
~~~

知识发布 JSONL 增加 base_id。

前端同步：

- KnowledgeOpsView 表单增加 effective_at、expired_at 输入。
- 知识列表增加运行状态：
  - 未生效
  - 当前有效
  - 已过期
  - 已归档
- 当前有效列表不得显示历史版本为“在线知识”。
- 点击同一 base_id 时可以展开版本历史。

验收：

- [ ] 六类后端过滤测试通过。
- [ ] 前端对固定当前时间测试四种运行状态。
- [ ] 发布一条 future knowledge 后，检索结果不包含它。
- [ ] 修改 effective_at 为当前时间后重新发布，检索结果包含它。

## Task 2.3：FAISS Manifest、批量 Embedding 和原子切换

后端新增：

- utils\vector_store_manifest.py
- tests\test_vector_store_manifest.py

Manifest 字段：

~~~json
{
  "schema_version": 1,
  "embedding_model_name": "",
  "preprocessing_version": "faq-v2",
  "document_fingerprint": "",
  "dimension": 512,
  "document_count": 0,
  "created_at": ""
}
~~~

文档 fingerprint 必须包含：

~~~text
id、base_id、version、title、question、answer、category、intent、
source、updated_at、effective_at、expired_at
~~~

索引构建改为一次批量 encode：

~~~python
vectors = model.encode(
    texts,
    batch_size=32,
    normalize_embeddings=True,
    convert_to_numpy=True,
    show_progress_bar=False,
).astype("float32")
~~~

加载规则：

- 内存已有 index 和 docs：直接返回。
- 进程启动时只读取一次 index、docs、manifest。
- manifest 不兼容：重建。
- 知识发布后主动 reset cache。

写入顺序：

1. index.tmp。
2. docs.tmp。
3. manifest.tmp。
4. replace index。
5. replace docs。
6. 最后 replace manifest。

前端同步：

- Retrieval Config 增加：
  - vector_preprocessing_version
  - vector_document_count
  - vector_dimension
  - vector_built_at
  - vector_manifest_status
- ModelInfoBar 展示索引版本和知识数量。
- Release 页面在 manifest 不一致时显示 fail。

验收：

- [ ] 第二次 ensure_real_vector_store 不调用 faiss.read_index。
- [ ] embedding model 名变化时 manifest 不兼容。
- [ ] 文档内容变化时 fingerprint 变化。
- [ ] 前端能显示 document_count 和 built_at。
- [ ] 发布失败时旧 manifest 仍保持可用。

## Task 2.4：Reranker Fail-open

后端候选新增：

~~~text
reranker_degraded
reranker_error
~~~

失败时：

- model_rerank_score 全部设为 0。
- 保留 Dense/Hybrid 原始排序。
- Chat 不进入无证据 fallback。
- full_trace 标记 reranker_degraded。

前端同步：

- Retrieval 卡片顶部显示“Reranker 已降级”。
- 只向 supervisor、qa、admin 展示 error 文本。
- 普通 agent 只看状态，不看内部异常。

验收：

- [ ] monkeypatch CrossEncoder 抛错后仍返回候选。
- [ ] 前端能渲染 degraded candidate。
- [ ] ChatResponse.degraded=true，failure_stage=reranker。

---

# 里程碑 3：真正的 Dense + BM25 + RRF

## Task 3.1：新增 BM25 Retriever

后端依赖：

~~~text
jieba==0.42.1
rank-bm25==0.2.2
~~~

新增：

- utils\lexical_retriever.py
- tests\test_lexical_retriever.py

接口按以下实现：

~~~python
from dataclasses import dataclass
import string

import jieba
from rank_bm25 import BM25Okapi

from utils.retriever import DOMAIN_KEYWORDS


PUNCTUATION = set(string.punctuation + "，。！？；：、（）《》“”‘’")


@dataclass(frozen=True)
class LexicalHit:
    doc_index: int
    score: float
    rank: int


def tokenize_for_bm25(text: str) -> list[str]:
    normalized = " ".join(str(text or "").split())
    tokens = [
        token.strip()
        for token in jieba.lcut(normalized)
        if token.strip() and not all(char in PUNCTUATION for char in token)
    ]
    for keyword in DOMAIN_KEYWORDS:
        if keyword in normalized and keyword not in tokens:
            tokens.append(keyword)
    return tokens


class LexicalRetriever:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.tokenized_corpus = [
            tokenize_for_bm25(document.get("text", ""))
            for document in documents
        ]
        self.index = BM25Okapi(self.tokenized_corpus)

    def search(self, query: str, top_k: int) -> list[LexicalHit]:
        scores = self.index.get_scores(tokenize_for_bm25(query))
        ranked = sorted(
            enumerate(scores),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        return [
            LexicalHit(
                doc_index=int(doc_index),
                score=float(score),
                rank=rank,
            )
            for rank, (doc_index, score) in enumerate(
                ranked[:top_k],
                start=1,
            )
            if float(score) > 0.0
        ]
~~~

分词规则：

- jieba.lcut。
- 删除空白和标点。
- 保留券、餐、钱等一字业务词。
- 追加命中的完整 DOMAIN_KEYWORDS。
- 不在 BM25 内做 QUERY_EXPANSIONS。

验收查询：

~~~text
退款多久到账
骑手联系不上
身份证信息
优惠券不能用
餐里有异物
~~~

每个查询 Top3 必须至少有一个期望 intent。

## Task 3.2：RRF 融合

新增：

- utils\hybrid_retriever.py
- tests\test_hybrid_retriever.py

固定参数初值：

~~~text
dense_top_k = 30
lexical_top_k = 30
fusion_top_k = 20
rrf_k = 60
reranker_top_n = 20
~~~

RRF：

~~~python
score = 0.0
if dense_rank is not None:
    score += 1.0 / (60 + dense_rank)
if lexical_rank is not None:
    score += 1.0 / (60 + lexical_rank)
~~~

每个结果必须返回：

~~~text
dense_rank、lexical_rank、dense_score、lexical_score、rrf_score、
keyword_bonus、direction_penalty、model_rerank_score、rerank_score、
retrieval_origin
~~~

retrieval_origin：

~~~text
dense
lexical
dense+lexical
intent_hint_supplement
~~~

## Task 3.3：前端展示真正的融合分数

修改：

- src\types\api.ts
- src\components\RetrievalPanel.tsx
- src\components\DiagnosticsPanel.tsx
- src\features\support\SupportView.tsx

RetrievalCard 必须显示：

~~~text
Dense rank
BM25 rank
RRF score
CrossEncoder score
Keyword bonus
Direction penalty
Final score
Origin
~~~

显示规则：

- dense+lexical：绿色“双路命中”。
- lexical：蓝色“BM25 补召回”。
- dense：灰色“Dense only”。
- intent_hint_supplement：黄色“规则补召回”。

前端 mode 改为 dense | hybrid。

旧 vector 值只在读取 localStorage 时迁移成 dense，不再写回 vector。

## Task 3.4：Retrieval V2 评测

后端新增：

- data\retrieval_eval_v2.jsonl
- scripts\evaluate_retrieval_v2.py
- tests\test_evaluate_retrieval_v2.py

120 条配额：

| 类型 | 数量 |
|---|---:|
| baseline | 20 |
| paraphrase | 20 |
| typo_colloquial | 20 |
| direction_conflict | 20 |
| multi_intent | 20 |
| high_risk | 20 |

输出：

~~~text
Recall@1、Recall@3、Recall@5、MRR、nDCG@5、P50、P95、
case_type 分组、risk_level 分组、missed_case_ids
~~~

固定消融：

~~~text
dense_only
dense_rules
dense_rerank
hybrid_no_rerank
hybrid_rerank
~~~

准入：

~~~text
Hybrid Recall@3 >= 0.95
Hybrid MRR >= dense_only
高风险 Recall@3 = 1.00
P95 <= dense_only P95 * 1.50
~~~

前端同步：

- Release/Operations 页面增加 Retrieval V2 指标。
- 展示当前配置和 baseline 对比。
- 未达门槛时 Release 显示 fail，不允许只显示总通过率。

---

# 里程碑 4：会话感知 Query Rewrite

## Task 4.1：后端 Rewrite Service

新增：

- services\query_rewrite_service.py
- tests\test_query_rewrite_service.py

返回：

~~~python
@dataclass(frozen=True)
class QueryRewriteResult:
    original_query: str
    rewritten_query: str
    applied: bool
    reason: str
    context_fields_used: tuple[str, ...]
~~~

触发：

- query 长度不超过 18。
- 包含那、这个、还是、然后、多久到账、怎么办、能退吗。
- 当前 query 没有完整业务词。
- context 有 last_primary_intent 或 last_primary_evidence。

不触发：

- query 已经完整描述订单问题。
- 当前为完整高风险表达。
- context 无事实。

只允许使用：

~~~text
order_id
summary
facts.last_primary_intent
facts.last_primary_evidence
facts.refund_mentioned
最近一条 user message
~~~

禁止使用手机号、地址和长期用户隐私。

## Task 4.2：接入 Chat Trace

顺序：

~~~text
load context
→ analyze intent
→ rewrite
→ intent hint
→ hybrid retrieval
~~~

ChatResponse 增加：

~~~text
original_query
rewritten_query
query_rewrite_applied
query_rewrite_reason
~~~

full_trace 增加 query_rewritten。

## Task 4.3：前端展示改写

修改：

- src\types\api.ts
- src\features\support\SupportView.tsx
- src\components\DiagnosticsPanel.tsx

Timeline 新步骤：

~~~text
query_rewritten → 查询改写
~~~

展开内容：

~~~text
原问题
改写后问题
触发原因
使用的上下文字段
~~~

普通聊天气泡仍显示用户原问题，不能把 rewritten_query 当成用户消息。

## Task 4.4：多轮评测

新增 data\multiturn_retrieval_cases.jsonl，共 24 条：

~~~text
退款 4
取消 4
配送 4
优惠券 4
食品安全 4
隐私 4
~~~

门槛：

~~~text
Rewrite 触发准确率 >= 90%
改写后意图命中率 >= 90%
完整问题误改写率 <= 5%
~~~

---

# 里程碑 5：异步模型网关和前端 Streaming

## Task 5.1：后端 Generation Gateway

新增：

- services\generation_gateway.py
- tests\test_generation_gateway.py

依赖：

~~~text
httpx>=0.28,<0.29
redis>=5,<7
~~~

统一结果：

~~~python
@dataclass(frozen=True)
class GenerationResult:
    text: str
    token_usage: dict
    provider: str
    model: str
    degraded: bool
    warnings: tuple[str, ...]
~~~

重试：

| 错误 | 行为 |
|---|---|
| 429 | 0.5、1、2 秒，最多 3 次 |
| 500/502/503/504 | 0.5、1、2 秒，最多 3 次 |
| Connect/Read Timeout | 最多 2 次 |
| 400/401/403/404 | 不重试 |
| JSON 缺 choices | schema_error，切 fallback |

Fallback：

~~~text
online → local
local → 固定安全模板
~~~

本地 generate 使用 anyio.to_thread，并使用 asyncio.Lock 防止同一模型并发。

## Task 5.2：后端 SSE

新增：

~~~text
POST /chat/prompt-stream
~~~

事件：

~~~text
trace
delta
final
error
~~~

风险策略：

| 风险 | 行为 |
|---|---|
| low | 发送 delta |
| medium | 发送 delta，final 为权威结果 |
| high | 不发送 delta，只发送 trace 和安全 final |
| critical/blocked | 不流式生成，直接安全 final |

客户端断开后停止在线流。

## Task 5.3：前端 SSE Client

新增：

- src\api\chatStream.ts
- src\api\chatStream.test.ts

接口：

~~~typescript
export type ChatStreamHandlers = {
  onTrace: (step: FullTraceStep) => void;
  onDelta: (text: string) => void;
  onFinal: (response: ChatResponse) => void;
  onError: (error: ChatStreamError) => void;
};

export async function streamChatPrompt(
  payload: ChatRequest,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<void>;
~~~

解析要求：

- 支持一个 data 跨多个 TCP chunk。
- 以空行结束一个 SSE event。
- 未知 event 忽略并记录。
- final 只处理一次。
- error 后若仍收到 final，以 final 为准。

## Task 5.4：提取 Support Chat Hook

新增：

- src\features\support\useSupportChat.ts
- src\features\support\useSupportChat.test.tsx

职责：

~~~text
消息追加
流式草稿
AbortController
loading
trace 增量
final response
错误回退
session_id 更新
~~~

App.tsx 不再直接管理流式解析细节。

UI：

- 低风险：实时显示 assistant 草稿。
- 高风险：显示“正在执行安全校验”，不显示 token。
- 增加“停止生成”按钮。
- final 到达后用 final.reply 替换草稿。
- 流失败且尚未收到 delta 时自动回退 POST /chat/prompt。
- 已收到 delta 后失败，不自动重试，显示“生成中断，可重新发送”。

验收：

- [ ] 后端四类 SSE 测试通过。
- [ ] 前端 chunk 拆分测试通过。
- [ ] Abort 后不再追加 delta。
- [ ] high risk 测试中 onDelta 调用次数为 0。
- [ ] npm run build 通过。

---

# 里程碑 6：结构化 Answer Plan 和 Claim-Level 引用

## Task 6.1：后端 Answer Plan

新增：

- services\answer_plan_service.py
- services\citation_validator.py
- tests\test_answer_plan_service.py

结构：

~~~python
class AnswerClaim(BaseModel):
    claim_id: str
    text: str
    evidence_ids: list[str]


class AnswerPlan(BaseModel):
    conclusion: str
    actions: list[str]
    caveats: list[str]
    claims: list[AnswerClaim]
    needs_human_review: bool
    review_reason: str
~~~

Prompt 每条证据必须带 knowledge_id。

校验：

- unknown evidence ID：删除 claim，标记人工审核。
- support score >= 0.60：supported。
- 0.45 至 0.60：review。
- < 0.45：unsupported，不能进入最终回复。
- Reranker 不可用：只做 ID 校验并强制人工审核。

只在以下场景启用结构化 Plan：

~~~text
high/critical
Top1/Top2 margin < 0.08
tool_results > 1
~~~

## Task 6.2：响应契约

ChatResponse 增加：

~~~text
answer_plan_applied
answer_claims
citation_validation
~~~

answer_claims 每项：

~~~json
{
  "claim_id": "claim_1",
  "text": "",
  "evidence_ids": ["kb_x"],
  "support_score": 0.72,
  "status": "supported"
}
~~~

## Task 6.3：前端 Claim-Evidence 视图

修改：

- src\features\support\SupportView.tsx
- src\components\DiagnosticsPanel.tsx
- src\types\api.ts

Evidence Tab 增加“回答事实映射”：

- supported：绿色。
- review：黄色。
- unsupported：红色且注明未进入最终回复。
- 点击 claim 高亮对应 evidence 卡片。
- 点击 evidence 反向高亮引用它的 claims。

普通客服默认只看 claim 文本和来源。

QA/Admin 可看 support_score 和 validation reason。

---

# 里程碑 7：受控 Agentic RAG

## Task 7.1：后端有界状态机

新增：

- services\agent_workflow.py
- tests\test_agent_workflow.py

节点固定为：

~~~text
load_context
classify_intent
plan_read_tools
execute_read_tools
retrieve
assess_evidence
rewrite_once
generate_answer
validate_answer
recommend_handoff
persist_result
~~~

只读工具白名单：

~~~text
query_order_status
query_refund_status
search_knowledge_base
~~~

禁止自动写：

~~~text
create_handoff_ticket
knowledge_publish
knowledge_rollback
prompt_activate
order_state_upsert
~~~

最多：

~~~text
1 次初始检索
1 次改写后检索
总计不超过 2 次
~~~

重试条件：

~~~text
无证据
Top1 score < 0.55
Top1/Top2 margin < 0.05
~~~

第二次仍低置信度：生成保守回复并强制人工审核。

## Task 7.2：Agent Trace 契约

响应增加：

~~~text
agent_enabled
agent_retry_count
agent_nodes
tool_plan
evidence_assessment
~~~

每个 node：

~~~json
{
  "node": "retrieve",
  "status": "success",
  "attempt": 1,
  "latency_ms": 12.5,
  "summary": ""
}
~~~

## Task 7.3：前端 Agent Timeline

修改：

- src\features\support\SupportView.tsx
- src\components\DiagnosticsPanel.tsx

Timeline 必须区分：

~~~text
业务 Trace
Agent Node
工具计划
实际工具执行
二次检索
人工接管建议
~~~

Tools Tab 同时显示：

- Planned。
- Executed。
- Skipped。
- Policy removed。

如果模型计划 refund tool，但策略因无退款意图删除，必须显示“被策略层移除”。

Agent 开关只读显示后端配置，不允许前端直接修改环境变量。

---

# 里程碑 8：评测、反馈和发布门禁同步

## Task 8.1：发布指纹

后端新增：

- services\release_fingerprint.py
- tests\test_release_fingerprint.py

指纹：

~~~text
git_commit
git_dirty
prompt_version
knowledge_sha256
vector_manifest_sha256
embedding_model_name
reranker_model_name
generation_provider
generation_model
frontend_commit
~~~

frontend_commit 由前端构建时写入：

~~~text
VITE_GIT_COMMIT
~~~

前端页面底部和 Release 页展示前后端 commit。

发布规则：

- 当前指纹与报告不一致：fail。
- 任一仓库 dirty：warn。
- 无指纹历史报告：warn，不能作为正式发布依据。

## Task 8.2：反馈候选池

后端新增状态：

~~~text
pending
approved
rejected
promoted
~~~

接口：

~~~text
GET  /feedback/eval-candidates
POST /feedback/eval-candidates/{id}/review
POST /feedback/eval-candidates/{id}/promote
~~~

promote 只能进入 regression set，不能自动写 blind/holdout。

前端新增：

- KnowledgeOpsView 或独立 QA 页面增加“评测候选”Tab。
- 展示 query、模型回复、期望回复、失败层、状态。
- QA 可 approve/reject。
- promote 前二次确认。
- promoted 后显示目标数据集和 case ID。

## Task 8.3：Release Dashboard

前端 Release 页面必须逐项展示：

~~~text
Prompt version
Knowledge version
Vector manifest
Backend commit
Frontend commit
Retrieval Recall@3
Retrieval MRR
Grounding strict pass
Evidence coverage
High-risk pass
Forbidden hits
Token tracking
Audit coverage
Tool fallback
~~~

状态颜色：

- pass：绿色。
- warn：黄色。
- fail：红色。

ready=false 时明确列出阻断原因，不能只显示“未就绪”。

---

# 里程碑 9：最终文档和联调验收

## Task 9.1：后端文档

更新：

- D:\llm\llm-customer-service\README.md
- D:\llm\llm-customer-service\docs\API_INTEGRATION.md
- D:\llm\llm-customer-service\docs\EVALUATION.md
- D:\llm\llm-customer-service\docs\ENTERPRISE_AI_CUSTOMER_SERVICE_PRD.md

必须包含：

- 原始用户问题与订单工具的正确数据流。
- Dense、BM25、RRF、Reranker 的职责。
- SSE 事件协议。
- Query Rewrite 字段。
- Answer Plan Schema。
- Agent 工具白名单和循环上限。
- 前后端 commit 指纹。
- 最新评测命令和门槛。

## Task 9.2：前端文档

更新：

- D:\llm\front\README.md
- D:\llm\front\PRODUCT.md
- D:\llm\front\PROJECT_SHOWCASE.md
- D:\llm\front\DESIGN.md

必须包含：

- VITE_API_BASE_URL。
- OpenAPI 类型生成命令。
- SSE 浏览器兼容和取消行为。
- 各角色能看到的诊断字段。
- Retrieval 分数卡解释。
- Agent Timeline 解释。
- Release Dashboard 使用方式。

## Task 9.3：后端最终验证

只在最终阶段运行一次完整测试：

~~~powershell
cd D:\llm\llm-customer-service
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
.\venv\Scripts\python.exe -m pytest -q
~~~

然后：

~~~powershell
.\venv\Scripts\python.exe scripts\evaluate_retrieval_v2.py --cases data\retrieval_eval_v2.jsonl --run-ablation --output-dir reports\retrieval_eval_v2
.\venv\Scripts\python.exe scripts\evaluate_chat_grounding.py --cases data\chat_grounding_cases.jsonl --use-local-judge
~~~

发布检查必须：

~~~text
ready = true
failed_count = 0
warning_count = 0
~~~

## Task 9.4：前端最终验证

~~~powershell
cd D:\llm\front
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
npm run types:api
npm run test
npm run build
~~~

必须全部成功。

## Task 9.5：真实联调 Smoke

启动后端：

~~~powershell
cd D:\llm\llm-customer-service
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
~~~

启动前端：

~~~powershell
cd D:\llm\front
npm run dev
~~~

逐项验证：

1. 打开客服工作台。
2. 选择已送达订单。
3. 输入“订单显示已送达但我没收到”。
4. 浏览器 Network 中只能有一次 chat 请求，不应同时自动请求 retrieval/search 和 prompt-preview。
5. 后端收到的 message 只能是原始问题。
6. Evidence Tab 有 Primary Evidence。
7. Retrieval Card 显示 Dense/BM25/RRF/Reranker。
8. Timeline 显示 query_rewritten 或 skipped。
9. low risk 能看到流式文字。
10. high risk 不出现未校验 delta。
11. 停止生成按钮能中止请求。
12. 人工采纳后状态变为 accepted。
13. bad case 能进入 eval candidate。
14. Release 页面显示前后端 commit。
15. Release ready 为 true。

---

## 4. 分阶段提交顺序

后端建议提交：

~~~text
test: add fullstack API baseline
fix: preserve grounded model replies
feat: filter inactive knowledge before indexing
feat: add versioned atomic FAISS store
fix: degrade gracefully when reranker fails
feat: add Chinese BM25 retriever
feat: add BM25 dense RRF retrieval
feat: add history aware query rewrite
feat: add resilient async generation gateway
feat: add risk aware SSE chat
feat: add evidence bound answer plans
feat: add bounded agentic RAG workflow
feat: bind evaluations to fullstack fingerprints
feat: add reviewed evaluation candidates
docs: finalize fullstack RAG contract
~~~

前端建议提交：

~~~text
chore: generate API types from backend OpenAPI
fix: send raw chat questions once
feat: show answer strategy and index status
feat: visualize hybrid retrieval scores
feat: show query rewrite diagnostics
feat: add cancellable chat streaming
feat: visualize claim evidence mapping
feat: add agent execution timeline
feat: add evaluation candidate workflow
feat: add release readiness dashboard
docs: update fullstack RAG console guide
~~~

---

## 5. 推荐排期

| 里程碑 | 时间 | 可独立交付结果 |
|---|---:|---|
| 0 基线保存 | 0.5 天 | 两仓修改不会丢失 |
| 1 契约同步 | 1.5 天 | OpenAPI 驱动 TS 类型，聊天只请求一次 |
| 2 正确性与索引 | 2.5 天 | Composer、有效知识、FAISS、降级修复 |
| 3 真 Hybrid | 3 天 | BM25 + Dense + RRF + 可视化 |
| 4 Query Rewrite | 1.5 天 | 多轮追问可解释 |
| 5 Gateway + SSE | 3 天 | 异步、重试、流式、取消 |
| 6 可信引用 | 2.5 天 | Claim-Evidence 校验 |
| 7 Agentic RAG | 2.5 天 | 有界多步检索和工具策略 |
| 8 评测发布 | 2 天 | 双仓指纹和 Release Gate |
| 9 文档联调 | 1.5 天 | 可演示、可复现、可写简历 |
| 合计 | 20.5 天 | 完整 Full-stack RAG v2 |

---

## 6. 停止条件

出现任一条件时停止增加新功能，先修复当前里程碑：

1. 前端显示的证据不是 ChatResponse 真正使用的证据。
2. 一个聊天动作仍产生三次检索。
3. Query Rewrite 修改了完整、明确的高风险问题。
4. Hybrid MRR 低于 dense baseline。
5. Reranker 故障导致 retrieved_items 为空。
6. high risk SSE 发送了 delta。
7. Claim 引用了不存在的 evidence ID。
8. Agent 自动执行了写工具。
9. Agent 一次请求检索超过两次。
10. Release report 指纹不一致仍显示 pass。
11. npm run build 或后端目标测试没有通过。
12. 为了单个固定 case 再增加意图专用 Answer Composer 硬编码。

---

## 7. 实施记录

### 2026-07-16：里程碑 1 后端契约基线

- 新增 `scripts/export_openapi.py`，从 FastAPI `app.openapi()` 导出完整接口契约到 `docs/openapi.json`。
- 新增 `tests/test_export_openapi.py`，固定 `/chat/prompt`、`/retrieval/search`、`/knowledge/items` 三条全栈关键链路。
- 验收命令：`python -m pytest tests/test_export_openapi.py -q`。
- 当前结果：`1 passed`；OpenAPI 文件可以被前端 `openapi-typescript` 直接消费。

### 2026-07-17：里程碑 2 正确性与索引生命周期

- Answer Composer 改为只修复低质量回复，合格模型回答只移除固定套话尾部，并新增 `answer_strategy`。
- 新增知识有效期过滤：排除未审核、未来生效、已过期和已归档知识；同一 `base_id` 仅索引最高版本。
- 知识发布 JSONL 补齐 `base_id`，保证跨版本去重和溯源。
- FAISS 改为批量 Embedding，新增文档指纹、模型名、维度、文档数和预处理版本 Manifest。
- index、docs、manifest 使用同目录临时文件写入，最后切换 manifest；内存缓存命中时不再重复读取磁盘索引。
- Reranker 异常改为 fail-open：保留 Dense/Hybrid 候选及基础排序，候选和 Chat trace 均标记降级原因。
- `/retrieval/config` 返回索引版本、知识数量、向量维度、构建时间和 Manifest 状态；Release Gate 增加 `vector_manifest` 检查。
