import unittest

from utils.rag_context import build_prompt_context_items


class RagContextTest(unittest.TestCase):
    def test_build_prompt_context_items_keeps_rank_and_dedupes_answer(self) -> None:
        items = [
            {
                "rank": 1,
                "category": "退款售后",
                "intent": "refund_progress",
                "question": "退款多久到账？",
                "answer": "退款到账时间取决于支付渠道。",
                "score": 0.91,
                "rerank_score": 0.93,
            },
            {
                "rank": 2,
                "category": "退款售后",
                "intent": "refund_progress_duplicate",
                "question": "退款多久到？",
                "answer": "退款到账时间取决于支付渠道。",
                "score": 0.9,
                "rerank_score": 0.91,
            },
        ]

        context_items = build_prompt_context_items(items, max_items=3)

        self.assertEqual(len(context_items), 1)
        self.assertEqual(context_items[0].role, "primary")
        self.assertEqual(context_items[0].evidence_strength, "normal")
        self.assertEqual(context_items[0].rank, 1)
        self.assertEqual(context_items[0].intent, "refund_progress")

    def test_build_prompt_context_items_marks_close_match_primary(self) -> None:
        items = [
            {
                "rank": 1,
                "category": "退款售后",
                "intent": "refund_progress",
                "question": "退款多久到账？",
                "answer": "退款到账时间取决于支付渠道。",
                "score": 0.90,
                "rerank_score": 0.82,
            },
            {
                "rank": 2,
                "category": "退款售后",
                "intent": "refund_status",
                "question": "在哪里查看退款进度？",
                "answer": "您可以在订单详情页查看退款进度。",
                "score": 0.89,
                "rerank_score": 0.78,
            },
        ]

        context_items = build_prompt_context_items(items, max_items=3)

        self.assertEqual(context_items[0].role, "primary")
        self.assertEqual(context_items[0].evidence_strength, "close_match")
        self.assertEqual(context_items[1].role, "supporting")
        self.assertEqual(context_items[1].evidence_strength, "normal")


if __name__ == "__main__":
    unittest.main()
