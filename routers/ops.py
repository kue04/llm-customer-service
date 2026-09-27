from services.runtime_db import scoped_route
from fastapi import APIRouter, Depends, Query

from schemas.ops_schema import OpsMetricsResponse
from services.auth_service import AuthContext, get_auth_context, require_read_operation_role
from services.ops_metrics import get_ops_metrics

router = APIRouter()


@router.get("/metrics", response_model=OpsMetricsResponse)
@scoped_route
def ops_metrics(auth: AuthContext = Depends(get_auth_context)):
    require_read_operation_role("ops_metrics_read", auth)
    return get_ops_metrics()


@router.get('/capacity')
@scoped_route
def runtime_capacity(auth: AuthContext = Depends(get_auth_context)):
    require_read_operation_role('ops_metrics_read', auth)
    from services.request_budget import admission, positive
    from services import request_budget
    from services.ingestion.db import get_engine
    from services.ingestion.models import IngestionJob
    from sqlalchemy import select, func
    from datetime import datetime, timezone

    with admission.lock:
        local = {'active': admission.active, 'tenant_active': admission.tenants.get(auth.tenant_id, 0)}
    pool = get_engine().pool
    with request_budget._model_lock:
        model = {'active': request_budget._model_active, 'waiting': request_budget._model_waiters}
    with get_engine().connect() as conn:
        rows = conn.execute(select(IngestionJob.status, func.count(), func.min(IngestionJob.created_at))
                            .where(IngestionJob.tenant_id == auth.tenant_id, IngestionJob.status.in_(('pending', 'running', 'retrying')))
                            .group_by(IngestionJob.status)).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    backlog = {state: {'count': count, 'oldest_age_seconds': max(0, (now-started).total_seconds())} for state, count, started in rows}
    signals = []
    if model['waiting']:
        signals.append('model_capacity_wait')
    if any(row['oldest_age_seconds'] > positive('RAG_BACKLOG_WARN_SECONDS', 60) for row in backlog.values()):
        signals.append('ingestion_backlog_age')
    return {'scope': 'this_api_process', 'chat': local,
            'model': model, 'tenant_durable_backlog': backlog, 'signals': signals,
            'pool_checked_out': pool.checkedout() if hasattr(pool, 'checkedout') else None,
            'pool_size': pool.size() if hasattr(pool, 'size') else None}


@router.get('/model-calls')
@scoped_route
def model_calls(auth: AuthContext = Depends(get_auth_context), request_id: str | None = None,
                job_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    require_read_operation_role('ops_metrics_read', auth)
    from services.call_ledger import calls
    from services.ingestion.db import get_engine
    from sqlalchemy import select
    statement = select(calls).where(calls.c.tenant_id == auth.tenant_id, calls.c.deleted_at.is_(None))
    if request_id:
        statement = statement.where(calls.c.request_id == request_id)
    if job_id:
        statement = statement.where(calls.c.job_id == job_id)
    with get_engine().connect() as conn:
        rows = conn.execute(statement.order_by(calls.c.started_at, calls.c.id).offset(offset).limit(limit)).mappings().all()
    return {'items': [dict(row) for row in rows], 'offset': offset, 'limit': limit}
