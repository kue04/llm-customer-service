import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.smoke_chat_prompt import (
    REQUIRED_CASE_FIELDS,
    evaluate_response,
    load_smoke_cases,
    run_smoke_cases,
)


class ChatPromptSmokeTest(unittest.TestCase):
    def test_default_smoke_cases_have_required_shape(self) -> None:
        cases = load_smoke_cases()

        self.assertGreaterEqual(len(cases), 10)
        self.assertLessEqual(len(cases), 15)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertTrue(all(REQUIRED_CASE_FIELDS.issubset(case) for case in cases))

        expected_intents = {case["expected_top1_intent"] for case in cases}
        self.assertIn("配送异常追问", expected_intents)
        self.assertIn("优惠券不可用", expected_intents)
        self.assertIn("退款进度", expected_intents)
        self.assertIn("未收到餐", expected_intents)

    def test_load_smoke_cases_rejects_duplicate_ids(self) -> None:
        case = {
            "id": "duplicate",
            "query": "优惠券不能用怎么办",
            "expected_top1_intent": "优惠券不可用",
            "expected_reply_keywords": ["优惠券"],
            "forbidden_reply_keywords": ["餐品错误售后"],
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as file:
            file.write(json.dumps(case, ensure_ascii=False) + "\n")
            file.write(json.dumps(case, ensure_ascii=False) + "\n")
            path = file.name

        with self.assertRaises(ValueError):
            load_smoke_cases(path)

    def test_evaluate_response_checks_intent_keywords_and_forbidden_terms(self) -> None:
        case = {
            "id": "coupon",
            "query": "优惠券不能用怎么办",
            "expected_top1_intent": "优惠券不可用",
            "expected_reply_keywords": ["优惠券", "有效期"],
            "forbidden_reply_keywords": ["餐品错误售后"],
        }
        response = {
            "reply": "优惠券不能使用通常与有效期有关。",
            "retrieved_items": [{"intent": "优惠券不可用"}],
        }

        result = evaluate_response(case, response)

        self.assertTrue(result["passed"])
        self.assertEqual(result["actual_top1_intent"], "优惠券不可用")

        bad_result = evaluate_response(
            case,
            {
                "reply": "请提交餐品错误售后。",
                "retrieved_items": [{"intent": "错送餐品"}],
            },
        )
        self.assertFalse(bad_result["passed"])
        self.assertEqual(bad_result["actual_top1_intent"], "错送餐品")
        self.assertTrue(bad_result["missing_reply_keywords"])
        self.assertEqual(bad_result["forbidden_reply_hits"], ["餐品错误售后"])

    def test_run_smoke_cases_uses_chat_prompt_provider(self) -> None:
        cases = [
            {
                "id": "rider",
                "query": "骑手联系不上怎么办",
                "expected_top1_intent": "配送异常追问",
                "expected_reply_keywords": ["配送异常"],
                "forbidden_reply_keywords": ["发票"],
            }
        ]

        with patch(
            "scripts.smoke_chat_prompt.post_chat_prompt",
            return_value={
                "reply": "请在订单详情页提交配送异常反馈。",
                "retrieved_items": [{"intent": "配送异常追问"}],
            },
        ) as mocked_post:
            results = run_smoke_cases(cases, base_url="http://server", timeout=1)

        self.assertTrue(results[0]["passed"])
        mocked_post.assert_called_once_with("http://server", "骑手联系不上怎么办", 1)


if __name__ == "__main__":
    unittest.main()
