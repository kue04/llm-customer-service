from pydantic import BaseModel, Field


class OrderStateRequest(BaseModel):
    user_id: str = "demo_user"
    order_id: str
    status: str
    status_label: str = ""
    delivery_status: str = ""
    summary: str = ""
    refund_status: str = "none"
    store_name: str = ""
    items: list[dict] = Field(default_factory=list)
    total: float = 0.0


class OrderStateResponse(OrderStateRequest):
    updated_at: str = ""
