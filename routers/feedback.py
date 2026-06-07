from fastapi import APIRouter, HTTPException, Query

from schemas.feedback_schema import (
    ExportEvalCaseRequest,
    ExportEvalCaseResponse,
    FeedbackRequest,
    FeedbackResponse,
    RecentFeedbackResponse,
)
from services.feedback_service import build_eval_case_from_feedback, list_recent_feedback, save_feedback

router = APIRouter()


@router.post("", response_model=FeedbackResponse)
def create_feedback(request: FeedbackRequest):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    feedback_id = save_feedback(payload)
    return {"feedback_id": feedback_id, "saved": True}


@router.get("/recent", response_model=RecentFeedbackResponse)
def recent_feedback(
    limit: int = Query(default=20, ge=1, le=100),
    helpful: bool | None = None,
    intent: str = "",
    failure_stage: str = "",
):
    items = list_recent_feedback(limit=limit, helpful=helpful, intent=intent, failure_stage=failure_stage)
    return {"count": len(items), "items": items}


@router.post("/export-eval-case", response_model=ExportEvalCaseResponse)
def export_eval_case(request: ExportEvalCaseRequest):
    try:
        eval_case = build_eval_case_from_feedback(request.feedback_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"feedback_id": request.feedback_id, "eval_case": eval_case}
