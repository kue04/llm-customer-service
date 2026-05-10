def apply_reply_rules(query: str, reply: str, retrieved_items: list[dict]) -> str:
    category_intent_text = " ".join(
        f"{item.get('category', '')} {item.get('intent', '')}"
        for item in retrieved_items
    )
    context = f"{query} {category_intent_text}"

    if "私下" in context and "转" in context:
        return (
            "不建议私下转账配送费。配送费应以平台订单结算页为准，任何额外费用都应通过官方渠道确认和处理。"
            "请您在订单内查看费用明细或提交反馈，避免产生资金纠纷。"
        )

    if "优惠券" in context or "优惠" in context:
        return (
            "优惠券不能使用通常与使用门槛、有效期、适用品类、适用商家或支付方式限制有关。"
            "请您点开优惠券详情或结算页查看不可用原因；如果确认满足条件仍不可用，可以截图后通过订单页或官方客服反馈核实。"
        )

    if "骑手联系不上" in query or ("骑手" in context and "联系不上" in context):
        return (
            "很抱歉让您久等了。请您先在订单详情页查看配送状态，并尝试通过平台内联系功能联系骑手。"
            "如果仍联系不上或订单已明显超时，建议立即在订单详情页提交配送异常或未收到餐反馈，平台会核实骑手位置和订单状态。"
        )

    if ("餐洒" in query or "撒漏" in context) and "申请售后" in query:
        return (
            "很抱歉影响您的用餐。餐品撒漏可以在订单详情页申请售后：请选择餐品问题、包装破损、撒漏或配送异常等对应类型，"
            "并上传餐品照片、包装破损、袋内撒漏、骑手送达照片或监控截图等凭证，平台会结合配送过程和凭证核实处理。"
        )

    return reply
