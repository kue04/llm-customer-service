from pydantic import BaseModel


class AuditLogItem(BaseModel):
    id: int
    operator_id: str
    operator_role: str
    action_type: str
    object_type: str
    object_id: str
    request_id: str = ""
    before_summary: str = ""
    after_summary: str = ""
    ip: str = ""
    device_info: str = ""
    created_at: str


class AuditLogListResponse(BaseModel):
    count: int
    items: list[AuditLogItem]
