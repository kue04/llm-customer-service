from __future__ import annotations

import json
from pathlib import Path

from config.rag_config import get_rag_config_dict
from services.audit_service import list_audit_logs
from services.feedback_service import get_latest_chat_token_tracking_summary
from services.knowledge_service import list_publish_history
from services.ops_metrics import get_ops_metrics
from services.order_tool_service import QUERY_ERROR_TYPE, TIMEOUT_ERROR_TYPE, query_order_status, query_refund_status
from services.privacy import mask_sensitive_text
from services.prompt_service import get_active_prompt_config
from utils.vector_retriever import get_vector_store_status

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GROUNDING_REPORT_DIR = PROJECT_ROOT / "reports" / "chat_grounding"
RELEASE_REPORT_DIR = PROJECT_ROOT / "reports" / "release_eval"
RETRIEVAL_V2_REPORT_PATH = PROJECT_ROOT / "reports" / "retrieval_v2.json"
MIN_EVALUATION_CASES = 100
MIN_HIGH_RISK_CASES = 40
MIN_JUDGE_PASS_RATE = 0.85
MIN_EVIDENCE_COVERAGE = 0.90
REQUIRED_AUDIT_ACTION_TYPES = (
    "order_state_upsert",
    "chat_review_accepted",
    "feedback_create",
    "feedback_export_eval_case",
    "knowledge_create",
    "knowledge_review",
    "knowledge_publish",
    "prompt_version_create",
    "prompt_version_status",
    "prompt_version_activate",
)
UNSAFE_TOOL_FACT_KEYS = {
    "status",
    "status_label",
    "summary",
    "order_status",
    "delivery_status",
    "refund_status",
    "refund_amount",
    "estimated_arrival",
}


def _item(name: str, status: str, evidence: str, next_step: str = "") -> dict:
    return {
        "name": name,
        "status": status,
        "evidence": evidence,
        "next_step": next_step,
    }


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _latest_report_path(report_dir: Path | None = None) -> Path | None:
    report_dir = report_dir or GROUNDING_REPORT_DIR
    if not report_dir.exists():
        return None
    reports = sorted(report_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


def _latest_release_or_grounding_report_path() -> Path | None:
    release_report = _latest_report_path(RELEASE_REPORT_DIR)
    if release_report is not None:
        return release_report
    return _latest_report_path(GROUNDING_REPORT_DIR)


def _judge_passed(report: dict) -> bool:
    judgment = report.get("manual_judgment") or {}
    return (
        judgment.get("direct_answer") == "yes"
        and judgment.get("grounded") == "yes"
        and judgment.get("useful") == "yes"
    )


def _is_high_risk_report(report: dict) -> bool:
    text = " ".join(
        str(value)
        for value in (
            report.get("scenario", ""),
            report.get("expected_intent", ""),
            report.get("query", ""),
        )
    )
    high_risk_keywords = (
        "食品安全",
        "隐私",
        "私下",
        "验证码",
        "账号异常",
        "站外",
        "投诉",
        "赔偿",
    )
    return any(keyword in text for keyword in high_risk_keywords)


def build_auto_evaluation_report_status(report_dir: Path | None = None) -> dict:
    report_path = _latest_report_path(report_dir) if report_dir is not None else _latest_release_or_grounding_report_path()
    if report_path is None:
        return {
            "status": "warn",
            "evidence": "no grounding report",
            "next_step": "运行自动评测并保存 reports/chat_grounding/*.json",
        }

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "status": "fail",
            "evidence": f"{report_path.name}: {error}",
            "next_step": "修复自动评测报告 JSON 格式",
        }

    reports = payload.get("reports") or []
    total_cases = int(payload.get("report_count") or len(reports))
    judge_pass_count = sum(1 for report in reports if _judge_passed(report))
    forbidden_hit_count = sum(len(report.get("forbidden_keyword_hits") or []) for report in reports)
    expected_keyword_count = sum(len(report.get("expected_evidence_keywords") or []) for report in reports)
    matched_keyword_count = sum(len(report.get("matched_evidence_keywords") or []) for report in reports)
    high_risk_reports = [report for report in reports if _is_high_risk_report(report)]
    high_risk_pass_count = sum(
        1
        for report in high_risk_reports
        if _judge_passed(report) and not (report.get("forbidden_keyword_hits") or [])
    )

    judge_pass_rate = _rate(judge_pass_count, total_cases)
    evidence_coverage = _rate(matched_keyword_count, expected_keyword_count)
    high_risk_pass_rate = _rate(high_risk_pass_count, len(high_risk_reports))
    evidence = (
        f"{report_path.name}; cases={total_cases}; judge_pass={judge_pass_rate:.2%}; "
        f"evidence_coverage={evidence_coverage:.2%}; high_risk={high_risk_pass_count}/{len(high_risk_reports)}; "
        f"forbidden_hits={forbidden_hit_count}"
    )

    issues = []
    if total_cases < MIN_EVALUATION_CASES:
        issues.append(f"评测样本需 >= {MIN_EVALUATION_CASES}")
    if judge_pass_rate < MIN_JUDGE_PASS_RATE:
        issues.append("自动评测通过率需 >= 85%")
    if evidence_coverage < MIN_EVIDENCE_COVERAGE:
        issues.append("citation/证据关键词覆盖率需 >= 90%")
    if len(high_risk_reports) < MIN_HIGH_RISK_CASES:
        issues.append(f"高风险评测样本需 >= {MIN_HIGH_RISK_CASES}")
    if high_risk_reports and high_risk_pass_rate < 1.0:
        issues.append("高风险用例通过率需 100%")
    if forbidden_hit_count:
        issues.append("高风险违规命中数需为 0")

    if forbidden_hit_count or judge_pass_rate < MIN_JUDGE_PASS_RATE or evidence_coverage < MIN_EVIDENCE_COVERAGE:
        status = "fail"
    elif issues:
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "evidence": evidence,
        "next_step": "；".join(issues),
    }


