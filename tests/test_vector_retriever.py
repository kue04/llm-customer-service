import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import utils.vector_retriever as vector_retriever
from utils.vector_retriever import (
    detect_intent_hint,
    rerank_candidates,
    save_real_vector_store,
    load_real_vector_store,
    retrieve_by_real_vector,
    retrieve_rag_documents,
    supplement_candidates_by_intent_hint,
)


class RerankCandidatesTest(unittest.TestCase):
    def test_detects_rule_based_intent_hint(self) -> None:
        self.assertEqual(
            detect_intent_hint("我都没吃上凭什么还扣我钱"),
            "退款金额咨询",
        )
        self.assertEqual(
            detect_intent_hint("别让我点联系商家了，你能直接给我店家手机号吗"),
            "商家电话咨询",
        )

    def test_detects_boundary_intent_hints_before_surface_phone_terms(self) -> None:
        self.assertEqual(
            detect_intent_hint("商家能看到我的真实手机号吗"),
            "隐私保护咨询",
        )
        self.assertEqual(
            detect_intent_hint("平台客服可以看我的手机号吗"),
            "隐私保护咨询",
        )
        self.assertEqual(
            detect_intent_hint("我不要在线联系，你直接帮我打给店家可以吗"),
            "联系商家咨询",
        )
        self.assertEqual(
            detect_intent_hint("骑手说到了但定位还在很远，我该怎么办"),
            "配送异常追问",
        )
        self.assertEqual(
            detect_intent_hint("骑手联系不上怎么办"),
            "配送异常追问",
        )
        self.assertEqual(
            detect_intent_hint("配送员电话打不通，我应该继续等还是反馈异常"),
            "配送异常追问",
        )
        self.assertEqual(
            detect_intent_hint("优惠券不能用怎么办"),
            "优惠券不可用",
        )
        self.assertEqual(
            detect_intent_hint("红包结算时用不了"),
            "优惠券不可用",
        )
        self.assertEqual(
            detect_intent_hint("店家已经开始做了我不想要了还能退全款吗"),
            "接单后取消",
        )
        self.assertEqual(
            detect_intent_hint("商家一直不接单我想取消订单"),
            "取消订单",
        )
        self.assertEqual(
            detect_intent_hint("显示送到了但我真没拿到"),
            "未收到餐",
        )
        self.assertEqual(
            detect_intent_hint("给我送错了，能处理不"),
            "错送餐品",
        )
        self.assertEqual(
            detect_intent_hint("外卖超时我取消了，重点是钱什么时候退回来"),
            "退款进度",
        )
        self.assertEqual(
            detect_intent_hint("没收到餐还扣了配送费，我不是问骑手在哪，我想问扣费能不能核实"),
            "退款金额咨询",
        )

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

    def test_intent_hint_boosts_matching_intent(self) -> None:
        candidates = [
            {
                "score": 0.70,
                "answer": "contact merchant",
                "source": {"intent": "联系商家咨询", "question": "question"},
            },
            {
                "score": 0.66,
                "answer": "merchant phone",
                "source": {"intent": "商家电话咨询", "question": "question"},
            },
        ]

        with patch(
            "utils.vector_retriever.calculate_model_rerank_scores",
            return_value=[0.0, 0.0],
        ):
            results = rerank_candidates(
                "别让我点联系商家了，你能直接给我店家手机号吗",
                candidates,
                model_rerank_weight=0.0,
            )

        self.assertEqual(results[0]["source"]["intent"], "商家电话咨询")

    def test_intent_hint_supplement_marks_candidate_origin(self) -> None:
        candidates = [
            {
                "score": 0.70,
                "answer": "other answer",
                "text": "other text",
                "source": {"intent": "other_intent", "question": "other question"},
            }
        ]
        documents = [
            {
                "answer": "target answer",
                "text": "target text",
                "source": {"intent": "target_intent", "question": "target question"},
            }
        ]

        with patch("utils.vector_retriever.get_real_vector_documents", return_value=documents):
            results = supplement_candidates_by_intent_hint(candidates, "target_intent")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[1]["answer"], "target answer")
        self.assertEqual(results[1]["_retrieval_origin"], "intent_hint_supplement")

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
