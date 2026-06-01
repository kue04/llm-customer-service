# app/services/chat_service.py
from pathlib import Path

from config.rag_config import get_rag_config
from models.prompt import create_prompt
from peft import PeftModel
from services.answer_composer import compose_answer_if_needed
from services.grounding_diagnostics import build_chat_grounding_diagnostics
from services.online_generation import generate_online_chat_completion
from services.reply_rules import apply_reply_rules_with_trace
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


def build_fallback_prompt(query: str) -> str:
    return (
        "没有检索到完全匹配的知识库材料。请只基于外卖客服通用处理原则回答，"
        "不要编造具体平台政策；如果需要平台核实，请建议用户在订单页或官方客服渠道处理。\n\n"
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


def get_answer_from_rag(query: str):
    retrieved_items = []
    prompt_context_items = []
    used_fallback_prompt = False
    degraded = False
    failure_stage = "none"
    fallback_reason = ""

    try:
        retrieved_items = retrieve_rag_items(query)
        prompt_context_items = build_prompt_context_items(retrieved_items)
    except Exception as error:
        degraded = True
        failure_stage = "retrieval"
        fallback_reason = f"retrieval_failed: {error}"
        retrieved_items = []
        prompt_context_items = []

    if prompt_context_items:
        prompt = create_prompt(query, prompt_context_items)
        confidence_score = 0.95
        answer_source = "rag"
    else:
        used_fallback_prompt = True
        prompt = build_fallback_prompt(query)
        confidence_score = 0.5
        answer_source = "fallback"
        if failure_stage == "none":
            fallback_reason = "no_retrieved_documents"

    try:
        reply = generate_reply(prompt)
    except Exception as error:
        degraded = True
        failure_stage = "generation"
        fallback_reason = f"generation_failed: {error}"
        reply = FALLBACK_REPLY
        return attach_grounding_diagnostics({
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
            ),
        }, query)

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

    return attach_grounding_diagnostics({
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
        ),
    }, query)


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