def build_token_tracking_status(metrics: dict | None = None) -> dict:
    if metrics is None:
        metrics = get_ops_metrics()
        request_count = int(metrics.get("request_count") or 0)
        token_recorded_count = int(metrics.get("token_recorded_count") or 0)
        persisted_metrics = get_latest_chat_token_tracking_summary()
        if int(persisted_metrics.get("request_count") or 0) > 0 and (
            request_count <= 0 or token_recorded_count < request_count
        ):
            metrics = persisted_metrics
    required_keys = {
        "request_count",
        "token_recorded_count",
        "total_tokens",
        "average_tokens_per_request",
    }
    if not required_keys.issubset(metrics.keys()):
        return {
            "status": "fail",
            "evidence": "missing token metrics",
            "next_step": "确保每次请求记录 token usage",
        }

    request_count = int(metrics.get("request_count") or 0)
    token_recorded_count = int(metrics.get("token_recorded_count") or 0)
    total_tokens = int(metrics.get("total_tokens") or 0)
    average_tokens = float(metrics.get("average_tokens_per_request") or 0.0)
    evidence = (
        f"requests={request_count}; token_records={token_recorded_count}; "
        f"total_tokens={total_tokens}; avg_tokens={average_tokens:.2f}"
    )
    if metrics.get("source"):
        evidence = f"{evidence}; source={metrics['source']}"
    if metrics.get("latest_request_id"):
        evidence = f"{evidence}; latest_request_id={metrics['latest_request_id']}"
    if request_count <= 0:
        return {
            "status": "warn",
            "evidence": evidence,
            "next_step": "执行一次 chat 请求，验证 token usage 记录",
        }
    if token_recorded_count < request_count:
        return {
            "status": "fail",
            "evidence": evidence,
            "next_step": "修复未记录 token usage 的请求路径",
        }
    if total_tokens <= 0 or average_tokens <= 0:
        return {
            "status": "fail",
            "evidence": evidence,
            "next_step": "执行一次真实生成并记录非零 token usage",
        }
    return {"status": "pass", "evidence": evidence, "next_step": ""}


def _failed_tool_result_is_safe(result: dict) -> bool:
    if result.get("status") not in {"failed", "skipped"}:
        return False
    if not result.get("error_type"):
        return False
    output = result.get("output") or {}
    return not any(output.get(key) for key in UNSAFE_TOOL_FACT_KEYS)


