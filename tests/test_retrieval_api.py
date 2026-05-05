import unittest
from unittest.mock import patch

from routers.retrieval import search_retrieval
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


if __name__ == "__main__":
    unittest.main()
