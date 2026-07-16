from fastapi import APIRouter, Depends, HTTPException

from schemas.order_schema import OrderStateRequest, OrderStateResponse
from services.audit_service import record_audit_log
from services.auth_service import get_operator_context, require_read_operation_role, require_write_operation_role
from services.order_state_store import get_order_state, upsert_order_state

router = APIRouter()


@router.put("/{order_id}/state", response_model=OrderStateResponse)
async def save_order_state(
    order_id: str,
    payload: OrderStateRequest,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("order_state_upsert", operator_context)
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="order_id path and body mismatch")
    order = upsert_order_state(payload.model_dump())
    record_audit_log(
        operator_id=operator_context["operator_id"],
        operator_role=operator_context["role"],
        action_type="order_state_upsert",
        object_type="order_state",
        object_id=order_id,
        after_summary=f"user={order['user_id']}; status={order['status']}",
        ip=operator_context.get("ip", ""),
        device_info=operator_context.get("user_agent", ""),
    )
    return order


@router.get("/{order_id}/state", response_model=OrderStateResponse)
async def read_order_state(
    order_id: str,
    operator_context: dict = Depends(get_operator_context),
):
    require_read_operation_role("order_state_read", operator_context)
    order = get_order_state(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order state not found")
    return order
