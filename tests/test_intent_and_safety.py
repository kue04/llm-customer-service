import unittest

from services.intent_service import analyze_intents
from services.safety_guard import validate_reply


class IntentAndSafetyTest(unittest.TestCase):
    def test_food_safety_and_refund_are_multi_intent_with_safety_first(self) -> None:
        result = analyze_intents("餐里有异物，我想退款，骑手态度也很差")

        self.assertEqual(result["primary_intent"], "食品安全投诉")
        self.assertEqual(result["risk_level"], "high")
        self.assertIn("退款进度", result["secondary_intents"])
        self.assertIn("骑手态度投诉", result["secondary_intents"])
        self.assertEqual(result["routing"], "high_risk_rag")

    def test_explicit_refund_focus_can_be_primary_but_keeps_high_risk(self) -> None:
        result = analyze_intents("餐里有异物，但我主要想问退款多久到账")

        self.assertEqual(result["primary_intent"], "退款进度")
        self.assertEqual(result["risk_level"], "high")
        self.assertIn("食品安全投诉", result["secondary_intents"])

    def test_safety_guard_blocks_risky_refund_promise(self) -> None:
        reply, status = validate_reply(
            query="餐里有异物，可以赔吗",
            reply="平台一定赔付，马上到账。",
            intent_analysis=analyze_intents("餐里有异物，可以赔吗"),
            retrieved_items=[{"intent": "食品安全投诉"}],
        )

        self.assertTrue(status["blocked"])
        self.assertIn("risky_promise", status["issues"])
        self.assertIn("以平台核实结果为准", reply)


if __name__ == "__main__":
    unittest.main()
