from __future__ import annotations

import time
from uuid import uuid4


MOCK_ORDERS = {
    "order_new": {
        "order_id": "order_new",
        "status": "created",
        "status_label": "未接单",
        "summary": "商家尚未接单，用户可在订单页尝试取消。",
        "refund_status": "none",
    },
    "order_cooking": {
        "order_id": "order_cooking",
        "status": "merchant_preparing",
        "status_label": "商家已制作",
        "summary": "商家已开始制作，退款金额需以平台和商家核实结果为准。",
        "refund_status": "pending_review",
    },
    "order_picked": {
        "order_id": "order_picked",
        "status": "rider_picked",
        "status_label": "骑手已取餐",
        "summary": "骑手已取餐，不应直接承诺全额退款，可引导用户走售后核实。",
        "refund_status": "pending_review",
    },
    "order_delivered": {
        "order_id": "order_delivered",
        "status": "delivered",
        "status_label": "已送达",
        "summary": "订单显示已送达，未收到餐需提交未收到餐反馈并等待核实。",
        "refund_status": "none",
    },
    "order_food_safety": {
        "order_id": "order_food_safety",
        "status": "delivered",
        "status_label": "已送达/食品安全反馈",
        "summary": "食品安全场景需保留餐品、包装、照片和订单信息，按高风险处理。",
        "refund_status": "manual_review",
    },
}


def _tool_result(
    tool_name: str,
    started_at: float,
    input_data: dict,
    output: dict | None = None,
    status: str = "success",
    error_type: str = "",
    retryable: bool = False,
) -> dict:
    return {
        "tool_name": tool_name,
        "status": status,
        "input": input_data,
        "output": output or {},
        "error_type": error_type,
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "retryable": retryable,
    }


def query_order_status(order_id: str | None) -> dict:
    started_at = time.perf_counter()
    if not order_id:
        return _tool_result(
            "query_order_status",
            started_at,
            {"order_id": order_id},
            status="skipped",
            error_type="missing_order_id",
        )
    order = MOCK_ORDERS.get(order_id)
    if not order:
        return _tool_result(
            "query_order_status",
            started_at,
            {"order_id": order_id},
            status="failed",
            error_type="order_not_found",
            retryable=False,
        )
    return _tool_result("query_order_status", started_at, {"order_id": order_id}, order)


def query_refund_status(order_id: str | None) -> dict:
    started_at = time.perf_counter()
    order = MOCK_ORDERS.get(order_id or "")
    if not order:
        return _tool_result(
            "query_refund_status",
            started_at,
            {"order_id": order_id},
            status="skipped" if not order_id else "failed",
            error_type="missing_order_id" if not order_id else "order_not_found",
        )
    return _tool_result(
        "query_refund_status",
        started_at,
        {"order_id": order_id},
        {
            "order_id": order_id,
            "refund_status": order.get("refund_status", "none"),
            "summary": "退款进度和金额以订单售后页展示及平台核实结果为准。",
        },
    )


def create_handoff_ticket(reason: str, context: dict) -> dict:
    started_at = time.perf_counter()
    ticket = {
        "ticket_id": f"handoff_{uuid4().hex[:12]}",
        "reason": reason,
        "context_summary": {
            "user_id": context.get("user_id", ""),
            "session_id": context.get("session_id", ""),
            "order_id": context.get("order_id"),
            "summary": context.get("summary", ""),
            "facts": context.get("facts", {}),
        },
    }
    return _tool_result(
        "create_handoff_ticket",
        started_at,
        {"reason": reason},
        ticket,
    )


def should_call_refund_tool(query: str, intent_analysis: dict) -> bool:
    text_hit = any(word in query for word in ("退款", "退钱", "到账", "退回", "赔"))
    intent_hit = any(
        "退款" in intent.get("name", "")
        for intent in intent_analysis.get("intents", [])
    )
    return text_hit or intent_hit
