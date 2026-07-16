import importlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


ADMIN_HEADERS = {"X-User-Role": "admin", "X-Operator-Id": "release_smoke_admin"}


def build_fake_chat_service() -> types.ModuleType:
    fake_chat_service = types.ModuleType("services.chat_service")

    def get_answer_from_rag(request):
        from services import conversation_store
        from services.feedback_service import save_chat_session

        request_id = "req-release-smoke-chat"
        session_id = getattr(request, "session_id", None) or "session-release-smoke"
        user_id = getattr(request, "user_id", "demo_user")
        order_id = getattr(request, "order_id", None)
        reply = "您好，退款进度请以订单售后页和平台审核结果为准。"
        trace = {
            "retrieval_count": 1,
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "order_id": order_id,
            "latency_ms": 12.0,
            "top1_intent": "退款进度",
            "used_fallback_prompt": False,
            "reply_rules_applied": False,
            "answer_source": "rag",
            "degraded": False,
            "failure_stage": "none",
            "fallback_reason": "",
        }
        response = {
            "request_id": request_id,
            "reply": reply,
            "risk_level": "medium",
            "confidence_level": "high",
            "need_human_review": True,
            "human_review_reason": "v1 默认客服确认后发送",
            "citations": [
                {
                    "knowledge_id": "kb_release_smoke",
                    "title": "退款到账规则",
                    "category": "refund",
                    "version": "v1",
                    "snippet": "退款进度以订单售后页为准。",
                    "score": 0.92,
                    "updated_at": "2026-07-06T00:00:00+00:00",
                }
            ],
            "conversation_status": "pending_agent_review",
            "answer_basis": "主证据：退款进度",
            "evidence_citations": [
                {
                    "evidence_id": "kb_release_smoke",
                    "knowledge_id": "kb_release_smoke",
                    "source_type": "knowledge_base",
                    "category": "refund",
                    "intent": "退款进度",
                    "risk_level": "medium",
                    "version": "v1",
                    "updated_at": "2026-07-06T00:00:00+00:00",
                    "score": 0.92,
                    "evidence_role": "primary",
                    "quote": "退款进度以订单售后页为准。",
                    "title": "退款到账规则",
                }
            ],
            "tool_results": [],
            "memory_snapshot": {"short_term": {}, "long_term": {"used": False}},
            "decision_trace": {"request_id": request_id},
            "full_trace": [{"step": "request_received", "status": "success"}],
            "handoff_ticket": None,
            "token_usage": {
                "provider": "local",
                "model": "release-smoke-model",
                "prompt_tokens": 80,
                "completion_tokens": 24,
                "total_tokens": 104,
                "counting_method": "release_smoke",
            },
            "confidence_score": 0.95,
            "session_id": session_id,
            "user_id": user_id,
            "order_id": order_id,
            "intent_analysis": {"primary_intent": "退款进度", "risk_level": "medium"},
            "context_used": {"session_id": session_id, "recent_message_count": 0},
            "safety_status": {"passed": True, "blocked": False, "issues": []},
            "final_prompt": "release smoke prompt",
            "prompt_version": "release_smoke_prompt",
            "retrieved_documents": ["退款进度以订单售后页为准。"],
            "retrieved_items": [{"intent": "退款进度"}],
            "prompt_context_items": [
                {
                    "role": "primary",
                    "evidence_strength": "normal",
                    "rank": 1,
                    "category": "refund",
                    "intent": "退款进度",
                    "question": "退款多久到账？",
                    "answer": "退款进度以订单售后页为准。",
                    "score": 0.92,
                    "rerank_score": 0.92,
                }
            ],
            "trace": trace,
        }
        conversation_store.get_or_create_conversation(user_id, session_id, order_id)
        conversation_store.save_turn_response(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            order_id=order_id,
            query=request.message,
            reply=reply,
            response=response,
        )
        conversation_store.set_conversation_status(session_id, "pending_agent_review")
        save_chat_session(
            query=request.message,
            reply=reply,
            trace=trace,
            token_usage=response["token_usage"],
        )
        return response

    fake_chat_service.get_answer_from_rag = get_answer_from_rag
    return fake_chat_service


class ReleaseSmokeFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.feedback_service = importlib.import_module("services.feedback_service")
        self.conversation_store = importlib.import_module("services.conversation_store")
        self.order_state_store = importlib.import_module("services.order_state_store")
        self.prompt_service = importlib.import_module("services.prompt_service")
        self.knowledge_service = importlib.import_module("services.knowledge_service")
        self.release_check_service = importlib.import_module("services.release_check_service")
        self.previous_paths = {
            "feedback_db": self.feedback_service.DB_PATH,
            "conversation_db": self.conversation_store.DB_PATH,
            "order_db": self.order_state_store.DB_PATH,
            "prompt_db": self.prompt_service.DB_PATH,
            "knowledge_db": self.knowledge_service.DB_PATH,
            "knowledge_path": self.knowledge_service.KNOWLEDGE_DATA_PATH,
            "backup_dir": self.knowledge_service.BACKUP_DIR,
            "grounding_report_dir": self.release_check_service.GROUNDING_REPORT_DIR,
            "release_report_dir": self.release_check_service.RELEASE_REPORT_DIR,
        }
        base_path = Path(self.temp_dir.name)
        shared_db_path = base_path / "ops_feedback.db"
        self.feedback_service.DB_PATH = shared_db_path
        self.conversation_store.DB_PATH = shared_db_path
        self.order_state_store.DB_PATH = shared_db_path
        self.prompt_service.DB_PATH = base_path / "prompt_versions.db"
        self.knowledge_service.DB_PATH = base_path / "knowledge_ops.db"
        self.knowledge_service.KNOWLEDGE_DATA_PATH = base_path / "seed.jsonl"
        self.knowledge_service.BACKUP_DIR = base_path / "backups"
        self.release_check_service.GROUNDING_REPORT_DIR = base_path / "reports"
        self.release_check_service.RELEASE_REPORT_DIR = base_path / "release_reports"
        self.knowledge_service.KNOWLEDGE_DATA_PATH.write_text("", encoding="utf-8")
        self.addCleanup(self.restore_paths)

        self.previous_chat_service = sys.modules.get("services.chat_service")
        sys.modules["services.chat_service"] = build_fake_chat_service()
        self.addCleanup(self.restore_chat_service)

        chat_router = importlib.import_module("routers.chat")
        feedback_router = importlib.import_module("routers.feedback")
        knowledge_router = importlib.import_module("routers.knowledge")
        order_router = importlib.import_module("routers.order")
        prompt_router = importlib.import_module("routers.prompt")
        release_router = importlib.import_module("routers.release")
        app = FastAPI()
        app.include_router(chat_router.router, prefix="/chat")
        app.include_router(feedback_router.router, prefix="/feedback")
        app.include_router(knowledge_router.router, prefix="/knowledge")
        app.include_router(order_router.router, prefix="/orders")
        app.include_router(prompt_router.router, prefix="/prompt")
        app.include_router(release_router.router, prefix="/release")
        self.client = TestClient(app)
        self.client.headers.update(ADMIN_HEADERS)

    def restore_paths(self) -> None:
        self.feedback_service.DB_PATH = self.previous_paths["feedback_db"]
        self.conversation_store.DB_PATH = self.previous_paths["conversation_db"]
        self.order_state_store.DB_PATH = self.previous_paths["order_db"]
        self.prompt_service.DB_PATH = self.previous_paths["prompt_db"]
        self.knowledge_service.DB_PATH = self.previous_paths["knowledge_db"]
        self.knowledge_service.KNOWLEDGE_DATA_PATH = self.previous_paths["knowledge_path"]
        self.knowledge_service.BACKUP_DIR = self.previous_paths["backup_dir"]
        self.release_check_service.GROUNDING_REPORT_DIR = self.previous_paths["grounding_report_dir"]
        self.release_check_service.RELEASE_REPORT_DIR = self.previous_paths["release_report_dir"]

    def restore_chat_service(self) -> None:
        if self.previous_chat_service is None:
            sys.modules.pop("services.chat_service", None)
        else:
            sys.modules["services.chat_service"] = self.previous_chat_service

    def write_release_report(self, report_count: int = 100) -> None:
        self.release_check_service.RELEASE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
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
        report_path = self.release_check_service.RELEASE_REPORT_DIR / "release_smoke.json"
        report_path.write_text(
            json.dumps(
                {"run_id": "release_smoke", "report_count": report_count, "reports": reports},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_chat_review_feedback_governance_flow_reaches_release_ready(self) -> None:
        self.write_release_report()

        order_response = self.client.put(
            "/orders/order_release_smoke/state",
            json={
                "user_id": "user_release_smoke",
                "order_id": "order_release_smoke",
                "status": "delivered",
                "status_label": "已送达",
                "delivery_status": "订单已送达",
                "summary": "订单已送达，用户咨询退款进度",
                "refund_status": "processing",
                "store_name": "烟测店铺",
                "items": [{"name": "测试餐品", "quantity": 1}],
                "total": 18.5,
            },
        )
        self.assertEqual(order_response.status_code, 200)

        prompt_version = "release_smoke_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        prompt_response = self.client.post(
            "/prompt/versions",
            json={
                "version": prompt_version,
                "system_prompt": self.prompt_service.DEFAULT_SYSTEM_PROMPT,
                "developer_prompt": self.prompt_service.DEFAULT_DEVELOPER_PROMPT,
                "change_reason": "release smoke",
                "evaluation_result": "release smoke",
            },
        )
        self.assertEqual(prompt_response.status_code, 200)
        prompt_id = prompt_response.json()["id"]
        self.assertEqual(
            self.client.post(
                f"/prompt/versions/{prompt_id}/status",
                json={"status": "approved", "evaluation_result": "release smoke passed"},
            ).status_code,
            200,
        )
        self.assertEqual(self.client.post(f"/prompt/versions/{prompt_id}/activate").status_code, 200)

        knowledge_response = self.client.post(
            "/knowledge/items",
            json={
                "title": "退款到账规则",
                "question": "退款多久到账？",
                "answer": "退款进度请以订单售后页和原支付渠道展示为准。",
                "category": "refund",
                "intent": "退款进度",
                "owner": "knowledge_ops",
                "source": "release_smoke",
            },
        )
        self.assertEqual(knowledge_response.status_code, 200)
        knowledge_id = knowledge_response.json()["id"]
        self.assertEqual(
            self.client.post(
                f"/knowledge/items/{knowledge_id}/review",
                json={"status": "approved", "review_note": "release smoke"},
            ).status_code,
            200,
        )
        with patch.object(self.knowledge_service, "rebuild_vector_store"):
            self.assertEqual(self.client.post("/knowledge/publish-approved").status_code, 200)

        chat_response = self.client.post(
            "/chat/prompt",
            json={
                "message": "我的退款为什么还没到账？",
                "user_id": "user_release_smoke",
                "session_id": "session_release_smoke",
                "order_id": "order_release_smoke",
                "channel": "agent_console",
            },
        )
        self.assertEqual(chat_response.status_code, 200)
        chat_body = chat_response.json()
        self.assertEqual(chat_body["request_id"], "req-release-smoke-chat")
        self.assertTrue(chat_body["need_human_review"])

        review_response = self.client.post(
            "/chat/review-action",
            json={
                "request_id": chat_body["request_id"],
                "action": "accepted",
                "final_reply": chat_body["reply"],
                "reason": "release smoke accepted",
            },
        )
        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response.json()["status"], "accepted")

        feedback_response = self.client.post(
            "/feedback",
            json={
                "request_id": chat_body["request_id"],
                "query": "我的退款为什么还没到账？",
                "reply": chat_body["reply"],
                "helpful": False,
                "reason": "release smoke bad case",
                "expected_reply": "补充官方渠道说明",
                "trace": chat_body["trace"],
            },
        )
        self.assertEqual(feedback_response.status_code, 200)
        feedback_id = feedback_response.json()["feedback_id"]
        self.assertEqual(
            self.client.post("/feedback/export-eval-case", json={"feedback_id": feedback_id}).status_code,
            200,
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
            checklist_response = self.client.get("/release/checklist")

        self.assertEqual(checklist_response.status_code, 200)
        checklist = checklist_response.json()
        self.assertTrue(checklist["ready"])
        self.assertEqual(checklist["failed_count"], 0)
        self.assertEqual(checklist["warning_count"], 0)
        items = {item["name"]: item for item in checklist["items"]}
        self.assertEqual(items["audit_coverage"]["status"], "pass")
        self.assertIn("covered=10/10", items["audit_coverage"]["evidence"])
        self.assertEqual(items["token_tracking"]["status"], "pass")
        self.assertEqual(items["auto_evaluation_report"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
