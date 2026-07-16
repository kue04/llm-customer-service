from fastapi import APIRouter, Depends, HTTPException, Query

from schemas.prompt_schema import (
    PromptVersionItem,
    PromptVersionListResponse,
    PromptVersionPayload,
    PromptVersionStatusRequest,
)
from services.audit_service import record_audit_log
from services.auth_service import get_operator_context, require_read_operation_role, require_write_operation_role
from services.prompt_service import (
    activate_prompt_version,
    create_prompt_version,
    get_active_prompt_config,
    list_prompt_versions,
    rollback_latest_prompt_version,
    update_prompt_version_status,
)

router = APIRouter()


def _audit_prompt_action(
    operator_context: dict,
    action_type: str,
    prompt_version: dict,
    before_summary: str = "",
) -> None:
    record_audit_log(
        operator_id=operator_context["operator_id"],
        operator_role=operator_context["role"],
        action_type=action_type,
        object_type="prompt_version",
        object_id=str(prompt_version["id"]),
        before_summary=before_summary,
        after_summary=f"{prompt_version['version']}:{prompt_version['status']}",
        ip=operator_context.get("ip", ""),
        device_info=operator_context.get("user_agent", ""),
    )


@router.get("/versions", response_model=PromptVersionListResponse)
def prompt_versions(
    limit: int = Query(default=20, ge=1, le=100),
    operator_context: dict = Depends(get_operator_context),
):
    require_read_operation_role("prompt_read", operator_context)
    return list_prompt_versions(limit=limit)


@router.get("/active", response_model=PromptVersionItem)
def active_prompt(operator_context: dict = Depends(get_operator_context)):
    require_read_operation_role("prompt_read", operator_context)
    return get_active_prompt_config()


@router.post("/versions", response_model=PromptVersionItem)
def create_version(
    request: PromptVersionPayload,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("prompt_write", operator_context)
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    try:
        version = create_prompt_version(payload, author=operator_context["operator_id"])
    except Exception as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit_prompt_action(operator_context, "prompt_version_create", version)
    return version


@router.post("/versions/{version_id}/status", response_model=PromptVersionItem)
def update_status(
    version_id: int,
    request: PromptVersionStatusRequest,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("prompt_write", operator_context)
    try:
        version = update_prompt_version_status(version_id, request.status, request.evaluation_result)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit_prompt_action(operator_context, "prompt_version_status", version)
    return version


@router.post("/versions/{version_id}/activate", response_model=PromptVersionItem)
def activate_version(
    version_id: int,
    operator_context: dict = Depends(get_operator_context),
):
    require_write_operation_role("prompt_write", operator_context)
    try:
        version = activate_prompt_version(version_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit_prompt_action(operator_context, "prompt_version_activate", version)
    return version


@router.post("/rollback-latest", response_model=PromptVersionItem)
def rollback_latest(operator_context: dict = Depends(get_operator_context)):
    require_write_operation_role("prompt_write", operator_context)
    try:
        version = rollback_latest_prompt_version()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _audit_prompt_action(operator_context, "prompt_version_rollback", version)
    return version
