import argparse
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.retriever import iter_knowledge_items


EXPRESSION_RULES = {
    "standard": ["怎么", "为什么", "吗", "能不能", "可以"],
    "emotional": ["凭什么", "别让我", "不想", "太", "恶心", "生气", "差", "投诉", "不合理"],
    "boundary": ["扣钱", "没全退", "只退", "手机号", "联系电话", "验证码", "私下", "赔", "忌口", "香菜"],
    "followup": ["多轮", "继续追问", "如果对方一直不处理", "现在我应该"],
    "terse": ["退款", "发票", "催单", "不到账"],
}

ANSWER_RULES = {
    "cause": ["因为", "可能", "取决于", "有关", "根据"],
    "action": ["建议", "可以", "请", "查看", "提交", "联系", "申请", "上传"],
    "caution": ["以页面", "以平台", "以订单", "为准", "核实", "不要", "不建议", "无法", "是否"],
    "evidence": ["照片", "截图", "凭证", "记录", "小票", "包装"],
}

CONFUSION_PAIRS = [
    ("退款金额咨询", "银行卡扣款异常"),
    ("退款金额咨询", "未收到餐"),
    ("商家电话咨询", "联系商家咨询"),
    ("催单", "配送异常追问"),
    ("备注未满足", "备注咨询"),
]


def classify_expression(question: str) -> set[str]:
    labels = {
        label
        for label, keywords in EXPRESSION_RULES.items()
        if any(keyword in question for keyword in keywords)
    }
    return labels or {"other"}


def classify_answer(answer: str) -> set[str]:
    return {
        label
        for label, keywords in ANSWER_RULES.items()
        if any(keyword in answer for keyword in keywords)
    }


def build_intent_audit(items: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for item in items:
        grouped[item.get("intent", "unknown")].append(item)

    rows = []
    for intent, intent_items in grouped.items():
        expression_counts = defaultdict(int)
        answer_signal_counts = defaultdict(int)
        categories = set()
        dialogue_types = defaultdict(int)

        for item in intent_items:
            question = item.get("question", "")
            answer = item.get("answer", "")
            categories.add(item.get("category", ""))
            dialogue_types[item.get("dialogue_type", "unknown")] += 1
            for label in classify_expression(question):
                expression_counts[label] += 1
            for label in classify_answer(answer):
                answer_signal_counts[label] += 1

        risks = []
        if len(intent_items) <= 2:
            risks.append("sample_count_low")
        if expression_counts.get("emotional", 0) == 0:
            risks.append("missing_emotional_expression")
        if expression_counts.get("boundary", 0) == 0:
            risks.append("missing_boundary_expression")
        if answer_signal_counts.get("action", 0) == 0:
            risks.append("answer_missing_action")
        if answer_signal_counts.get("caution", 0) == 0:
            risks.append("answer_missing_caution")

        rows.append(
            {
                "intent": intent,
                "category": "/".join(sorted(category for category in categories if category)),
                "sample_count": len(intent_items),
                "expression_counts": dict(sorted(expression_counts.items())),
                "answer_signal_counts": dict(sorted(answer_signal_counts.items())),
                "dialogue_type_counts": dict(sorted(dialogue_types.items())),
                "risks": risks,
            }
        )

    return sorted(rows, key=lambda row: (row["sample_count"], row["intent"]))


def summarize_confusion_pairs(rows: list[dict]) -> list[dict]:
    by_intent = {row["intent"]: row for row in rows}
    results = []
    for left, right in CONFUSION_PAIRS:
        left_row = by_intent.get(left)
        right_row = by_intent.get(right)
        if not left_row or not right_row:
            continue
        results.append(
            {
                "pair": f"{left} vs {right}",
                "left_count": left_row["sample_count"],
                "right_count": right_row["sample_count"],
                "left_risks": left_row["risks"],
                "right_risks": right_row["risks"],
            }
        )
    return results


def print_audit(rows: list[dict], top: int) -> None:
    print("Knowledge base audit")
    print(f"intent_count: {len(rows)}")
    print(f"shown_intents: {min(top, len(rows))}")
    print()
    print("Weak intents")
    for row in rows[:top]:
        print("-" * 80)
        print(f"intent: {row['intent']}")
        print(f"category: {row['category']}")
        print(f"sample_count: {row['sample_count']}")
        print(f"dialogue_type_counts: {row['dialogue_type_counts']}")
        print(f"expression_counts: {row['expression_counts']}")
        print(f"answer_signal_counts: {row['answer_signal_counts']}")
        print(f"risks: {row['risks']}")

    confusion_rows = summarize_confusion_pairs(rows)
    if not confusion_rows:
        return

    print()
    print("Confusion pair watchlist")
    for row in confusion_rows:
        print("-" * 80)
        print(f"pair: {row['pair']}")
        print(f"sample_count: left={row['left_count']}, right={row['right_count']}")
        print(f"left_risks: {row['left_risks']}")
        print(f"right_risks: {row['right_risks']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit RAG knowledge base samples.")
    parser.add_argument("--top", type=int, default=20, help="Number of weakest intents to show.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = build_intent_audit(list(iter_knowledge_items()))
    print_audit(rows, args.top)


if __name__ == "__main__":
    main()
