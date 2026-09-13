"""知识库扩容：把爬取的京东帮助中心 FAQ 清洗后合并进知识库（路线 B 第二步）。

流程：
  1. 读取 data/raw/jd_help_faq.jsonl（爬虫产物）
  2. 清洗：剥离答案尾部日期、去空行、最短长度过滤
  3. 领域过滤：只保留与外卖客服可迁移的分类（订单/支付/配送/退换/售后/优惠/促销/评价）；
     剔除京东特有业务（自提/PLUS/全球购/DIY/白条等）；"京东" → "平台" 机械替换
  4. 去重：与现有知识库按问题精确匹配 + 近似匹配（difflib ≥ 0.82）双重去重
  5. 合并：备份现有种子文件后追加新条目（id 前缀 jd_，source=jd_help_center）

用法：
    python scripts/expand_knowledge_base.py --dry-run   # 只报告，不写文件
    python scripts/expand_knowledge_base.py --merge     # 备份 + 追加
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

RAW_FAQ_PATH = PROJECT_ROOT / "data" / "raw" / "jd_help_faq.jsonl"
SEED_PATH = PROJECT_ROOT / "data" / "takeout_customer_service_seed.jsonl"
BACKUP_DIR = PROJECT_ROOT / "data" / "raw" / "backups"

# 与外卖客服领域可迁移的顶级分类关键词
RELEVANT_CATEGORY_RE = re.compile(r"订单|支付|配送|退换|售后|退款|优惠|促销|评价|发票|会员")
# 京东特有业务/实体，出现即剔除
DROP_ENTITY_RE = re.compile(
    r"自提|PLUS|全球购|DIY|装机|京东白条|京豆|E卡|京东卡|港澳|海外| Global |海囤"
)
# 机械替换：让条目领域中性化（注意：长词必须放在短词之前）
REWRITE_RULES = (
    ("我的京东", "我的订单"),
    ("京东", "平台"),
    ("美团", "平台"),
    ("饿了么", "平台"),
)
MIN_ANSWER_LEN = 25
MIN_QUESTION_LEN = 6
SIMILAR_THRESHOLD = 0.82

# 分类映射：京东子分类名 → 现有知识库 category
CATEGORY_MAPPING = (
    (re.compile(r"修改订单|订单信息|订单百事通|提交订单|订单查询"), "订单信息修改"),
    (re.compile(r"取消订单|退订"), "订单取消"),
    (re.compile(r"支付|付款|结算|货到付款|在线支付"), "订单支付问题"),
    (re.compile(r"配送|运费|物流|签收|送货"), "配送进度"),
    (re.compile(r"退换|售后|退款|维修|返修"), "退款售后"),
    (re.compile(r"优惠|促销|满减|红包|秒杀|赠品"), "优惠券和促销类问题"),
    (re.compile(r"评价|晒单"), "评价反馈"),
    (re.compile(r"发票"), "常见问答"),
    (re.compile(r"会员"), "常见问答"),
)


def clean_answer(answer: str) -> str:
    # 剥离答案尾部的日期行（如 "2018-02-20"）与多余空行
    lines = [ln.strip() for ln in answer.splitlines()]
    lines = [ln for ln in lines if ln and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", ln)]
    return "\n".join(lines).strip()


def rewrite(text: str) -> str:
    for old, new in REWRITE_RULES:
        text = text.replace(old, new)
    return text


def map_category(category: str, parent_category: str, question: str) -> str:
    text = f"{category} {parent_category} {question}"
    for pattern, mapped in CATEGORY_MAPPING:
        if pattern.search(text):
            return mapped
    return "常见问答"


def derive_intent(question: str, category: str) -> str:
    rules = (
        (re.compile(r"怎么.*(改|修改|变更)|修改"), "修改信息"),
        (re.compile(r"取消|撤销|退订"), "取消订单"),
        (re.compile(r"退款|退钱|退回"), "申请退款"),
        (re.compile(r"退货|换货|退换"), "退换货"),
        (re.compile(r"优惠|促销|满减|红包|券"), "优惠咨询"),
        (re.compile(r"多久|什么时候|几天|时效"), "时效咨询"),
        (re.compile(r"运费|配送费|多少钱"), "费用咨询"),
        (re.compile(r"为什么|凭什么|怎么就"), "原因咨询"),
        (re.compile(r"忘记|密码|账号"), "账号问题"),
    )
    for pattern, intent in rules:
        if pattern.search(question):
            return intent
    return category


def normalize_question(question: str) -> str:
    return re.sub(r"[\s?？!！。，,\.]+$", "", question.strip().lower())


def load_existing_questions(path: Path) -> list[str]:
    questions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                questions.append(normalize_question(json.loads(line)["question"]))
    return questions


def is_similar_to_any(candidate: str, pool: list[str], threshold: float) -> bool:
    # 先快速路径：精确重复
    if candidate in pool:
        return True
    for existing in pool:
        if difflib.SequenceMatcher(None, candidate, existing).ratio() >= threshold:
            return True
    return False


def transform(raw_records: list[dict]) -> list[dict]:
    kept, dropped = [], Counter()
    for rec in raw_records:
        question = rewrite(rec["question"].strip())
        answer = rewrite(clean_answer(rec["answer"]))
        full_text = f"{question} {answer} {rec['category']} {rec['parent_category']}"

        if not (MIN_QUESTION_LEN <= len(question) <= 80):
            dropped["question_length"] += 1
            continue
        if len(answer) < MIN_ANSWER_LEN:
            dropped["answer_too_short"] += 1
            continue
        if DROP_ENTITY_RE.search(full_text):
            dropped["jd_specific_entity"] += 1
            continue
        if not RELEVANT_CATEGORY_RE.search(f"{rec['category']} {rec['parent_category']}"):
            dropped["out_of_domain_category"] += 1
            continue

        mapped = map_category(rec["category"], rec["parent_category"], question)
        kept.append(
            {
                "raw_category": rec["category"],
                "parent_category": rec["parent_category"],
                "url": rec["url"],
                "question": question,
                "answer": answer,
                "category": mapped,
                "intent": derive_intent(question, mapped),
            }
        )
    return kept, dropped


def dedupe(candidates: list[dict], existing_questions: list[str]) -> tuple[list[dict], int]:
    kept = []
    pool = list(existing_questions)
    dup = 0
    for item in candidates:
        q = normalize_question(item["question"])
        if is_similar_to_any(q, pool, SIMILAR_THRESHOLD):
            dup += 1
            continue
        pool.append(q)
        kept.append(item)
    return kept, dup


def to_entry(idx: int, item: dict) -> dict:
    return {
        "id": f"jd_{idx:04d}",
        "source": "jd_help_center",
        "dialogue_type": "single_turn",
        "quality": "medium",
        "question": item["question"],
        "answer": item["answer"],
        "category": item["category"],
        "intent": item["intent"],
        "sentiment": "neutral",
        "entities": {"risk": "低", "origin_category": item["raw_category"]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="知识库扩容：清洗合并京东 FAQ")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写入")
    parser.add_argument("--merge", action="store_true", help="备份现有种子文件并追加")
    parser.add_argument("--limit", type=int, default=0, help="最多合并条数（0=不限）")
    args = parser.parse_args()
    if not args.dry_run and not args.merge:
        print("请指定 --dry-run 或 --merge")
        return

    raw_records = [
        json.loads(line) for line in RAW_FAQ_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    print(f"原始 FAQ：{len(raw_records)} 条")

    kept, dropped = transform(raw_records)
    print("清洗结果：")
    print(f"  保留 {len(kept)} 条；剔除 {sum(dropped.values())} 条：{dict(dropped)}")
    print("  分类分布：", Counter(item["category"] for item in kept).most_common())

    existing_questions = load_existing_questions(SEED_PATH)
    final, dup_count = dedupe(kept, existing_questions)
    print(f"  与现有知识库去重：剔除 {dup_count} 条，新增 {len(final)} 条")
    if args.limit:
        final = final[: args.limit]

    if args.dry_run:
        for item in final[:5]:
            print(f"  示例：[{item['category']}/{item['intent']}] {item['question']} → {item['answer'][:60]}…")
        return

    backup_dir = BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"takeout_customer_service_seed_{stamp}.jsonl"
    backup_path.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"已备份知识库 → {backup_path}")

    with open(SEED_PATH, "a", encoding="utf-8") as f:
        offset = len(existing_questions)
        for i, item in enumerate(final):
            f.write(json.dumps(to_entry(offset + i, item), ensure_ascii=False) + "\n")
    total = offset + len(final)
    print(f"合并完成：知识库 {offset} → {total} 条（新增 {len(final)}）")


if __name__ == "__main__":
    main()
