import importlib
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


class FeedbackOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.feedback_service = importlib.import_module("services.feedback_service")
        self.previous_db_path = self.feedback_service.DB_PATH
        self.feedback_service.DB_PATH = Path(self.temp_dir.name) / "ops_feedback.db"
        self.addCleanup(self.restore_db_path)

        feedback_router = importlib.import_module("routers.feedback")
        ops_router = importlib.import_module("routers.ops")
        app = FastAPI()
        app.include_router(feedback_router.router, prefix="/feedback")
        app.include_router(ops_router.router, prefix="/ops")
        self.client = TestClient(app)

    def restore_db_path(self) -> None:
        self.feedback_service.DB_PATH = self.previous_db_path

    def test_feedback_save_recent_and_export_eval_case(self) -> None:
        payload = {
            "request_id": "req-1",
            "query": "付款失败又被扣款",
            "reply": "请查看订单状态。",
            "helpful": False,
            "reason": "不够直接",
            "expected_reply": "说明去哪里看进度",
            "trace": {
                "top1_intent": "退款失败",
                "latency_ms": 12.5,
                "answer_source": "rag",
                "failure_stage": "none",
            },
        }

        create_response = self.client.post("/feedback", json=payload)
        self.assertEqual(create_response.status_code, 200)
        feedback_id = create_response.json()["feedback_id"]

        recent_response = self.client.get("/feedback/recent?helpful=false")
        self.assertEqual(recent_response.status_code, 200)
        recent = recent_response.json()
        self.assertEqual(recent["count"], 1)
        self.assertEqual(recent["items"][0]["request_id"], "req-1")
        self.assertEqual(recent["items"][0]["top1_intent"], "退款失败")

        export_response = self.client.post("/feedback/export-eval-case", json={"feedback_id": feedback_id})
        self.assertEqual(export_response.status_code, 200)
        eval_case = export_response.json()["eval_case"]
        self.assertEqual(eval_case["query"], "付款失败又被扣款")
        self.assertEqual(eval_case["expected_intent"], "退款失败")
        self.assertIn("expected_evidence_keywords", eval_case)
        self.assertIn("forbidden_keywords", eval_case)

    def test_feedback_requires_core_fields(self) -> None:
        response = self.client.post("/feedback", json={"helpful": False})

        self.assertEqual(response.status_code, 422)

    def test_ops_metrics_returns_base_shape(self) -> None:
        response = self.client.get("/ops/metrics")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("request_count", body)
        self.assertIn("p95_latency_ms", body)
        self.assertIn("reply_rules_hit_count", body)

    def test_mask_sensitive_text_masks_phone_code_and_order_id(self) -> None:
        privacy = importlib.import_module("services.privacy")

        text = privacy.mask_sensitive_text("手机号13812345678，验证码123456，订单号202606061234567890")

        self.assertIn("[手机号已脱敏]", text)
        self.assertIn("验证码[已脱敏]", text)
        self.assertIn("[订单号已脱敏]", text)


if __name__ == "__main__":
    unittest.main()
