import unittest

from services.reply_rules import apply_reply_rules


class ReplyRulesTest(unittest.TestCase):
    def test_private_transfer_rule_returns_required_safety_points(self) -> None:
        reply = apply_reply_rules(
            query="骑手让我私下转配送费可以吗",
            reply="请在订单内处理。",
            retrieved_items=[{"category": "平台安全", "intent": "私下收费风险"}],
        )

        self.assertIn("不建议私下转账", reply)
        self.assertIn("平台订单结算页", reply)
        self.assertIn("官方渠道", reply)

    def test_coupon_rule_returns_common_restrictions(self) -> None:
        reply = apply_reply_rules(
            query="优惠券为什么不能用",
            reply="请看页面。",
            retrieved_items=[{"category": "优惠券和促销类问题", "intent": "优惠券不可用"}],
        )

        for keyword in ["使用门槛", "有效期", "适用品类", "适用商家", "支付方式"]:
            self.assertIn(keyword, reply)

    def test_rider_unreachable_rule_returns_exception_flow(self) -> None:
        reply = apply_reply_rules(
            query="骑手联系不上怎么办",
            reply="请稍等。",
            retrieved_items=[{"category": "配送进度", "intent": "催单"}],
        )

        self.assertIn("订单详情页", reply)
        self.assertIn("配送异常", reply)
        self.assertIn("未收到餐反馈", reply)

    def test_spilled_food_rule_returns_after_sales_flow(self) -> None:
        reply = apply_reply_rules(
            query="餐洒了怎么申请售后",
            reply="请按页面提示操作。",
            retrieved_items=[{"category": "售后流程", "intent": "餐品撒漏售后"}],
        )

        self.assertIn("订单详情页", reply)
        self.assertIn("餐品问题", reply)
        self.assertIn("包装破损", reply)
        self.assertIn("撒漏", reply)
        self.assertIn("照片", reply)


if __name__ == "__main__":
    unittest.main()
