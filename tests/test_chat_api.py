import importlib
import sys
import types
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class ChatPromptApiTest(unittest.TestCase):
    def test_chat_prompt_returns_retrieved_documents(self) -> None:
        fake_chat_service = types.ModuleType("services.chat_service")
        fake_chat_service.get_answer_from_rag = lambda message: {
            "reply": "answer",
            "confidence_score": 0.95,
            "retrieved_documents": ["first document", "second document"],
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
        self.assertEqual(
            body["retrieved_documents"],
            ["first document", "second document"],
        )


if __name__ == "__main__":
    unittest.main()
