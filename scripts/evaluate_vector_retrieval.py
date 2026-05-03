from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.vector_retriever import retrieve_by_real_vector


EVAL_QUERIES = [
    {
        "query": "会员退款多久到账",
        "expected_intents": ["退款进度", "会员退款"],
    },
    {
        "query": "外卖超时怎么办",
        "expected_intents": ["催单", "配送超时", "配送延迟"],
    },
    {
        "query": "骑手联系不上怎么办",
        "expected_intents": ["联系骑手", "配送异常"],
    },
]


def print_query_result(case: dict) -> None:
    results = retrieve_by_real_vector(case["query"], limit=3)

    print("=" * 60)
    print(f"用户问题：{case['query']}")
    print(f"期望意图：{'、'.join(case['expected_intents'])}")
    print()

    for index, item in enumerate(results, start=1):
        source = item["source"]
        print(
            f"{index}. score={item['score']:.4f} "
            f"vector={item['vector_score']:.4f} "
            f"bonus={item['keyword_bonus']:.4f}"
        )
        print(f"   分类：{source.get('category', '')}")
        print(f"   意图：{source.get('intent', '')}")
        print(f"   问题：{source.get('question', '')}")
        print()


for case in EVAL_QUERIES:
    print_query_result(case)
