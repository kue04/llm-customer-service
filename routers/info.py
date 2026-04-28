from fastapi import APIRouter
from schemas.info_schema import ModelInfoResponse
from services.chat_service import get_model_info

router = APIRouter()


@router.get("/info", response_model=ModelInfoResponse)
def model_info():
    return get_model_info()
