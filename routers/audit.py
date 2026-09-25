from fastapi import APIRouter, Depends, Query

from schemas.audit_schema import AuditLogListResponse
from services.audit_service import list_audit_logs
from services.auth_service import AuthContext, get_auth_context, require_read_operation_role

router = APIRouter()


@router.get("/logs", response_model=AuditLogListResponse)
def audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    action_type: str = "",
    object_type: str = "",
    operator_role: str = "",
    request_id: str = "",
    auth: AuthContext = Depends(get_auth_context),
):
    require_read_operation_role("audit_read", auth)
    return list_audit_logs(
        limit=limit,
        action_type=action_type,
        object_type=object_type,
        operator_role=operator_role,
        request_id=request_id,
    )
