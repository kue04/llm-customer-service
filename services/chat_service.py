# app/services/chat_service.py
import logging
import os
from pathlib import Path
import time
from uuid import uuid4

from config.rag_config import get_rag_config
from models.prompt import create_prompt
from services import conversation_store
from services.answer_composer import compose_answer_if_needed
from services.conversation_service import (
    build_context_used,
    get_or_create_context,
    maybe_update_summary,
    save_message,
    update_facts,
)
from services.customer_memory_service import get_user_memory, update_user_memory_from_turn
from services.feedback_service import save_chat_session
from services.grounding_diagnostics import build_chat_grounding_diagnostics
from services.intent_service import analyze_intents
from services.online_generation import (
    generate_online_chat_completion,
    generate_online_chat_completion_with_usage,
)
from services.ops_metrics import record_chat_metrics, record_token_usage
from services.order_tool_service import (
    create_handoff_ticket,
    query_order_status,
    query_refund_status,
    should_call_refund_tool,
)
from services.privacy import mask_sensitive_text
from services.prompt_service import DEFAULT_SYSTEM_PROMPT, get_active_prompt_config
from services.redis_context_cache import get_redis_context_cache
from services.reply_rules import apply_reply_rules_with_trace
from services.safety_guard import validate_reply
from utils.rag_context import build_prompt_context_items, prompt_context_items_to_dicts
from services.query_resolution import resolve_query
from utils.vector_retriever import detect_intent_hint

try:
    import torch
except ModuleNotFoundError:
    torch = None

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ModuleNotFoundError:
    AutoModelForCausalLM = None
    AutoTokenizer = None

try:
    from peft import PeftModel
except ModuleNotFoundError:
    PeftModel = None


MODEL_PATH = Path(__file__).resolve().parents[1] / "local_models" / "qwen2.5-1.5b-instruct"
ADAPTER_PATH = Path(__file__).resolve().parents[1] / "models" / "takeout-qwen-lora-minimal"
DEVICE = "cuda" if torch is not None and torch.cuda.is_available() else "cpu"
MODEL_DTYPE = torch.float16 if torch is not None and DEVICE == "cuda" else (torch.float32 if torch is not None else None)

# ================================================================ 检索轨道（F1 切轨）

#: 聊天问答**默认**使用的检索轨道。
#:
#: - ``"chunk"``：B 轨 —— 文档 chunk 级索引（209 份文档 / 9229 个 chunk），
#:   带 tenant 与 document ACL 的**服务端预过滤**（fail closed）；
#: - ``"seed"``：A 轨 —— 781 条手工种子 FAQ，**没有任何权限过滤**，原为演示 / 兼容路径。
#:
#: ⚠️ **改这个默认值是里程碑事件，不是普通调参**：README 的机器可读锚点
#: ``<!-- f1-track: chat-service-retrieval=... -->`` 必须同步改，
#: 两边由 ``tests/test_ingestion_pipeline.py::TestReadmeTrackConsistency`` 双向校验。
#: 守卫直接读这个常量的字面量，**读不到也算失败** —— 不做"读不到就放行"的假绿
#: （踩坑 D20：让探针先失效的改动会伪装成通过）。
DEFAULT_CHAT_RETRIEVAL_PATH = "chunk"

#: 正式聊天链路只有 chunk 轨道。seed FAQ 仅保留在显式 demo API，不允许通过环境变量
#: 改变生产聊天数据源，避免无权限的兼容路径重新成为线上分支。
CHAT_RETRIEVAL_PATHS = ("chunk",)


def resolve_chat_retrieval_path() -> str:
    """返回正式聊天检索轨道；历史环境变量被忽略。"""

    return DEFAULT_CHAT_RETRIEVAL_PATH


#: 聊天链路在 B 轨内使用的**召回模式**（B8 新增）。
#:
#: - ``"dense"``：纯稠密单路 —— 与接入混合检索之前**完全一致**；
#: - ``"hybrid"``：稠密 + 词法（FTS5）双路，加权 RRF 融合；
#: - ``"sparse"``：纯词法单路（只用于消融对比，不建议线上用）。
#:
#: **默认使用 ``hybrid``：正式聊天链路与检索 API 采用同一召回策略，避免线上入口和调试入口出现两套默认行为。**
#:
#: dense 仍可通过 ``RAG_CHAT_RETRIEVAL_MODE=dense`` 显式选择，用于故障隔离和评测对照。
#:
#:
#: hybrid 的线上收益与代价以 B 轨评测和 F6 延迟报告为准；口语化查询仍需独立改写。
#: 显式设置 ``RAG_CHAT_RETRIEVAL_MODE=dense`` 仅用于故障隔离和离线对照。
#: 生效索引缺少稀疏路时显式报错，不静默退回 dense。
DEFAULT_CHAT_RETRIEVAL_MODE = "hybrid"

#: 合法召回模式。非法值同样回落默认值而不是抛错（与轨道开关同一理由）。
CHAT_RETRIEVAL_MODES = ("dense", "hybrid", "sparse")


def resolve_chat_retrieval_mode() -> str:
    """运行时解析聊天链路的召回模式（环境变量优先，非法值回落默认）。"""

    value = (os.getenv("RAG_CHAT_RETRIEVAL_MODE") or "").strip().lower()
    return value if value in CHAT_RETRIEVAL_MODES else DEFAULT_CHAT_RETRIEVAL_MODE


def adapt_chunk_items_for_prompt(chunk_items: list[dict]) -> list[dict]:
    """把 B 轨的 chunk 命中映射成 prompt 组装层认得的条目形状。

    **为什么要有这一层**：``utils.rag_context.build_prompt_context_items`` 只认
    ``answer`` 字段，取不到就**跳过该条**（那是有意的守卫：空证据不得进 prompt）。
    而 chunk 命中的正文在 ``text`` 里。两种修法里选了"检索侧适配"而不是
    "让下游兼容两套字段" —— 后者会把那道判空守卫改松，等于用降低契约强度换兼容。

    **``intent`` 的取舍**：文档 chunk 没有"业务意图"这个维度（那是种子 FAQ 才有的
    业务字段）。这里用**标题路径的末级**（最具体的章节名）充当即刻主题标识，
    而不是留空 —— 留空会让 ``_build_display_title`` 渲染成「优先按 unknown 回答」，
    在面向用户的证据列表里是明显的退化。
    """

    adapted: list[dict] = []
    for index, item in enumerate(chunk_items, start=1):
        heading_path = [str(part) for part in (item.get("heading_path") or [])]
        document_title = str(item.get("document_title") or "").strip()
        heading_text = " > ".join(heading_path)
        adapted.append(
            {
                "rank": int(item.get("rank", index)),
                # 自证来源：chunk 级 id 优先，退化到文档 id（3.4 的要求）
                "knowledge_id": str(item.get("chunk_id") or item.get("document_id") or ""),
                "title": document_title or heading_text or str(item.get("chunk_id", "")),
                "version": str(item.get("document_version", "")),
                "updated_at": "",
                "source": str(item.get("source_uri") or item.get("filename") or ""),
                "answer": str(item.get("text", "")),
                "category": str(item.get("source_type", "")),
                "intent": heading_path[-1] if heading_path else document_title,
                "question": heading_text,
                "score": float(item.get("score", 0.0)),
                "rerank_score": float(item.get("score", 0.0)),
                "retrieval_origin": str(item.get("retrieval_origin", "chunk-index")),
                # B 轨独有、A 轨没有的溯源字段：透出来供 trace 与证据引用使用
                "chunk_id": str(item.get("chunk_id", "")),
                "document_id": str(item.get("document_id", "")),
                "heading_path": heading_path,
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
            }
        )
    return adapted


def _chunk_index_root():
    """B 轨索引根目录（惰性 import；测试通过替换本函数注入临时索引）。"""

    from services.ingestion.pipeline import default_index_root

    return default_index_root()


