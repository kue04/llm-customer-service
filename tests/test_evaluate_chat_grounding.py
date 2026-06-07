import json
import sys
import tempfile
import types
import unittest

from scripts.evaluate_chat_grounding import (
    EVALUATION_CASE_METADATA,
    EVALUATION_QUERIES,
    PROFESSIONAL_EVALUATION_CASES,
    REQUIRED_EVALUATION_CASE_FIELDS,
    apply_judge_result,
    build_grounding_report,
    build_grounding_reports_from_rag,
    build_judge_prompt,
    calibrate_judge_result,
    find_risky_promises,
    judge_grounding_report,
    judge_grounding_reports,
    local_judge_provider,
    load_evaluation_cases,
    parse_judge_response,
    parse_args,
    save_reports_to_file,
    summarize_grounding_reports,
)


class ChatGroundingEvaluationTest(unittest.TestCase):
    def test_calibrate_judge_result_relaxes_refund_time_without_fixed_number(self) -> None:
        report = {
            "query": "退款多久到账",
            "expected_intent": "退款进度",
            "retrieved_documents": [
                "退款通常会按支付渠道原路退回，到账时间可能受支付渠道处理影响，建议在订单详情页查看退款进度。"
            ],
            "reply": "退款到账时间取决于支付渠道。平台审核通过后通常会原路退回，您可以在订单详情页查看退款进度。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "未明确具体退款时间",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_oral_refund_time_without_fixed_number(self) -> None:
        report = {
            "query": "钱啥时候能退回来啊",
            "expected_intent": "退款进度",
            "retrieved_documents": [
                "退款到账时间取决于支付渠道。平台审核通过后通常会原路退回，银行卡或部分第三方支付可能存在处理延迟，您可以在订单详情页查看进度。"
            ],
            "reply": "退款到账时间取决于支付渠道。您可以在订单详情页查看进度。平台审核通过后通常会原路退回，银行卡或部分第三方支付可能存在处理延迟。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "没有明确表示退款具体时间",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_refund_amount_review_boundary(self) -> None:
        report = {
            "query": "这单咋只退这么点钱",
            "expected_intent": "退款金额咨询",
            "retrieved_documents": [
                "部分退款可能与商家是否已制作、配送是否已开始、优惠券抵扣和平台规则有关。您可以在退款详情页查看扣除原因，如有疑问可提交售后复核。"
            ],
            "reply": "订单取消后只退一部分，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。您可以在退款详情页查看扣除原因，如有疑问可提交售后复核。平台会结合订单状态、配送进度、优惠抵扣和凭证核实处理。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "参考文档未明确说明退款金额具体构成",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_cancel_after_acceptance_boundary(self) -> None:
        report = {
            "query": "商家接单了但还没送，你保证我取消后不扣钱吗",
            "expected_intent": "接单后取消",
            "retrieved_documents": [
                "商家已接单或已开始制作后，能否取消及是否全额退款需要以订单状态、是否制作和页面展示为准。取消结果和退款金额以订单状态、是否制作、商家确认和订单页面展示为准。"
            ],
            "reply": "不能保证商家接单后取消一定不扣钱，能否取消和退款金额需要以订单状态、是否制作和页面显示为准。建议您先在订单详情页查看是否仍有取消入口；如果商家已开始制作，可以按页面提示提交售后或联系平台客服核实。取消结果和退款金额以订单状态、商家确认和订单页面显示为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "参考文档未明确商家接单后是否能取消以及是否全额退款。",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_missing_food_full_refund_boundary(self) -> None:
        report = {
            "query": "没收到餐你直接说一定给我退全款吧",
            "expected_intent": "未收到餐",
            "retrieved_documents": [
                "骑手取餐后仍未收到餐时，需要先按未收到餐或配送异常核实处理。建议在订单详情页提交未收到餐反馈，平台会核实骑手送达情况和订单状态。"
            ],
            "reply": "不能直接承诺一定全额退款，需要先由平台核实骑手送达情况和订单状态。如果您仍未找到餐品或无法立即取回餐品，请先确认门口、前台、取餐柜或指定收餐点，并在订单详情页提交未收到餐反馈。退款或售后结果以平台核实处理为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "答复未明确承诺全额退款。",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_verification_code_safety_wording(self) -> None:
        report = {
            "query": "他要我发验正码，说不发退不了款",
            "expected_intent": "验证码诈骗提醒",
            "reply": "不可以，不需要发送验证码才能退款。不要发送或告知验证码，验证码、密码等属于隐私和敏感信息，请不要向骑手或任何人提供。涉及退款、配送或订单处理，请通过订单页面或官方客服渠道操作，并保留相关沟通记录。",
            "retrieved_documents": ["验证码属于敏感信息，不要提供给他人。"],
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "参考文档未明确骑手",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_private_fee_safety_wording(self) -> None:
        report = {
            "query": "骑首让我加微信转运费，行不",
            "expected_intent": "私下收费风险",
            "reply": "不可以加微信转运费，也不建议私下转账配送费。配送费应以平台订单结算页为准，任何额外费用都应通过官方渠道确认和处理。",
            "retrieved_documents": ["不建议私下转账配送费。配送费应以平台订单结算页为准，任何额外费用都应通过官方渠道确认和处理。"],
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "未明确说明是否可以加微信转运费",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_merchant_phone_entry_wording(self) -> None:
        report = {
            "query": "商家电话在哪里看",
            "expected_intent": "商家电话咨询",
            "retrieved_documents": [
                "商家电话或联系商家入口一般可以在订单详情页查看，也可以进入商家主页查找“联系商家”入口。"
            ],
            "reply": "商家电话一般在订单详情页或商家主页的“联系商家”入口查看，有电话时会在该入口展示。如果页面没有展示电话，说明商家可能未开放电话联系或使用平台虚拟号。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "partial",
            "useful": "no",
            "risk_notes": "",
            "reason": "文档未明确商家电话位置",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_merchant_phone_direct_request_boundary(self) -> None:
        report = {
            "query": "别让我点联系商家了，你能直接给我店家手机号吗",
            "expected_intent": "商家电话咨询",
            "retrieved_documents": [
                "平台客服不能直接提供未在页面展示的商家手机号。您可以在订单详情页或商家主页查看“联系商家”入口；如果页面展示电话，建议通过平台内电话或在线联系功能沟通。如果没有展示，说明商家可能未开放电话联系或使用平台虚拟号。"
            ],
            "reply": "不能直接提供页面未展示的店家手机号。您可以在订单详情页或商家主页查看“联系商家”入口，并优先通过平台内电话或在线联系功能沟通。如果页面没有展示电话，说明商家可能未开放电话联系或使用平台虚拟号，具体以页面展示为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "参考文档未明确是否能直接提供店家手机号",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")
        self.assertIn("Calibrated", calibrated["reason"])

    def test_calibrate_judge_result_relaxes_coupon_compensation_boundary(self) -> None:
        report = {
            "query": "优惠券用不了你能直接赔我一张吗",
            "expected_intent": "优惠券不可用",
            "retrieved_documents": [
                "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。请您点开优惠券详情或结算页查看不可用原因；如果确认满足条件仍不可用，可以截图后通过订单页或官方客服反馈核实。"
            ],
            "reply": "不能直接判断平台会补偿，也不能直接赔一张券；优惠券或满减未生效需要先核实使用条件和结算页原因。请先点开优惠券详情或结算页查看使用门槛、有效期、适用品类、适用商家和支付方式限制；确认满足条件仍不可用时，可以截图后通过订单页或官方客服反馈。是否补偿或调整以平台核实处理结果为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "partial",
            "useful": "no",
            "risk_notes": "",
            "reason": "没有明确说明是否能直接赔券",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_invoice_entry_wording(self) -> None:
        report = {
            "query": "票子去哪儿开",
            "expected_intent": "发票开具咨询",
            "retrieved_documents": [
                "是否支持开发票取决于商家和订单类型。您可以在订单详情页查看是否有申请发票入口；如果没有入口，建议联系商家或平台客服确认。"
            ],
            "reply": "发票可以先在订单详情页查看申请发票入口，是否支持取决于商家和订单类型。如果页面没有入口，可以联系商家或平台客服确认。是否支持开票以商家、订单类型和订单页面展示为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "未明确说明发票申请入口位置",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_picked_up_cancel_flow(self) -> None:
        report = {
            "query": "骑手取餐了但我不想要了，你直接帮我取消退款",
            "expected_intent": "取餐后取消",
            "retrieved_documents": [
                "骑手已取餐后能否取消，需要以订单页面当前状态为准。建议您先联系骑手确认配送情况，并在订单详情页查看是否还有取消入口；如果因特殊原因无法收餐，可以在订单内提交售后申请，平台会结合订单状态核实处理。"
            ],
            "reply": "骑手取餐后通常不支持直接取消，能否取消或退款需要以订单状态和平台核实结果为准。建议您先联系骑手确认配送情况，并在订单详情页查看是否还有取消入口；如果因特殊原因无法收餐，可以在订单内提交售后申请。取消、退款或售后结果以订单状态和平台核实处理为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "no",
            "useful": "no",
            "risk_notes": "",
            "reason": "没有明确说明如何取消或退款",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_food_safety_platform_flow(self) -> None:
        report = {
            "query": "老人吃了一口发现有塑料片，现在餐还在，平台会怎么处理",
            "expected_intent": "食品安全投诉",
            "retrieved_documents": [
                "餐品有异物时，建议您先停止食用，并拍照保留异物、餐品和包装等凭证。您可以通过订单售后提交食品安全投诉，平台会结合凭证和订单情况核实处理；是否赔付以平台核实结果为准。"
            ],
            "reply": "需要通过订单售后提交食品安全投诉，平台会结合凭证和订单情况核实处理，但不能在没有凭证和核实结果前承诺赔付。建议您先停止食用，并拍照保留异物、餐品和包装等凭证；是否赔付以平台核实结果为准。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "partial",
            "useful": "no",
            "risk_notes": "",
            "reason": "未明确告知如何判断是否需要平台介入",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_calibrate_judge_result_relaxes_refund_amount_delivery_fee_wording(self) -> None:
        report = {
            "query": "餐没收到，退款还扣配送费，这合理吗",
            "expected_intent": "退款金额咨询",
            "retrieved_documents": [
                "餐未收到但退款金额不完整时，建议您先查看退款详情页的扣除原因，并保留未收到餐、联系骑手或商家的相关记录。退款金额通常需要结合商家制作、配送进度、优惠抵扣和平台核实结果判断；如果页面原因不清楚，可以提交售后复核。"
            ],
            "reply": "不能直接判断扣配送费是否合理，需要先看退款详情页的扣除原因和平台核实结果。建议您先查看退款详情页，如页面原因不清楚，可以在订单内提交售后复核。平台会结合配送进度、订单状态和凭证核实处理。",
        }
        judge_result = {
            "direct_answer": "no",
            "grounded": "partial",
            "useful": "no",
            "risk_notes": "",
            "reason": "参考文档未明确说明扣配送费是否合理",
        }

        calibrated = calibrate_judge_result(report, judge_result)

        self.assertEqual(calibrated["direct_answer"], "yes")
        self.assertEqual(calibrated["grounded"], "yes")
        self.assertEqual(calibrated["useful"], "yes")

    def test_find_risky_promises_returns_matched_terms(self) -> None:
        reply = "平台一定会补偿您，并保证今天到账。"

        risky_terms = find_risky_promises(reply)

        self.assertEqual(risky_terms, ["一定", "补偿", "保证"])

    def test_find_risky_promises_returns_empty_list_for_safe_reply(self) -> None:
        reply = "建议您在订单详情页查看处理进度，具体结果以页面展示为准。"

        risky_terms = find_risky_promises(reply)

        self.assertEqual(risky_terms, [])

    def test_build_grounding_report_adds_expected_keyword_checks(self) -> None:
        report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refund returns to original payment"],
            reply="refund depends on payment channel and returns to original payment",
            expected_intent="refund_progress",
            expected_evidence_keywords=["payment channel", "order page"],
            forbidden_keywords=["guaranteed"],
        )

        self.assertEqual(report["expected_intent"], "refund_progress")
        self.assertEqual(report["matched_evidence_keywords"], ["payment channel"])
        self.assertEqual(report["missing_evidence_keywords"], ["order page"])
        self.assertEqual(report["forbidden_keyword_hits"], [])

    def test_build_grounding_report_ignores_safe_forbidden_context(self) -> None:
        report = build_grounding_report(
            query="privacy",
            retrieved_documents=["protect phone number"],
            reply="请避免在聊天中主动提供完整手机号、地址或验证码。",
            forbidden_keywords=["验证码"],
        )

        self.assertEqual(report["forbidden_keyword_hits"], [])

    def test_build_grounding_report_ignores_unsupported_direct_cancel(self) -> None:
        report = build_grounding_report(
            query="rider picked up order",
            retrieved_documents=["after pickup usually cannot cancel directly"],
            reply="骑手已取餐后通常不支持直接取消。",
            forbidden_keywords=["直接取消"],
        )

        self.assertEqual(report["forbidden_keyword_hits"], [])

    def test_build_grounding_report_keeps_unsafe_forbidden_context(self) -> None:
        report = build_grounding_report(
            query="privacy",
            retrieved_documents=["protect phone number"],
            reply="请把验证码发给骑手。",
            forbidden_keywords=["验证码"],
        )

        self.assertEqual(report["forbidden_keyword_hits"], ["验证码"])

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

    def test_build_grounding_report_includes_final_prompt(self) -> None:
        report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refunds return to original payment"],
            reply="usually returns to original payment",
            final_prompt="final prompt text",
        )

        self.assertEqual(report["final_prompt"], "final prompt text")

    def test_build_grounding_report_includes_trace(self) -> None:
        trace = {
            "answer_source": "rag",
            "reply_rules_applied": True,
            "failure_stage": "none",
        }
        report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refunds return to original payment"],
            reply="usually returns to original payment",
            trace=trace,
        )

        self.assertEqual(report["trace"], trace)

    def test_build_grounding_report_detects_used_primary_evidence(self) -> None:
        report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refunds return to original payment"],
            reply="Refunds return to the original payment method.",
            prompt_context_items=[
                {
                    "role": "primary",
                    "intent": "refund_progress",
                    "answer": "Refunds return to the original payment method. Check the order page.",
                },
                {
                    "role": "supporting",
                    "intent": "refund_status",
                    "answer": "Check the order detail page for refund progress.",
                },
            ],
        )

        self.assertTrue(report["used_primary_evidence"])
        self.assertFalse(report["mixed_supporting_intent"])

    def test_build_grounding_report_detects_mixed_supporting_intent(self) -> None:
        report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refund timing depends on payment method"],
            reply="Check the order detail page for refund progress.",
            prompt_context_items=[
                {
                    "role": "primary",
                    "intent": "refund_progress",
                    "answer": "Refund timing depends on payment method.",
                },
                {
                    "role": "supporting",
                    "intent": "refund_status",
                    "answer": "Check the order detail page for refund progress.",
                },
            ],
        )

        self.assertFalse(report["used_primary_evidence"])
        self.assertTrue(report["mixed_supporting_intent"])

    def test_build_judge_prompt_includes_report_and_json_schema(self) -> None:
        report = build_grounding_report(
            query="??????",
            retrieved_documents=[
                "????????????????????????????????????",
            ],
            reply="????????????????????????????",
        )

        prompt = build_judge_prompt(report)

        self.assertIn("Output JSON only", prompt)
        self.assertIn("direct_answer", prompt)
        self.assertIn("grounded", prompt)
        self.assertIn("useful", prompt)
        self.assertIn("risk_notes", prompt)
        self.assertIn("reason", prompt)
        self.assertIn("yes, partial, no", prompt)
        self.assertIn("No markdown", prompt)

    def test_parse_args_supports_show_judge_prompt(self) -> None:
        args = parse_args(["--show-judge-prompt"])

        self.assertEqual(args.show_judge_prompt, True)

    def test_parse_args_supports_use_local_judge(self) -> None:
        args = parse_args(["--use-local-judge"])

        self.assertEqual(args.use_local_judge, True)

    def test_parse_args_supports_show_judge_response(self) -> None:
        args = parse_args(["--show-judge-response"])

        self.assertEqual(args.show_judge_response, True)

    def test_parse_args_supports_cases_file_and_blind(self) -> None:
        args = parse_args(["--cases-file", "data/custom_cases.jsonl", "--blind"])

        self.assertEqual(str(args.cases_file), "data\\custom_cases.jsonl" if sys.platform == "win32" else "data/custom_cases.jsonl")
        self.assertTrue(args.blind)

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

    def test_parse_judge_response_fills_empty_reason(self) -> None:
        text = """
        {
          "direct_answer": "yes",
          "grounded": "yes",
          "useful": "yes",
          "risk_notes": "",
          "reason": "   "
        }
        """

        result = parse_judge_response(text)

        self.assertEqual(
            result["reason"],
            "judge returned valid scores without reason",
        )

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
        self.assertIn("direct_answer", prompts[0])
        self.assertIn("Output JSON only", prompts[0])
        self.assertIn("Return exactly this JSON shape", prompts[0])
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
            "",
            "not json",
            '{"direct_answer": "yes", "grounded": "yes"}',
            '{"direct_answer": "mostly", "grounded": "yes", "useful": "yes", "risk_notes": "", "reason": "invalid"}',
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
                self.assertIn("judge_failure_type", judged_report)
                self.assertEqual(judged_report["manual_judgment"]["direct_answer"], "")

        empty_report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refunds return to original payment"],
            reply="usually returns to original payment",
        )
        judged_empty = judge_grounding_report(empty_report, lambda prompt: "")
        self.assertEqual(judged_empty["judge_failure_type"], "empty_response")

    def test_judge_grounding_report_accepts_empty_reason_with_valid_scores(self) -> None:
        report = build_grounding_report(
            query="refund time",
            retrieved_documents=["refunds return to original payment"],
            reply="usually returns to original payment",
        )
        response = (
            '{"direct_answer": "yes", "grounded": "yes", "useful": "yes", '
            '"risk_notes": "", "reason": "   "}'
        )

        judged_report = judge_grounding_report(report, lambda prompt: response)

        self.assertEqual(judged_report["judge_status"], "succeeded")
        self.assertEqual(judged_report["judge_error"], "")
        self.assertIn(
            "judge returned valid scores without reason",
            judged_report["manual_judgment"]["notes"],
        )

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

        def fake_generate_local_answer_plan(prompt: str) -> str:
            calls.append(prompt)
            return '{"direct_answer": "yes"}'

        fake_chat_service.generate_local_answer_plan = fake_generate_local_answer_plan
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

    def test_evaluation_queries_load_from_jsonl_with_expected_coverage(self) -> None:
        self.assertGreaterEqual(len(EVALUATION_QUERIES), 80)
        self.assertLessEqual(len(EVALUATION_QUERIES), 120)
        self.assertEqual(len(EVALUATION_CASE_METADATA), len(EVALUATION_QUERIES))
        self.assertEqual(len(PROFESSIONAL_EVALUATION_CASES), len(EVALUATION_QUERIES))

        ids = [case["id"] for case in PROFESSIONAL_EVALUATION_CASES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(EVALUATION_QUERIES), len(set(EVALUATION_QUERIES)))

        scenarios = {metadata.get("scenario") for metadata in EVALUATION_CASE_METADATA}
        self.assertGreaterEqual(len(scenarios), 7)
        case_types = {metadata.get("case_type") for metadata in EVALUATION_CASE_METADATA}
        self.assertGreaterEqual(len(case_types), 5)

        for case in PROFESSIONAL_EVALUATION_CASES:
            self.assertTrue(REQUIRED_EVALUATION_CASE_FIELDS.issubset(case))
            self.assertIsInstance(case["expected_evidence_keywords"], list)
            self.assertIsInstance(case["forbidden_keywords"], list)
            self.assertGreaterEqual(len(case["expected_evidence_keywords"]), 3)

    def test_load_evaluation_cases_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            case_path = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".jsonl",
                dir=temp_dir,
                delete=False,
            )
            duplicate_case = {
                "id": "duplicate",
                "scenario": "退款售后",
                "case_type": "baseline",
                "query": "退款多久到账",
                "expected_intent": "退款进度",
                "expected_evidence_keywords": ["支付渠道", "原路退回", "订单详情页"],
                "forbidden_keywords": ["保证到账"],
                "notes": "duplicate check",
            }
            with case_path:
                case_path.write(json.dumps(duplicate_case, ensure_ascii=False) + "\n")
                case_path.write(json.dumps(duplicate_case, ensure_ascii=False) + "\n")

            with self.assertRaises(ValueError):
                load_evaluation_cases(case_path.name)

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
        succeeded_report["used_primary_evidence"] = True

        failed_report = build_grounding_report(
            query="food issue",
            retrieved_documents=["keep evidence and submit after-sales request"],
            reply="platform will compensate",
        )
        failed_report["judge_status"] = "failed"
        failed_report["judge_failure_type"] = "not_json"
        failed_report["needs_manual_review"] = True
        failed_report["mixed_supporting_intent"] = True

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
        self.assertEqual(summary["used_primary_evidence_count"], 1)
        self.assertEqual(summary["mixed_supporting_intent_count"], 1)
        self.assertEqual(
            summary["judge_failure_type_counts"],
            {
                "empty_response": 0,
                "not_json": 1,
                "missing_field": 0,
                "invalid_enum": 0,
                "empty_reason": 0,
                "other": 0,
            },
        )
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
        self.assertIn("run_config", payload)
        self.assertIn("rag_config", payload["run_config"])
        self.assertEqual(payload["run_config"]["use_local_judge"], True)
        self.assertEqual(
            payload["run_config"]["rag_config"]["embedding_model_name"],
            "BAAI/bge-small-zh-v1.5",
        )
        self.assertEqual(payload["report_count"], 1)
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["summary"]["judge_status_counts"]["not_run"], 1)
        self.assertIn("judge_failure_type_counts", payload["summary"])
        self.assertEqual(payload["reports"][0]["query"], "refund time")
        self.assertEqual(payload["reports"][0]["judge_status"], "not_run")
        self.assertIn("raw_judge_response", payload["reports"][0])
        self.assertIn("judge_error", payload["reports"][0])
        self.assertIn("judge_failure_type", payload["reports"][0])
        self.assertIn("retrieved_items", payload["reports"][0])
        self.assertIn("prompt_context_items", payload["reports"][0])
        self.assertIn("final_prompt", payload["reports"][0])
        self.assertIn("trace", payload["reports"][0])

    def test_build_grounding_reports_from_rag_uses_answer_provider(self) -> None:
        calls = []

        def fake_answer_provider(query: str) -> dict:
            calls.append(query)
            return {
                "reply": f"{query} 的客服回复",
                "retrieved_documents": [f"{query} 的参考资料"],
                "retrieved_items": [{"intent": f"{query} intent"}],
                "final_prompt": f"{query} prompt",
                "trace": {
                    "answer_source": "rag",
                    "reply_rules_applied": False,
                    "failure_stage": "none",
                },
            }

        reports = build_grounding_reports_from_rag(
            queries=["退款多久到账", "外卖超时了怎么办"],
            answer_provider=fake_answer_provider,
            case_metadata=[
                {
                    "expected_intent": "退款进度",
                    "expected_evidence_keywords": ["退款"],
                    "forbidden_keywords": ["保证"],
                },
                {
                    "expected_intent": "超时取消追问",
                    "expected_evidence_keywords": ["超时"],
                    "forbidden_keywords": ["一定赔付"],
                },
            ],
        )

        self.assertEqual(calls, ["退款多久到账", "外卖超时了怎么办"])
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["query"], "退款多久到账")
        self.assertEqual(reports[0]["reply"], "退款多久到账 的客服回复")
        self.assertEqual(reports[0]["retrieved_document_count"], 1)
        self.assertEqual(reports[0]["retrieved_items"], [{"intent": "退款多久到账 intent"}])
        self.assertEqual(reports[0]["expected_intent"], "退款进度")
        self.assertEqual(reports[0]["expected_evidence_keywords"], ["退款"])


        self.assertEqual(reports[0]["final_prompt"], f"{calls[0]} prompt")
        self.assertEqual(reports[0]["trace"]["answer_source"], "rag")


if __name__ == "__main__":
    unittest.main()
