from fastapi import APIRouter, Depends

from schemas.release_schema import ReleaseChecklistResponse
from services.auth_service import get_operator_context, require_read_operation_role
from services.release_check_service import build_release_checklist

router = APIRouter()


@router.get("/checklist", response_model=ReleaseChecklistResponse)
def release_checklist(operator_context: dict = Depends(get_operator_context)):
    require_read_operation_role("release_read", operator_context)
    return build_release_checklist()
