from fastapi import APIRouter, Depends
from schemas.info_schema import ModelInfoResponse
from services.auth_service import get_operator_context, require_read_operation_role

router = APIRouter()


@router.get("/info", response_model=ModelInfoResponse)
def model_info(operator_context: dict = Depends(get_operator_context)):
    require_read_operation_role("model_info_read", operator_context)
    from services.chat_service import get_model_info

    return get_model_info()
