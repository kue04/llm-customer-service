from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.retriever import retrieve_document_candidates

EVAL_QUERIES = [
    {
        "query": "会员退款怎么办？",
        "expected_keywords": ["会员", "退款"],
    },
    {
        "query": "外卖超时怎么办？",
        "expected_keywords": ["超时", "订单", "售后"],
    },
    {
        "query": "订单取消后钱多久到账？",
        "expected_keywords": ["取消", "退款", "到账"],
    },
    {
        "query": "骑手联系不上怎么办？",
        "expected_keywords": ["骑手", "联系", "订单"],
    },
    {
        "query": "食品有异物怎么办？",
        "expected_keywords": ["食品", "异物", "售后"],
    },
    {
        "query": "优惠券不能用怎么办？",
        "expected_keywords": ["优惠券", "不可用", "限制"],
    },
]



def check_expected_keywords(answer: str, expected_keywords: list[str]) -> list[str]:
    return [keyword for keyword in expected_keywords if keyword in answer]

def calculate_hit_rate(matched_keywords: list[str], expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 0

    return len(matched_keywords) / len(expected_keywords)

def judge_hit_rate(hit_rate: float) -> str:
    if hit_rate == 1:
        return "OK"
    if hit_rate >= 0.5:
        return "部分命中"
    if hit_rate > 0:
        return "弱命中"

    return "未命中"


def print_query_results(query: str, expected_keywords: list[str], limit: int = 3):
    candidates = retrieve_document_candidates(query, limit=limit)

    print("=" * 60)
    print(f"用户问题：{query}")
    print(f"期望关键词：{'、'.join(expected_keywords)}")
    print()

    if candidates:
        top_answer = candidates[0]["answer"]
        matched_expected_keywords = check_expected_keywords(top_answer, expected_keywords)
    else:
        matched_expected_keywords = []
  
    hit_rate = calculate_hit_rate(matched_expected_keywords, expected_keywords)
    judgement = judge_hit_rate(hit_rate)
    

    print(f"Top1 命中关键词：{'、'.join(matched_expected_keywords) or '无'}")
    print(
        f"Top1 命中率：{len(matched_expected_keywords)}/"
        f"{len(expected_keywords)} = {hit_rate:.1%}"
    )
    print(f"初步判断：{judgement}")

    print()

    for index, item in enumerate(candidates, start=1):
        print(
            f"{index}. score={item['score']} "
            f"matched_terms={item['matched_term_count']}"
        )
        print(f"  {item['answer']}")
        print()
    
    return {
        "query": query,
        "hit_rate": hit_rate,
        "judgement": judgement,
    }

def print_summary(results: list[dict]):
    total = len(results)
    ok_count = sum(1 for result in results if result["judgement"] == "OK")
    partial_count = sum(1 for result in results if result["judgement"] == "部分命中")
    weak_count = sum(1 for result in results if result["judgement"] == "弱命中")
    missed_count = sum(1 for result in results if result["judgement"] == "未命中")

    average_hit_rate = sum(result["hit_rate"] for result in results) / total if total else 0

    print("=" * 60)
    print("评估汇总：")
    print(f"总问题数：{total}")
    print(f"OK：{ok_count}")
    print(f"部分命中：{partial_count}")
    print(f"弱命中：{weak_count}")
    print(f"未命中：{missed_count}")
    print(f"平均命中率：{average_hit_rate:.1%}")


results = []

for case in EVAL_QUERIES:
    result = print_query_results(
        case["query"],
        case["expected_keywords"],
    )
    results.append(result)

print_summary(results)
