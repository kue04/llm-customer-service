import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import retrieval
from routers.retrieval import preview_prompt, search_retrieval
from schemas.retrieval_schema import RetrievalSearchRequest


class RetrievalSearchApiTest(unittest.TestCase):
    def test_config_endpoint_returns_current_rag_config(self) -> None:
        app = FastAPI()
        app.include_router(retrieval.router, prefix="/retrieval")
        client = TestClient(app)

        response = client.get("/retrieval/config")

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["embedding_model_name"], "BAAI/bge-small-zh-v1.5")
        self.assertEqual(body["reranker_model_name"], "BAAI/bge-reranker-base")
        self.assertEqual(body["model_rerank_weight"], 0.01)
        self.assertEqual(body["min_vector_score"], 0.40)
        self.assertTrue(body["reply_rules_enabled"])
        self.assertIn("real_vector.index", body["faiss_index_path"])
        self.assertIn("real_vector_docs.json", body["faiss_docs_path"])

    def test_search_response_includes_rerank_debug_scores(self) -> None:
        candidate = {
            "score": 0.78,
            "rerank_score": 0.792,
            "model_rerank_score": 0.95,
            "vector_score": 0.72,
            "keyword_bonus": 0.06,
            "direction_penalty": 0.0,
            "answer": "Refunds usually arrive within 1-3 business days.",
            "source": {
                "category": "refund",
                "intent": "refund_progress",
                "question": "When will the refund arrive?",
            },
        }

        request = RetrievalSearchRequest(
            query="refund arrival time",
            mode="hybrid",
            limit=1,
            min_score=0.62,
        )

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=[candidate]):
            response = search_retrieval(request)

        result = response.results[0]
        self.assertEqual(result.score, 0.78)
        self.assertEqual(result.rerank_score, 0.792)
        self.assertEqual(result.model_rerank_score, 0.95)

    def test_prompt_preview_response_includes_prompt_context_items(self) -> None:
        candidates = [
            {
                "score": 0.88,
                "rerank_score": 0.891,
                "model_rerank_score": 0.91,
                "vector_score": 0.82,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Refunds return to the original payment method.",
                "source": {
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                },
            },
            {
                "score": 0.77,
                "rerank_score": 0.776,
                "model_rerank_score": 0.6,
                "vector_score": 0.73,
                "keyword_bonus": 0.04,
                "direction_penalty": 0.0,
                "answer": "Check the order detail page for refund status.",
                "source": {
                    "category": "order",
                    "intent": "refund_status",
                    "question": "Where can I check refund status?",
                },
            },
        ]

        request = RetrievalSearchRequest(
            query="refund arrival time",
            mode="hybrid",
            limit=2,
            min_score=0.62,
        )

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=candidates) as mocked_retrieve:
            response = preview_prompt(request)

        mocked_retrieve.assert_called_once_with(
            "refund arrival time",
            limit=2,
            min_score=0.62,
            use_hybrid=True,
        )
        self.assertEqual(response.count, 2)
        self.assertEqual(len(response.prompt_context_items), 2)
        self.assertEqual(response.prompt_context_items[0].role, "primary")
        self.assertEqual(response.prompt_context_items[0].evidence_strength, "normal")
        self.assertEqual(response.prompt_context_items[1].role, "supporting")
        self.assertEqual(response.prompt_context_items[1].evidence_strength, "normal")
        self.assertEqual(response.prompt_context_items[0].intent, "refund_progress")
        self.assertIn("最相关参考资料", response.prompt)
        self.assertIn("补充参考资料", response.prompt)
        self.assertIn("intent: refund_progress", response.prompt)
        self.assertIn("question: When will the refund arrive?", response.prompt)

    def test_prompt_preview_http_endpoint_returns_context_json(self) -> None:
        app = FastAPI()
        app.include_router(retrieval.router, prefix="/retrieval")
        client = TestClient(app)
        candidates = [
            {
                "score": 0.88,
                "rerank_score": 0.891,
                "model_rerank_score": 0.91,
                "vector_score": 0.82,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Refunds return to the original payment method.",
                "source": {
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                },
            },
        ]

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=candidates):
            response = client.post(
                "/retrieval/prompt-preview",
                json={
                    "query": "refund arrival time",
                    "mode": "hybrid",
                    "limit": 1,
                    "min_score": 0.62,
                },
            )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["prompt_context_items"][0]["role"], "primary")
        self.assertEqual(body["prompt_context_items"][0]["evidence_strength"], "normal")
        self.assertEqual(body["prompt_context_items"][0]["intent"], "refund_progress")
        self.assertIn("最相关参考资料", body["prompt"])
        self.assertIn("intent: refund_progress", body["prompt"])

    def test_prompt_preview_response_marks_close_match_primary(self) -> None:
        candidates = [
            {
                "score": 0.90,
                "rerank_score": 0.82,
                "model_rerank_score": 0.70,
                "vector_score": 0.84,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Refund timing depends on payment method.",
                "source": {
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "When will the refund arrive?",
                },
            },
            {
                "score": 0.89,
                "rerank_score": 0.78,
                "model_rerank_score": 0.68,
                "vector_score": 0.83,
                "keyword_bonus": 0.06,
                "direction_penalty": 0.0,
                "answer": "Check the order detail page for refund progress.",
                "source": {
                    "category": "refund",
                    "intent": "refund_status",
                    "question": "Where can I check refund status?",
                },
            },
        ]

        request = RetrievalSearchRequest(
            query="refund arrival time",
            mode="hybrid",
            limit=2,
            min_score=0.62,
        )

        with patch("routers.retrieval.retrieve_by_real_vector", return_value=candidates):
            response = preview_prompt(request)

        self.assertEqual(response.prompt_context_items[0].role, "primary")
        self.assertEqual(response.prompt_context_items[0].evidence_strength, "close_match")
        self.assertIn("与补充资料较接近", response.prompt)


if __name__ == "__main__":
    unittest.main()
