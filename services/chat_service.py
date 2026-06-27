# app/services/chat_service.py
import logging
from pathlib import Path
import time
from uuid import uuid4

from config.rag_config import get_rag_config
from models.prompt import create_prompt
from peft import PeftModel
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
from services.online_generation import generate_online_chat_completion
from services.ops_metrics import record_chat_metrics
from services.order_tool_service import (
    create_handoff_ticket,
    query_order_status,
    query_refund_status,
    should_call_refund_tool,
)
from services.privacy import mask_sensitive_text
from services.redis_context_cache import get_redis_context_cache
from services.reply_rules import apply_reply_rules_with_trace
from services.safety_guard import validate_reply
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from utils.rag_context import build_prompt_context_items, prompt_context_items_to_dicts
from utils.vector_retriever import detect_intent_hint, retrieve_rag_items


MODEL_PATH = Path(__file__).resolve().parents[1] / "local_models" / "qwen2.5-1.5b-instruct"
ADAPTER_PATH = Path(__file__).resolve().parents[1] / "models" / "takeout-qwen-lora-minimal"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32
SYSTEM_PROMPT = (
    "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，"
    "最后给出可执行的下一步。不要编造平台规则；遇到支付、隐私、食品安全、"
    "站外交易等高风险问题时要提醒用户保留证据并通过官方渠道处理。"
)
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

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH,
    local_files_only=True,
    dtype=MODEL_DTYPE,
)
if ADAPTER_PATH.exists() and (ADAPTER_PATH / "adapter_config.json").exists():
    model = PeftModel.from_pretrained(model, ADAPTER_PATH, local_files_only=True)
model = model.to(DEVICE)
model.eval()


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


def generate_online_text_with_system_prompt(prompt: str, system_prompt: str) -> str:
    config = get_rag_config()
    return generate_online_chat_completion(
        prompt=prompt,
        system_prompt=system_prompt,
        model_name=config.online_model_name,
        api_base_url=config.online_api_base_url,
        api_key_env=config.online_api_key_env,
    )


