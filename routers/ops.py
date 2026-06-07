from fastapi import APIRouter

from schemas.ops_schema import OpsMetricsResponse
from services.ops_metrics import get_ops_metrics

router = APIRouter()


@router.get("/metrics", response_model=OpsMetricsResponse)
def ops_metrics():
    return get_ops_metrics()
