import unittest
from unittest.mock import patch

from scripts.debug_answer_plan import (
    build_answer_plan_prompt,
    debug_answer_plan,
    extract_json_object_text,
    normalize_answer_plan,
    parse_answer_plan,
    render_plan_debug,
    render_user_reply,
    normalize_action_suggestion,
    shorten_direct_answer_brief,
)


class DebugAnswerPlanTest(unittest.TestCase):
    def test_build_answer_plan_prompt_includes_query_documents_and_json_fields(self) -> None:
        prompt = build_answer_plan_prompt(
            "退款多久到账",
            [
                "退款到账时间取决于支付渠道。",
                "可以在订单页查看退款进度。",
            ],
        )

        self.assertIn("用户问题：", prompt)
        self.assertIn("退款多久到账", prompt)
        self.assertIn("1. 退款到账时间取决于支付渠道。", prompt)
        self.assertIn("2. 可以在订单页查看退款进度。", prompt)
        self.assertIn('"answer_type": "..."', prompt)
        self.assertIn('"direct_answer_brief": "..."', prompt)

    def test_parse_answer_plan_accepts_valid_json(self) -> None:
        result = parse_answer_plan(
            """{
                "user_intent": "用户想知道退款多久到账",
                "answer_type": "refund_progress",
                "direct_answer_brief": "退款到账时间取决于支付渠道",
                "key_evidence": ["平台审核通过后通常会原路退回", "银行卡或部分第三方支付可能存在处理延迟"],
                "action_suggestion": "到订单页查看退款进度",
                "needs_caution": true,
                "caution_reason": "到账时间可能受支付渠道影响"
            }"""
        )

        self.assertEqual(result["answer_type"], "refund_progress")
        self.assertTrue(result["needs_caution"])

    def test_parse_answer_plan_accepts_json_wrapped_by_extra_text(self) -> None:
        result = parse_answer_plan(
            """下面是回答计划
            {
                "user_intent": "用户想知道退款多久到账",
                "answer_type": "refund_progress",
                "direct_answer_brief": "退款到账时间取决于支付渠道",
                "key_evidence": ["平台审核通过后通常会原路退回"],
                "action_suggestion": "到订单页查看退款进度",
                "needs_caution": false,
                "caution_reason": ""
            }
            """
        )

        self.assertEqual(result["direct_answer_brief"], "退款到账时间取决于支付渠道")

    def test_extract_json_object_text_rejects_empty_output(self) -> None:
        with self.assertRaises(ValueError):
            extract_json_object_text("")

    def test_render_plan_debug_includes_evidence_and_caution_when_needed(self) -> None:
        reply = render_plan_debug(
            {
                "user_intent": "用户想知道退款多久到账",
                "answer_type": "refund_progress",
                "direct_answer_brief": "退款到账时间取决于支付渠道",
                "key_evidence": ["平台审核通过后通常会原路退回", "银行卡或部分第三方支付可能存在处理延迟"],
                "action_suggestion": "到订单页查看退款进度",
                "needs_caution": True,
                "caution_reason": "到账时间可能受支付渠道影响",
            }
        )

        self.assertIn("退款到账时间取决于支付渠道", reply)
        self.assertIn("参考依据：平台审核通过后通常会原路退回；银行卡或部分第三方支付可能存在处理延迟。", reply)
        self.assertIn("建议您到订单页查看退款进度。", reply)
        self.assertIn("到账时间可能受支付渠道影响", reply)

    def test_render_user_reply_hides_debug_phrases(self) -> None:
        reply = render_user_reply(
            {
                "user_intent": "用户想知道退款多久到账",
                "answer_type": "refund_progress",
                "direct_answer_brief": "退款到账时间取决于支付渠道",
                "key_evidence": ["平台审核通过后通常会原路退回", "银行卡或部分第三方支付可能存在处理延迟"],
                "action_suggestion": "到订单页查看退款进度",
                "needs_caution": True,
                "caution_reason": "到账时间可能受支付渠道影响",
            }
        )

        self.assertIn("退款到账时间取决于支付渠道", reply)
        self.assertIn("建议您到订单页查看退款进度。", reply)
        self.assertIn("具体结果请以订单页面展示或平台核实结果为准。", reply)
        self.assertNotIn("参考依据：", reply)
        self.assertNotIn("原因是：", reply)

    def test_normalize_answer_plan_fills_default_caution_reason(self) -> None:
        plan = normalize_answer_plan(
            {
                "user_intent": "用户想知道退款多久到账 ",
                "answer_type": " refund_progress ",
                "direct_answer_brief": " 平台审核通过后通常会原路退回，具体到账时间取决于支付渠道。 ",
                "key_evidence": [" 退款到账时间取决于支付渠道。 "],
                "action_suggestion": " 请您查看订单页退款进度，相关结果以页面展示和平台处理为准。 ",
                "needs_caution": True,
                "caution_reason": " ",
            }
        )

        self.assertEqual(plan["answer_type"], "refund_progress")
        self.assertEqual(plan["direct_answer_brief"], "平台审核通过后通常会原路退回")
        self.assertEqual(plan["action_suggestion"], "查看订单页退款进度")
        self.assertTrue(plan["caution_reason"])

    def test_shorten_direct_answer_brief_prefers_first_clause(self) -> None:
        self.assertEqual(
            shorten_direct_answer_brief("平台审核通过后通常会原路退回，具体到账时间取决于支付渠道。"),
            "平台审核通过后通常会原路退回",
        )

    def test_normalize_action_suggestion_removes_template_phrases(self) -> None:
        self.assertEqual(
            normalize_action_suggestion("请您根据订单页面提示操作，相关结果以页面展示和平台处理为准。"),
            "根据订单页面提示操作",
        )

    def test_parse_answer_plan_rejects_missing_field(self) -> None:
        with self.assertRaises(ValueError):
            parse_answer_plan(
                """{
                    "user_intent": "用户想知道退款多久到账",
                    "answer_type": "refund_progress",
                    "direct_answer_brief": "退款到账时间取决于支付渠道",
                    "key_evidence": ["平台审核通过后通常会原路退回"],
                    "needs_caution": true,
                    "caution_reason": "到账时间可能受支付渠道影响"
                }"""
            )

    def test_debug_answer_plan_uses_structured_schema_and_renderer(self) -> None:
        with patch(
            "scripts.debug_answer_plan.retrieve_rag_items",
            return_value=[
                {"answer": "退款到账时间取决于支付渠道。", "intent": "refund_progress"},
            ],
        ), patch(
            "scripts.debug_answer_plan.generate_answer_plan",
            return_value="""{
                "user_intent": "用户想知道退款多久到账",
                "answer_type": "refund_progress",
                "direct_answer_brief": "退款到账时间取决于支付渠道",
                "key_evidence": ["平台审核通过后通常会原路退回"],
                "action_suggestion": "到订单页查看退款进度",
                "needs_caution": true,
                "caution_reason": ""
            }""",
        ) as generate_mock:
            with patch(
                "scripts.debug_answer_plan.generate_reply",
                return_value="退款到账时间取决于支付渠道，建议到订单页查看退款进度。",
            ):
                result = debug_answer_plan("退款多久到账", limit=1)

        generate_mock.assert_called_once()
        self.assertEqual(result["plan"]["answer_type"], "refund_progress")
        self.assertIn("建议您到订单页查看退款进度。", result["debug_rendered_reply"])
        self.assertIn("建议您到订单页查看退款进度。", result["user_rendered_reply"])
        self.assertTrue(result["plan"]["caution_reason"])


if __name__ == "__main__":
    unittest.main()
