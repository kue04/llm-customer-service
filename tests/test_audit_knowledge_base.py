import unittest

from scripts.audit_knowledge_base import build_intent_audit, classify_expression


class AuditKnowledgeBaseTest(unittest.TestCase):
    def test_classifies_boundary_and_emotional_expression(self) -> None:
        labels = classify_expression("我都没吃上凭什么还扣钱")

        self.assertIn("emotional", labels)
        self.assertIn("boundary", labels)

    def test_build_intent_audit_flags_thin_intent(self) -> None:
        rows = build_intent_audit(
            [
                {
                    "category": "退款售后",
                    "intent": "退款金额咨询",
                    "dialogue_type": "single_turn",
                    "question": "为什么取消后没有全退",
                    "answer": "您可以在退款详情页查看扣除原因。",
                }
            ]
        )

        self.assertEqual(rows[0]["sample_count"], 1)
        self.assertIn("sample_count_low", rows[0]["risks"])
        self.assertIn("answer_missing_caution", rows[0]["risks"])


if __name__ == "__main__":
    unittest.main()
