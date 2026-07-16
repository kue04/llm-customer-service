import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


class ChatPromptApiTest(unittest.TestCase):
    def test_chat_prompt_returns_retrieved_documents_and_trace(self) -> None:
        fake_chat_service = types.ModuleType("services.chat_service")
        fake_chat_service.get_answer_from_rag = lambda request: {
            "request_id": "req-test",
            "reply": "answer",
            "risk_level": "medium",
            "confidence_level": "high",
            "need_human_review": True,
            "human_review_reason": "v1 默认客服确认后发送",
            "citations": [{"knowledge_id": "kb_1", "snippet": "rule"}],
            "conversation_status": "pending_agent_review",
            "answer_basis": "主证据：refund_progress",
            "answer_strategy": "model_reply",
            "evidence_citations": [{"evidence_id": "kb_1", "evidence_role": "primary"}],
            "tool_results": [],
            "memory_snapshot": {"short_term": {}, "long_term": {"used": False}},
            "decision_trace": {"request_id": "req-test"},
            "full_trace": [{"step": "request_received", "status": "success"}],
            "handoff_ticket": None,
            "token_usage": {
                "provider": "local",
                "model": "qwen2.5-1.5b-instruct",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "counting_method": "local_tokenizer",
            },
            "confidence_score": 0.95,
            "session_id": getattr(request, "session_id", None) or "session-test",
            "user_id": getattr(request, "user_id", "demo_user"),
            "order_id": getattr(request, "order_id", None),
            "intent_analysis": {"primary_intent": "refund_progress"},
            "context_used": {"recent_message_count": 0},
            "safety_status": {"passed": True},
            "final_prompt": "prompt text",
            "retrieved_documents": ["first document", "second document"],
            "retrieved_items": [{"intent": "refund_progress"}],
            "prompt_context_items": [
                {
                    "role": "primary",
                    "evidence_strength": "normal",
                    "rank": 1,
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                    "answer": "first document",
                    "score": 0.91,
                    "rerank_score": 0.93,
                }
            ],
            "trace": {
                "retrieval_count": 1,
                "request_id": "req-test",
                "user_id": getattr(request, "user_id", "demo_user"),
                "session_id": getattr(request, "session_id", None) or "session-test",
                "order_id": getattr(request, "order_id", None),
                "latency_ms": 12.3,
                "top1_intent": "refund_progress",
                "used_fallback_prompt": False,
                "reply_rules_applied": False,
                "answer_source": "rag",
                "degraded": False,
                "failure_stage": "none",
                "fallback_reason": "",
            },
        }

        previous_chat_service = sys.modules.get("services.chat_service")
        previous_chat_router = sys.modules.pop("routers.chat", None)
        sys.modules["services.chat_service"] = fake_chat_service

        try:
            chat_router = importlib.import_module("routers.chat")
            app = FastAPI()
            app.include_router(chat_router.router, prefix="/chat")
            client = TestClient(app)

            response = client.post(
                "/chat/prompt",
                headers={"X-User-Role": "agent", "X-Operator-Id": "agent_1"},
                json={"message": "refund question"},
            )
        finally:
            sys.modules.pop("routers.chat", None)
            if previous_chat_router is not None:
                sys.modules["routers.chat"] = previous_chat_router
            if previous_chat_service is not None:
                sys.modules["services.chat_service"] = previous_chat_service
            else:
                sys.modules.pop("services.chat_service", None)

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["reply"], "answer")
        self.assertEqual(body["request_id"], "req-test")
        self.assertEqual(body["risk_level"], "medium")
        self.assertEqual(body["confidence_level"], "high")
        self.assertTrue(body["need_human_review"])
        self.assertEqual(body["conversation_status"], "pending_agent_review")
        self.assertEqual(body["answer_strategy"], "model_reply")
        self.assertEqual(body["citations"][0]["knowledge_id"], "kb_1")
        self.assertEqual(body["user_id"], "demo_user")
        self.assertEqual(body["session_id"], "session-test")
        self.assertEqual(body["confidence_score"], 0.95)
        self.assertEqual(body["final_prompt"], "prompt text")
        self.assertEqual(
            body["retrieved_documents"],
            ["first document", "second document"],
        )
        self.assertEqual(body["prompt_context_items"][0]["intent"], "refund_progress")
        self.assertEqual(body["trace"]["retrieval_count"], 1)
        self.assertEqual(body["trace"]["request_id"], "req-test")
        self.assertEqual(body["trace"]["top1_intent"], "refund_progress")
        self.assertEqual(body["trace"]["latency_ms"], 12.3)
        self.assertEqual(body["evidence_citations"][0]["evidence_id"], "kb_1")
        self.assertEqual(body["full_trace"][0]["step"], "request_received")
        self.assertEqual(body["token_usage"]["total_tokens"], 120)
        self.assertFalse(body["trace"]["used_fallback_prompt"])
        self.assertEqual(body["trace"]["answer_source"], "rag")
        self.assertFalse(body["trace"]["degraded"])
        self.assertEqual(body["trace"]["failure_stage"], "none")

    def test_chat_prompt_requires_operator_identity(self) -> None:
        chat_router = importlib.import_module("routers.chat")
        app = FastAPI()
        app.include_router(chat_router.router, prefix="/chat")
        client = TestClient(app)

        response = client.post("/chat/prompt", json={"message": "refund question"})

        self.assertEqual(response.status_code, 401)

    def test_review_action_updates_chat_turn_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            feedback_service = importlib.import_module("services.feedback_service")
            conversation_store = importlib.import_module("services.conversation_store")
            previous_feedback_db_path = feedback_service.DB_PATH
            previous_db_path = conversation_store.DB_PATH
            feedback_service.DB_PATH = Path(temp_dir) / "ops_feedback.db"
            conversation_store.DB_PATH = feedback_service.DB_PATH

            try:
                chat_router = importlib.import_module("routers.chat")
                app = FastAPI()
                app.include_router(chat_router.router, prefix="/chat")
                client = TestClient(app)

                conversation_store.get_or_create_conversation("u1", "s1", "o1")
                conversation_store.save_turn_response(
                    request_id="req-review",
                    session_id="s1",
                    user_id="u1",
                    order_id="o1",
                    query="退款多久到账",
                    reply="请查看订单页。",
                    response={
                        "reply": "请查看订单页。",
                        "conversation_status": "pending_agent_review",
                    },
                )

                response = client.post(
                    "/chat/review-action",
                    headers={"X-User-Role": "agent", "X-Operator-Id": "agent_1"},
                    json={
                        "request_id": "req-review",
                        "action": "accepted",
                        "operator_id": "agent_1",
                        "operator_role": "agent",
                    },
                )
            finally:
                feedback_service.DB_PATH = previous_feedback_db_path
                conversation_store.DB_PATH = previous_db_path

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["request_id"], "req-review")
        self.assertEqual(body["action"], "accepted")
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["final_reply"], "请查看订单页。")
        self.assertIsInstance(body["audit_id"], int)

    def test_review_action_rejects_unauthorized_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            feedback_service = importlib.import_module("services.feedback_service")
            conversation_store = importlib.import_module("services.conversation_store")
            previous_feedback_db_path = feedback_service.DB_PATH
            previous_store_db_path = conversation_store.DB_PATH
            feedback_service.DB_PATH = Path(temp_dir) / "ops_feedback.db"
            conversation_store.DB_PATH = feedback_service.DB_PATH

            try:
                chat_router = importlib.import_module("routers.chat")
                app = FastAPI()
                app.include_router(chat_router.router, prefix="/chat")
                client = TestClient(app)

                conversation_store.get_or_create_conversation("u1", "s1", "o1")
                conversation_store.save_turn_response(
                    request_id="req-forbid",
                    session_id="s1",
                    user_id="u1",
                    order_id="o1",
                    query="退款多久到账",
                    reply="请查看订单页。",
                    response={"reply": "请查看订单页。"},
                )

                response = client.post(
                    "/chat/review-action",
                    headers={"X-User-Role": "knowledge_ops", "X-Operator-Id": "ops_1"},
                    json={"request_id": "req-forbid", "action": "accepted"},
                )
            finally:
                feedback_service.DB_PATH = previous_feedback_db_path
                conversation_store.DB_PATH = previous_store_db_path

        self.assertEqual(response.status_code, 403)

    def test_human_handoff_action_creates_ticket_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            feedback_service = importlib.import_module("services.feedback_service")
            conversation_store = importlib.import_module("services.conversation_store")
            previous_feedback_db_path = feedback_service.DB_PATH
            previous_store_db_path = conversation_store.DB_PATH
            feedback_service.DB_PATH = Path(temp_dir) / "ops_feedback.db"
            conversation_store.DB_PATH = feedback_service.DB_PATH

            try:
                chat_router = importlib.import_module("routers.chat")
                app = FastAPI()
                app.include_router(chat_router.router, prefix="/chat")
                client = TestClient(app)

                conversation_store.get_or_create_conversation("u1", "s1", "o1")
                conversation_store.save_turn_response(
                    request_id="req-handoff",
                    session_id="s1",
                    user_id="u1",
                    order_id="o1",
                    query="我要人工",
                    reply="建议转人工。",
                    response={
                        "reply": "建议转人工。",
                        "handoff_recommendation": {
                            "recommended": True,
                            "reason": "用户明确要求人工",
                        },
                    },
                )

                response = client.post(
                    "/chat/review-action",
                    headers={"X-User-Role": "agent", "X-Operator-Id": "agent_1"},
                    json={
                        "request_id": "req-handoff",
                        "action": "human_handoff",
                        "reason": "用户明确要求人工",
                    },
                )
            finally:
                feedback_service.DB_PATH = previous_feedback_db_path
                conversation_store.DB_PATH = previous_store_db_path

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "human_handoff")
        self.assertTrue(body["handoff_ticket"]["ticket_id"].startswith("handoff_"))
        self.assertIsInstance(body["audit_id"], int)


if __name__ == "__main__":
    unittest.main()
