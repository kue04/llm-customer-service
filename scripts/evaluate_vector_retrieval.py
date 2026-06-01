import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from datetime import datetime
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "retrieval_eval"

from config.rag_config import get_rag_config_dict
from utils.vector_retriever import DEFAULT_MODEL_RERANK_WEIGHT, retrieve_by_real_vector


EVAL_QUERIES = [
    {
        "query": "会员退款多久到账",
        "expected_intents": ["退款进度", "会员退款"],
        "error_type": "",
        "notes": "同时覆盖会员服务和退款售后两个相邻业务域，用来观察检索是否能优先理解“多久到账”。",
    },
    {
        "query": "外卖超时怎么办",
        "expected_intents": ["催单", "配送延迟咨询", "超时取消追问"],
        "error_type": "意图粒度相近",
        "notes": "用户只说超时怎么办，可能是催单、配送延迟咨询，也可能进一步转向超时取消。",
    },
    {
        "query": "骑手联系不上怎么办",
        "expected_intents": ["配送异常追问", "催单"],
        "error_type": "角色方向相反",
        "notes": "用户联系不上骑手，不等于骑手联系不到用户。",
    },
    {
        "query": "饭里吃出异物怎么办",
        "expected_intents": ["食品安全投诉", "食品安全补充材料"],
        "error_type": "安全投诉粒度",
        "notes": "食品异物属于高风险售后，应优先命中食品安全投诉，而不是普通餐品撒漏或错送。",
    },
    {
        "query": "优惠券结算时不能用",
        "expected_intents": ["优惠券不可用", "优惠叠加咨询", "促销未生效"],
        "error_type": "优惠规则相近",
        "notes": "优惠问题容易在券不可用、叠加规则、活动未生效之间混淆。",
    },
    {
        "query": "取消订单后钱多久退回来",
        "expected_intents": ["退款进度", "退款金额咨询"],
        "error_type": "取消和退款跨域",
        "notes": "问题里有取消订单，但真正要问的是退款到账时间，适合检查 hybrid 是否能抓住退款主意图。",
    },
    {
        "query": "外卖已经超时了我不想要了",
        "expected_intents": ["超时取消", "超时取消追问", "取消订单"],
        "error_type": "配送转取消",
        "notes": "这类问题从配送异常转向取消订单，用来测试检索能否理解用户的下一步诉求。",
    },
    {
        "query": "商家一直不接单我想取消",
        "expected_intents": ["取消订单", "自动取消原因"],
        "error_type": "取消原因粒度",
        "notes": "核心诉求是取消订单，商家不接单只是取消原因，不应优先命中商家投诉。",
    },
    {
        "query": "我的账号被别人下单了怎么办",
        "expected_intents": ["账号异常"],
        "error_type": "账号安全",
        "notes": "账号被他人下单属于平台安全问题，不是普通历史订单查询。",
    },
    {
        "query": "我不想让骑手知道我的真实号码",
        "expected_intents": ["隐私保护咨询"],
        "error_type": "隐私保护",
        "notes": "这是隐私保护诉求，容易和修改联系方式、联系骑手等配送沟通问题混淆。",
    },
    {
        "query": "地址填错了骑手已经到原地址",
        "expected_intents": ["地址修改追问", "修改地址"],
        "error_type": "多轮追问",
        "notes": "包含地址错误和骑手已到达两个条件，优先命中复杂对话里的追问更合理。",
    },
    {
        "query": "骑手让我私下转配送费可以吗",
        "expected_intents": ["私下收费风险", "站外交易风险"],
        "error_type": "平台安全风险",
        "notes": "用户问是否可以私下转账，应该归入安全风险，而不是配送费咨询。",
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


def analyze_rerank_top1_change(
    results: list[dict],
    expected_intents: list[str],
) -> dict:
    if not results:
        return {
            "top1_changed": False,
            "impact": "unchanged",
            "original_top_intent": "",
            "reranked_top_intent": "",
        }

    original_results = sorted(
        results,
        key=lambda item: item["score"],
        reverse=True,
    )
    original_top_intent = original_results[0]["source"].get("intent", "")
    reranked_top_intent = results[0]["source"].get("intent", "")
    top1_changed = original_top_intent != reranked_top_intent

    original_hit = original_top_intent in expected_intents
    reranked_hit = reranked_top_intent in expected_intents

    if not top1_changed:
        impact = "unchanged"
    elif not original_hit and reranked_hit:
        impact = "improved"
    elif original_hit and not reranked_hit:
        impact = "worsened"
    else:
        impact = "changed_neutral"

    return {
        "top1_changed": top1_changed,
        "impact": impact,
        "original_top_intent": original_top_intent,
        "reranked_top_intent": reranked_top_intent,
    }


def summarize_rerank_top1_changes(results: list[dict]) -> dict[str, int]:
    changed = sum(
        1 for result in results
        if result["rerank_impact"] != "unchanged"
    )

    return {
        "changed": changed,
        "improved": sum(
            1 for result in results
            if result["rerank_impact"] == "improved"
        ),
        "worsened": sum(
            1 for result in results
            if result["rerank_impact"] == "worsened"
        ),
        "unchanged": sum(
            1 for result in results
            if result["rerank_impact"] == "unchanged"
        ),
        "changed_neutral": sum(
            1 for result in results
            if result["rerank_impact"] == "changed_neutral"
        ),
    }


def build_result_items(results: list[dict]) -> list[dict]:
    items = []
    for index, item in enumerate(results, start=1):
        source = item.get("source", {})
        items.append(
            {
                "rank": index,
                "score": item.get("score", 0.0),
                "rerank_score": item.get("rerank_score", item.get("score", 0.0)),
                "model_rerank_score": item.get("model_rerank_score", 0.0),
                "vector_score": item.get("vector_score", 0.0),
                "keyword_bonus": item.get("keyword_bonus", 0.0),
                "direction_penalty": item.get("direction_penalty", 0.0),
                "category": source.get("category", ""),
                "intent": source.get("intent", ""),
                "question": source.get("question", ""),
                "answer": item.get("answer", ""),
            }
        )
    return items


def print_query_result(
    case: dict,
    rerank_weight: float = DEFAULT_MODEL_RERANK_WEIGHT,
) -> dict:
    results = retrieve_by_real_vector(
        case["query"],
        limit=3,
        rerank_weight=rerank_weight,
    )
    result_items = build_result_items(results)
    top_intents = collect_top_intents(results)
    judgement = judge_result(top_intents, case["expected_intents"])
    rerank_analysis = analyze_rerank_top1_change(
        results,
        case["expected_intents"],
    )

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
            f"rerank={item.get('rerank_score', item['score']):.4f} "
            f"model={item.get('model_rerank_score', 0.0):.4f} "
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
        "rerank_impact": rerank_analysis["impact"],
        "original_top_intent": rerank_analysis["original_top_intent"],
        "reranked_top_intent": rerank_analysis["reranked_top_intent"],
        "results": result_items,
        "rerank_changed_count": sum(
            1
            for item in results
            if item.get("rerank_score", item["score"]) != item["score"]
        ),
    }


def summarize_error_types(results: list[dict]) -> dict[str, int]:
    error_counts = {}

    for result in results:
        if result["judgement"] == "Top1 命中":
            continue

        error_type = result.get("error_type") or "未标注"
        error_counts[error_type] = error_counts.get(error_type, 0) + 1

    return error_counts


def summarize_results(results: list[dict]) -> dict:
    rerank_top1_summary = summarize_rerank_top1_changes(results)

    return {
        "rag_config": get_rag_config_dict(),
        "total": len(results),
        "top1": sum(
            1 for result in results
            if result["judgement"].startswith("Top1")
        ),
        "top3_error": sum(
            1 for result in results
            if result["judgement"].startswith("Top3")
        ),
        "miss": sum(
            1 for result in results
            if not result["judgement"].startswith(("Top1", "Top3"))
        ),
        "rerank_changed_count": sum(
            result["rerank_changed_count"]
            for result in results
        ),
        "changed_top1": rerank_top1_summary["changed"],
        "improved": rerank_top1_summary["improved"],
        "worsened": rerank_top1_summary["worsened"],
        "unchanged": rerank_top1_summary["unchanged"],
        "changed_neutral": rerank_top1_summary["changed_neutral"],
    }


def save_report(
    results: list[dict],
    rerank_weight: float,
    output_dir: str | Path = DEFAULT_REPORT_DIR,
) -> Path:
    created_at = datetime.now().astimezone()
    run_id = created_at.strftime("%Y-%m-%d_%H-%M-%S")
    payload = {
        "run_id": run_id,
        "created_at": created_at.isoformat(timespec="seconds"),
        "script": "scripts/evaluate_vector_retrieval.py",
        "rerank_weight": rerank_weight,
        "rag_config": get_rag_config_dict(),
        "summary": summarize_results(results),
        "cases": results,
    }

    output_path = Path(output_dir) / f"{run_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    counter = 1
    while output_path.exists():
        output_path = Path(output_dir) / f"{run_id}-{counter}.json"
        counter += 1
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def print_summary(
    results: list[dict],
    rerank_weight: float = DEFAULT_MODEL_RERANK_WEIGHT,
) -> None:
    rag_config = get_rag_config_dict()
    total = len(results)
    top1_count = sum(1 for result in results if result["judgement"] == "Top1 命中")
    top3_ranking_error_count = sum(
        1 for result in results
        if result["judgement"] == "Top3 召回但 Top1 错误"
    )
    missed_count = sum(1 for result in results if result["judgement"] == "未命中")
    rerank_changed_count = sum(
        result["rerank_changed_count"]
        for result in results
    )
    rerank_top1_summary = summarize_rerank_top1_changes(results)
    error_counts = summarize_error_types(results)

    print(f"Rerank model weight: {rerank_weight}")
    print(f"Embedding model: {rag_config['embedding_model_name']}")
    print(f"Reranker model: {rag_config['reranker_model_name']}")
    print(f"Min vector score: {rag_config['min_vector_score']}")
    print(f"Reply rules enabled: {rag_config['reply_rules_enabled']}")
    print(f"FAISS store dir: {rag_config['faiss_store_dir']}")
    print("=" * 60)
    print("向量检索评估汇总：")
    print(f"总问题数：{total}")
    print(f"Top1 命中：{top1_count}")
    print(f"Top3 召回但 Top1 错误：{top3_ranking_error_count}")
    print(f"未命中：{missed_count}")
    print(f"Rerank 调整候选数：{rerank_changed_count}")

    print(f"Rerank 改变 Top1: {rerank_top1_summary['changed']}")
    print(f"Rerank 提高 Top1: {rerank_top1_summary['improved']}")
    print(f"Rerank 降低 Top1: {rerank_top1_summary['worsened']}")
    print(f"Rerank 未改变 Top1: {rerank_top1_summary['unchanged']}")
    print(f"Rerank 改变 Top1 neutral: {rerank_top1_summary['changed_neutral']}")

    if error_counts:
        print()
        print("错误类型分布：")
        for error_type, count in error_counts.items():
            print(f"{error_type}：{count}")




def evaluate_cases(
    rerank_weight: float,
    verbose: bool = True,
) -> list[dict]:
    results = []

    for case in EVAL_QUERIES:
        if verbose:
            result = print_query_result(case, rerank_weight=rerank_weight)
        else:
            with redirect_stdout(StringIO()):
                result = print_query_result(case, rerank_weight=rerank_weight)

        results.append(result)

    return results


def print_weight_comparison(weights: list[float]) -> None:
    print("Rerank weight comparison:")
    print(
        "weight\tTop1\tTop3Error\tMiss\tChangedTop1\t"
        "Improved\tWorsened"
    )

    for weight in weights:
        results = evaluate_cases(weight, verbose=False)
        summary = summarize_results(results)
        print(
            f"{weight:.2f}\t"
            f"{summary['top1']}\t"
            f"{summary['top3_error']}\t"
            f"{summary['miss']}\t"
            f"{summary['changed_top1']}\t"
            f"{summary['improved']}\t"
            f"{summary['worsened']}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rerank-weight",
        type=float,
        default=DEFAULT_MODEL_RERANK_WEIGHT,
        help="Weight applied to model_rerank_score.",
    )
    parser.add_argument(
        "--compare-rerank-weights",
        type=float,
        nargs="+",
        default=None,
        help="Run compact comparison for multiple rerank weights.",
    )
    parser.add_argument(
        "--save-report",
        action="store_true",
        help="Save evaluation results to a timestamped JSON report.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    if args.compare_rerank_weights:
        print_weight_comparison(args.compare_rerank_weights)
        return

    results = evaluate_cases(args.rerank_weight, verbose=True)
    print_summary(results, rerank_weight=args.rerank_weight)
    if args.save_report:
        output_path = save_report(results, rerank_weight=args.rerank_weight)
        print()
        print(f"Saved report: {output_path}")


if __name__ == "__main__":
    main()
