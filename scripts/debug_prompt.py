import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from models.prompt import create_prompt
from utils.retriever import retrieve_document_candidates




FIELD_LABELS = {
    "intent": "意图字段",
    "category": "分类字段",
    "question": "问题字段",
    "answer": "答案字段",
}

REASON_LABELS = {
    "matched_terms": "命中关键词",
    "full_query_match": "完整命中用户问题",
}



def parse_args():
    parser = argparse.ArgumentParser(description="调试用户问题生成的 RAG prompt")
    parser.add_argument("query", nargs="?", default="会员退款怎么办？", help="要调试的用户问题")
    parser.add_argument("--limit", type=int, default=3, help="返回的候选资料数量")
    parser.add_argument("--no-prompt", action="store_true", help="只显示检索结果，不显示最终 prompt")

    return parser.parse_args()


args = parse_args()
candidates = retrieve_document_candidates(args.query, limit=args.limit)


print("检索候选资料：")
for index, item in enumerate(candidates, start=1):
    print(
        f"{index}. score={item['score']} "
        f"matched_terms={item['matched_term_count']} "
        f"answer={item['answer']}"
    )


    for detail in item["details"]:
        field_label = FIELD_LABELS.get(detail["field"], detail["field"])
        reason_label = REASON_LABELS.get(detail["reason"], detail["reason"])
        terms = "、".join(detail["terms"])

        print(f"   - {field_label}{reason_label}：{terms} -> +{detail['points']}")

if not args.no_prompt:
    documents = [item["answer"] for item in candidates]
    prompt = create_prompt(args.query, documents)

    print()
    print("最终 prompt：")
    print(prompt)