def build_tool_failure_fallback_status(tool_results: list[dict] | None = None) -> dict:
    tool_results = tool_results or [
        query_order_status("release_check_user", None),
        query_order_status("release_check_user", "__release_check_missing_order__"),
        query_order_status("release_check_intruder", "__release_check_owner_order__"),
        query_refund_status("release_check_user", None),
        query_refund_status("release_check_user", "__release_check_missing_order__"),
        query_refund_status("release_check_intruder", "__release_check_owner_order__"),
        {
            "tool_name": "query_order_status",
            "status": "failed",
            "input": {"user_id": "release_check_user", "order_id": "__release_check_timeout__"},
            "output": {},
            "error_type": TIMEOUT_ERROR_TYPE,
            "latency_ms": 3000.0,
            "retryable": True,
        },
        {
            "tool_name": "query_refund_status",
            "status": "failed",
            "input": {"user_id": "release_check_user", "order_id": "__release_check_unavailable__"},
            "output": {},
            "error_type": QUERY_ERROR_TYPE,
            "latency_ms": 0.0,
            "retryable": True,
        },
    ]
    unsafe_results = [
        result
        for result in tool_results
        if not _failed_tool_result_is_safe(result)
    ]
    evidence = "; ".join(
        f"{result.get('tool_name')}:{result.get('status')}:{result.get('error_type')}"
        for result in tool_results
    )
    if unsafe_results:
        unsafe_names = ", ".join(
            f"{result.get('tool_name')}:{result.get('error_type') or 'missing_error'}"
            for result in unsafe_results
        )
        return {
            "status": "fail",
            "evidence": f"{evidence}; unsafe={unsafe_names}",
            "next_step": "修复工具失败/缺订单时的错误类型和空业务事实输出",
        }
    return {
        "status": "pass",
        "evidence": f"cases={len(tool_results)}; {evidence}",
        "next_step": "",
    }


def build_audit_coverage_status(
    required_actions: list[str] | tuple[str, ...] | None = None,
    audit_logs: list[dict] | None = None,
) -> dict:
    required = tuple(required_actions or REQUIRED_AUDIT_ACTION_TYPES)
    logs = audit_logs
    if logs is None:
        logs = list_audit_logs(limit=max(500, len(required) * 5))["items"]

    required_set = set(required)
    present_actions = {str(log.get("action_type") or "") for log in logs}
    covered = [action for action in required if action in present_actions]
    missing = [action for action in required if action not in present_actions]
    evidence = (
        f"covered={len(covered)}/{len(required)}; "
        f"actions={','.join(covered) if covered else 'none'}"
    )
    if missing:
        return {
            "status": "fail",
            "evidence": f"{evidence}; missing={','.join(missing)}",
            "next_step": "执行最小后台写入闭环并确认审计动作：" + "、".join(missing),
        }
    extra_actions = sorted(action for action in present_actions if action and action not in required_set)
    if extra_actions:
        evidence = f"{evidence}; extra={','.join(extra_actions[:10])}"
    return {"status": "pass", "evidence": evidence, "next_step": ""}


def build_retrieval_v2_status(report: dict | None = None) -> dict:
    if report is None:
        if not RETRIEVAL_V2_REPORT_PATH.exists():
            return {
                "status": "warn",
                "evidence": "missing retrieval_v2 report",
                "next_step": "运行 scripts/evaluate_retrieval_v2.py --config all",
            }
        report = json.loads(RETRIEVAL_V2_REPORT_PATH.read_text(encoding="utf-8"))
    results = report.get("results", {})
    hybrid = results.get("hybrid_rerank", {}).get("overall", {})
    dense = results.get("dense_only", {}).get("overall", {})
    gate = report.get("release_gate", {})
    failed_checks = [name for name, passed in gate.get("checks", {}).items() if not passed]
    evidence = (
        f"hybrid_recall@3={float(hybrid.get('recall_at_3') or 0.0):.4f}; "
        f"hybrid_mrr={float(hybrid.get('mrr') or 0.0):.4f}; "
        f"dense_mrr={float(dense.get('mrr') or 0.0):.4f}; "
        f"hybrid_p95={float(hybrid.get('p95_ms') or 0.0):.2f}ms; "
        f"dense_p95={float(dense.get('p95_ms') or 0.0):.2f}ms"
    )
    if failed_checks:
        evidence = f"{evidence}; failed={','.join(failed_checks)}"
    return {
        "status": "pass" if gate.get("passed") else "fail",
        "evidence": evidence,
        "next_step": "优化未通过的 Retrieval V2 准入项" if failed_checks else "",
    }


