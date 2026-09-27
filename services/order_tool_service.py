from __future__ import annotations

import time
from uuid import uuid4

from services.order_state_store import get_order_state

TOOL_TIMEOUT_SECONDS = 3.0
QUERY_ERROR_TYPE = "tool_unavailable"
TIMEOUT_ERROR_TYPE = "tool_timeout"

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


def _lookup_order(order_id: str | None, *, user_id: str, tenant_id: str | None) -> dict | None:
    if not user_id or not tenant_id:
        return None
    return get_order_state(order_id, user_id=user_id, tenant_id=tenant_id)


def _failed_tool_result(
    tool_name: str,
    started_at: float,
    input_data: dict,
    error_type: str,
    retryable: bool,
) -> dict:
    return _tool_result(
        tool_name,
        started_at,
        input_data,
        status="failed",
        error_type=error_type,
        retryable=retryable,
    )


def _lookup_order_with_contract(tool_name: str, started_at: float, input_data: dict) -> tuple[dict | None, dict | None]:
    try:
        order = _lookup_order(str(input_data.get("order_id") or ""),
                              user_id=input_data['user_id'], tenant_id=input_data.get('tenant_id'))
    except Exception:
        return None, _failed_tool_result(
            tool_name,
            started_at,
            input_data,
            error_type=QUERY_ERROR_TYPE,
            retryable=True,
        )

    if time.perf_counter() - started_at > TOOL_TIMEOUT_SECONDS:
        return None, _failed_tool_result(
            tool_name,
            started_at,
            input_data,
            error_type=TIMEOUT_ERROR_TYPE,
            retryable=True,
        )

    return order, None


def _is_wrong_user(order: dict, user_id: str, tenant_id: str | None) -> bool:
    return not user_id or not tenant_id or order.get('user_id') != user_id or order.get('tenant_id') != tenant_id


def query_order_status(user_id: str, order_id: str | None, *, tenant_id: str | None = None) -> dict:
    started_at = time.perf_counter()
    input_data = {"user_id": user_id, "order_id": order_id, 'tenant_id': tenant_id}
    if not order_id:
        return _tool_result(
            "query_order_status",
            started_at,
            input_data,
            status="skipped",
            error_type="missing_order_id",
        )
    order, failure = _lookup_order_with_contract("query_order_status", started_at, input_data)
    if failure:
        return failure
    if order and _is_wrong_user(order, user_id, tenant_id):
        return _tool_result(
            "query_order_status",
            started_at,
            input_data,
            status="failed",
            error_type="order_not_found",
            retryable=False,
        )
    if not order:
        return _tool_result(
            "query_order_status",
            started_at,
            input_data,
            status="failed",
            error_type="order_not_found",
            retryable=False,
        )
    return _tool_result("query_order_status", started_at, input_data, order)


def query_refund_status(user_id: str, order_id: str | None, *, tenant_id: str | None = None) -> dict:
    started_at = time.perf_counter()
    input_data = {"user_id": user_id, "order_id": order_id, 'tenant_id': tenant_id}
    if not order_id:
        return _tool_result(
            "query_refund_status",
            started_at,
            input_data,
            status="skipped",
            error_type="missing_order_id",
        )
    order, failure = _lookup_order_with_contract("query_refund_status", started_at, input_data)
    if failure:
        return failure
    if order and _is_wrong_user(order, user_id, tenant_id):
        return _tool_result(
            "query_refund_status",
            started_at,
            input_data,
            status="failed",
            error_type="order_not_found",
            retryable=False,
        )
    if not order:
        return _tool_result(
            "query_refund_status",
            started_at,
            input_data,
            status="failed",
            error_type="order_not_found",
        )
    return _tool_result(
        "query_refund_status",
        started_at,
        input_data,
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
