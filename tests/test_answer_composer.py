import unittest

from services.answer_composer import (
    compose_answer_if_needed,
    detect_query_needs,
    extract_answer_parts,
)


class AnswerComposerTest(unittest.TestCase):
    def test_detects_query_needs_for_boundary_request(self) -> None:
        needs = detect_query_needs("别让我点联系商家了，你能直接给我店家手机号吗")

        self.assertIn("yes_no_request", needs)
        self.assertIn("safety_or_privacy_boundary", needs)

    def test_extracts_refund_time_answer_parts(self) -> None:
        parts = extract_answer_parts(
            query="订单取消后钱多久退回来",
            primary_answer="取消后如已支付，退款通常会按支付渠道原路退回；到账时间可能受支付渠道处理影响，建议在订单详情页查看退款进度。",
        )

        self.assertIn("支付渠道", parts.conclusion)
        self.assertIn("订单详情页", parts.action)

    def test_composes_structured_reply_from_primary_answer(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="优惠券为什么不能用",
            reply="优惠券通常会有使用门槛、有效期、适用品类、适用商家和支付方式限制。您可以点开优惠券详情查看不可用原因。 我理解您现在比较着急，这类问题建议优先在订单内处理，方便平台核实。",
            retrieved_items=[
                {
                    "category": "优惠券和促销类问题",
                    "intent": "优惠券不可用",
                    "answer": "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。请您点开优惠券详情或结算页查看不可用原因；如果确认满足条件仍不可用，可以截图后通过订单页或官方客服反馈核实。",
                }
            ],
        )

        self.assertIn("使用门槛", reply)
        self.assertIn("优惠券详情", reply)
        self.assertNotIn("我理解您现在比较着急", reply)
        self.assertEqual(trace["reason"], "structured_from_primary_evidence")
        self.assertEqual(trace["answer_parts"]["conclusion"], "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。")

    def test_coupon_unavailable_reply_keeps_coupon_flow(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="优惠券不能用怎么办",
            reply="建议提交售后。",
            retrieved_items=[
                {
                    "category": "优惠券和促销类问题",
                    "intent": "优惠券不可用",
                    "answer": "优惠券通常会有使用门槛、有效期、适用品类、适用商家和支付方式限制。您可以点开优惠券详情查看不可用原因。",
                }
            ],
        )

        self.assertTrue(reply.startswith("优惠券不能使用通常"))
        self.assertIn("优惠券详情", reply)
        self.assertIn("结算页", reply)
        self.assertIn("截图", reply)
        self.assertNotIn("餐品错误售后", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。")

    def test_query_echo_is_marked_as_low_quality(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="订单取消后钱多久退回来",
            reply="订单取消后钱多久退回来？",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款进度",
                    "answer": "取消后如已支付，退款通常会按支付渠道原路退回；到账时间可能受支付渠道处理影响，建议在订单详情页查看退款进度。",
                }
            ],
        )

        self.assertIn("支付渠道", reply)
        self.assertTrue(trace["applied"])
        self.assertEqual(trace["reason"], "low_quality_model_reply")

    def test_can_or_not_question_prefers_direct_conclusion(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="骑手让我发验证码给他可以吗",
            reply="不可以。",
            retrieved_items=[
                {
                    "category": "平台安全",
                    "intent": "验证码诈骗提醒",
                    "answer": "不可以。验证码、密码等属于隐私和敏感信息，请不要向骑手或任何人提供。涉及退款、配送或订单处理，请通过订单页面或官方客服渠道操作，并保留相关沟通记录。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不可以。"))
        self.assertIn("官方客服渠道", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不可以")

    def test_adds_required_steps_for_unaccepted_order_cancel(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="商家一直不接单我想取消订单",
            reply="可以取消。",
            retrieved_items=[
                {
                    "category": "订单取消",
                    "intent": "取消订单",
                    "answer": "您可以在订单详情页发起取消申请。如果页面无法取消，可以联系平台客服进一步处理。系统会根据订单状态处理退款。",
                }
            ],
        )

        self.assertIn("商家一直未接单", reply)
        self.assertIn("订单详情页", reply)
        self.assertIn("平台客服", reply)
        self.assertEqual(trace["answer_parts"]["action"], "如果商家一直未接单，您可以在订单详情页发起取消申请；页面无法取消时再联系平台客服处理。")

    def test_adds_required_steps_for_privacy_phone_query(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="平台客服可以看我的手机号吗",
            reply="平台会保护手机号。",
            retrieved_items=[
                {
                    "category": "平台安全",
                    "intent": "隐私保护咨询",
                    "answer": "平台通常会通过隐私号等方式保护用户和骑手手机号。请尽量通过平台内联系功能沟通，不要在聊天中主动发送完整手机号、地址或验证码。",
                }
            ],
        )

        self.assertIn("不需要主动提供完整手机号", reply)
        self.assertIn("平台内联系功能", reply)
        self.assertIn("隐私号", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "平台客服处理订单时也应遵守平台隐私保护规则，您不需要主动提供完整手机号。")

    def test_uses_direct_conclusion_for_partial_refund(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="我取消订单后为什么只退了一部分钱",
            reply="这个需要看订单情况。",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款金额咨询",
                    "answer": "部分退款可能与商家是否已制作、配送是否已开始、优惠券抵扣和平台规则有关。您可以在退款详情页查看扣除原因，如有疑问可提交售后复核。",
                }
            ],
        )

        self.assertTrue(reply.startswith("订单取消后只退一部分"))
        self.assertIn("退款详情页", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "订单取消后只退一部分，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。")

    def test_partial_refund_handles_emotional_deducted_money_query(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="我都没吃上凭什么还扣我钱",
            reply="支付状态可能会延迟同步。",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款金额咨询",
                    "answer": "订单取消后只退一部分，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。您可以在退款详情页查看扣除原因，如有疑问可提交售后复核。",
                }
            ],
        )

        self.assertTrue(reply.startswith("没吃上但仍被扣款或只退部分金额"))
        self.assertIn("退款详情页", reply)
        self.assertIn("已制作", reply)
        self.assertEqual(
            trace["answer_parts"]["conclusion"],
            "没吃上但仍被扣款或只退部分金额，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。",
        )

    def test_merchant_phone_reply_avoids_repeating_lookup_path(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="商家电话在哪里看",
            reply="商家电话或联系商家入口一般可以在订单详情页查看，也可以进入商家主页查找联系商家入口。",
            retrieved_items=[
                {
                    "category": "常见问答",
                    "intent": "商家电话咨询",
                    "answer": "商家电话或联系商家入口一般可以在订单详情页查看，也可以进入商家主页查找“联系商家”入口。建议优先使用平台内电话或在线联系功能；如果页面没有展示电话，说明该商家可能未开放电话联系或使用平台虚拟号。",
                }
            ],
        )

        self.assertTrue(reply.startswith("商家电话一般在订单详情页"))
        self.assertIn("平台内电话或在线联系功能", reply)
        self.assertEqual(reply.count("订单详情页"), 1)
        self.assertEqual(trace["answer_parts"]["action"], "建议您优先通过平台内电话或在线联系功能沟通。")

    def test_merchant_phone_strong_request_uses_boundary_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="别让我点联系商家了，你能直接给我店家手机号吗",
            reply="商家电话可以在订单详情页查看。",
            retrieved_items=[
                {
                    "category": "常见问答",
                    "intent": "商家电话咨询",
                    "answer": "平台客服不能直接提供未在页面展示的商家手机号。您可以在订单详情页或商家主页查看“联系商家”入口；如果页面展示电话，建议通过平台内电话或在线联系功能沟通。如果没有展示，说明商家可能未开放电话联系或使用平台虚拟号，具体以页面展示为准。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接提供页面未展示的店家手机号。"))
        self.assertIn("联系商家", reply)
        self.assertIn("虚拟号", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接提供页面未展示的店家手机号。")

    def test_refund_dispute_keeps_deduction_reason_and_action(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="我都没吃上凭什么还扣我钱",
            reply="支付状态可能会延迟同步。",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款金额咨询",
                    "answer": "如果您没有实际用餐但仍被扣款或只退部分金额，建议先在退款详情页查看扣除原因。部分退款可能与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关；如您认为扣款不合理，可以在订单内提交售后复核或申请平台介入，平台会结合订单状态和凭证核实处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("没吃上但仍被扣款或只退部分金额"))
        self.assertIn("退款详情页", reply)
        self.assertIn("平台介入", reply)
        self.assertIn("商家是否已制作", trace["answer_parts"]["conclusion"])

    def test_delivery_fee_refund_dispute_mentions_refund_detail_and_review(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="餐没拿到还扣配送费合理吗",
            reply="需要看情况。",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款金额咨询",
                    "answer": "餐未收到但退款金额不完整时，建议您先查看退款详情页的扣除原因，并保留未收到餐、联系骑手或商家的相关记录。退款金额通常需要结合商家制作、配送进度、优惠抵扣和平台核实结果判断；如果页面原因不清楚，可以提交售后复核。",
                }
            ],
        )

        self.assertIn("退款详情页", reply)
        self.assertIn("售后复核", reply)
        self.assertIn("核实处理", reply)
        self.assertIn("配送费", trace["answer_parts"]["conclusion"])

    def test_spilled_food_intent_uses_after_sales_flow(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="汤全漏了咋弄",
            reply="请拍照。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "餐品撒漏",
                    "answer": "外卖汤洒了一袋子，建议保留餐品和包装照片，并在订单售后中提交问题，平台会结合配送过程和凭证核实处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("餐品撒漏、汤汁漏出或包装破损"))
        self.assertIn("订单详情页申请售后", reply)
        self.assertIn("凭证", reply)
        self.assertIn("核实处理", reply)
        self.assertEqual(trace["answer_parts"]["action"], "请在订单详情页申请售后，并上传包装破损、撒漏情况、餐品照片等凭证。")

    def test_privacy_query_about_merchant_real_phone_uses_privacy_boundary(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="商家能看到我的真实手机号吗",
            reply="商家电话可以在订单页查看。",
            retrieved_items=[
                {
                    "category": "平台安全",
                    "intent": "隐私保护咨询",
                    "answer": "平台通常会通过隐私号等方式保护用户和骑手手机号。请尽量通过平台内联系功能沟通，不要在聊天中主动发送完整手机号、地址或验证码。",
                }
            ],
        )

        self.assertTrue(reply.startswith("商家一般不应直接看到您的真实手机号"))
        self.assertIn("隐私号", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "商家一般不应直接看到您的真实手机号，平台通常会通过隐私号等方式保护联系方式。")

    def test_dietary_note_issue_mentions_food_problem(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="我写了忌口备注商家没照做，这算餐品问题吗",
            reply="可以申请售后。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "备注未满足",
                    "answer": "如果订单备注中已明确写了忌口要求，但商家未按备注制作，可以作为未按备注制作或餐品问题提交售后。建议您保留订单备注截图、餐品照片和相关沟通记录，平台会结合备注内容、餐品情况和凭证核实处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("如果已写明忌口或过敏备注"))
        self.assertIn("餐品问题", reply)
        self.assertIn("餐品照片", reply)
        self.assertIn("凭证", trace["answer_parts"]["caveat"])

    def test_rider_stuck_reply_avoids_forbidden_wording(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="骑手一直停在一个地方不动怎么办",
            reply="建议联系骑手。",
            retrieved_items=[
                {
                    "category": "配送进度",
                    "intent": "催单",
                    "answer": "骑手位置长时间未更新可能是网络延迟、等待商家出餐或路况影响。建议您先尝试联系骑手；如果无法联系，可以反馈配送异常。",
                }
            ],
        )

        self.assertTrue(reply.startswith("骑手位置一直停在一个地方"))
        self.assertIn("订单详情页反馈配送异常", reply)
        self.assertNotIn("不送了", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "骑手位置一直停在一个地方，通常可能与等待出餐、网络延迟或路况有关。")

    def test_rider_unreachable_reply_uses_delivery_exception_intent(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="骑手联系不上怎么办",
            reply="建议查看订单。",
            retrieved_items=[
                {
                    "category": "复杂对话",
                    "intent": "配送异常追问",
                    "answer": "理解您的着急。如果订单已超时且骑手无法联系，建议您立即在订单详情页提交配送异常或未收到餐反馈，平台会优先核实骑手位置和订单状态。",
                }
            ],
        )

        self.assertTrue(reply.startswith("骑手联系不上时"))
        self.assertIn("配送异常反馈", reply)
        self.assertNotIn("订单显示已送达但您没有收到餐", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "骑手联系不上时，建议先按配送异常处理。")

    def test_address_change_reply_requires_confirming_rider_location(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="我的地址写错了骑手已经到原地址了怎么办",
            reply="联系骑手。",
            retrieved_items=[
                {
                    "category": "复杂对话",
                    "intent": "地址修改追问",
                    "answer": "如果地址写错且骑手已经到达原地址，现在应立即联系骑手说明新地址。若新地址较远或超出配送范围，可能无法改送，您可以与骑手和平台客服协商处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("地址写错且骑手已经到原地址时"))
        self.assertIn("确认骑手是否仍在原地址", reply)
        self.assertIn("平台客服", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "地址写错且骑手已经到原地址时，需要尽快确认骑手当前位置并说明新地址。")

    def test_missing_food_guarantee_refuses_full_refund_promise_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="没收到餐你直接说一定给我退全款吧",
            reply="可以提交反馈。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "未收到餐",
                    "answer": "骑手取餐后仍未收到餐时，需要先按未收到餐或配送异常核实处理。建议您先确认门口、前台、取餐柜或指定收餐点是否有餐品，并尝试联系骑手；如果仍未找到，请在订单详情页提交未收到餐反馈。平台会核实骑手送达情况和订单状态，退款或售后结果以平台核实处理为准。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接承诺一定全额退款"))
        self.assertIn("无法立即取回餐品", reply)
        self.assertIn("订单详情页提交未收到餐反馈", reply)
        self.assertIn("平台核实处理为准", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接承诺一定全额退款，需要先由平台核实骑手送达情况和订单状态。")

    def test_accepted_order_cancel_guarantee_refuses_no_deduction_promise_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="商家接单了但还没送，你保证我取消后不扣钱吗",
            reply="可以查看订单。",
            retrieved_items=[
                {
                    "category": "订单取消",
                    "intent": "接单后取消",
                    "answer": "商家已接单或已开始制作后，能否取消及是否全额退款需要以订单状态、是否制作和页面展示为准。建议您先在订单详情页查看是否仍有取消入口；如果商家已开始制作，可能需要商家确认，也可以按页面提示提交售后或联系平台客服核实。取消结果和退款金额以订单状态、是否制作、商家确认和订单页面展示为准。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能保证商家接单后取消一定不扣钱"))
        self.assertIn("页面显示", reply)
        self.assertIn("订单详情页", reply)
        self.assertIn("联系平台客服核实", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能保证商家接单后取消一定不扣钱，能否取消和退款金额需要以订单状态、是否制作和页面显示为准。")

    def test_no_primary_item_keeps_reply(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="退款多久到账",
            reply="请在订单详情页查看退款进度。",
            retrieved_items=[],
        )

        self.assertEqual(reply, "请在订单详情页查看退款进度。")
        self.assertFalse(trace["applied"])
        self.assertEqual(trace["reason"], "no_primary_item")


if __name__ == "__main__":
    unittest.main()
