# app/models/prompt.py
from typing import Sequence

from utils.rag_context import PromptContextItem


def create_prompt(query: str, context_items: Sequence[PromptContextItem]) -> str:
    lines = [
        "你是外卖平台中文客服。请只根据参考资料，直接输出给用户的最终客服回复。",
        "不要解释你的回答思路，不要复述任务要求，不要输出无关套话。",
        "如果参考资料不足以支持确定结论，请明确说明以订单页面或官方客服核实结果为准。",
        "",
        "用户问题：",
        query,
        "",
        "参考资料：",
    ]

    for item in context_items:
        reference_label = "最相关参考资料" if item.role == "primary" else "补充参考资料"
        if item.role == "primary" and item.evidence_strength == "close_match":
            reference_label = "最相关参考资料（与补充资料较接近，优先使用本条，不要混用其他意图结论）"
        lines.extend(
            [
                f"{item.rank}. {reference_label}",
                f"   title: {item.display_title}",
                f"   instruction: {item.prompt_instruction}",
                f"   intent: {item.intent or 'unknown'}",
                f"   question: {item.source_question or item.question or 'unknown'}",
                f"   evidence_summary: {item.evidence_summary or item.answer}",
            ]
        )

    lines.extend(
        [
            "",
            "回复要求：",
            "1. 使用中文，语气礼貌、简洁。",
            "2. 必须优先使用第 1 条最相关参考资料；补充参考资料只能用于补充流程，不得覆盖第 1 条的意图结论。",
            "3. 第一句必须直接回答用户核心问题，不能先说“我理解/建议您先/可能需要”等铺垫话。",
            "4. 回答必须覆盖最相关参考资料里的关键操作词、入口、限制条件和凭证要求；不要只写笼统的“以页面为准”。",
            "5. 不要编造平台规则，不要承诺一定赔付、一定退款或一定处理成功。",
            "6. 用户问“可以吗/能不能/可不可以”时，第一句先回答“可以/不建议/需要以订单状态为准”，再说明依据或步骤。",
            "7. 用户问“在哪里看”时，第一句必须给具体入口路径，例如订单详情页、商家主页、联系商家入口、售后入口。",
            "8. 用户问“怎么办/怎么处理”时，按“先确认状态 -> 执行页面操作或联系对象 -> 无效时提交反馈/售后/平台客服”的顺序回答。",
            "9. 用户问“多久到账/多久退回”时，第一句先回答到账时间受支付渠道影响，再说明原路退回、可能延迟和查看退款进度的入口；不确定时再加以订单页为准。",
            "10. 如果有条件限制，放在最后说明，并表达为以订单页或平台处理结果为准。",
            "",
            "最终客服回复：",
        ]
    )

    return "\n".join(lines)
