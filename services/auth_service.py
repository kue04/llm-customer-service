from __future__ import annotations

from fastapi import Header, HTTPException, Request


VALID_ROLES = {"agent", "supervisor", "knowledge_ops", "qa", "admin"}


REVIEW_ACTION_ROLES = {
    "accepted": {"agent", "supervisor", "admin"},
    "edited_and_sent": {"agent", "supervisor", "admin"},
    "human_handoff": {"agent", "supervisor", "admin"},
    "marked_bad_case": {"agent", "supervisor", "qa", "knowledge_ops", "admin"},
}

WRITE_OPERATION_ROLES = {
    "knowledge_create": {"supervisor", "knowledge_ops", "admin"},
    "knowledge_update": {"supervisor", "knowledge_ops", "admin"},
    "knowledge_archive": {"supervisor", "knowledge_ops", "admin"},
    "knowledge_review": {"supervisor", "knowledge_ops", "admin"},
    "knowledge_publish": {"supervisor", "knowledge_ops", "admin"},
    "knowledge_rollback": {"supervisor", "admin"},
    "feedback_create": {"agent", "supervisor", "qa", "knowledge_ops", "admin"},
    "feedback_export_eval_case": {"agent", "supervisor", "qa", "knowledge_ops", "admin"},
    "order_state_upsert": {"agent", "supervisor", "admin"},
    "prompt_write": {"admin"},
}

READ_OPERATION_ROLES = {
    "chat_generate": {"agent", "supervisor", "qa", "admin"},
    "chat_history": {"agent", "supervisor", "qa", "admin"},
    "knowledge_read": {"supervisor", "knowledge_ops", "qa", "admin"},
    "feedback_read": {"supervisor", "qa", "knowledge_ops", "admin"},
    "ops_metrics_read": {"supervisor", "qa", "admin"},
    "order_state_read": {"agent", "supervisor", "admin"},
    "prompt_read": {"supervisor", "qa", "admin"},
    "audit_read": {"supervisor", "qa", "admin"},
    "release_read": {"supervisor", "qa", "admin"},
    "retrieval_read": {"agent", "supervisor", "qa", "admin"},
    "model_info_read": {"supervisor", "qa", "admin"},
    "example_read": {"supervisor", "knowledge_ops", "qa", "admin"},
}


def get_operator_context(
    request: Request,
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
    x_operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
) -> dict:
    role = (x_user_role or "").strip()
    operator_id = (x_operator_id or "").strip()
    if not role or not operator_id:
        raise HTTPException(status_code=401, detail="missing operator identity headers")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"unknown role {role}")
    return {
        "operator_id": operator_id,
        "role": role,
        "ip": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
    }


def require_review_action_role(action: str, operator_context: dict) -> None:
    role = str(operator_context.get("role", ""))
    allowed_roles = REVIEW_ACTION_ROLES.get(action, set())
    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"role {role or 'unknown'} cannot perform {action}",
        )


def require_write_operation_role(operation: str, operator_context: dict) -> None:
    role = str(operator_context.get("role", ""))
    allowed_roles = WRITE_OPERATION_ROLES.get(operation, set())
    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"role {role or 'unknown'} cannot perform {operation}",
        )


def require_read_operation_role(operation: str, operator_context: dict) -> None:
    role = str(operator_context.get("role", ""))
    allowed_roles = READ_OPERATION_ROLES.get(operation, set())
    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"role {role or 'unknown'} cannot read {operation}",
        )
