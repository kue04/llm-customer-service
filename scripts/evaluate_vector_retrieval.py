from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.vector_retriever import retrieve_by_real_vector


EVAL_QUERIES = [
    {
        "query": "会员退款多久到账",
        "expected_intents": ["退款进度", "会员退款"],
        "error_type": "",
        "notes": "",
    },
    {
        "query": "外卖超时怎么办",
        "expected_intents": ["催单", "配送超时", "配送延迟"],
        "error_type": "意图粒度相近",
        "notes": "向量检索把配送超时排到了超时取消后面。",
    },
    {
        "query": "骑手联系不上怎么办",
        "expected_intents": ["联系骑手", "配送异常", "配送异常追问"],
        "error_type": "角色方向相反",
        "notes": "用户联系不上骑手，不等于骑手联系不到用户。",
    },
]


def collect_top_intents(results: list[dict]) -> list[str]:
    return [item["source"].get("intent", "") for item in results]


def judge_result(top_intents: list[str], expected_intents: list[str]) -> str:
    if not top_intents:
        return "未命中"

    if top_intents[0] in expected_intents:
        return "Top1 命中"

    if any(intent in expected_intents for intent in top_intents):
        return "Top3 召回但 Top1 错误"

    return "未命中"


def print_query_result(case: dict) -> dict:
    results = retrieve_by_real_vector(case["query"], limit=3)
    top_intents = collect_top_intents(results)
    judgement = judge_result(top_intents, case["expected_intents"])

    print("=" * 60)
    print(f"用户问题：{case['query']}")
    print(f"期望意图：{'、'.join(case['expected_intents'])}")
    print(f"判断：{judgement}")

    if judgement != "Top1 命中" and case.get("error_type"):
        print(f"错误类型：{case['error_type']}")
        print(f"错误说明：{case.get('notes', '')}")

    print()

    for index, item in enumerate(results, start=1):
        source = item["source"]
        print(
            f"{index}. score={item['score']:.4f} "
            f"vector={item['vector_score']:.4f} "
            f"bonus={item['keyword_bonus']:.4f} "
            f"penalty={item.get('direction_penalty', 0.0):.4f}"
        )
        print(f"   分类：{source.get('category', '')}")
        print(f"   意图：{source.get('intent', '')}")
        print(f"   问题：{source.get('question', '')}")
        print()

    return {
        "query": case["query"],
        "judgement": judgement,
        "top_intents": top_intents,
        "error_type": case.get("error_type", ""),
        "notes": case.get("notes", ""),
    }


def summarize_error_types(results: list[dict]) -> dict[str, int]:
    error_counts = {}

    for result in results:
        if result["judgement"] == "Top1 命中":
            continue

        error_type = result.get("error_type") or "未标注"
        error_counts[error_type] = error_counts.get(error_type, 0) + 1

    return error_counts


def print_summary(results: list[dict]) -> None:
    total = len(results)
    top1_count = sum(1 for result in results if result["judgement"] == "Top1 命中")
    top3_ranking_error_count = sum(
        1 for result in results
        if result["judgement"] == "Top3 召回但 Top1 错误"
    )
    missed_count = sum(1 for result in results if result["judgement"] == "未命中")
    error_counts = summarize_error_types(results)

    print("=" * 60)
    print("向量检索评估汇总：")
    print(f"总问题数：{total}")
    print(f"Top1 命中：{top1_count}")
    print(f"Top3 召回但 Top1 错误：{top3_ranking_error_count}")
    print(f"未命中：{missed_count}")

    if error_counts:
        print()
        print("错误类型分布：")
        for error_type, count in error_counts.items():
            print(f"{error_type}：{count}")




def main() -> None:
    results = []

    for case in EVAL_QUERIES:
        result = print_query_result(case)
        results.append(result)

    print_summary(results)


if __name__ == "__main__":
    main()