def _chunk_embedder(text: str):
    """B 轨查询向量化（惰性 import：没装模型的机器不该因为 import 就变脆）。"""

    from services.ingestion.pipeline import default_embedder

    return default_embedder(text)


def _chunk_embedding_model() -> str:
    from services.ingestion.pipeline import default_embedding_model

    return default_embedding_model()


def _chunk_index_name() -> str:
    from utils.vector_retriever import CHUNK_INDEX_NAME

    return CHUNK_INDEX_NAME


def describe_chat_retrieval_source() -> dict:
    """返回聊天本次使用的正式 chunk 来源元数据。

    元数据探测失败不改变检索结果，只将版本标为未知，避免观测逻辑反过来
    影响正式回答链路。
    """
    started_at = time.perf_counter()
    metadata = {
        "retrieval_path": "chunk-index",
        "data_source": "document_chunks",
        "index_name": _chunk_index_name(),
        "index_version": None,
        "embedding_model": "",
        "sparse_available": False,
        "index_load_ms": 0.0,
    }
    try:
        from utils.vector_retriever import describe_chunk_index

        info = describe_chunk_index(root=_chunk_index_root(), index_name=_chunk_index_name())
        metadata["index_name"] = str(info.get("index_name") or metadata["index_name"])
        metadata["index_version"] = int(info["index_version"])
        metadata["embedding_model"] = str(info.get("embedding_model") or "")
        metadata["sparse_available"] = bool(info.get("sparse_available", False))
    except Exception:
        pass
    metadata["index_load_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
    return metadata


def retrieve_chunk_items_for_chat(query: str, auth, limit: int = 3) -> list[dict]:
    """B 轨取数：服务端构造 access 过滤（**fail closed**）后查 chunk 索引。

    ``auth`` 为 ``None`` 时**直接返回空**，而不是回退到没有权限过滤的 A 轨 ——
    "拿不到身份"与"没有权限"都必须是零命中，不能变成"换个数据源把答案答出来"。
    """

    if auth is None:
        return []

    from services.ingestion.db import session_scope
    from services.retrieval_access import build_chunk_access_filter

    mode = resolve_chat_retrieval_mode()

    with session_scope() as session:
        access = build_chunk_access_filter(session, auth)
        kwargs = {
            "access": access,
            "limit": limit,
            "embedder": _chunk_embedder,
            "embedding_model": _chunk_embedding_model(),
            "root": _chunk_index_root(),
            "index_name": _chunk_index_name(),
        }
        if mode == "dense":
            from utils.vector_retriever import retrieve_chunk_items

            return retrieve_chunk_items(query, **kwargs)

        # 混合 / 纯词法：走同一份生产融合实现（不是评测脚本里的另一套）。
        # 稀疏索引缺席时它会显式报错 —— 不在这里兜底退回稠密，
        # 否则"请求了 hybrid、实际是 dense"会静默发生。
        from utils.hybrid_retriever import retrieve_hybrid_items

        return retrieve_hybrid_items(query, mode=mode, **kwargs)


def retrieve_chat_items(query: str, auth=None, limit: int = 3) -> list[dict]:
    """聊天链路的证据获取入口：按当前轨道分发，形状统一成下游认得的条目。

    这是 F1 的**唯一分发点**。两条轨道的差异全部收敛在这个函数里：
    调用方（``get_answer_from_rag``）与下游（``build_prompt_context_items``）
    都不需要知道走的是哪条 —— 所以"切轨"在代码上是改一个默认值，
    而不是改一条调用链。
    """

    return adapt_chunk_items_for_prompt(retrieve_chunk_items_for_chat(query, auth, limit))
SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
ANSWER_PLAN_SYSTEM_PROMPT = (
    "你是外卖平台客服回答规划助手。你的任务不是直接回复用户，"
    "而是根据用户问题和参考资料，输出一个稳定的 JSON 回答计划。"
    "不要输出客服口吻，不要补充解释，不要输出 Markdown。"
    "如果资料不足，请在字段中保守表达，并把 needs_caution 设为 true。"
)
FALLBACK_REPLY = (
    "抱歉，这个问题我暂时无法稳定判断。建议您先在订单页面查看最新状态，"
    "如仍有疑问，请通过官方客服渠道进一步核实处理。"
)
logger = logging.getLogger(__name__)

tokenizer = None
model = None


def load_local_model() -> tuple[object, object]:
    global tokenizer, model
    if tokenizer is not None and model is not None:
        return tokenizer, model
    if torch is None or AutoModelForCausalLM is None or AutoTokenizer is None:
        raise RuntimeError("local generation dependencies are unavailable")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    model_kwargs = {"local_files_only": True}
    if MODEL_DTYPE is not None:
        model_kwargs["dtype"] = MODEL_DTYPE
    loaded_model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, **model_kwargs)
    if PeftModel is not None and ADAPTER_PATH.exists() and (ADAPTER_PATH / "adapter_config.json").exists():
        loaded_model = PeftModel.from_pretrained(loaded_model, ADAPTER_PATH, local_files_only=True)
    loaded_model = loaded_model.to(DEVICE)
    loaded_model.eval()
    model = loaded_model
    return tokenizer, model


def load_local_tokenizer() -> object | None:
    global tokenizer
    if tokenizer is not None:
        return tokenizer
    if AutoTokenizer is None:
        return None
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    except Exception as error:
        logger.warning("local tokenizer unavailable: %s", error)
        return None
    return tokenizer


def normalize_chat_request(request) -> dict:
    if isinstance(request, str):
        return {
            "message": request,
            "user_id": "demo_user",
            "session_id": None,
            "order_id": None,
            "channel": "test",
        }
    return {
        "message": request.message,
        "user_id": getattr(request, "user_id", "demo_user") or "demo_user",
        "session_id": getattr(request, "session_id", None),
        "order_id": getattr(request, "order_id", None),
        "channel": getattr(request, "channel", "test") or "test",
    }


def preprocess_retrieval_query(query: str, intent_analysis: dict, context: dict | None = None) -> dict:
    """统一查询解析接缝，返回可序列化的 QueryResolutionPlan。"""

    return resolve_query(query, context=context, intent_analysis=intent_analysis).to_dict()


def retrieve_with_query_plan(plan: dict, auth=None, limit: int = 3) -> tuple[list[dict], list[dict]]:
    """对每个查询/子问题独立召回，返回合并证据和覆盖诊断。"""

    sub_queries = list(plan.get("sub_queries") or [])
    if sub_queries:
        work = [(str(item.get("sub_query_id") or f"q{i}"), str(item.get("query") or ""), bool(item.get("required", True))) for i, item in enumerate(sub_queries, 1)]
    else:
        queries = list(plan.get("retrieval_queries") or [plan.get("original_query", "")])
        work = [("main", query, True) for query in queries[:4]]

    merged: dict[str, dict] = {}
    coverage: list[dict] = []
    for subquery_id, query, required in work:
        if not query:
            continue
        items = retrieve_chat_items(query, auth, limit)
        evidence_ids: list[str] = []
        for item in items:
            key = str(item.get("chunk_id") or item.get("knowledge_id") or item.get("document_id") or item.get("answer", ""))
            if not key:
                continue
            evidence_ids.append(key)
            previous = merged.get(key)
            if previous is None or float(item.get("score") or 0.0) > float(previous.get("score") or 0.0):
                merged[key] = item
        coverage.append({
            "sub_query_id": subquery_id,
            "query": query,
            "status": "covered" if evidence_ids else "missing",
            "evidence_ids": evidence_ids[:limit],
            "evidence_count": len(evidence_ids),
            "required": required,
        })

    results = list(merged.values())
    results.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    for rank, item in enumerate(results[:limit], start=1):
        item["rank"] = rank
    return results[:limit], coverage

