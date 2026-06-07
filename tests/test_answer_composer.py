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

    def test_merchant_owner_phone_request_refuses_direct_phone(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="别走平台了，把店主电话直接发我，我自己去说",
            reply="商家电话可以在订单详情页查看。",
            retrieved_items=[
                {
                    "category": "常见问答",
                    "intent": "商家电话咨询",
                    "answer": "商家电话一般在订单详情页或商家主页的“联系商家”入口查看，有电话时会在该入口展示。建议您优先通过平台内电话或在线联系功能沟通。如果页面没有展示电话，说明商家可能未开放电话联系或使用平台虚拟号。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接提供页面未展示的店家手机号"))
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

    def test_delivery_fee_refund_dispute_answers_reasonable_question_directly(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="餐没收到，退款还扣配送费，这合理吗",
            reply="需要看情况。",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款金额咨询",
                    "answer": "餐未收到但退款金额不完整时，建议您先查看退款详情页的扣除原因，并保留未收到餐、联系骑手或商家的相关记录。退款金额通常需要结合商家制作、配送进度、优惠抵扣和平台核实结果判断；如果页面原因不清楚，可以提交售后复核。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接判断扣配送费是否合理"))
        self.assertIn("退款详情页", reply)
        self.assertIn("售后复核", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接判断扣配送费是否合理，需要先看退款详情页的扣除原因和平台核实结果。")

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

    def test_note_issue_uses_requested_ingredient_instead_of_hardcoded_spicy(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="我备注不要葱，结果全是葱，这算商家没按备注做吗",
            reply="可以申请售后。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "备注未满足",
                    "answer": "如果备注了不要辣但餐品仍然很辣，可以按未按备注制作申请售后。提交售后时建议您上传餐品照片和订单备注截图，反馈未按备注制作。平台会结合订单备注、商家制作情况和凭证核实处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("如果已备注不要葱"))
        self.assertIn("订单备注截图", reply)
        self.assertNotIn("不要辣", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "如果已备注不要葱但餐品仍放了葱，可以按未按备注制作提交售后。")

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

        self.assertTrue(reply.startswith("不能直接承诺全额退款"))
        self.assertIn("无法立即取回餐品", reply)
        self.assertIn("订单详情页提交未收到餐反馈", reply)
        self.assertIn("平台核实处理为准", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接承诺全额退款，需要先由平台核实骑手送达情况和订单状态。")

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

    def test_refund_progress_guarantee_refuses_full_refund_promise_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="商家取消了订单，你直接承诺我全额退款可以吗",
            reply="可以查看订单。",
            retrieved_items=[
                {
                    "category": "退款售后",
                    "intent": "退款进度",
                    "answer": "退款到账时间取决于支付渠道。平台审核通过后通常会原路退回，银行卡或部分第三方支付可能存在处理延迟，您可以在订单详情页查看进度。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接承诺全额退款"))
        self.assertIn("支付渠道原路退回", reply)
        self.assertIn("订单详情页", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接承诺全额退款或具体到账时间，退款需要以订单状态和平台核实结果为准。")

    def test_delay_compensation_guarantee_refuses_payout_promise_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="超时这么久你能不能保证给我赔钱",
            reply="提交反馈。",
            retrieved_items=[
                {
                    "category": "配送进度",
                    "intent": "延误补偿",
                    "answer": "恶劣天气可能会影响配送时效。若订单超过承诺送达时间，您可以在订单详情页查看是否有延误补偿入口，或提交配送延迟反馈。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能保证超时后都会赔钱"))
        self.assertIn("补偿入口", reply)
        self.assertIn("配送延迟反馈", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能保证超时后都会赔钱，是否补偿需要看承诺送达时间、订单页面入口和平台核实结果。")

    def test_missing_item_guarantee_refuses_immediate_reship_promise_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="少送了一份你直接让商家马上补送可以吗",
            reply="可以联系商家。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "少送漏送",
                    "answer": "很抱歉出现漏送。建议您拍照保留收到的餐品和小票，并在订单详情页选择少送或漏送提交售后，平台会根据凭证协助处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接承诺由商家立即补送"))
        self.assertIn("拍照保留", reply)
        self.assertIn("少送或漏送提交售后", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接承诺由商家立即补送，少送漏送需要先提交售后并由平台核实。")

    def test_food_safety_payout_boundary_still_allows_feedback(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="饭里有异勿，我能直接让你们赔不",
            reply="可以申请。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "食品安全投诉",
                    "answer": "餐品有异物时，建议您先停止食用，并拍照保留异物、餐品和包装等凭证。您可以通过订单售后提交食品安全投诉，平台会结合凭证和订单情况核实处理；是否赔付以平台核实结果为准。",
                }
            ],
        )

        self.assertTrue(reply.startswith("需要通过订单售后提交食品安全投诉"))
        self.assertIn("平台会结合凭证和订单情况核实处理", reply)
        self.assertIn("停止食用", reply)
        self.assertIn("食品安全投诉", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "需要通过订单售后提交食品安全投诉，平台会结合凭证和订单情况核实处理，但不能在没有凭证和核实结果前承诺赔付。")

    def test_coupon_compensation_request_refuses_direct_payout(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="红包不能用，满减也没减，是不是平台要补我",
            reply="可以反馈。",
            retrieved_items=[
                {
                    "category": "优惠券和促销类问题",
                    "intent": "优惠券不可用",
                    "answer": "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。请您点开优惠券详情或结算页查看不可用原因；如果确认满足条件仍不可用，可以截图后通过订单页或官方客服反馈核实。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能直接判断平台会补偿"))
        self.assertIn("使用门槛", reply)
        self.assertIn("截图", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能直接判断平台会补偿，也不能直接赔一张券；优惠券或满减未生效需要先核实使用条件和结算页原因。")

    def test_invoice_oral_query_uses_non_duplicated_order_detail_flow(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="票子去哪儿开",
            reply="发票一般需要在订单详情页查看是否有申请发票入口。",
            retrieved_items=[
                {
                    "category": "常见问答",
                    "intent": "发票开具咨询",
                    "answer": "是否支持开发票取决于商家和订单类型。您可以在订单详情页查看是否有申请发票入口；如果没有入口，建议联系商家或平台客服确认。",
                }
            ],
        )

        self.assertTrue(reply.startswith("发票可以先在订单详情页"))
        self.assertEqual(reply.count("申请发票入口"), 1)
        self.assertIn("商家、订单类型", reply)
        self.assertEqual(trace["answer_parts"]["action"], "如果页面没有入口，可以联系商家或平台客服确认。")

    def test_picked_up_order_cancel_includes_after_sales_application(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="骑手取餐了但我不想要了，你直接帮我取消退款",
            reply="不能取消。",
            retrieved_items=[
                {
                    "category": "订单取消",
                    "intent": "取餐后取消",
                    "answer": "骑手已取餐后能否取消，需要以订单页面当前状态为准。建议您先联系骑手确认配送情况，并在订单详情页查看是否还有取消入口；如果因特殊原因无法收餐，可以在订单内提交售后申请，平台会结合订单状态核实处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("骑手取餐后通常不支持直接取消"))
        self.assertIn("售后申请", reply)
        self.assertIn("订单状态", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "骑手取餐后通常不支持直接取消，能否取消或退款需要以订单状态和平台核实结果为准。")

    def test_rider_attitude_guarantee_refuses_punishment_promise_first(self) -> None:
        reply, trace = compose_answer_if_needed(
            query="骑手态度差，你能保证处罚他并赔偿我吗",
            reply="可以投诉。",
            retrieved_items=[
                {
                    "category": "用户投诉",
                    "intent": "骑手态度投诉",
                    "answer": "抱歉给您带来不好的体验。您可以在订单详情页选择骑手服务投诉，并描述具体情况，平台会根据投诉内容进行核实处理。",
                }
            ],
        )

        self.assertTrue(reply.startswith("不能保证处罚骑手或赔偿"))
        self.assertIn("骑手服务投诉", reply)
        self.assertIn("平台核实", reply)
        self.assertEqual(trace["answer_parts"]["conclusion"], "不能保证处罚骑手或赔偿，平台需要先根据投诉内容核实处理。")

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
