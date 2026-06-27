from __future__ import annotations

from services import conversation_store


def get_user_memory(user_id: str) -> dict:
    return conversation_store.get_user_memory(user_id)


def update_user_memory_from_turn(
    user_id: str,
    query: str,
    reply: str,
    intent_analysis: dict,
) -> dict:
    updates: dict[str, str] = {}
    primary_intent = str(intent_analysis.get("primary_intent", ""))
    risk_level = str(intent_analysis.get("risk_level", "low"))

    if primary_intent:
        updates["last_service_summary"] = f"最近咨询：{primary_intent}；风险等级：{risk_level}"
    if any(word in query for word in ("食品安全", "异物", "变质", "吃坏", "拉肚子")):
        updates["risk_tags"] = "食品安全相关咨询"
    if any(word in query for word in ("退款", "退钱", "赔")):
        updates["common_issue_types"] = "退款/售后"
    if any(word in query for word in ("电话", "短信", "别打电话", "打字")):
        updates["communication_preference"] = "优先平台内文字沟通"
    if "地址" in query:
        updates["address_preference"] = "地址相关问题需以订单页为准"

    if updates:
        conversation_store.upsert_user_memory(user_id, updates)

    memory = conversation_store.get_user_memory(user_id)
    memory["last_reply_summary"] = reply[:120]
    return memory
