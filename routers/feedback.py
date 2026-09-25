from fastapi import APIRouter, Depends, HTTPException, Query

from schemas.feedback_schema import (
    ExportEvalCaseRequest,
    ExportEvalCaseResponse,
    FeedbackRequest,
    FeedbackResponse,
    RecentFeedbackResponse,
)
from services.audit_service import record_audit_log
from services.auth_service import (
    AuthContext,
    RequestMeta,
    get_auth_context,
    get_request_meta,
    require_read_operation_role,
    require_write_operation_role,
)
from services.feedback_service import build_eval_case_from_feedback, list_recent_feedback, save_feedback

router = APIRouter()


@router.post("", response_model=FeedbackResponse)
def create_feedback(
    request: FeedbackRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("feedback_create", auth)
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    feedback_id = save_feedback(payload)
    record_audit_log(
        operator_id=auth.user_id,
        operator_role=auth.primary_role,
        action_type="feedback_create",
        object_type="feedback",
        object_id=str(feedback_id),
        request_id=payload["request_id"],
        after_summary=f"helpful={payload['helpful']}; reason={payload.get('reason', '')}",
        ip=meta.ip,
        device_info=meta.user_agent,
    )
    return {"feedback_id": feedback_id, "saved": True}


@router.get("/recent", response_model=RecentFeedbackResponse)
def recent_feedback(
    limit: int = Query(default=20, ge=1, le=100),
    helpful: bool | None = None,
    intent: str = "",
    failure_stage: str = "",
    auth: AuthContext = Depends(get_auth_context),
):
    require_read_operation_role("feedback_read", auth)
    items = list_recent_feedback(limit=limit, helpful=helpful, intent=intent, failure_stage=failure_stage)
    return {"count": len(items), "items": items}


@router.post("/export-eval-case", response_model=ExportEvalCaseResponse)
def export_eval_case(
    request: ExportEvalCaseRequest,
    auth: AuthContext = Depends(get_auth_context),
    meta: RequestMeta = Depends(get_request_meta),
):
    require_write_operation_role("feedback_export_eval_case", auth)
    try:
        eval_case = build_eval_case_from_feedback(request.feedback_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    record_audit_log(
        operator_id=auth.user_id,
        operator_role=auth.primary_role,
        action_type="feedback_export_eval_case",
        object_type="feedback",
        object_id=str(request.feedback_id),
        request_id=auth.request_id,
        after_summary=eval_case.get("id", ""),
        ip=meta.ip,
        device_info=meta.user_agent,
    )
    return {"feedback_id": request.feedback_id, "eval_case": eval_case}
