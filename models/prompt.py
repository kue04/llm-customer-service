# app/models/prompt.py
from typing import List


def create_prompt(query: str, documents: List[str]) -> str:
    lines = [
        "请根据以下参考资料回答用户问题。",
        "",
        "用户问题：",
        query,
        "",
        "参考资料：",
    ]

    for index, doc in enumerate(documents, start=1):
        lines.append(f"{index}. {doc}")

    lines.extend(
        [
            "",
            "回答要求：",
            "- 使用中文。",
            "- 语气礼貌、简洁。",
            "- 先安抚用户，再给出可执行建议。",
            "- 不要编造平台规则；如果资料不足，请建议用户通过订单页或官方客服渠道核实。",
            "",
            "客服回答：",
        ]
    )

    return "\n".join(lines)
