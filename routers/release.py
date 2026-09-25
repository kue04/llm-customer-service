from fastapi import APIRouter, Depends

from schemas.release_schema import ReleaseChecklistResponse
from services.auth_service import AuthContext, get_auth_context, require_read_operation_role
from services.release_check_service import build_release_checklist

router = APIRouter()


@router.get("/checklist", response_model=ReleaseChecklistResponse)
def release_checklist(auth: AuthContext = Depends(get_auth_context)):
    require_read_operation_role("release_read", auth)
    return build_release_checklist()