def decide_evidence_gate(
    subquery_coverage: list[dict],
    *,
    evidence_count: int,
    risk_level: str = "low",
) -> dict:
    """把每跳覆盖转换成回答模式，禁止缺证据时生成完整确定性结论。"""

    if not subquery_coverage:
        if evidence_count:
            return {"mode": "complete", "missing_subqueries": []}
        return {"mode": "clarify", "missing_subqueries": ["main"]}
    missing = [
        str(item.get("sub_query_id"))
        for item in subquery_coverage
        if item.get("status") != "covered" and item.get("required", True)
    ]
    if not missing:
        return {"mode": "complete", "missing_subqueries": []}
    if risk_level in {"high", "critical"}:
        mode = "human_review"
    elif len(missing) == len(subquery_coverage):
        mode = "clarify"
    else:
        mode = "partial"
    return {"mode": mode, "missing_subqueries": missing}


def build_query_with_intent_hint(query: str, intent_analysis: dict) -> str:
    primary_intent = intent_analysis.get("primary_intent", "")
    secondary_intents = intent_analysis.get("secondary_intents", [])
    intent_parts = [primary_intent, *secondary_intents]
    intent_text = " ".join(intent for intent in intent_parts if intent)
    if not intent_text:
        return query
    return f"{query}\n意图提示：{intent_text}"


def attach_runtime_fields(
    result: dict,
    context: dict,
    intent_analysis: dict,
    context_used: dict,
    safety_status: dict,
) -> dict:
    result["session_id"] = context.get("session_id", "")
    result["user_id"] = context.get("user_id", "demo_user")
    result["order_id"] = context.get("order_id")
    result["intent_analysis"] = intent_analysis
    result["context_used"] = context_used
    result["safety_status"] = safety_status
    trace = result.setdefault("trace", {})
    trace["user_id"] = result["user_id"]
    trace["session_id"] = result["session_id"]
    trace["order_id"] = result["order_id"]
    trace["intent_analysis"] = intent_analysis
    trace["safety_status"] = safety_status
    return result


