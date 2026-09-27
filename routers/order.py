from services.runtime_db import scoped_route
from fastapi import APIRouter, Depends, Header, HTTPException

from schemas.order_schema import OrderStateRequest, OrderStateResponse
from services.auth_service import (
    AuthContext,
    RequestMeta,
    get_auth_context,
    get_request_meta,
    require_read_operation_role,
    require_write_operation_role,
)
from services.order_state_store import (
    OrderConflictError, OrderNotFoundError, get_order_state, upsert_order_state,
)

router = APIRouter()


@router.put("/{order_id}/state", response_model=OrderStateResponse)
@scoped_route
async def save_order_state(
    order_id: str,
    payload: OrderStateRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=200),
):
    require_write_operation_role("order_state_upsert", auth)
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="order_id path and body mismatch")
    try:
        return upsert_order_state(
            payload.model_dump(), user_id=auth.user_id, tenant_id=auth.tenant_id,
            idempotency_key=idempotency_key, request_id=auth.request_id,
            operator_role=auth.primary_role, ip=meta.ip, device_info=meta.user_agent,
        )
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail='order state not found') from exc
    except OrderConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{order_id}/state", response_model=OrderStateResponse)
@scoped_route
async def read_order_state(
    order_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    require_read_operation_role("order_state_read", auth)
    order = get_order_state(order_id, user_id=auth.user_id, tenant_id=auth.tenant_id)
    if not order:
        raise HTTPException(status_code=404, detail="order state not found")
    return order
