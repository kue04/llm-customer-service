from __future__ import annotations

from services import conversation_store
from services.privacy import mask_sensitive_text
from services.redis_context_cache import get_redis_context_cache


DEFAULT_USER_ID = "demo_user"
RECENT_MESSAGE_LIMIT = 10
PROMPT_RECENT_MESSAGE_LIMIT = 5
SUMMARY_LIMIT = 500
FACT_LIMIT = 10


def _normalize_user_id(user_id: str | None) -> str:
    return (user_id or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID


def _shorten(text: str, limit: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


def _load_recent_messages(session_id: str) -> list[dict]:
    cache = get_redis_context_cache()
    messages = cache.get_recent_messages(session_id)
    if messages:
        return messages[-RECENT_MESSAGE_LIMIT:]
    return conversation_store.list_recent_messages(session_id, limit=RECENT_MESSAGE_LIMIT)


def _load_summary(session_id: str) -> str:
    cache = get_redis_context_cache()
    summary = cache.get_summary(session_id)
    if summary:
        return summary
    return conversation_store.get_summary(session_id)


def _load_facts(session_id: str) -> dict[str, str]:
    cache = get_redis_context_cache()
    facts = cache.get_facts(session_id)
    if facts:
        return dict(list(facts.items())[:FACT_LIMIT])
    return conversation_store.get_facts(session_id, limit=FACT_LIMIT)


def get_or_create_context(
    user_id: str | None = None,
    session_id: str | None = None,
    order_id: str | None = None,
) -> dict:
    resolved_user_id = _normalize_user_id(user_id)
    conversation = conversation_store.get_or_create_conversation(
        user_id=resolved_user_id,
        session_id=session_id,
        order_id=order_id,
    )
    resolved_session_id = conversation["session_id"]
    return {
        "user_id": resolved_user_id,
        "session_id": resolved_session_id,
        "order_id": conversation.get("order_id") or order_id,
        "summary": _shorten(_load_summary(resolved_session_id), SUMMARY_LIMIT),
        "recent_messages": _load_recent_messages(resolved_session_id)[-PROMPT_RECENT_MESSAGE_LIMIT:],
        "facts": _load_facts(resolved_session_id),
    }


def _append_cached_message(
    session_id: str,
    role: str,
    content: str,
    intent_analysis: dict | None,
    risk_level: str,
) -> None:
    cache = get_redis_context_cache()
    cache.push_recent_message(
        session_id,
        {
            "role": role,
            "content": content,
            "intent": intent_analysis or {},
            "risk_level": risk_level,
        },
    )


def save_message(
    session_id: str,
    role: str,
    content: str,
    intent_analysis: dict | None = None,
    risk_level: str = "low",
) -> None:
    safe_content = mask_sensitive_text(content)
    conversation_store.append_message(
        session_id=session_id,
        role=role,
        content=safe_content,
        intent_analysis=intent_analysis,
        risk_level=risk_level,
    )
    _append_cached_message(session_id, role, safe_content, intent_analysis, risk_level)


def _facts_from_turn(query: str, intent_analysis: dict) -> dict[str, str]:
    facts = {
        "last_primary_intent": str(intent_analysis.get("primary_intent", "")),
        "last_risk_level": str(intent_analysis.get("risk_level", "low")),
    }
    if intent_analysis.get("secondary_intents"):
        facts["last_secondary_intents"] = "、".join(intent_analysis["secondary_intents"])
    if any(word in query for word in ("退款", "退钱", "全额", "全款")):
        facts["refund_mentioned"] = "true"
    if any(word in query for word in ("异物", "变质", "酸了", "不敢吃", "吃坏")):
        facts["food_safety_evidence_needed"] = "餐品照片、包装照片、异物照片、订单信息"
    return {key: value for key, value in facts.items() if value}


def update_facts(session_id: str, query: str, intent_analysis: dict) -> dict[str, str]:
    facts = _facts_from_turn(query, intent_analysis)
    conversation_store.upsert_facts(session_id, facts, source="intent_analysis")
    all_facts = conversation_store.get_facts(session_id, limit=FACT_LIMIT)
    get_redis_context_cache().set_facts(session_id, all_facts)
    return all_facts


def maybe_update_summary(session_id: str, query: str, intent_analysis: dict) -> str:
    message_count = conversation_store.count_messages(session_id)
    risk_level = intent_analysis.get("risk_level", "low")
    should_update = message_count >= 6 and (message_count % 4 == 0 or risk_level in {"high", "critical"})
    summary = conversation_store.get_summary(session_id)
    if not should_update:
        return summary

    primary_intent = intent_analysis.get("primary_intent", "通用客服咨询")
    new_piece = f"最近用户咨询：{_shorten(query, 80)}；主意图：{primary_intent}；风险等级：{risk_level}。"
    merged = _shorten(f"{summary} {new_piece}".strip(), SUMMARY_LIMIT)
    conversation_store.update_summary(session_id, merged)
    get_redis_context_cache().set_summary(session_id, merged)
    return merged


def build_context_used(context: dict) -> dict:
    return {
        "session_id": context.get("session_id", ""),
        "recent_message_count": len(context.get("recent_messages", [])),
        "summary_chars": len(context.get("summary", "")),
        "fact_count": len(context.get("facts", {})),
        "redis_enabled": get_redis_context_cache().enabled,
    }