def generate_local_text_with_system_prompt(prompt: str, system_prompt: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt")
    inputs = {key: value.to(DEVICE) for key, value in inputs.items()}
    input_length = inputs["input_ids"].shape[-1]
    outputs = model.generate(
        **inputs,
        max_new_tokens=256,
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id,
    )
    return tokenizer.decode(outputs[0][input_length:], skip_special_tokens=True).strip()


def generate_text_with_system_prompt(prompt: str, system_prompt: str) -> str:
    provider = get_rag_config().generation_provider
    if provider == "online":
        return generate_online_text_with_system_prompt(prompt, system_prompt)
    if provider == "local":
        return generate_local_text_with_system_prompt(prompt, system_prompt)
    raise ValueError(f"Unsupported generation_provider: {provider}")


def generate_reply(prompt: str) -> str:
    return generate_text_with_system_prompt(prompt, SYSTEM_PROMPT)


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
    return round((time.perf_counter() - started_at) * 1000, 2)


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
        "input_summary": input_summary,
        "output_summary": output_summary,
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
        score = getattr(item, "rerank_score", 0.0) or getattr(item, "score", 0.0)
        quote = getattr(item, "evidence_summary", "") or getattr(item, "answer", "")
        citations.append(
            {
                "evidence_id": f"kb_{rank}_{index + 1}",
                "source_type": "knowledge_base",
                "category": getattr(item, "category", ""),
                "intent": getattr(item, "intent", ""),
                "risk_level": "unknown",
                "version": "local",
                "score": score,
                "evidence_role": getattr(item, "role", "supporting"),
                "quote": quote,
                "title": getattr(item, "display_title", ""),
            }
        )
    return citations


def evidence_citations_from_result(result: dict) -> list[dict]:
    class ItemAdapter:
        def __init__(self, data: dict):
            self.__dict__.update(data)

    return build_evidence_citations(
        [ItemAdapter(item) for item in result.get("prompt_context_items", [])]
    )


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
    result["evidence_citations"] = evidence_citations
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
    result = finalize_chat_result(result, query)
    full_trace.append(
        trace_step(
            "grounding_checked",
            output_summary=f"needs_manual_review={bool(result.get('needs_manual_review'))}",
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
    handoff_ticket = None
    if handoff_needed:
        started_at = time.perf_counter()
        handoff_ticket = create_handoff_ticket(handoff_reason, context)
        tool_results.append(handoff_ticket)
        result["needs_manual_review"] = True
        full_trace.append(
            trace_step(
                "handoff_ticket_created",
                input_summary=handoff_reason,
                output_summary=handoff_ticket.get("output", {}).get("ticket_id", ""),
                started_at=started_at,
                metadata={"tool_status": handoff_ticket.get("status", "")},
            )
        )

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
        )
    )
    full_trace.append(trace_step("response_returned", output_summary="response assembled"))
    return attach_enhanced_fields(
        result=result,
        request_id=request_id,
        context=context,
        user_memory=user_memory,
        updated_user_memory=updated_user_memory,
        intent_analysis=intent_analysis,
        tool_results=tool_results,
        safety_status=safety_status,
        full_trace=full_trace,
        handoff_ticket=handoff_ticket.get("output") if handoff_ticket else None,
    )


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
    save_chat_session(query=query, reply=result.get("reply", ""), trace=trace)
    log_chat_trace(query, result.get("trace", {}))
    return attach_grounding_diagnostics(result, query)


def get_answer_from_rag(request):
    request_data = normalize_chat_request(request)
    query = request_data["message"]
    request_id = uuid4().hex
    started_at = time.perf_counter()
    full_trace = [
        trace_step(
            "request_received",
            input_summary=mask_sensitive_text(query)[:160],
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

    context = get_or_create_context(
        user_id=request_data["user_id"],
        session_id=request_data["session_id"],
        order_id=request_data["order_id"],
    )
    user_memory = get_user_memory(context["user_id"])
    full_trace.append(
        trace_step(
            "memory_loaded",
            output_summary=f"recent={len(context.get('recent_messages', []))}, user_memory_fields={len(user_memory)}",
            metadata={"session_id": context["session_id"]},
        )
    )
    intent_analysis = analyze_intents(query, context)
    full_trace.append(
        trace_step(
            "intent_detected",
            output_summary=str(intent_analysis.get("primary_intent", "")),
            metadata={"risk_level": intent_analysis.get("risk_level", "low")},
        )
    )
    full_trace.append(
        trace_step(
            "risk_precheck",
            status="high_risk" if intent_analysis.get("risk_level") in {"high", "critical"} else "success",
            output_summary=str(intent_analysis.get("routing", "rag")),
        )
    )
    get_redis_context_cache().cache_intent_analysis(request_id, intent_analysis)
    get_redis_context_cache().set_risk_state(
        context["session_id"],
        {
            "risk_level": intent_analysis.get("risk_level", "low"),
            "primary_intent": intent_analysis.get("primary_intent", ""),
        },
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
    order_status_result = query_order_status(context.get("order_id"))
    tool_results.append(order_status_result)
    if should_call_refund_tool(query, intent_analysis):
        tool_results.append(query_refund_status(context.get("order_id")))
    order_context = build_order_context(tool_results)
    full_trace.append(
        trace_step(
            "order_tool_called",
            status="success" if all(result["status"] != "failed" for result in tool_results) else "degraded",
            output_summary="；".join(filter(None, (summarize_tool_output(result) for result in tool_results))),
            started_at=order_tool_started_at,
            metadata={"tool_count": len(tool_results)},
        )
    )
    context_used = build_context_used(context)
    retrieval_query = build_query_with_intent_hint(query, intent_analysis)

    retrieval_started_at = time.perf_counter()
    full_trace.append(trace_step("retrieval_started", input_summary=retrieval_query[:160]))
    try:
        retrieved_items = retrieve_rag_items(retrieval_query)
        prompt_context_items = build_prompt_context_items(retrieved_items)
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
                metadata={"primary_intent": prompt_context_items[0].intent if prompt_context_items else ""},
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
        fallback_reason = f"retrieval_failed: {error}"
        retrieved_items = []
        prompt_context_items = []
        full_trace.append(
            trace_step(
                "rerank_completed",
                status="failed",
                output_summary=str(error),
                started_at=retrieval_started_at,
            )
        )
        full_trace.append(trace_step("evidence_selected", status="degraded", output_summary="selected=0"))

    if prompt_context_items:
        prompt = create_prompt(
            query,
            prompt_context_items,
            conversation_context=context,
            intent_analysis=intent_analysis,
            order_context=order_context,
            user_memory=user_memory,
        )
        confidence_score = 0.95
        answer_source = "rag"
    else:
        used_fallback_prompt = True
        prompt = build_fallback_prompt(query, order_context=order_context, user_memory=user_memory)
        confidence_score = 0.5
        answer_source = "fallback"
        if failure_stage == "none":
            fallback_reason = "no_retrieved_documents"
    full_trace.append(
        trace_step(
            "prompt_built",
            output_summary=f"source={answer_source}, chars={len(prompt)}",
            metadata={"used_fallback_prompt": used_fallback_prompt},
        )
    )

    generation_started_at = time.perf_counter()
    try:
        reply = generate_reply(prompt)
        full_trace.append(
            trace_step(
                "generation_completed",
                output_summary=f"reply_chars={len(reply)}",
                started_at=generation_started_at,
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
            "confidence_score": 0.2,
            "final_prompt": prompt,
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
    if get_rag_config().answer_composer_enabled:
        try:
            updated_reply, answer_composer_trace = compose_answer_if_needed(
                query,
                reply,
                retrieved_items,
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
                metadata={"safety_issues": safety_status.get("issues", [])},
            )
        )
    except Exception as error:
        degraded = True
        failure_stage = "safety_guard"
        fallback_reason = f"safety_guard_failed: {error}"
        full_trace.append(trace_step("reply_rules_checked", status="failed", output_summary=str(error)))

    result = attach_runtime_fields({
        "reply": reply,
        "confidence_score": confidence_score,
        "final_prompt": prompt,
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
