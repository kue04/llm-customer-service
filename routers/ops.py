from fastapi import APIRouter, Depends

from schemas.ops_schema import OpsMetricsResponse
from services.auth_service import AuthContext, get_auth_context, require_read_operation_role
from services.ops_metrics import get_ops_metrics

router = APIRouter()


@router.get("/metrics", response_model=OpsMetricsResponse)
def ops_metrics(auth: AuthContext = Depends(get_auth_context)):
    require_read_operation_role("ops_metrics_read", auth)
    return get_ops_metrics()
