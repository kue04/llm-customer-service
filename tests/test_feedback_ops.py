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
        self.app = app
        self.client = TestClient(app)
        self.client.headers.update(
            {
                "X-User-Role": "admin",
                "X-Operator-Id": "admin_1",
            }
        )

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

    def test_backoffice_routes_require_operator_identity(self) -> None:
        bare_client = TestClient(self.app)

        feedback_response = bare_client.post("/feedback", json={"helpful": False})
        ops_response = bare_client.get("/ops/metrics")

        self.assertEqual(feedback_response.status_code, 401)
        self.assertEqual(ops_response.status_code, 401)

    def test_ops_metrics_returns_base_shape(self) -> None:
        response = self.client.get("/ops/metrics")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("source", body)
        self.assertIn("request_count", body)
        self.assertIn("p95_latency_ms", body)
        self.assertIn("reply_rules_hit_count", body)
        self.assertIn("accepted_rate", body)
        self.assertIn("human_handoff_count", body)
        self.assertIn("average_tokens_per_request", body)
        self.assertIn("token_recorded_count", body)
        self.assertIn("token_record_rate", body)

    def test_ops_metrics_records_review_actions_and_token_usage(self) -> None:
        ops_metrics = importlib.import_module("services.ops_metrics")
        before = ops_metrics.get_ops_metrics()

        ops_metrics.record_chat_metrics(
            {
                "retrieval_count": 1,
                "failure_stage": "none",
                "latency_ms": 20,
                "answer_source": "rag",
            }
        )
        ops_metrics.record_token_usage(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        ops_metrics.record_review_action_metrics("accepted")
        ops_metrics.record_review_action_metrics("human_handoff")

        after = ops_metrics.get_ops_metrics()
        self.assertEqual(after["request_count"], before["request_count"] + 1)
        self.assertEqual(after["accepted_count"], before["accepted_count"] + 1)
        self.assertEqual(after["human_handoff_count"], before["human_handoff_count"] + 1)
        self.assertEqual(after["token_recorded_count"], before["token_recorded_count"] + 1)
        self.assertEqual(after["total_tokens"], before["total_tokens"] + 15)
        self.assertGreater(after["token_record_rate"], 0)
        self.assertGreater(after["average_tokens_per_request"], 0)

    def test_ops_metrics_reads_persisted_chat_and_review_metrics(self) -> None:
        ops_metrics = importlib.import_module("services.ops_metrics")
        self.feedback_service.save_chat_session(
            query="refund status",
            reply="checking refund",
            trace={
                "request_id": "req-persisted-1",
                "retrieval_count": 1,
                "reply_rules_applied": True,
                "latency_ms": 20,
                "answer_source": "rag",
                "failure_stage": "none",
            },
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        self.feedback_service.save_chat_session(
            query="refund failed",
            reply="fallback reply",
            trace={
                "request_id": "req-persisted-2",
                "retrieval_count": 0,
                "degraded": True,
                "used_fallback_prompt": True,
                "latency_ms": 80,
                "answer_source": "fallback",
                "failure_stage": "model_error",
            },
            token_usage={"prompt_tokens": 6, "completion_tokens": 3, "total_tokens": 9},
        )
        connection = self.feedback_service.get_connection()
        try:
            connection.execute(
                "CREATE TABLE conversation_review_actions (action TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO conversation_review_actions (action) VALUES (?)",
                [("accepted",), ("edited_and_sent",)],
            )
            connection.commit()
        finally:
            connection.close()

        metrics = ops_metrics.get_ops_metrics()

        self.assertEqual(metrics["source"], "persisted_chat_sessions")
        self.assertEqual(metrics["request_count"], 2)
        self.assertEqual(metrics["failure_count"], 1)
        self.assertEqual(metrics["empty_retrieval_count"], 1)
        self.assertEqual(metrics["reply_rules_hit_count"], 1)
        self.assertEqual(metrics["fallback_count"], 1)
        self.assertEqual(metrics["token_recorded_count"], 2)
        self.assertEqual(metrics["total_prompt_tokens"], 16)
        self.assertEqual(metrics["total_completion_tokens"], 8)
        self.assertEqual(metrics["total_tokens"], 24)
        self.assertEqual(metrics["average_latency_ms"], 50)
        self.assertEqual(metrics["p95_latency_ms"], 80)
        self.assertEqual(metrics["accepted_count"], 1)
        self.assertEqual(metrics["edited_sent_count"], 1)
        self.assertEqual(metrics["reviewed_count"], 2)
        self.assertEqual(metrics["accepted_rate"], 0.5)
        self.assertEqual(metrics["edited_sent_rate"], 0.5)
        self.assertEqual(metrics["average_tokens_per_request"], 12)

        response = self.client.get("/ops/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "persisted_chat_sessions")
        self.assertEqual(response.json()["request_count"], 2)

    def test_mask_sensitive_text_masks_phone_code_and_order_id(self) -> None:
        privacy = importlib.import_module("services.privacy")

        text = privacy.mask_sensitive_text("手机号13812345678，验证码123456，订单号202606061234567890")

        self.assertIn("[手机号已脱敏]", text)
        self.assertIn("验证码[已脱敏]", text)
        self.assertIn("[订单号已脱敏]", text)

        payload = privacy.mask_sensitive_payload({"nested": ["联系13812345678", {"code": "验证码123456"}]})
        self.assertEqual(payload["nested"][0], "联系[手机号已脱敏]")
        self.assertEqual(payload["nested"][1]["code"], "验证码[已脱敏]")

    def test_mask_sensitive_text_covers_prd_sensitive_fields(self) -> None:
        privacy = importlib.import_module("services.privacy")

        text = privacy.mask_sensitive_text(
            "姓名王小明，收货地址上海市浦东新区世纪大道100号，"
            "身份证11010119900307421X，银行卡号6222020202020202020，"
            "支付流水pay_abcdef123456"
        )

        self.assertNotIn("王小明", text)
        self.assertNotIn("上海市浦东新区世纪大道100号", text)
        self.assertNotIn("11010119900307421X", text)
        self.assertNotIn("6222020202020202020", text)
        self.assertNotIn("pay_abcdef123456", text)
        self.assertIn("姓名[已脱敏]", text)
        self.assertIn("收货地址[已脱敏]", text)
        self.assertIn("身份证[已脱敏]", text)
        self.assertIn("银行卡号[已脱敏]", text)
        self.assertIn("支付流水[已脱敏]", text)

    def test_mask_sensitive_payload_masks_sensitive_keys_without_masking_safe_names(self) -> None:
        privacy = importlib.import_module("services.privacy")

        payload = privacy.mask_sensitive_payload(
            {
                "customer_name": "王小明",
                "address": "上海市浦东新区世纪大道100号",
                "payment_no": "pay_abcdef123456",
                "items": [{"dish_name": "鱼香肉丝"}],
            }
        )

        self.assertEqual(payload["customer_name"], "[敏感信息已脱敏]")
        self.assertEqual(payload["address"], "[敏感信息已脱敏]")
        self.assertEqual(payload["payment_no"], "[敏感信息已脱敏]")
        self.assertEqual(payload["items"][0]["dish_name"], "鱼香肉丝")

    def test_feedback_and_chat_session_mask_sensitive_text_before_persisting(self) -> None:
        self.feedback_service.save_chat_session(
            query="我的手机号13812345678，订单号202606061234567890",
            reply="请提供验证码123456",
            trace={
                "request_id": "req-sensitive",
                "top1_intent": "隐私",
                "latency_ms": 1,
                "answer_source": "rag",
                "user_input": "13812345678",
            },
            token_usage={"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12, "counting_method": "test"},
        )
        self.feedback_service.save_feedback(
            {
                "request_id": "req-feedback-sensitive",
                "query": "手机号13812345678",
                "reply": "验证码123456",
                "helpful": False,
                "reason": "订单号202606061234567890",
                "expected_reply": "联系13812345678",
                "trace": {"top1_intent": "隐私", "raw": "13812345678"},
            }
        )

        connection = self.feedback_service.get_connection()
        try:
            chat_row = connection.execute(
                "SELECT query, reply, trace_json, total_tokens FROM chat_sessions WHERE request_id = ?",
                ("req-sensitive",),
            ).fetchone()
            feedback_row = connection.execute(
                "SELECT query, reply, reason, expected_reply, trace_json FROM feedback WHERE request_id = ?",
                ("req-feedback-sensitive",),
            ).fetchone()
        finally:
            connection.close()

        persisted_text = " ".join(str(value) for value in [*chat_row, *feedback_row])
        self.assertNotIn("13812345678", persisted_text)
        self.assertNotIn("123456", persisted_text)
        self.assertNotIn("202606061234567890", persisted_text)
        self.assertIn("[手机号已脱敏]", persisted_text)
        self.assertEqual(chat_row["total_tokens"], 12)

    def test_latest_chat_token_tracking_summary_reads_persisted_usage(self) -> None:
        self.feedback_service.save_chat_session(
            query="退款多久到账",
            reply="请查看订单详情页。",
            trace={
                "request_id": "req-token",
                "top1_intent": "退款进度",
                "latency_ms": 10,
                "answer_source": "rag",
            },
            token_usage={
                "prompt_tokens": 20,
                "completion_tokens": 7,
                "total_tokens": 27,
                "counting_method": "test_counter",
            },
        )

        summary = self.feedback_service.get_latest_chat_token_tracking_summary()

        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["token_recorded_count"], 1)
        self.assertEqual(summary["total_tokens"], 27)
        self.assertEqual(summary["latest_request_id"], "req-token")
        self.assertEqual(summary["token_counting_method"], "test_counter")


if __name__ == "__main__":
    unittest.main()
