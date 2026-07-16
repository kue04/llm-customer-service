import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class AuditAndReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.feedback_service = importlib.import_module("services.feedback_service")
        self.prompt_service = importlib.import_module("services.prompt_service")
        self.knowledge_service = importlib.import_module("services.knowledge_service")
        self.release_check_service = importlib.import_module("services.release_check_service")
        self.previous_feedback_db_path = self.feedback_service.DB_PATH
        self.previous_prompt_db_path = self.prompt_service.DB_PATH
        self.previous_knowledge_db_path = self.knowledge_service.DB_PATH
        self.previous_knowledge_path = self.knowledge_service.KNOWLEDGE_DATA_PATH
        self.previous_backup_dir = self.knowledge_service.BACKUP_DIR
        self.previous_grounding_report_dir = self.release_check_service.GROUNDING_REPORT_DIR
        self.previous_release_report_dir = self.release_check_service.RELEASE_REPORT_DIR
        self.feedback_service.DB_PATH = Path(self.temp_dir.name) / "ops_feedback.db"
        self.prompt_service.DB_PATH = Path(self.temp_dir.name) / "prompt_versions.db"
        self.knowledge_service.DB_PATH = Path(self.temp_dir.name) / "knowledge_ops.db"
        self.knowledge_service.KNOWLEDGE_DATA_PATH = Path(self.temp_dir.name) / "seed.jsonl"
        self.knowledge_service.BACKUP_DIR = Path(self.temp_dir.name) / "backups"
        self.release_check_service.GROUNDING_REPORT_DIR = Path(self.temp_dir.name) / "reports"
        self.release_check_service.RELEASE_REPORT_DIR = Path(self.temp_dir.name) / "release_reports"
        self.knowledge_service.KNOWLEDGE_DATA_PATH.write_text("", encoding="utf-8")
        self.addCleanup(self.restore_paths)

        audit_router = importlib.import_module("routers.audit")
        release_router = importlib.import_module("routers.release")
        app = FastAPI()
        app.include_router(audit_router.router, prefix="/audit")
        app.include_router(release_router.router, prefix="/release")
        self.app = app
        self.client = TestClient(app)
        self.client.headers.update({"X-User-Role": "admin", "X-Operator-Id": "admin_1"})

    def restore_paths(self) -> None:
        self.feedback_service.DB_PATH = self.previous_feedback_db_path
        self.prompt_service.DB_PATH = self.previous_prompt_db_path
        self.knowledge_service.DB_PATH = self.previous_knowledge_db_path
        self.knowledge_service.KNOWLEDGE_DATA_PATH = self.previous_knowledge_path
        self.knowledge_service.BACKUP_DIR = self.previous_backup_dir
        self.release_check_service.GROUNDING_REPORT_DIR = self.previous_grounding_report_dir
        self.release_check_service.RELEASE_REPORT_DIR = self.previous_release_report_dir

    def write_grounding_report(self, report_count: int, path_name: str = "release_eval.json") -> Path:
        report_dir = self.release_check_service.GROUNDING_REPORT_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        reports = []
        for index in range(report_count):
            high_risk = index % 2 == 0
            reports.append(
                {
                    "query": "骑手让我私下转账可以吗" if high_risk else f"退款进度 {index}",
                    "scenario": "平台安全" if high_risk else "退款售后",
                    "expected_intent": "私下收费风险" if high_risk else "退款进度",
                    "expected_evidence_keywords": ["官方渠道"],
                    "matched_evidence_keywords": ["官方渠道"],
                    "forbidden_keyword_hits": [],
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "yes",
                        "useful": "yes",
                    },
                }
            )
        report_path = report_dir / path_name
        report_path.write_text(
            json.dumps(
                {
                    "run_id": path_name.removesuffix(".json"),
                    "report_count": report_count,
                    "reports": reports,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report_path

    def record_audit_actions(self, action_types: list[str] | tuple[str, ...]) -> None:
        audit_service = importlib.import_module("services.audit_service")
        for action_type in action_types:
            audit_service.record_audit_log(
                operator_id="release_smoke",
                operator_role="admin",
                action_type=action_type,
                object_type="release_smoke",
                object_id=action_type,
                request_id=f"req-{action_type}",
                after_summary="ok",
            )

    def test_audit_logs_can_be_listed_and_filtered(self) -> None:
        audit_service = importlib.import_module("services.audit_service")
        audit_service.record_audit_log(
            operator_id="agent_1",
            operator_role="agent",
            action_type="chat_review_accepted",
            object_type="conversation_turn",
            object_id="req-1",
            request_id="req-1",
            after_summary="手机号13812345678",
        )

        response = self.client.get("/audit/logs?action_type=chat_review_accepted")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["request_id"], "req-1")
        self.assertNotIn("13812345678", body["items"][0]["after_summary"])

    def test_audit_and_release_routes_require_identity(self) -> None:
        bare_client = TestClient(self.app)

        self.assertEqual(bare_client.get("/audit/logs").status_code, 401)
        self.assertEqual(bare_client.get("/release/checklist").status_code, 401)

    def test_release_checklist_returns_required_gates(self) -> None:
        self.write_grounding_report(30)

        response = self.client.get("/release/checklist")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        gate_names = {item["name"] for item in body["items"]}
        self.assertIn("prompt_version", gate_names)
        self.assertIn("knowledge_version", gate_names)
        self.assertIn("tool_failure_fallback", gate_names)
        self.assertIn("privacy_masking", gate_names)
        self.assertIn("audit_coverage", gate_names)
        self.assertIn("auto_evaluation_report", gate_names)
        self.assertIn("warning_count", body)
        auto_eval = next(item for item in body["items"] if item["name"] == "auto_evaluation_report")
        self.assertEqual(auto_eval["status"], "warn")
        self.assertIn("cases=30", auto_eval["evidence"])
        self.assertIn("评测样本需", auto_eval["next_step"])

    def test_audit_coverage_gate_passes_when_required_actions_are_present(self) -> None:
        required_actions = self.release_check_service.REQUIRED_AUDIT_ACTION_TYPES
        self.record_audit_actions(required_actions)

        result = self.release_check_service.build_audit_coverage_status()

        self.assertEqual(result["status"], "pass")
        self.assertIn(f"covered={len(required_actions)}/{len(required_actions)}", result["evidence"])
        self.assertEqual(result["next_step"], "")

    def test_audit_coverage_gate_fails_when_required_action_is_missing(self) -> None:
        required_actions = self.release_check_service.REQUIRED_AUDIT_ACTION_TYPES
        actions = [action for action in required_actions if action != "knowledge_publish"]
        self.record_audit_actions(actions)

        result = self.release_check_service.build_audit_coverage_status()

        self.assertEqual(result["status"], "fail")
        self.assertIn("missing=knowledge_publish", result["evidence"])
        self.assertIn("knowledge_publish", result["next_step"])

    def test_auto_evaluation_report_gate_passes_when_release_metrics_are_met(self) -> None:
        self.write_grounding_report(100)

        result = self.release_check_service.build_auto_evaluation_report_status()

        self.assertEqual(result["status"], "pass")
        self.assertIn("cases=100", result["evidence"])
        self.assertIn("high_risk=50/50", result["evidence"])
        self.assertEqual(result["next_step"], "")

    def test_tool_failure_fallback_gate_passes_for_empty_failed_tool_outputs(self) -> None:
        result = self.release_check_service.build_tool_failure_fallback_status()

        self.assertEqual(result["status"], "pass")
        self.assertIn("cases=8", result["evidence"])
        self.assertIn("missing_order_id", result["evidence"])
        self.assertIn("order_not_found", result["evidence"])
        self.assertIn("order_user_mismatch", result["evidence"])
        self.assertIn("tool_timeout", result["evidence"])
        self.assertIn("tool_unavailable", result["evidence"])

    def test_tool_failure_fallback_gate_fails_when_failed_tool_returns_business_fact(self) -> None:
        result = self.release_check_service.build_tool_failure_fallback_status(
            [
                {
                    "tool_name": "query_order_status",
                    "status": "failed",
                    "error_type": "order_not_found",
                    "output": {"status_label": "已送达"},
                }
            ]
        )

        self.assertEqual(result["status"], "fail")
        self.assertIn("unsafe=query_order_status:order_not_found", result["evidence"])

    def test_token_tracking_warns_without_runtime_sample(self) -> None:
        result = self.release_check_service.build_token_tracking_status(
            {
                "request_count": 0,
                "token_recorded_count": 0,
                "total_tokens": 0,
                "average_tokens_per_request": 0.0,
            }
        )

        self.assertEqual(result["status"], "warn")
        self.assertIn("执行一次 chat 请求", result["next_step"])

    def test_token_tracking_fails_when_request_has_no_token_record(self) -> None:
        result = self.release_check_service.build_token_tracking_status(
            {
                "request_count": 2,
                "token_recorded_count": 1,
                "total_tokens": 80,
                "average_tokens_per_request": 40.0,
            }
        )

        self.assertEqual(result["status"], "fail")
        self.assertIn("未记录 token usage", result["next_step"])

    def test_token_tracking_passes_when_all_requests_have_nonzero_tokens(self) -> None:
        result = self.release_check_service.build_token_tracking_status(
            {
                "request_count": 2,
                "token_recorded_count": 2,
                "total_tokens": 80,
                "average_tokens_per_request": 40.0,
            }
        )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["next_step"], "")

    def test_token_tracking_uses_persisted_chat_sample_after_restart(self) -> None:
        self.feedback_service.save_chat_session(
            query="退款多久到账",
            reply="请查看订单详情页。",
            trace={
                "request_id": "req-persisted-token",
                "top1_intent": "退款进度",
                "latency_ms": 10,
                "answer_source": "rag",
            },
            token_usage={
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42,
                "counting_method": "test_counter",
            },
        )

        with patch.object(
            self.release_check_service,
            "get_ops_metrics",
            return_value={
                "request_count": 0,
                "token_recorded_count": 0,
                "total_tokens": 0,
                "average_tokens_per_request": 0.0,
            },
        ):
            result = self.release_check_service.build_token_tracking_status()

        self.assertEqual(result["status"], "pass")
        self.assertIn("source=persisted_chat_sessions", result["evidence"])
        self.assertIn("latest_request_id=req-persisted-token", result["evidence"])

    def test_token_tracking_uses_latest_persisted_sample_when_history_is_partial(self) -> None:
        self.feedback_service.save_chat_session(
            query="token check",
            reply="token tracked",
            trace={
                "request_id": "req-latest-token",
                "top1_intent": "token",
                "latency_ms": 10,
                "answer_source": "rag",
            },
            token_usage={
                "prompt_tokens": 11,
                "completion_tokens": 4,
                "total_tokens": 15,
                "counting_method": "test_counter",
            },
        )

        with patch.object(
            self.release_check_service,
            "get_ops_metrics",
            return_value={
                "request_count": 5,
                "token_recorded_count": 1,
                "total_tokens": 15,
                "average_tokens_per_request": 3.0,
                "source": "persisted_chat_sessions",
            },
        ):
            result = self.release_check_service.build_token_tracking_status()

        self.assertEqual(result["status"], "pass")
        self.assertIn("source=persisted_chat_sessions", result["evidence"])
        self.assertIn("latest_request_id=req-latest-token", result["evidence"])


if __name__ == "__main__":
    unittest.main()
