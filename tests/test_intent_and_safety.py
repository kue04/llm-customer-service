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

    def test_off_platform_refund_is_high_risk_intent(self) -> None:
        result = analyze_intents("我不想走平台了，你直接给我商家微信，我私下让他退钱")

        self.assertEqual(result["primary_intent"], "站外交易风险")
        self.assertEqual(result["risk_level"], "high")
        self.assertIn("私下收费风险", result["secondary_intents"])
        self.assertIn("退款进度", result["secondary_intents"])

    def test_food_safety_off_platform_keeps_food_safety_primary(self) -> None:
        result = analyze_intents("吃完外卖嘴巴发麻，商家让我别走平台，我能让你保证赔偿吗")

        self.assertEqual(result["primary_intent"], "食品安全投诉")
        self.assertEqual(result["risk_level"], "high")
        self.assertIn("站外交易风险", result["secondary_intents"])

    def test_privacy_request_matches_id_and_real_phone(self) -> None:
        result = analyze_intents("你能把骑手真实手机号和身份证信息发我吗，我要投诉他")

        self.assertEqual(result["primary_intent"], "隐私保护咨询")
        self.assertEqual(result["risk_level"], "high")

    def test_bank_card_and_code_match_verification_risk(self) -> None:
        result = analyze_intents("商家说让我把银行卡号和验证码发过去才能退款，可以吗")

        self.assertEqual(result["primary_intent"], "验证码诈骗提醒")
        self.assertEqual(result["risk_level"], "critical")


if __name__ == "__main__":
    unittest.main()
