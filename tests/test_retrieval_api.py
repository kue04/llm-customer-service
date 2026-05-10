import unittest
from unittest.mock import patch

from routers.retrieval import preview_prompt, search_retrieval
from schemas.retrieval_schema import RetrievalSearchRequest


class RetrievalSearchApiTest(unittest.TestCase):
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

        with patch(
            "routers.retrieval.retrieve_by_real_vector",
            return_value=[candidate],
        ):
            response = search_retrieval(request)

        result = response.results[0]
        self.assertEqual(result.score, 0.78)
        self.assertEqual(result.rerank_score, 0.792)
        self.assertEqual(result.model_rerank_score, 0.95)

    def test_prompt_preview_response_includes_prompt_and_results(self) -> None:
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

        with patch(
            "routers.retrieval.retrieve_by_real_vector",
            return_value=candidates,
        ) as mocked_retrieve:
            response = preview_prompt(request)

        mocked_retrieve.assert_called_once_with(
            "refund arrival time",
            limit=2,
            min_score=0.62,
            use_hybrid=True,
        )
        self.assertEqual(response.query, "refund arrival time")
        self.assertEqual(response.mode, "hybrid")
        self.assertEqual(response.count, 2)
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].rank, 1)
        self.assertEqual(response.results[1].rank, 2)
        self.assertIn("refund arrival time", response.prompt)
        self.assertIn("Refunds return to the original payment method.", response.prompt)
        self.assertIn("Check the order detail page for refund status.", response.prompt)

    import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import retrieval
from routers.retrieval import preview_prompt, search_retrieval
from schemas.retrieval_schema import RetrievalSearchRequest


class RetrievalSearchApiTest(unittest.TestCase):
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

        with patch(
            "routers.retrieval.retrieve_by_real_vector",
            return_value=[candidate],
        ):
            response = search_retrieval(request)

        result = response.results[0]
        self.assertEqual(result.score, 0.78)
        self.assertEqual(result.rerank_score, 0.792)
        self.assertEqual(result.model_rerank_score, 0.95)

    def test_prompt_preview_response_includes_prompt_and_results(self) -> None:
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

        with patch(
            "routers.retrieval.retrieve_by_real_vector",
            return_value=candidates,
        ) as mocked_retrieve:
            response = preview_prompt(request)

        mocked_retrieve.assert_called_once_with(
            "refund arrival time",
            limit=2,
            min_score=0.62,
            use_hybrid=True,
        )
        self.assertEqual(response.query, "refund arrival time")
        self.assertEqual(response.mode, "hybrid")
        self.assertEqual(response.count, 2)
        self.assertEqual(len(response.results), 2)
        self.assertEqual(response.results[0].rank, 1)
        self.assertEqual(response.results[1].rank, 2)
        self.assertIn("refund arrival time", response.prompt)
        self.assertIn("Refunds return to the original payment method.", response.prompt)
        self.assertIn("Check the order detail page for refund status.", response.prompt)

    def test_prompt_preview_http_endpoint_returns_prompt_json(self) -> None:
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

        with patch(
            "routers.retrieval.retrieve_by_real_vector",
            return_value=candidates,
        ) as mocked_retrieve:
            response = client.post(
                "/retrieval/prompt-preview",
                json={
                    "query": "refund arrival time",
                    "mode": "hybrid",
                    "limit": 1,
                    "min_score": 0.62,
                },
            )

        mocked_retrieve.assert_called_once_with(
            "refund arrival time",
            limit=1,
            min_score=0.62,
            use_hybrid=True,
        )
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["query"], "refund arrival time")
        self.assertEqual(body["mode"], "hybrid")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["rank"], 1)
        self.assertEqual(body["results"][0]["intent"], "refund_progress")
        self.assertIn("refund arrival time", body["prompt"])
        self.assertIn("Refunds return to the original payment method.", body["prompt"])


if __name__ == "__main__":
    unittest.main()
