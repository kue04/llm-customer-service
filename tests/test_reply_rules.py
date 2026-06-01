import unittest

from services.reply_rules import apply_reply_rules, apply_reply_rules_with_trace


class ReplyRulesTest(unittest.TestCase):
    def test_normal_intent_keeps_model_reply(self) -> None:
        reply = apply_reply_rules(
            query="退款多久到账",
            reply="model reply",
            retrieved_items=[{"category": "退款售后", "intent": "退款进度"}],
        )

        self.assertEqual(reply, "model reply")

    def test_trace_records_pass_through_rule_match(self) -> None:
        reply, trace = apply_reply_rules_with_trace(
            query="商家电话在哪里看",
            reply="model reply",
            retrieved_items=[{"category": "常见问答", "intent": "商家电话咨询"}],
        )

        self.assertEqual(reply, "model reply")
        self.assertTrue(trace["matched"])
        self.assertEqual(trace["mode"], "pass_through")
        self.assertEqual(trace["primary_intent"], "商家电话咨询")

    def test_private_transfer_rule_still_forces_safety_reply(self) -> None:
        reply, trace = apply_reply_rules_with_trace(
            query="骑手让我私下转配送费可以吗",
            reply="model reply",
            retrieved_items=[{"category": "平台安全", "intent": "私下收费风险"}],
        )

        self.assertNotEqual(reply, "model reply")
        self.assertIn("不建议私下转账", reply)
        self.assertIn("保留聊天记录", reply)
        self.assertIn("资金纠纷", reply)
        self.assertEqual(trace["mode"], "force")

    def test_verification_code_rule_still_forces_safety_reply(self) -> None:
        reply = apply_reply_rules(
            query="骑手让我发验证码给他可以吗",
            reply="model reply",
            retrieved_items=[{"category": "平台安全", "intent": "验证码诈骗提醒"}],
        )

        self.assertNotEqual(reply, "model reply")
        self.assertIn("不可以", reply)
        self.assertIn("隐私", reply)

    def test_unknown_intent_keeps_original_reply(self) -> None:
        reply, trace = apply_reply_rules_with_trace(
            query="未知问题",
            reply="original reply",
            retrieved_items=[{"category": "未知", "intent": "未知意图"}],
        )

        self.assertEqual(reply, "original reply")
        self.assertFalse(trace["matched"])
        self.assertEqual(trace["mode"], "pass_through")


if __name__ == "__main__":
    unittest.main()
