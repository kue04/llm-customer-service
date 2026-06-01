import unittest

from models.prompt import create_prompt
from utils.rag_context import PromptContextItem


class PromptModelTest(unittest.TestCase):
    def test_create_prompt_uses_processed_context_items(self) -> None:
        prompt = create_prompt(
            "refund eta",
            [
                PromptContextItem(
                    role="primary",
                    evidence_strength="normal",
                    display_title="primary title",
                    evidence_summary="primary evidence summary",
                    prompt_instruction="use primary",
                    source_question="when refund arrives",
                    source_answer="raw primary answer",
                    rank=1,
                    category="refund",
                    intent="refund_progress",
                    question="when refund arrives",
                    answer="raw primary answer",
                    score=0.91,
                    rerank_score=0.93,
                ),
                PromptContextItem(
                    role="supporting",
                    evidence_strength="normal",
                    display_title="supporting title",
                    evidence_summary="supporting evidence summary",
                    prompt_instruction="use supporting as supplement",
                    source_question="refund status",
                    source_answer="raw supporting answer",
                    rank=2,
                    category="refund",
                    intent="refund_status",
                    question="refund status",
                    answer="raw supporting answer",
                    score=0.84,
                    rerank_score=0.85,
                ),
            ],
        )

        self.assertIn("refund eta", prompt)
        self.assertIn("title: primary title", prompt)
        self.assertIn("instruction: use primary", prompt)
        self.assertIn("intent: refund_progress", prompt)
        self.assertIn("evidence_summary: primary evidence summary", prompt)
        self.assertIn("title: supporting title", prompt)
        self.assertIn("必须优先使用第 1 条最相关参考资料", prompt)
        self.assertIn("用户问“在哪里看”时", prompt)
        self.assertIn("用户问“多久到账/多久退回”时", prompt)
        self.assertNotIn("answer: raw primary answer", prompt)


if __name__ == "__main__":
    unittest.main()
