import json
import random
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SOURCE_PATH = DATA_DIR / "takeout_customer_service_seed.jsonl"
MESSAGES_DIR = DATA_DIR / "messages"
TARGET_SIZE = 500
RANDOM_SEED = 42

SYSTEM_PROMPT = (
    "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，"
    "最后给出可执行的下一步。不要编造平台规则；遇到支付、隐私、食品安全、"
    "站外交易等高风险问题时要提醒用户保留证据并通过官方渠道处理。"
)

QUESTION_PREFIXES = [
    "您好，",
    "客服你好，",
    "请问一下，",
    "麻烦问下，",
    "我想咨询一下，",
]

QUESTION_SUFFIXES = [
    "这个要怎么处理？",
    "现在我该怎么办？",
    "可以帮我看一下吗？",
    "这种情况平台怎么处理？",
    "能不能给我一个解决办法？",
]

COLLOQUIAL_REPLACEMENTS = [
    ("我的外卖", "我这单外卖"),
    ("怎么还没到", "咋还没到"),
    ("可以", "能不能"),
    ("怎么办", "咋办"),
    ("为什么", "为啥"),
    ("订单", "这单"),
    ("骑手", "配送员"),
    ("商家", "店家"),
    ("退款", "退钱"),
]

FOLLOW_UPS = [
    "那现在我应该先做什么？",
    "如果对方一直不处理怎么办？",
    "需要我上传什么凭证吗？",
    "我还能继续申请平台介入吗？",
    "这个会影响退款吗？",
]

BRIDGE_SENTENCES = {
    "negative": "我理解您现在比较着急，这类问题建议优先在订单内处理，方便平台核实。",
    "neutral": "您可以先根据订单页面提示操作，相关结果以页面展示和平台处理为准。",
    "positive": "感谢您的反馈，您也可以在订单评价中继续补充体验，帮助平台和商家改进服务。",
}


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def normalize_seed(row: dict, index: int) -> dict:
    entities = dict(row.get("entities") or {})
    risk = entities.get("risk", "低")
    quality = "high" if risk in {"低", "中", "高"} else "medium"
    return {
        "id": row.get("id") or f"takeout_{index:04d}",
        "source": row.get("source") or "curated_seed",
        "dialogue_type": row.get("dialogue_type") or "single_turn",
        "quality": row.get("quality") or quality,
        "question": row["question"].strip(),
        "answer": row["answer"].strip(),
        "category": row["category"].strip(),
        "intent": row["intent"].strip(),
        "sentiment": row["sentiment"].strip(),
        "entities": entities,
    }


def apply_colloquial_style(text: str) -> str:
    result = text
    for old, new in COLLOQUIAL_REPLACEMENTS:
        result = result.replace(old, new)
    if result == text:
        result = f"这个问题我有点不太明白，{text}"
    return result


def answer_with_followup(row: dict) -> str:
    bridge = BRIDGE_SENTENCES.get(row["sentiment"], BRIDGE_SENTENCES["neutral"])
    return f"{row['answer']} {bridge}"


def make_single_variant(row: dict, variant_no: int, prefix: str, suffix: str) -> dict:
    question = row["question"]
    if variant_no % 2 == 0:
        question = f"{prefix}{question}"
    else:
        question = f"{apply_colloquial_style(question)}{suffix}"

    item = dict(row)
    item.update(
        {
            "id": "",
            "source": "synthetic_augmentation",
            "dialogue_type": "single_turn",
            "quality": "medium",
            "question": question,
            "answer": answer_with_followup(row),
        }
    )
    return item


def make_multi_turn_variant(row: dict, follow_up: str) -> dict:
    item = dict(row)
    entities = dict(row["entities"])
    entities["context"] = "multi_turn_follow_up"
    item.update(
        {
            "id": "",
            "source": "synthetic_multi_turn",
            "dialogue_type": "multi_turn",
            "quality": "high" if row["sentiment"] == "negative" else "medium",
            "question": f"多轮对话：用户先问“{row['question']}”，客服初步回复后，用户继续追问“{follow_up}”",
            "answer": answer_with_followup(row),
            "entities": entities,
        }
    )
    return item