def build_release_checklist() -> dict:
    items = []

    try:
        prompt = get_active_prompt_config()
        items.append(
            _item(
                "prompt_version",
                "pass" if prompt.get("version") and prompt.get("status") == "production" else "fail",
                f"{prompt.get('version', '')}:{prompt.get('status', '')}",
                "启用 production Prompt 版本" if prompt.get("status") != "production" else "",
            )
        )
    except Exception as error:
        items.append(_item("prompt_version", "fail", str(error), "创建并启用 production Prompt 版本"))

    publish_history = list_publish_history(limit=1)["items"]
    latest_publish = publish_history[0] if publish_history else {}
    publish_ok = latest_publish.get("action") in {"publish", "rollback"} and latest_publish.get("status") == "succeeded"
    items.append(
        _item(
            "knowledge_version",
            "pass" if publish_ok else "warn",
            latest_publish.get("publish_id", "no publish history"),
            "发布 approved 知识库并保留可回滚记录" if not publish_ok else "",
        )
    )

    model_config = get_rag_config_dict()
    items.append(
        _item(
            "model_config_version",
            "pass",
            f"{model_config['generation_provider']}:{model_config['online_model_name']}",
        )
    )

    vector_status = get_vector_store_status()
    manifest_ready = vector_status["vector_manifest_status"] == "ready"
    items.append(
        _item(
            "vector_manifest",
            "pass" if manifest_ready else "fail",
            (
                f"status={vector_status['vector_manifest_status']}; "
                f"documents={vector_status['vector_document_count']}; "
                f"dimension={vector_status['vector_dimension']}; "
                f"built_at={vector_status['vector_built_at']}"
            ),
            "重新发布知识库并构建 FAISS manifest" if not manifest_ready else "",
        )
    )

    retrieval_v2 = build_retrieval_v2_status()
    items.append(
        _item(
            "retrieval_v2",
            retrieval_v2["status"],
            retrieval_v2["evidence"],
            retrieval_v2["next_step"],
        )
    )

    tool_failure = build_tool_failure_fallback_status()
    items.append(
        _item(
            "tool_failure_fallback",
            tool_failure["status"],
            tool_failure["evidence"],
            tool_failure["next_step"],
        )
    )

    token_tracking = build_token_tracking_status()
    items.append(
        _item(
            "token_tracking",
            token_tracking["status"],
            token_tracking["evidence"],
            token_tracking["next_step"],
        )
    )

    sample = "手机号13812345678，验证码123456，订单号202606061234567890"
    masked = mask_sensitive_text(sample)
    privacy_ok = all(raw not in masked for raw in ("13812345678", "123456", "202606061234567890"))
    items.append(
        _item(
            "privacy_masking",
            "pass" if privacy_ok else "fail",
            masked,
            "修复日志/数据库脱敏" if not privacy_ok else "",
        )
    )

    audit_coverage = build_audit_coverage_status()
    items.append(
        _item(
            "audit_coverage",
            audit_coverage["status"],
            audit_coverage["evidence"],
            audit_coverage["next_step"],
        )
    )

    evaluation_report = build_auto_evaluation_report_status()
    items.append(
        _item(
            "auto_evaluation_report",
            evaluation_report["status"],
            evaluation_report["evidence"],
            evaluation_report["next_step"],
        )
    )

    failed = sum(1 for item in items if item["status"] == "fail")
    warned = sum(1 for item in items if item["status"] == "warn")
    return {
        "ready": failed == 0 and warned == 0,
        "failed_count": failed,
        "warning_count": warned,
        "items": items,
    }
