from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from services.auth_service import AuthContext, get_auth_context, require_read_operation_role
from services.health_service import dependency_health

router = APIRouter()


@router.get('/health')
@router.get('/health/live')
def live():
    return {'status': 'ok'}


@router.get('/health/ready')
def ready():
    result = dependency_health()
    return JSONResponse(status_code=200 if result['ready'] else 503,
                        content={'status': result['status']})


@router.get('/health/dependencies')
def dependencies(auth: AuthContext = Depends(get_auth_context)):
    require_read_operation_role('ops_metrics_read', auth)
    result = dependency_health()
    return JSONResponse(status_code=200 if result['ready'] else 503, content=result)
