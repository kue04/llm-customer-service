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


class IntentContextAndRoutingTest(unittest.TestCase):
    """B5（上下文继承）/ B6 第一步（修饰词归一化）/ B7（低置信度澄清）三处的行为锁定。

    这三处都改变了线上识别结果，改动前已确认：兜底场景、高风险场景、归一化反向
    场景都有显式守卫，避免「修一个缺陷、造出另一个」。
    """

    def test_filler_words_are_stripped_before_keyword_matching(self) -> None:
        # 「骑手一直联系不上」精确子串匹配不到「骑手联系不上」，归一化后才命中
        result = analyze_intents("我想退款，另外骑手一直联系不上")

        self.assertEqual(result["primary_intent"], "退款进度")
        self.assertIn("配送异常追问", result["secondary_intents"])

    def test_direct_hit_confidence_is_not_diluted_by_inheritance(self) -> None:
        result = analyze_intents("会员退款多久到账")

        self.assertEqual(result["primary_intent"], "退款进度")
        self.assertGreaterEqual(result["intents"][0]["confidence"], 0.84)
        self.assertNotIn("inherited_from_context", result)

    def test_coreference_inherits_last_primary_intent_at_lower_confidence(self) -> None:
        context = {"facts": {"last_primary_intent": "退款进度", "last_risk_level": "medium"}}
        result = analyze_intents("那要多久才能到", context)

        self.assertEqual(result["primary_intent"], "退款进度")
        self.assertEqual(result["inherited_from_context"], "退款进度")
        self.assertEqual(result["intents"][0]["confidence"], 0.6)
        self.assertEqual(result["intents"][0]["evidence"], [])  # 继承来的不伪造证据
        self.assertTrue(result["intents"][0]["inherited_from_context"])
        # 继承置信度 0.6 不落 clarify（0.6 < 0.6 为假），继承仍算「拿得准」
        self.assertEqual(result["routing"], "rag")

    def test_inheritance_rejected_when_last_intent_was_fallback(self) -> None:
        context = {"facts": {"last_primary_intent": "通用客服咨询"}}
        result = analyze_intents("那怎么办", context)

        self.assertEqual(result["primary_intent"], "通用客服咨询")
        self.assertNotIn("inherited_from_context", result)

    def test_inheritance_requires_coreference_hint(self) -> None:
        context = {"facts": {"last_primary_intent": "退款进度", "last_risk_level": "medium"}}
        result = analyze_intents("你们几点上班", context)

        self.assertNotIn("inherited_from_context", result)

    def test_inheritance_rejects_unknown_intent_name_from_stale_facts(self) -> None:
        context = {"facts": {"last_primary_intent": "已下线的旧意图"}}
        result = analyze_intents("那这个呢", context)

        self.assertEqual(result["primary_intent"], "通用客服咨询")
        self.assertNotIn("inherited_from_context", result)

    def test_low_confidence_fallback_routes_to_clarify(self) -> None:
        result = analyze_intents("你们几点上班")

        self.assertEqual(result["primary_intent"], "通用客服咨询")
        self.assertEqual(result["routing"], "clarify")

    def test_high_risk_still_routes_to_high_risk_rag(self) -> None:
        result = analyze_intents("餐里有异物，我想退款")

        self.assertEqual(result["routing"], "high_risk_rag")

    def test_normalization_does_not_create_false_positive(self) -> None:
        # 归一化只做加法：删掉修饰词后仍不应命中任何规则
        result = analyze_intents("我平时都很忙，先问问情况")

        self.assertEqual(result["primary_intent"], "通用客服咨询")
        self.assertEqual(result["routing"], "clarify")


if __name__ == "__main__":
    unittest.main()
