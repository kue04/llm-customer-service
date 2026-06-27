from fastapi import APIRouter, HTTPException

from schemas.order_schema import OrderStateRequest, OrderStateResponse
from services.order_state_store import get_order_state, upsert_order_state

router = APIRouter()


@router.put("/{order_id}/state", response_model=OrderStateResponse)
async def save_order_state(order_id: str, payload: OrderStateRequest):
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="order_id path and body mismatch")
    return upsert_order_state(payload.model_dump())


@router.get("/{order_id}/state", response_model=OrderStateResponse)
async def read_order_state(order_id: str):
    order = get_order_state(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order state not found")
    return order
