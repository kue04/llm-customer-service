import json
import sys
import tempfile
import types
import unittest

from scripts.evaluate_chat_grounding import (
    EVALUATION_QUERIES,
    apply_judge_result,
    build_grounding_report,
    build_grounding_reports_from_rag,
    build_judge_prompt,
    find_risky_promises,
    judge_grounding_report,
    judge_grounding_reports,
    local_judge_provider,
    parse_judge_response,
    parse_args,
    save_reports_to_file,
    summarize_grounding_reports,
)


class ChatGroundingEvaluationTest(unittest.TestCase):
    def test_find_risky_promises_returns_matched_terms(self) -> None:
        reply = "平台一定会补偿您，并保证今天到账。"

        risky_terms = find_risky_promises(reply)

        self.assertEqual(risky_terms, ["一定", "补偿", "保证"])

    def test_find_risky_promises_returns_empty_list_for_safe_reply(self) -> None:
        reply = "建议您在订单详情页查看处理进度，具体结果以页面展示为准。"

        risky_terms = find_risky_promises(reply)

        self.assertEqual(risky_terms, [])

    def test_build_grounding_report_includes_risky_terms(self) -> None:
        report = build_grounding_report(
            query="退款多久到账",
            retrieved_documents=["退款到账时间取决于支付渠道。"],
            reply="平台一定会补偿您。",
        )

        self.assertEqual(report["query"], "退款多久到账")
        self.assertEqual(report["retrieved_document_count"], 1)
        self.assertEqual(report["risky_promises"], ["一定", "补偿"])
        self.assertEqual(report["needs_manual_review"], True)

    def test_build_grounding_report_includes_review_questions(self) -> None:
        report = build_grounding_report(
            query="骑手让我私下转配送费可以吗",
            retrieved_documents=["请不要私下转账。"],
            reply="请在订单内处理。",
        )

        self.assertEqual(
            report["review_questions"],
            [
                "回复是否直接回答了用户问题？",
                "回复中的关键说法是否能被参考资料支撑？",
                "回复是否给出了安全、可执行的下一步？",
            ],
        )

    def test_build_grounding_report_includes_empty_manual_judgment(self) -> None:
        report = build_grounding_report(
            query="餐品有异物可以赔吗",
            retrieved_documents=["平台会根据核实结果处理。"],
            reply="请保留证据并提交售后。",
        )

        self.assertEqual(
            report["manual_judgment"],
            {
                "direct_answer": "",
                "grounded": "",
                "useful": "",
                "notes": "",
            },
        )

    def test_build_grounding_report_includes_retrieval_metadata(self) -> None:
        retrieved_items = [
            {
                "rank": 1,
                "intent": "食品安全投诉",
                "vector_score": 0.82,
            }
        ]

        report = build_grounding_report(
            query="餐品有异物可以赔吗",
            retrieved_documents=["请停止食用并提交食品安全投诉。"],
            reply="可以申请售后核实赔付，请先保留证据。",
            retrieved_items=retrieved_items,
        )

        self.assertEqual(report["retrieved_items"], retrieved_items)

    def test_build_judge_prompt_includes_report_and_json_schema(self) -> None:
        report = build_grounding_report(
            query="骑手让我私下转配送费可以吗",
            retrieved_documents=[
                "请不要私下转账。配送费应以平台订单结算页为准。",
            ],
            reply="请不要私下转账，请在订单内处理。",
        )

        prompt = build_judge_prompt(report)

        self.assertIn("用户问题：骑手让我私下转配送费可以吗", prompt)
        self.assertIn("1. 请不要私下转账。配送费应以平台订单结算页为准。", prompt)
        self.assertIn("客服回复：请不要私下转账，请在订单内处理。", prompt)
        self.assertIn("direct_answer", prompt)
        self.assertIn("grounded", prompt)
        self.assertIn("useful", prompt)
        self.assertIn("risk_notes", prompt)
        self.assertIn("reason", prompt)
        self.assertIn("yes | partial | no", prompt)
        self.assertIn(
            "risk_notes 只能指出客服回复中存在的问题",
            prompt,
        )
        self.assertIn(
            "如果用户问题包含“可以吗”或“能不能”",
            prompt,
        )
        self.assertIn("reason 必须填写，不能为空", prompt)
        self.assertIn("即使三个评分都是 yes，也必须说明", prompt)
        self.assertIn('"reason": "必须用一句中文解释评分理由，不能留空"', prompt)
        self.assertNotIn('"reason": ""', prompt)
        self.assertIn(
            "回复只给泛泛建议，没有回答核心问题",
            prompt,
        )

    def test_parse_args_supports_show_judge_prompt(self) -> None:
        args = parse_args(["--show-judge-prompt"])

        self.assertEqual(args.show_judge_prompt, True)

    def test_parse_args_supports_use_local_judge(self) -> None:
        args = parse_args(["--use-local-judge"])

        self.assertEqual(args.use_local_judge, True)

    def test_parse_args_supports_show_judge_response(self) -> None:
        args = parse_args(["--show-judge-response"])

        self.assertEqual(args.show_judge_response, True)

    def test_parse_args_supports_save_report(self) -> None:
        args = parse_args(["--save-report"])

        self.assertEqual(args.save_report, True)

    def test_parse_judge_response_returns_structured_result(self) -> None:
        text = """
        {
          "direct_answer": "partial",
          "grounded": "partial",
          "useful": "partial",
          "risk_notes": "没有明确提醒不要私下转账。",
          "reason": "回复方向正确，但安全边界不够明确。"
        }
        """

        result = parse_judge_response(text)

        self.assertEqual(result["direct_answer"], "partial")
        self.assertEqual(result["grounded"], "partial")
        self.assertEqual(result["useful"], "partial")
        self.assertEqual(result["risk_notes"], "没有明确提醒不要私下转账。")
        self.assertEqual(result["reason"], "回复方向正确，但安全边界不够明确。")

    def test_parse_judge_response_rejects_invalid_score_value(self) -> None:
        text = """
        {
          "direct_answer": "mostly",
          "grounded": "yes",
          "useful": "partial",
          "risk_notes": "",
          "reason": "invalid score"
        }
        """

        with self.assertRaises(ValueError):
            parse_judge_response(text)

    def test_parse_judge_response_rejects_empty_reason(self) -> None:
        text = """
        {
          "direct_answer": "yes",
          "grounded": "yes",
          "useful": "yes",
          "risk_notes": "",
          "reason": "   "
        }
        """

        with self.assertRaises(ValueError):
            parse_judge_response(text)

    def test_apply_judge_result_updates_manual_judgment(self) -> None:
        report = build_grounding_report(
            query="骑手让我私下转配送费可以吗",
            retrieved_documents=["请不要私下转账。"],
            reply="请在订单内处理。",
        )
        judge_result = {
            "direct_answer": "partial",
            "grounded": "partial",
            "useful": "partial",
            "risk_notes": "没有明确提醒不要私下转账。",
            "reason": "回复方向正确，但安全边界不够明确。",
        }

        updated_report = apply_judge_result(report, judge_result)

        self.assertEqual(
            updated_report["manual_judgment"],
            {
                "direct_answer": "partial",
                "grounded": "partial",
                "useful": "partial",
                "notes": "风险提示：没有明确提醒不要私下转账。 判断理由：回复方向正确，但安全边界不够明确。",
            },
        )

    def test_judge_grounding_report_runs_prompt_parse_and_apply_flow(self) -> None:
        report = build_grounding_report(
            query="骑手让我私下转配送费可以吗",
            retrieved_documents=["请不要私下转账。"],
            reply="请在订单内处理。",
        )
        prompts = []

        def fake_judge_provider(prompt: str) -> str:
            prompts.append(prompt)
            return """
            {
              "direct_answer": "partial",
              "grounded": "partial",
              "useful": "partial",
              "risk_notes": "没有明确提醒不要私下转账。",
              "reason": "回复方向正确，但安全边界不够明确。"
            }
            """

        judged_report = judge_grounding_report(report, fake_judge_provider)

        self.assertEqual(len(prompts), 1)
        self.assertIn("用户问题：骑手让我私下转配送费可以吗", prompts[0])
        self.assertIn(
            '"direct_answer": "partial"',
            judged_report["raw_judge_response"],
        )
        self.assertEqual(judged_report["judge_status"], "succeeded")
        self.assertEqual(judged_report["judge_error"], "")
        self.assertEqual(judged_report["manual_judgment"]["direct_answer"], "partial")
        self.assertEqual(judged_report["manual_judgment"]["grounded"], "partial")
        self.assertEqual(judged_report["manual_judgment"]["useful"], "partial")

    def test_judge_grounding_report_records_parse_error(self) -> None:
        bad_responses = [
            "not json",
            '{"direct_answer": "yes", "grounded": "yes"}',
            '{"direct_answer": "mostly", "grounded": "yes", "useful": "yes", "risk_notes": "", "reason": "invalid"}',
            '{"direct_answer": "yes", "grounded": "yes", "useful": "yes", "risk_notes": "", "reason": "   "}',
        ]
        for response in bad_responses:
            with self.subTest(response=response):
                report = build_grounding_report(
                    query="refund time",
                    retrieved_documents=["refunds return to original payment"],
                    reply="usually returns to original payment",
                )

                judged_report = judge_grounding_report(report, lambda prompt: response)

                self.assertEqual(judged_report["raw_judge_response"], response)
                self.assertEqual(judged_report["judge_status"], "failed")
                self.assertIn("judge_error", judged_report)
                self.assertEqual(judged_report["manual_judgment"]["direct_answer"], "")

    def test_judge_grounding_reports_updates_each_report(self) -> None:
        reports = [
            build_grounding_report(
                query="退款多久到账",
                retrieved_documents=["退款到账时间取决于支付渠道。"],
                reply="通常原路退回。",
            ),
            build_grounding_report(
                query="骑手让我私下转配送费可以吗",
                retrieved_documents=["请不要私下转账。"],
                reply="请在订单内处理。",
            ),
        ]

        def fake_judge_provider(prompt: str) -> str:
            return """
            {
              "direct_answer": "yes",
              "grounded": "yes",
              "useful": "yes",
              "risk_notes": "",
              "reason": "回复可以被资料支撑。"
            }
            """

        judged_reports = judge_grounding_reports(reports, fake_judge_provider)

        self.assertEqual(len(judged_reports), 2)
        self.assertEqual(judged_reports[0]["manual_judgment"]["direct_answer"], "yes")
        self.assertEqual(judged_reports[1]["manual_judgment"]["grounded"], "yes")

    def test_local_judge_provider_returns_generated_text(self) -> None:
        calls = []
        fake_chat_service = types.ModuleType("services.chat_service")

        def fake_generate_reply(prompt: str) -> str:
            calls.append(prompt)
            return '{"direct_answer": "yes"}'

        fake_chat_service.generate_reply = fake_generate_reply
        previous_chat_service = sys.modules.get("services.chat_service")
        sys.modules["services.chat_service"] = fake_chat_service

        try:
            result = local_judge_provider("judge prompt")
        finally:
            if previous_chat_service is not None:
                sys.modules["services.chat_service"] = previous_chat_service
            else:
                sys.modules.pop("services.chat_service", None)

        self.assertEqual(calls, ["judge prompt"])
        self.assertEqual(result, '{"direct_answer": "yes"}')

    def test_evaluation_queries_have_eight_to_twelve_items(self) -> None:
        self.assertGreaterEqual(len(EVALUATION_QUERIES), 8)
        self.assertLessEqual(len(EVALUATION_QUERIES), 12)

    def test_summarize_grounding_reports_counts_status_and_scores(self) -> None:
        succeeded_report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refunds return to original payment"],
            reply="usually returns to original payment",
        )
        succeeded_report["judge_status"] = "succeeded"
        succeeded_report["manual_judgment"] = {
            "direct_answer": "yes",
            "grounded": "partial",
            "useful": "no",
            "notes": "checked",
        }

        failed_report = build_grounding_report(
            query="food issue",
            retrieved_documents=["keep evidence and submit after-sales request"],
            reply="platform will compensate",
        )
        failed_report["judge_status"] = "failed"
        failed_report["needs_manual_review"] = True

        not_run_report = build_grounding_report(
            query="delivery delay",
            retrieved_documents=["check order page"],
            reply="check order page",
        )

        summary = summarize_grounding_reports([
            succeeded_report,
            failed_report,
            not_run_report,
        ])

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["manual_review_count"], 1)
        self.assertEqual(
            summary["judge_status_counts"],
            {
                "succeeded": 1,
                "failed": 1,
                "not_run": 1,
            },
        )
        self.assertEqual(
            summary["judgment_counts"]["direct_answer"],
            {
                "yes": 1,
                "partial": 0,
                "no": 0,
                "empty": 2,
            },
        )
        self.assertEqual(
            summary["judgment_counts"]["grounded"],
            {
                "yes": 0,
                "partial": 1,
                "no": 0,
                "empty": 2,
            },
        )
        self.assertEqual(
            summary["judgment_counts"]["useful"],
            {
                "yes": 0,
                "partial": 0,
                "no": 1,
                "empty": 2,
            },
        )

    def test_save_reports_to_file_writes_complete_json(self) -> None:
        reports = [
            build_grounding_report(
                query="refund time",
                retrieved_documents=["refunds return to original payment"],
                reply="usually returns to original payment",
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = save_reports_to_file(
                reports=reports,
                output_dir=temp_dir,
                use_local_judge=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["script"], "scripts/evaluate_chat_grounding.py")
        self.assertEqual(payload["use_local_judge"], True)
        self.assertEqual(payload["report_count"], 1)
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["summary"]["judge_status_counts"]["not_run"], 1)
        self.assertEqual(payload["reports"][0]["query"], "refund time")
        self.assertEqual(payload["reports"][0]["judge_status"], "not_run")
        self.assertIn("raw_judge_response", payload["reports"][0])
        self.assertIn("judge_error", payload["reports"][0])
        self.assertIn("retrieved_items", payload["reports"][0])

    def test_build_grounding_reports_from_rag_uses_answer_provider(self) -> None:
        calls = []

        def fake_answer_provider(query: str) -> dict:
            calls.append(query)
            return {
                "reply": f"{query} 的客服回复",
                "retrieved_documents": [f"{query} 的参考资料"],
                "retrieved_items": [{"intent": f"{query} intent"}],
            }

        reports = build_grounding_reports_from_rag(
            queries=["退款多久到账", "外卖超时了怎么办"],
            answer_provider=fake_answer_provider,
        )

        self.assertEqual(calls, ["退款多久到账", "外卖超时了怎么办"])
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["query"], "退款多久到账")
        self.assertEqual(reports[0]["reply"], "退款多久到账 的客服回复")
        self.assertEqual(reports[0]["retrieved_document_count"], 1)
        self.assertEqual(reports[0]["retrieved_items"], [{"intent": "退款多久到账 intent"}])


if __name__ == "__main__":
    unittest.main()
