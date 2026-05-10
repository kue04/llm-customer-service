import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import utils.vector_retriever as vector_retriever
from utils.vector_retriever import (
    rerank_candidates,
    save_real_vector_store,
    load_real_vector_store,
    retrieve_by_real_vector,
    retrieve_rag_documents,
)


class RerankCandidatesTest(unittest.TestCase):
    def test_uses_model_rerank_weight_when_calculating_rerank_score(self) -> None:
        candidates = [
            {
                "score": 0.50,
                "answer": "answer",
                "source": {"intent": "refund_progress", "question": "question"},
            }
        ]

        with patch(
            "utils.vector_retriever.calculate_model_rerank_scores",
            return_value=[1.0],
        ):
            results = rerank_candidates(
                "refund",
                candidates,
                model_rerank_weight=0.20,
            )

        self.assertEqual(results[0]["rerank_score"], 0.70)
        self.assertEqual(results[0]["model_rerank_score"], 1.0)

    def test_retrieve_rag_documents_returns_candidate_answers(self) -> None:
        candidates = [
            {"answer": "first answer"},
            {"answer": "second answer"},
        ]

        with patch(
            "utils.vector_retriever.retrieve_by_real_vector",
            return_value=candidates,
        ) as mocked_retrieve:
            documents = retrieve_rag_documents("refund question", limit=2)

        mocked_retrieve.assert_called_once_with(
            "refund question",
            limit=2,
            min_score=0.40,
            use_hybrid=True,
        )
        self.assertEqual(documents, ["first answer", "second answer"])

    def test_retrieve_by_real_vector_uses_faiss_index_hits(self) -> None:
        documents = [
            {
                "id": 0,
                "text": "doc 0",
                "answer": "first answer",
                "source": {"category": "refund", "intent": "refund_progress"},
            },
            {
                "id": 1,
                "text": "doc 1",
                "answer": "second answer",
                "source": {"category": "order", "intent": "order_status"},
            },
        ]

        def fake_build_embedding(text: str) -> list[float]:
            if text == "query":
                return [1.0, 0.0]
            if text == "doc 0":
                return [1.0, 0.0]
            return [0.0, 1.0]

        with patch(
            "utils.vector_retriever.get_real_vector_documents",
            return_value=documents,
        ), patch(
            "utils.vector_retriever.build_embedding",
            side_effect=fake_build_embedding,
        ), patch(
            "utils.vector_retriever.rerank_candidates",
            side_effect=lambda query, candidates, model_rerank_weight=0.01: candidates,
        ):
            results = retrieve_by_real_vector("query", limit=1, min_score=0.5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["answer"], "first answer")

    def test_save_and_load_real_vector_store_writes_files(self) -> None:
        documents = [
            {
                "id": 0,
                "text": "doc 0",
                "answer": "first answer",
                "source": {"category": "refund", "intent": "refund_progress"},
            }
        ]

        def fake_build_embedding(text: str) -> list[float]:
            return [1.0, 0.0]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("utils.vector_retriever.VECTOR_STORE_DIR", temp_path / "store"), patch(
                "utils.vector_retriever.FAISS_INDEX_PATH",
                temp_path / "store" / "real_vector.index",
            ), patch(
                "utils.vector_retriever.FAISS_DOCS_PATH",
                temp_path / "store" / "real_vector_docs.json",
            ), patch(
                "utils.vector_retriever.get_real_vector_documents",
                return_value=documents,
            ), patch(
                "utils.vector_retriever.build_embedding",
                side_effect=fake_build_embedding,
            ):
                vector_retriever._REAL_FAISS_INDEX = None
                vector_retriever._REAL_VECTOR_DOCS = documents
                save_real_vector_store()
                self.assertTrue((temp_path / "store" / "real_vector.index").exists())
                self.assertTrue((temp_path / "store" / "real_vector_docs.json").exists())
                vector_retriever._REAL_FAISS_INDEX = None
                vector_retriever._REAL_VECTOR_DOCS = None
                self.assertTrue(load_real_vector_store())


if __name__ == "__main__":
    unittest.main()
