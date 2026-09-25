from fastapi import APIRouter, Depends, HTTPException

from schemas.order_schema import OrderStateRequest, OrderStateResponse
from services.audit_service import record_audit_log
from services.auth_service import (
    AuthContext,
    RequestMeta,
    get_auth_context,
    get_request_meta,
    require_read_operation_role,
    require_write_operation_role,
)
from services.order_state_store import get_order_state, upsert_order_state

router = APIRouter()


@router.put("/{order_id}/state", response_model=OrderStateResponse)
async def save_order_state(
    order_id: str,
    payload: OrderStateRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("order_state_upsert", auth)
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="order_id path and body mismatch")
    order = upsert_order_state(payload.model_dump())
    record_audit_log(
        operator_id=auth.user_id,
        operator_role=auth.primary_role,
        action_type="order_state_upsert",
        object_type="order_state",
        object_id=order_id,
        request_id=auth.request_id,
        after_summary=f"user={order['user_id']}; status={order['status']}",
        ip=meta.ip,
        device_info=meta.user_agent,
    )
    return order


@router.get("/{order_id}/state", response_model=OrderStateResponse)
async def read_order_state(
    order_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    require_read_operation_role("order_state_read", auth)
    order = get_order_state(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order state not found")
    return order
