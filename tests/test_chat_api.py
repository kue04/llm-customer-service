import importlib
import sys
import types
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class ChatPromptApiTest(unittest.TestCase):
    def test_chat_prompt_returns_retrieved_documents_and_trace(self) -> None:
        fake_chat_service = types.ModuleType("services.chat_service")
        fake_chat_service.get_answer_from_rag = lambda message: {
            "reply": "answer",
            "confidence_score": 0.95,
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
        self.assertFalse(body["trace"]["used_fallback_prompt"])
        self.assertEqual(body["trace"]["answer_source"], "rag")
        self.assertFalse(body["trace"]["degraded"])
        self.assertEqual(body["trace"]["failure_stage"], "none")


if __name__ == "__main__":
    unittest.main()