def build_chat_template_text(prompt: str, system_prompt: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    local_tokenizer = load_local_tokenizer()
    if local_tokenizer is None:
        return f"{system_prompt}\n\n{prompt}"
    return local_tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def _count_input_ids(input_ids) -> int:
    if hasattr(input_ids, "shape"):
        return int(input_ids.shape[-1])
    if isinstance(input_ids, list):
        if input_ids and isinstance(input_ids[0], list):
            return len(input_ids[0])
        return len(input_ids)
    return 0


def count_text_tokens(text: str) -> int:
    if not text:
        return 0
    local_tokenizer = load_local_tokenizer()
    if local_tokenizer is None:
        return max(1, round(len(text) / 4))
    try:
        tokenized = local_tokenizer(text, return_tensors="pt", add_special_tokens=False)
    except TypeError:
        tokenized = local_tokenizer(text, return_tensors="pt")
    return _count_input_ids(tokenized["input_ids"])


def build_token_usage(
    provider: str,
    model_name: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    counting_method: str,
) -> dict:
    prompt_count = int(prompt_tokens or 0)
    completion_count = int(completion_tokens or 0)
    return {
        "provider": provider,
        "model": model_name,
        "prompt_tokens": prompt_count,
        "completion_tokens": completion_count,
        "total_tokens": prompt_count + completion_count,
        "counting_method": counting_method,
    }


def estimate_token_usage(
    prompt: str,
    system_prompt: str,
    reply: str,
    provider: str,
    model_name: str,
    counting_method: str = "estimated_local_tokenizer",
) -> dict:
    prompt_tokens = count_text_tokens(build_chat_template_text(prompt, system_prompt))
    completion_tokens = count_text_tokens(reply)
    return build_token_usage(
        provider=provider,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        counting_method=counting_method,
    )


def generate_online_text_with_system_prompt(prompt: str, system_prompt: str) -> str:
    config = get_rag_config()
    return generate_online_chat_completion(
        prompt=prompt,
        system_prompt=system_prompt,
        model_name=config.online_model_name,
        api_base_url=config.online_api_base_url,
        api_key_env=config.online_api_key_env,
    )


def generate_online_text_result_with_system_prompt(prompt: str, system_prompt: str) -> dict:
    config = get_rag_config()
    result = generate_online_chat_completion_with_usage(
        prompt=prompt,
        system_prompt=system_prompt,
        model_name=config.online_model_name,
        api_base_url=config.online_api_base_url,
        api_key_env=config.online_api_key_env,
    )
    text = result["text"]
    usage = result.get("usage") or {}
    if all(usage.get(key) is not None for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
        token_usage = {
            "provider": "online",
            "model": config.online_model_name,
            "prompt_tokens": int(usage["prompt_tokens"]),
            "completion_tokens": int(usage["completion_tokens"]),
            "total_tokens": int(usage["total_tokens"]),
            "counting_method": "api_usage",
        }
    else:
        token_usage = estimate_token_usage(
            prompt=prompt,
            system_prompt=system_prompt,
            reply=text,
            provider="online",
            model_name=config.online_model_name,
        )
    return {"text": text, "token_usage": token_usage}


def generate_local_text_with_system_prompt(prompt: str, system_prompt: str) -> str:
    return generate_local_text_result_with_system_prompt(prompt, system_prompt)["text"]


def generate_local_text_result_with_system_prompt(prompt: str, system_prompt: str) -> dict:
    local_tokenizer, local_model = load_local_model()
    text = build_chat_template_text(prompt, system_prompt)
    inputs = local_tokenizer(text, return_tensors="pt")
    inputs = {
        key: value.to(DEVICE) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }
    input_length = _count_input_ids(inputs["input_ids"])
    outputs = local_model.generate(
        **inputs,
        max_new_tokens=256,
        do_sample=False,
        pad_token_id=local_tokenizer.eos_token_id,
    )
    completion_ids = outputs[0][input_length:]
    completion_tokens = _count_input_ids(completion_ids)
    reply = local_tokenizer.decode(completion_ids, skip_special_tokens=True).strip()
    return {
        "text": reply,
        "token_usage": build_token_usage(
            provider="local",
            model_name=MODEL_PATH.name,
            prompt_tokens=int(input_length),
            completion_tokens=completion_tokens,
            counting_method="local_tokenizer",
        ),
    }


def generate_text_result_with_system_prompt(prompt: str, system_prompt: str) -> dict:
    provider = get_rag_config().generation_provider
    if provider == "online":
        return generate_online_text_result_with_system_prompt(prompt, system_prompt)
    if provider == "local":
        return generate_local_text_result_with_system_prompt(prompt, system_prompt)
    raise ValueError(f"Unsupported generation_provider: {provider}")


def generate_text_with_system_prompt(prompt: str, system_prompt: str) -> str:
    provider = get_rag_config().generation_provider
    if provider == "online":
        return generate_online_text_with_system_prompt(prompt, system_prompt)
    if provider == "local":
        return generate_local_text_with_system_prompt(prompt, system_prompt)
    raise ValueError(f"Unsupported generation_provider: {provider}")


def generate_reply(prompt: str) -> str:
    return generate_text_with_system_prompt(prompt, get_active_prompt_config()["system_prompt"])


def generate_reply_with_usage(prompt: str, system_prompt: str | None = None) -> dict:
    return generate_text_result_with_system_prompt(
        prompt,
        system_prompt or get_active_prompt_config()["system_prompt"],
    )


def generate_answer_plan(prompt: str) -> str:
    return generate_text_with_system_prompt(prompt, ANSWER_PLAN_SYSTEM_PROMPT)


def generate_local_answer_plan(prompt: str) -> str:
    return generate_local_text_with_system_prompt(prompt, ANSWER_PLAN_SYSTEM_PROMPT)


def build_fallback_prompt(
    query: str,
    order_context: dict | None = None,
    user_memory: dict | None = None,
) -> str:
    order_context = order_context or {}
    user_memory = user_memory or {}
    order_lines = "\n".join(f"- {key}: {value}" for key, value in order_context.items()) or "- 无"
    memory_lines = "\n".join(f"- {key}: {value}" for key, value in user_memory.items()) or "- 无"
    return (
        "没有检索到完全匹配的知识库材料。请只基于外卖客服通用处理原则回答，"
        "不要编造具体平台政策；如果需要平台核实，请建议用户在订单页或官方客服渠道处理。\n\n"
        f"订单工具结果：\n{order_lines}\n\n"
        f"长期用户记忆（user_memory，优先级低于订单状态）：\n{memory_lines}\n\n"
        f"用户问题：{query}"
    )


def build_trace(
    retrieval_count: int,
    retrieval_trace: dict | None,
    used_fallback_prompt: bool,
    answer_composer_applied: bool,
    answer_composer_trace: dict | None,
    reply_rules_applied: bool,
    reply_rules_trace: dict | None,
    answer_source: str,
    degraded: bool,
    failure_stage: str,
    fallback_reason: str,
    request_id: str = "",
    latency_ms: float = 0.0,
    top1_intent: str = "",
) -> dict:
    config = get_rag_config()
    return {
        "retrieval_count": retrieval_count,
        "retrieval_trace": retrieval_trace or {},
        "used_fallback_prompt": used_fallback_prompt,
        "generation_provider": config.generation_provider,
        "online_model_name": config.online_model_name,
        "answer_composer_applied": answer_composer_applied,
        "answer_composer_trace": answer_composer_trace or {},
        "reply_rules_applied": reply_rules_applied,
        "reply_rules_trace": reply_rules_trace or {},
        "answer_source": answer_source,
        "degraded": degraded,
        "failure_stage": failure_stage,
        "fallback_reason": fallback_reason,
        "request_id": request_id,
        "latency_ms": latency_ms,
        "top1_intent": top1_intent,
    }


def build_retrieval_trace(query: str, retrieved_items: list[dict]) -> dict:
    intent_hint = detect_intent_hint(query)
    hint_supplement_count = sum(
        1
        for item in retrieved_items
        if item.get("retrieval_origin") == "intent_hint_supplement"
    )
    hint_match_count = sum(
        1
        for item in retrieved_items
        if intent_hint and item.get("intent") == intent_hint
    )
    return {
        "intent_hint": intent_hint,
        "intent_hint_applied": bool(intent_hint),
        "hint_supplement_added": hint_supplement_count > 0,
        "hint_supplement_count": hint_supplement_count,
        "hint_match_count": hint_match_count,
    }


def attach_grounding_diagnostics(result: dict, query: str) -> dict:
    diagnostics = build_chat_grounding_diagnostics(
        query=query,
        reply=result["reply"],
        retrieved_documents=result.get("retrieved_documents", []),
        retrieved_items=result.get("retrieved_items", []),
        prompt_context_items=result.get("prompt_context_items", []),
        final_prompt=result.get("final_prompt", ""),
    )
    for field in (
        "expected_intent",
        "expected_evidence_keywords",
        "matched_evidence_keywords",
        "missing_evidence_keywords",
        "forbidden_keywords",
        "forbidden_keyword_hits",
        "used_primary_evidence",
        "mixed_supporting_intent",
        "risky_promises",
        "needs_manual_review",
        "manual_judgment",
        "issue_type",
        "suggested_layer",
        "evaluation_metrics",
    ):
        result[field] = diagnostics.get(field)
    return result


def build_top1_intent(retrieved_items: list[dict]) -> str:
    if not retrieved_items:
        return ""
    return str(retrieved_items[0].get("intent", ""))


def elapsed_ms(started_at: float) -> float:
    return max(round((time.perf_counter() - started_at) * 1000, 2), 0.01)


def trace_step(
    step: str,
    status: str = "success",
    input_summary: str = "",
    output_summary: str = "",
    started_at: float | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "step": step,
        "status": status,
        "input_summary": mask_sensitive_text(input_summary)[:500],
        "output_summary": mask_sensitive_text(output_summary)[:500],
        "latency_ms": elapsed_ms(started_at) if started_at else 0.0,
        "metadata": metadata or {},
    }


def summarize_tool_output(tool_result: dict) -> str:
    output = tool_result.get("output") or {}
    return str(output.get("summary") or output.get("status_label") or tool_result.get("error_type") or "")


def build_order_context(tool_results: list[dict]) -> dict[str, str]:
    context = {}
    for result in tool_results:
        output = result.get("output") or {}
        if result.get("tool_name") == "query_order_status" and output:
            context["order_status"] = str(output.get("status_label", ""))
            context["order_status_summary"] = str(output.get("summary", ""))
        if result.get("tool_name") == "query_refund_status" and output:
            context["refund_status"] = str(output.get("refund_status", ""))
            context["refund_summary"] = str(output.get("summary", ""))
    return {key: value for key, value in context.items() if value}


def build_evidence_citations(prompt_context_items: list) -> list[dict]:
    citations = []
    for index, item in enumerate(prompt_context_items):
        rank = getattr(item, "rank", index + 1)
        knowledge_id = getattr(item, "knowledge_id", "") or f"kb_rank_{rank}"
        score = getattr(item, "rerank_score", 0.0) or getattr(item, "score", 0.0)
        quote = getattr(item, "evidence_summary", "") or getattr(item, "answer", "")
        citations.append(
            {
                "evidence_id": knowledge_id,
                "knowledge_id": knowledge_id,
                "source_type": "knowledge_base",
                "source": getattr(item, "source", ""),
                "category": getattr(item, "category", ""),
                "intent": getattr(item, "intent", ""),
                "risk_level": "unknown",
                "version": getattr(item, "version", ""),
                "updated_at": getattr(item, "updated_at", ""),
                "score": score,
                "evidence_role": getattr(item, "role", "supporting"),
                "quote": quote,
                "title": getattr(item, "title", "") or getattr(item, "display_title", ""),
                "chunk_id": getattr(item, "chunk_id", ""),
                "document_id": getattr(item, "document_id", ""),
                "heading_path": list(getattr(item, "heading_path", ()) or ()),
                "page_start": getattr(item, "page_start", None),
                "page_end": getattr(item, "page_end", None),
            }
        )
    return citations


def validate_evidence_citations(citations: list[dict], evidence_items: list[dict]) -> dict:
    """校验 citation 是否来自本次返回的证据集合。"""
    evidence_ids = {str(item.get("knowledge_id") or item.get("chunk_id") or "") for item in evidence_items}
    missing = []
    invalid = []
    for citation in citations:
        evidence_id = str(citation.get("evidence_id") or citation.get("knowledge_id") or "")
        if not evidence_id or not str(citation.get("quote") or "").strip():
            missing.append(evidence_id or "<empty>")
        elif evidence_id not in evidence_ids:
            invalid.append(evidence_id)
    return {
        "citation_count": len(citations),
        "missing_count": len(missing),
        "invalid_source_count": len(invalid),
        "missing_ids": missing,
        "invalid_source_ids": invalid,
        "passed": not missing and not invalid,
    }


def evidence_citations_from_result(result: dict) -> list[dict]:
    class ItemAdapter:
        def __init__(self, data: dict):
            self.__dict__.update(data)

    return build_evidence_citations(
        [ItemAdapter(item) for item in result.get("prompt_context_items", [])]
    )


def normalize_prd_risk_level(risk_level: str) -> str:
    if risk_level == "critical":
        return "blocked"
    if risk_level in {"low", "medium", "high", "blocked"}:
        return risk_level
    return "low"


def build_confidence_level(confidence_score: float) -> str:
    if confidence_score >= 0.8:
        return "high"
    if confidence_score >= 0.5:
        return "medium"
    return "low"


def build_prd_citations(evidence_citations: list[dict]) -> list[dict]:
    citations = []
    for item in evidence_citations:
        citations.append(
            {
                "knowledge_id": item.get("knowledge_id", "") or item.get("evidence_id", ""),
                "title": item.get("title", ""),
                "category": item.get("category", ""),
                "version": item.get("version", ""),
                "snippet": item.get("quote", ""),
                "score": item.get("score", 0.0),
                "updated_at": item.get("updated_at", ""),
                "source": item.get("source", ""),
                "chunk_id": item.get("chunk_id", ""),
                "document_id": item.get("document_id", ""),
                "heading_path": item.get("heading_path", []),
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
            }
        )
    return citations


def build_human_review_reason(
    result: dict,
    safety_status: dict,
    handoff_ticket: dict | None,
) -> str:
    if handoff_ticket:
        return str(handoff_ticket.get("reason") or "建议创建人工接管工单")
    handoff_recommendation = result.get("handoff_recommendation") or {}
    if handoff_recommendation.get("recommended"):
        return str(handoff_recommendation.get("reason") or "建议客服确认后转人工")
    if safety_status.get("blocked"):
        return "命中安全规则，需要客服确认"
    if result.get("needs_manual_review"):
        return "诊断结果建议人工复核"
    return "v1 默认客服确认后发送"


def build_answer_basis(evidence_citations: list[dict], tool_results: list[dict], safety_status: dict) -> str:
    parts = []
    primary = next(
        (item for item in evidence_citations if item.get("evidence_role") == "primary"),
        None,
    )
    if primary:
        parts.append(f"主证据：{primary.get('intent') or primary.get('category')}")
    tool_summaries = [summarize_tool_output(result) for result in tool_results if summarize_tool_output(result)]
    if tool_summaries:
        parts.append(f"工具结果：{'；'.join(tool_summaries[:2])}")
    if safety_status.get("fallback_applied"):
        parts.append("已触发安全规则兜底")
    return "；".join(parts)


def build_memory_snapshot(context: dict, user_memory: dict, updated_user_memory: dict | None = None) -> dict:
    active_memory = updated_user_memory or user_memory
    return {
        "short_term": {
            "session_id": context.get("session_id", ""),
            "summary": context.get("summary", ""),
            "facts": context.get("facts", {}),
            "recent_messages": context.get("recent_messages", []),
        },
        "long_term": {
            "used": bool(user_memory),
            "fields": user_memory,
            "updated_fields": {
                key: value
                for key, value in active_memory.items()
                if user_memory.get(key) != value
            },
            "priority_note": "user_memory 只作客服提示，低于订单状态和知识库证据。",
        },
    }


def build_decision_trace(
    request_id: str,
    intent_analysis: dict,
    evidence_citations: list[dict],
    tool_results: list[dict],
    safety_status: dict,
    needs_manual_review: bool,
    handoff_ticket: dict | None,
) -> dict:
    handoff_ticket = handoff_ticket or {}
    return {
        "request_id": request_id,
        "primary_intent": intent_analysis.get("primary_intent", ""),
        "risk_level": intent_analysis.get("risk_level", "low"),
        "routing": intent_analysis.get("routing", "rag"),
        "primary_evidence_id": next(
            (item["evidence_id"] for item in evidence_citations if item.get("evidence_role") == "primary"),
            "",
        ),
        "tool_statuses": {
            result.get("tool_name", ""): result.get("status", "")
            for result in tool_results
        },
        "safety_status": safety_status,
        "needs_manual_review": needs_manual_review,
        "handoff_ticket_id": handoff_ticket.get("ticket_id") or handoff_ticket.get("output", {}).get("ticket_id", ""),
    }


def should_handoff(
    intent_analysis: dict,
    evidence_citations: list[dict],
    tool_results: list[dict],
    safety_status: dict,
    diagnostics_needs_review: bool,
) -> tuple[bool, str]:
    risk_level = intent_analysis.get("risk_level", "low")
    high_risk = risk_level in {"high", "critical"}
    has_primary_evidence = any(item.get("evidence_role") == "primary" for item in evidence_citations)
    failed_tool = any(
        result.get("status") == "failed" and not result.get("retryable", False)
        for result in tool_results
    )
    if high_risk and not has_primary_evidence:
        return True, "高风险场景缺少主证据"
    if failed_tool and not has_primary_evidence:
        return True, "订单工具失败且证据不足"
    if safety_status.get("blocked"):
        return True, "回复命中安全拦截规则"
    if diagnostics_needs_review:
        return True, "grounding 检查建议人工复核"
    return False, ""


def attach_enhanced_fields(
    result: dict,
    request_id: str,
    context: dict,
    user_memory: dict,
    updated_user_memory: dict | None,
    intent_analysis: dict,
    tool_results: list[dict],
    safety_status: dict,
    full_trace: list[dict],
    handoff_ticket: dict | None = None,
) -> dict:
    evidence_citations = evidence_citations_from_result(result)
    confidence_score = float(result.get("confidence_score") or 0.0)
    risk_level = normalize_prd_risk_level(intent_analysis.get("risk_level", "low"))
    human_review_reason = build_human_review_reason(result, safety_status, handoff_ticket)
    result["evidence_citations"] = evidence_citations
    result["citation_quality"] = validate_evidence_citations(
        evidence_citations, result.get("prompt_context_items", [])
    )
    result["request_id"] = request_id
    result["risk_level"] = risk_level
    result["confidence_level"] = build_confidence_level(confidence_score)
    manual_review = bool(result.get("needs_manual_review", True))
    result["need_human_review"] = manual_review
    result["needs_manual_review"] = manual_review
    result["human_review_reason"] = human_review_reason
    result["citations"] = build_prd_citations(evidence_citations)
    result.setdefault("conversation_status", "pending_agent_review")
    result["tool_results"] = tool_results
    result["memory_snapshot"] = build_memory_snapshot(context, user_memory, updated_user_memory)
    result["answer_basis"] = build_answer_basis(evidence_citations, tool_results, safety_status)
    result["handoff_ticket"] = handoff_ticket
    result["decision_trace"] = build_decision_trace(
        request_id=request_id,
        intent_analysis=intent_analysis,
        evidence_citations=evidence_citations,
        tool_results=tool_results,
        safety_status=safety_status,
        needs_manual_review=bool(result.get("needs_manual_review")),
        handoff_ticket=handoff_ticket,
    )
    result["full_trace"] = full_trace
    return result


def complete_chat_response(
    result: dict,
    query: str,
    request_id: str,
    context: dict,
    user_memory: dict,
    intent_analysis: dict,
    tool_results: list[dict],
    safety_status: dict,
    full_trace: list[dict],
) -> dict:
    grounding_started_at = time.perf_counter()
    result = finalize_chat_result(result, query)
    result.setdefault("trace", {}).update({
        "retrieval_path": result.get("retrieval_path", "chunk-index"),
        "data_source": result.get("data_source", "document_chunks"),
        "index_name": result.get("index_name", ""),
        "index_version": result.get("index_version"),
    })
    full_trace.append(
        trace_step(
            "grounding_checked",
            output_summary=f"needs_manual_review={bool(result.get('needs_manual_review'))}",
            started_at=grounding_started_at,
            metadata={
                "missing_evidence_keywords": result.get("missing_evidence_keywords", []),
                "risky_promises": result.get("risky_promises", []),
            },
        )
    )
    evidence_citations = evidence_citations_from_result(result)
    handoff_needed, handoff_reason = should_handoff(
        intent_analysis=intent_analysis,
        evidence_citations=evidence_citations,
        tool_results=tool_results,
        safety_status=safety_status,
        diagnostics_needs_review=bool(result.get("needs_manual_review")),
    )
    answer_mode = str(result.get("answer_mode") or "complete")
    handoff_ticket = None
    if answer_mode == "clarify":
        missing = result.get("decision_trace", {}).get("missing_subqueries") or result.get("missing_subqueries") or []
        missing_text = "、".join(str(item) for item in missing if item) or "主要问题"
        result["reply"] = f"为了准确处理你的请求，还需要你补充或确认：{missing_text}。请提供相关订单号、时间或具体问题。"
        result["conversation_status"] = "awaiting_clarification"
        result["human_review_reason"] = "信息不足，等待用户澄清"
        result["needs_manual_review"] = False
        full_trace.append(trace_step("clarification_requested", output_summary="user_clarification_required", started_at=grounding_started_at, metadata={"missing_subqueries": missing}))
    elif answer_mode == "human_review" or handoff_needed:
        handoff_reason = handoff_reason or "当前请求需要人工处理"
        ticket_result = create_handoff_ticket(
            handoff_reason,
            {
                "user_id": context.get("user_id", "demo_user"),
                "session_id": context.get("session_id", ""),
                "order_id": context.get("order_id"),
                "summary": context.get("summary", ""),
                "facts": context.get("facts", {}),
            },
        )
        handoff_ticket = ticket_result.get("output") or {}
        result["reply"] = f"你的问题已转交人工客服处理。工单号：{handoff_ticket.get('ticket_id', '处理中')}。人工客服会根据会话记录继续跟进。"
        result["conversation_status"] = "human_handoff"
        result["needs_manual_review"] = True
        result["handoff_recommendation"] = {
            "recommended": True,
            "reason": handoff_reason,
            "priority": "high" if intent_analysis.get("risk_level") in {"high", "critical"} else "normal",
        }
        full_trace.append(trace_step("human_handoff_created", input_summary=handoff_reason, output_summary=handoff_ticket.get("ticket_id", "created"), started_at=grounding_started_at, metadata={"write_tool_deferred": False}))

    memory_update_started_at = time.perf_counter()
    updated_user_memory = update_user_memory_from_turn(
        user_id=context.get("user_id", "demo_user"),
        query=query,
        reply=result.get("reply", ""),
        intent_analysis=intent_analysis,
    )
    full_trace.append(
        trace_step(
            "memory_updated",
            output_summary=f"short_term=updated,long_term_fields={len(updated_user_memory)}",
            started_at=memory_update_started_at,
        )
    )
    response_started_at = time.perf_counter()
    final_result = attach_enhanced_fields(
        result=result,
        request_id=request_id,
        context=context,
        user_memory=user_memory,
        updated_user_memory=updated_user_memory,
        intent_analysis=intent_analysis,
        tool_results=tool_results,
        safety_status=safety_status,
        full_trace=full_trace,
        handoff_ticket=handoff_ticket,
    )
    conversation_store.save_turn_response(
        request_id=request_id,
        session_id=context.get("session_id", ""),
        user_id=context.get("user_id", "demo_user"),
        order_id=context.get("order_id"),
        query=query,
        reply=final_result.get("reply", ""),
        response=final_result,
    )
    conversation_store.set_conversation_status(
        context.get("session_id", ""),
        final_result.get("conversation_status", "pending_agent_review"),
    )
    full_trace.append(trace_step("response_returned", output_summary="response assembled", started_at=response_started_at))
    return final_result


def log_chat_trace(query: str, trace: dict) -> None:
    safe_query = mask_sensitive_text(query)
    logger.info(
        (
            "chat_request request_id=%s query=%r top1_intent=%s answer_source=%s "
            "degraded=%s failure_stage=%s latency_ms=%s"
        ),
        trace.get("request_id", ""),
        safe_query,
        trace.get("top1_intent", ""),
        trace.get("answer_source", ""),
        trace.get("degraded", False),
        trace.get("failure_stage", ""),
        trace.get("latency_ms", 0.0),
        extra={
            "request_id": trace.get("request_id", ""),
            "query": safe_query,
            "top1_intent": trace.get("top1_intent", ""),
            "answer_source": trace.get("answer_source", ""),
            "degraded": trace.get("degraded", False),
            "failure_stage": trace.get("failure_stage", ""),
            "latency_ms": trace.get("latency_ms", 0.0),
        },
    )


def finalize_chat_result(result: dict, query: str) -> dict:
    trace = result.get("trace", {})
    record_chat_metrics(trace)
    record_token_usage(result.get("token_usage", {}))
    save_chat_session(
        query=query,
        reply=result.get("reply", ""),
        trace=trace,
        token_usage=result.get("token_usage", {}),
    )
    log_chat_trace(query, result.get("trace", {}))
    return attach_grounding_diagnostics(result, query)


def get_answer_from_rag(request, auth=None):
    """问答主入口。

    ``auth`` 是切轨（F1）新增的：B 轨（chunk 索引）的权限过滤必须由**服务端身份**
    构造，所以身份要一路传到检索层。缺省 ``None`` 仅用于向后兼容的调用方 ——
    此时若轨道是 B 轨，检索结果是**零命中**（fail closed），
    不会退回没有权限过滤的 A 轨。
    """

    request_data = normalize_chat_request(request)
    query = request_data["message"]
    request_id = uuid4().hex
    started_at = time.perf_counter()
    full_trace = [
        trace_step(
            "request_received",
            input_summary=mask_sensitive_text(query)[:160],
            started_at=started_at,
            metadata={
                "request_id": request_id,
                "channel": request_data["channel"],
                "user_id": request_data["user_id"],
                "order_id": request_data["order_id"],
            },
        )
    ]
    retrieved_items = []
    prompt_context_items = []
    tool_results = []
    token_usage = {}
    used_fallback_prompt = False
    degraded = False
    failure_stage = "none"
    fallback_reason = ""
    safety_status = {
        "passed": True,
        "blocked": False,
        "issues": [],
        "fallback_applied": False,
    }

    memory_started_at = time.perf_counter()
    context = get_or_create_context(
        user_id=request_data["user_id"],
        session_id=request_data["session_id"],
        order_id=request_data["order_id"],
    )
    user_memory = get_user_memory(context["user_id"])
    recent_messages = context.get("recent_messages", []) or []
    memory_metadata = {
        "session_id": context["session_id"],
        # 只给「步骤当时的预览」，全量 facts 由顶层 memory_snapshot 提供，这里不重复
        "recent_preview": [
            f"{'客服' if message.get('role') == 'assistant' else '用户'}：{str(message.get('content', ''))[:40]}"
            for message in recent_messages[-2:]
        ],
    }
    # 没有画像字段就不放这个键——空串和「没返回」在前端是两种文案
    if user_memory:
        memory_metadata["long_term_summary"] = "；".join(
            f"{key}={value}" for key, value in list(user_memory.items())[:8]
        )
    full_trace.append(
        trace_step(
            "memory_loaded",
            output_summary=f"recent={len(recent_messages)}, user_memory_fields={len(user_memory)}",
            started_at=memory_started_at,
            metadata=memory_metadata,
        )
    )
    intent_started_at = time.perf_counter()
    intent_analysis = analyze_intents(query, context)
    primary_intent_name = intent_analysis.get("primary_intent", "")
    primary_entry = next(
        (item for item in intent_analysis.get("intents", []) if item.get("name") == primary_intent_name),
        {},
    )
    intent_metadata = {
        "risk_level": intent_analysis.get("risk_level", "low"),
        "primary_intent": primary_intent_name,
        "evidence": list(primary_entry.get("evidence", [])),
        "secondary_intents": list(intent_analysis.get("secondary_intents", [])),
    }
    confidence = primary_entry.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        # 取不到就不放这个键（前端显示「未返回」）——不放比放个假值 0.5 好
        intent_metadata["confidence"] = float(confidence)
    inherited_from = intent_analysis.get("inherited_from_context")
    if inherited_from:
        intent_metadata["inherited_from_context"] = str(inherited_from)
    full_trace.append(
        trace_step(
            "intent_detected",
            output_summary=str(primary_intent_name),
            started_at=intent_started_at,
            metadata=intent_metadata,
        )
    )
    risk_started_at = time.perf_counter()
    get_redis_context_cache().cache_intent_analysis(request_id, intent_analysis)
    get_redis_context_cache().set_risk_state(
        context["session_id"],
        {
            "risk_level": intent_analysis.get("risk_level", "low"),
            "primary_intent": intent_analysis.get("primary_intent", ""),
        },
    )
    risk_level = intent_analysis.get("risk_level", "low")
    routing = intent_analysis.get("routing", "rag")
    high_risk_intents = [
        item.get("name", "")
        for item in intent_analysis.get("intents", [])
        if item.get("risk_level") in {"high", "critical"}
    ]
    full_trace.append(
        trace_step(
            "risk_precheck",
            status="high_risk" if risk_level in {"high", "critical"} else "success",
            output_summary=str(routing),
            started_at=risk_started_at,
            metadata={
                "routing": routing,
                "risk_level": risk_level,
                # 风险等级是 intent_detected 判的，本步只做前置处置，必须标清楚
                "risk_source": "intent_detected",
                "matched_high_risk_intents": high_risk_intents,
                # 前端 readMetaNumber() 只认 number，传 bool 会被静默忽略
                "requires_safety_prefix": int(bool(intent_analysis.get("requires_safety_prefix"))),
            },
        )
    )
    save_message(
        session_id=context["session_id"],
        role="user",
        content=query,
        intent_analysis=intent_analysis,
        risk_level=intent_analysis.get("risk_level", "low"),
    )
    facts = update_facts(context["session_id"], query, intent_analysis)
    summary = maybe_update_summary(context["session_id"], query, intent_analysis)
    context = get_or_create_context(
        user_id=context["user_id"],
        session_id=context["session_id"],
        order_id=context.get("order_id"),
    )
    context["facts"] = facts or context.get("facts", {})
    context["summary"] = summary or context.get("summary", "")
    order_tool_started_at = time.perf_counter()
    order_status_result = query_order_status(context.get("user_id", "demo_user"), context.get("order_id"))
    tool_results.append(order_status_result)
    if should_call_refund_tool(query, intent_analysis):
        tool_results.append(query_refund_status(context.get("user_id", "demo_user"), context.get("order_id")))
    order_context = build_order_context(tool_results)
    tool_summaries = []
    for result in tool_results:
        name = result.get("tool_name", "unknown_tool")
        detail = summarize_tool_output(result) or "（无可读摘要）"
        tool_summaries.append(f"{name}: {detail}")
    full_trace.append(
        trace_step(
            "order_tool_called",
            status="success" if all(result["status"] != "failed" for result in tool_results) else "degraded",
            output_summary="；".join(tool_summaries),
            started_at=order_tool_started_at,
            metadata={
                "tool_count": len(tool_results),
                # 前端暂未消费（走顶层 tool_results），补上以便后续切换
                "tools": [
                    {
                        "tool_name": result.get("tool_name", ""),
                        "status": result.get("status", ""),
                        "latency_ms": result.get("latency_ms"),
                        "summary": summarize_tool_output(result),
                    }
                    for result in tool_results
                ],
            },
        )
    )
    context_used = build_context_used(context)
    query_plan = preprocess_retrieval_query(query, intent_analysis, context)
    retrieval_query = query_plan["resolved_query"]

    retrieval_started_at = time.perf_counter()
    retrieval_source = describe_chat_retrieval_source()
    retrieval_path = retrieval_source["retrieval_path"]
    full_trace.append(
        trace_step(
            "retrieval_started",
            input_summary=mask_sensitive_text(retrieval_query)[:160],
            started_at=retrieval_started_at,
            # 走过的轨道要留痕：切轨后"怎么一条都没命中"的第一个排查点就是它
            metadata={
                "retrieval_path": retrieval_path,
                "data_source": retrieval_source["data_source"],
                "index_name": retrieval_source["index_name"],
                "index_version": retrieval_source["index_version"],
                "embedding_model": retrieval_source["embedding_model"],
                "sparse_available": retrieval_source["sparse_available"],
                "index_load_ms": retrieval_source["index_load_ms"],
                "retrieval_mode": resolve_chat_retrieval_mode(),
                "original_query": mask_sensitive_text(query_plan["original_query"])[:160],
                "resolved_query": mask_sensitive_text(query_plan["resolved_query"])[:160],
                "rewrite_applied": int(query_plan["rewrite_applied"]),
                "rewrite_strategy": query_plan["rewrite_strategy"],
                "rewrite_confidence": query_plan.get("confidence", 0.0),
                "ambiguity_type": query_plan.get("ambiguity_type", ""),
                "unresolved_slots": list(query_plan.get("unresolved_slots") or []),
                "conversation_dependencies": list(query_plan.get("conversation_dependencies") or []),
                "sub_queries": list(query_plan.get("sub_queries") or []),
                "rewrite_fallback_reason": query_plan.get("fallback_reason", ""),
            },
        )
    )
    try:
        retrieved_items, subquery_coverage = retrieve_with_query_plan(query_plan, auth)
        evidence_started_at = time.perf_counter()
        prompt_context_items = build_prompt_context_items(retrieved_items)
        covered = sum(1 for item in subquery_coverage if item["status"] == "covered")
        total_subqueries = len(subquery_coverage)
        if total_subqueries > 1 and covered < total_subqueries:
            evidence_status = "partial" if covered else "insufficient"
        else:
            evidence_status = "sufficient" if prompt_context_items else "insufficient"
        evidence_decision = decide_evidence_gate(
            subquery_coverage,
            evidence_count=len(prompt_context_items),
            risk_level=intent_analysis.get("risk_level", "low"),
        )
        full_trace.append(
            trace_step(
                "rerank_completed",
                output_summary=f"retrieved={len(retrieved_items)}",
                started_at=retrieval_started_at,
            )
        )
        full_trace.append(
            trace_step(
                "evidence_selected",
                output_summary=f"selected={len(prompt_context_items)}",
                started_at=evidence_started_at,
                metadata={
                    "primary_intent": prompt_context_items[0].intent if prompt_context_items else "",
                    "evidence_status": evidence_status,
                    "evidence_count": len(prompt_context_items),
                    "subquery_coverage": subquery_coverage,
                },
            )
        )
        if prompt_context_items:
            conversation_store.upsert_facts(
                context["session_id"],
                {
                    "last_primary_evidence": (
                        f"{prompt_context_items[0].intent}: "
                        f"{prompt_context_items[0].evidence_summary[:120]}"
                    )
                },
                source="retrieval",
            )
            context["facts"] = conversation_store.get_facts(context["session_id"])
    except Exception as error:
        degraded = True
        failure_stage = "retrieval"
        error_code = str(getattr(error, "error_code", "retrieval_error"))
        fallback_reason = f"retrieval_failed:{error_code}"
        retrieved_items = []
        prompt_context_items = []
        subquery_coverage = []
        evidence_status = "retrieval_error"
        evidence_decision = {"mode": "retrieval_error", "missing_subqueries": ["main"]}
        full_trace.append(
            trace_step(
                "rerank_completed",
                status="failed",
                output_summary=str(error),
                started_at=retrieval_started_at,
            )
        )
        full_trace.append(trace_step("evidence_selected", status="degraded", output_summary="selected=0", started_at=retrieval_started_at))

    full_trace.append(
        trace_step(
            "evidence_gate",
            status="pass" if prompt_context_items else "clarify",
            output_summary=evidence_status,
            started_at=evidence_started_at if "evidence_started_at" in locals() else retrieval_started_at,
            metadata={
                "evidence_count": len(prompt_context_items),
                "subquery_coverage": subquery_coverage,
                "covered_subqueries": sum(1 for item in subquery_coverage if item["status"] == "covered"),
                "total_subqueries": len(subquery_coverage),
                "answer_mode": evidence_decision["mode"],
                "missing_subqueries": evidence_decision["missing_subqueries"],
            },
        )
    )

    prompt_started_at = time.perf_counter()
    if prompt_context_items:
        prompt = create_prompt(
            query,
            prompt_context_items,
            conversation_context=context,
            intent_analysis=intent_analysis,
            order_context=order_context,
            user_memory=user_memory,
        )
        if evidence_decision["mode"] == "complete":
            confidence_score = 0.95
            answer_source = "rag"
        elif evidence_decision["mode"] == "partial":
            confidence_score = 0.65
            answer_source = "rag_partial"
            fallback_reason = "partial_evidence"
        else:
            confidence_score = 0.2
            answer_source = evidence_decision["mode"]
            fallback_reason = f"evidence_gate:{evidence_decision['mode']}"
    else:
        used_fallback_prompt = True
        prompt = build_fallback_prompt(query, order_context=order_context, user_memory=user_memory)
        confidence_score = 0.2 if evidence_decision["mode"] in {"clarify", "human_review"} else 0.5
        answer_source = "fallback" if failure_stage == "retrieval" else (evidence_decision["mode"] if evidence_decision["mode"] != "complete" else "fallback")
        if failure_stage == "none":
            fallback_reason = f"evidence_gate:{evidence_decision['mode']}" if evidence_decision["mode"] != "complete" else "no_retrieved_documents"
    prompt_config = get_active_prompt_config()
    full_trace.append(
        trace_step(
            "prompt_built",
            output_summary=f"source={answer_source}, chars={len(prompt)}",
            started_at=prompt_started_at,
            metadata={
                "used_fallback_prompt": used_fallback_prompt,
                "prompt_version": prompt_config.get("version", ""),
            },
        )
    )

    generation_started_at = time.perf_counter()
    try:
        generation_result = generate_reply_with_usage(prompt, prompt_config.get("system_prompt", SYSTEM_PROMPT))
        reply = generation_result["text"]
        token_usage = generation_result.get("token_usage", {})
        full_trace.append(
            trace_step(
                "generation_completed",
                output_summary=f"reply_chars={len(reply)}",
                started_at=generation_started_at,
                metadata={"token_usage": token_usage},
            )
        )
    except Exception as error:
        degraded = True
        failure_stage = "generation"
        fallback_reason = f"generation_failed: {error}"
        reply = FALLBACK_REPLY
        full_trace.append(
            trace_step(
                "generation_completed",
                status="failed",
                output_summary=str(error),
                started_at=generation_started_at,
            )
        )
        result = attach_runtime_fields({
            "reply": reply,
            "answer_mode": evidence_decision.get("mode", "complete"),
            "retrieval_path": retrieval_source["retrieval_path"],
            "data_source": retrieval_source["data_source"],
            "index_name": retrieval_source["index_name"],
            "index_version": retrieval_source["index_version"],
            "embedding_model": retrieval_source["embedding_model"],
            "sparse_available": retrieval_source["sparse_available"],
            "missing_subqueries": evidence_decision.get("missing_subqueries", []),
            "confidence_score": 0.2,
            "final_prompt": prompt,
            "prompt_version": prompt_config.get("version", ""),
            "token_usage": token_usage,
            "retrieved_documents": [item.answer for item in prompt_context_items],
            "retrieved_items": retrieved_items,
            "prompt_context_items": prompt_context_items_to_dicts(prompt_context_items),
            "trace": build_trace(
                retrieval_count=len(retrieved_items),
                retrieval_trace=build_retrieval_trace(query, retrieved_items),
                used_fallback_prompt=used_fallback_prompt,
                answer_composer_applied=False,
                answer_composer_trace={},
                reply_rules_applied=False,
                reply_rules_trace={},
                answer_source="fallback",
                degraded=degraded,
                failure_stage=failure_stage,
                fallback_reason=fallback_reason,
                request_id=request_id,
                latency_ms=elapsed_ms(started_at),
                top1_intent=build_top1_intent(retrieved_items),
            ),
        }, context, intent_analysis, context_used, safety_status)
        save_message(
            session_id=context["session_id"],
            role="assistant",
            content=reply,
            intent_analysis=intent_analysis,
            risk_level=intent_analysis.get("risk_level", "low"),
        )
        return complete_chat_response(
            result=result,
            query=query,
            request_id=request_id,
            context=context,
            user_memory=user_memory,
            intent_analysis=intent_analysis,
            tool_results=tool_results,
            safety_status=safety_status,
            full_trace=full_trace,
        )

    answer_composer_applied = False
    answer_composer_trace = {}
    rules_started_at = time.perf_counter()
    if get_rag_config().answer_composer_enabled:
        try:
            updated_reply, answer_composer_trace = compose_answer_if_needed(
                query,
                reply,
                retrieved_items,
                mode=get_rag_config().answer_composer_mode,
            )
            answer_composer_applied = updated_reply != reply
            reply = updated_reply
        except Exception as error:
            degraded = True
            failure_stage = "answer_composer"
            fallback_reason = f"answer_composer_failed: {error}"

    reply_rules_applied = False
    reply_rules_trace = {}
    if get_rag_config().reply_rules_enabled:
        try:
            updated_reply, reply_rules_trace = apply_reply_rules_with_trace(
                query,
                reply,
                retrieved_items,
            )
            reply_rules_applied = updated_reply != reply
            reply = updated_reply
        except Exception as error:
            degraded = True
            failure_stage = "reply_rules"
            fallback_reason = f"reply_rules_failed: {error}"

    try:
        updated_reply, safety_status = validate_reply(
            query=query,
            reply=reply,
            intent_analysis=intent_analysis,
            retrieved_items=retrieved_items,
        )
        reply = updated_reply
        full_trace.append(
            trace_step(
                "reply_rules_checked",
                output_summary=f"reply_rules_applied={reply_rules_applied}, safety_passed={safety_status.get('passed')}",
                started_at=rules_started_at,
                metadata={"safety_issues": safety_status.get("issues", [])},
            )
        )
    except Exception as error:
        degraded = True
        failure_stage = "safety_guard"
        fallback_reason = f"safety_guard_failed: {error}"
        full_trace.append(trace_step("reply_rules_checked", status="failed", output_summary=str(error), started_at=rules_started_at))

    result = attach_runtime_fields({
        "reply": reply,
        "answer_mode": evidence_decision.get("mode", "complete"),
        "retrieval_path": retrieval_source["retrieval_path"],
        "data_source": retrieval_source["data_source"],
        "index_name": retrieval_source["index_name"],
        "index_version": retrieval_source["index_version"],
        "embedding_model": retrieval_source["embedding_model"],
        "sparse_available": retrieval_source["sparse_available"],
        "missing_subqueries": evidence_decision.get("missing_subqueries", []),
        "confidence_score": confidence_score,
        "final_prompt": prompt,
        "prompt_version": prompt_config.get("version", ""),
        "token_usage": token_usage,
        "retrieved_documents": [item.answer for item in prompt_context_items],
        "retrieved_items": retrieved_items,
        "prompt_context_items": prompt_context_items_to_dicts(prompt_context_items),
        "trace": build_trace(
            retrieval_count=len(retrieved_items),
            retrieval_trace=build_retrieval_trace(query, retrieved_items),
            used_fallback_prompt=used_fallback_prompt,
            answer_composer_applied=answer_composer_applied,
            answer_composer_trace=answer_composer_trace,
            reply_rules_applied=reply_rules_applied,
            reply_rules_trace=reply_rules_trace,
            answer_source=answer_source,
            degraded=degraded,
            failure_stage=failure_stage,
            fallback_reason=fallback_reason,
            request_id=request_id,
            latency_ms=elapsed_ms(started_at),
            top1_intent=build_top1_intent(retrieved_items),
        ),
    }, context, intent_analysis, context_used, safety_status)
    save_message(
        session_id=context["session_id"],
        role="assistant",
        content=reply,
        intent_analysis=intent_analysis,
        risk_level=intent_analysis.get("risk_level", "low"),
    )
    return complete_chat_response(
        result=result,
        query=query,
        request_id=request_id,
        context=context,
        user_memory=user_memory,
        intent_analysis=intent_analysis,
        tool_results=tool_results,
        safety_status=safety_status,
        full_trace=full_trace,
    )


def get_model_info():
    adapter_enabled = ADAPTER_PATH.exists() and (ADAPTER_PATH / "adapter_config.json").exists()
    config = get_rag_config()

    return {
        "generation_provider": config.generation_provider,
        "online_model_name": config.online_model_name,
        "online_api_base_url_configured": bool(config.online_api_base_url),
        "online_api_key_env": config.online_api_key_env,
        "base_model": MODEL_PATH.name,
        "adapter_enabled": adapter_enabled,
        "adapter_name": ADAPTER_PATH.name if adapter_enabled else None,
    }