def dedupe_rows(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        key = (row["question"], row["answer"], row["intent"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def expand_dataset(seed_rows: list[dict]) -> list[dict]:
    random.seed(RANDOM_SEED)
    normalized = [normalize_seed(row, i + 1) for i, row in enumerate(seed_rows)]
    expanded = list(normalized)

    variants = []
    for index, row in enumerate(normalized):
        variants.append(
            make_single_variant(
                row,
                index,
                QUESTION_PREFIXES[index % len(QUESTION_PREFIXES)],
                QUESTION_SUFFIXES[index % len(QUESTION_SUFFIXES)],
            )
        )
        variants.append(
            make_single_variant(
                row,
                index + 1,
                QUESTION_PREFIXES[(index + 2) % len(QUESTION_PREFIXES)],
                QUESTION_SUFFIXES[(index + 2) % len(QUESTION_SUFFIXES)],
            )
        )
        variants.append(make_multi_turn_variant(row, FOLLOW_UPS[index % len(FOLLOW_UPS)]))

    expanded.extend(variants)
    expanded = dedupe_rows(expanded)

    if len(expanded) < TARGET_SIZE:
        cycle = 0
        while len(expanded) < TARGET_SIZE:
            row = normalized[cycle % len(normalized)]
            follow_up = FOLLOW_UPS[(cycle + 3) % len(FOLLOW_UPS)]
            variant = make_multi_turn_variant(row, follow_up)
            variant["question"] = variant["question"].replace("继续追问", "再次追问")
            expanded.append(variant)
            expanded = dedupe_rows(expanded)
            cycle += 1

    final_rows = expanded[:TARGET_SIZE]
    for index, row in enumerate(final_rows, start=1):
        row["id"] = f"takeout_{index:04d}"
    return final_rows


def to_messages(row: dict) -> dict:
    metadata = {
        "id": row["id"],
        "category": row["category"],
        "intent": row["intent"],
        "sentiment": row["sentiment"],
        "quality": row["quality"],
        "dialogue_type": row["dialogue_type"],
        "entities": row["entities"],
    }

    if row["dialogue_type"] == "multi_turn":
        quoted_parts = re.findall(r"“([^”]+)”", row["question"])
        first_user_message = quoted_parts[0] if quoted_parts else row["question"]
        follow_up_message = quoted_parts[1] if len(quoted_parts) > 1 else "那现在我应该怎么处理？"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": first_user_message},
            {"role": "assistant", "content": "我会先帮您按订单问题来判断，并尽量给出可以立即操作的处理方式。"},
            {"role": "user", "content": follow_up_message},
            {"role": "assistant", "content": row["answer"]},
        ]
    else:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": row["question"]},
            {"role": "assistant", "content": row["answer"]},
        ]

    return {"messages": messages, "metadata": metadata}


def split_messages(messages: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    shuffled = list(messages)
    random.seed(RANDOM_SEED)
    random.shuffle(shuffled)
    train_end = int(len(shuffled) * 0.8)
    val_end = int(len(shuffled) * 0.9)
    return shuffled[:train_end], shuffled[train_end:val_end], shuffled[val_end:]


def main() -> None:
    seed_rows = read_jsonl(SOURCE_PATH)
    expanded_rows = expand_dataset(seed_rows)
    write_jsonl(SOURCE_PATH, expanded_rows)

    messages = [to_messages(row) for row in expanded_rows]
    train_rows, val_rows, test_rows = split_messages(messages)

    write_jsonl(MESSAGES_DIR / "takeout_sft_messages_all.jsonl", messages)
    write_jsonl(MESSAGES_DIR / "takeout_sft_train.jsonl", train_rows)
    write_jsonl(MESSAGES_DIR / "takeout_sft_val.jsonl", val_rows)
    write_jsonl(MESSAGES_DIR / "takeout_sft_test.jsonl", test_rows)

    print(f"expanded_rows={len(expanded_rows)}")
    print(f"messages_all={len(messages)}")
    print(f"train={len(train_rows)} val={len(val_rows)} test={len(test_rows)}")
    print("category_counts=", dict(Counter(row["category"] for row in expanded_rows)))
    print("dialogue_type_counts=", dict(Counter(row["dialogue_type"] for row in expanded_rows)))
    print("quality_counts=", dict(Counter(row["quality"] for row in expanded_rows)))


if __name__ == "__main__":
    main()
