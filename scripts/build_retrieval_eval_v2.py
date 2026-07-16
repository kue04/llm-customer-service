import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SOURCE_PATH = PROJECT_ROOT / "data" / "chat_grounding_cases.jsonl"
OUTPUT_PATH = PROJECT_ROOT / "data" / "retrieval_eval_v2.jsonl"


def load_source_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in SOURCE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_case(case_type: str, index: int, source: dict, query: str, expected_intents=None, risk_level="low") -> dict:
    return {
        "id": f"retrieval_{case_type}_{index:03d}",
        "case_type": case_type,
        "risk_level": risk_level,
        "query": query,
        "expected_intents": expected_intents or [source["expected_intent"]],
        "source_case_id": source["id"],
    }


def paraphrase(query: str) -> str:
    replacements = (
        ("多久到账", "一般几时能退回来"),
        ("怎么办", "该怎么处理"),
        ("为什么", "是什么原因"),
        ("不能用", "用不了"),
        ("联系不上", "电话一直打不通"),
        ("可以吗", "行不行"),
    )
    rewritten = query
    for source, target in replacements:
        rewritten = rewritten.replace(source, target)
    return rewritten if rewritten != query else f"换个说法咨询一下：{query}"


def colloquialize(query: str) -> str:
    rewritten = query.replace("怎么办", "咋整").replace("为什么", "咋回事").replace("骑手", "骑首")
    rewritten = rewritten.replace("可以", "能").replace("没有", "没")
    return rewritten if rewritten != query else f"帮我瞅瞅，{query}"


def main() -> None:
    from utils.vector_retriever import detect_intent_hint

    sources = load_source_cases()
    baseline_sources = [item for item in sources if item["case_type"] == "baseline"]
    oral_sources = [item for item in sources if item["case_type"] == "oral"]
    multi_sources = [item for item in sources if item["case_type"] == "multi_intent"]
    risky_sources = [item for item in sources if item["case_type"] in {"boundary_promise", "inducement"}]
    direction_sources = [
        item for item in sources
        if any(term in item["query"] for term in ("骑手", "商家", "电话", "联系", "地址", "配送员", "客服"))
    ][:20]

    rows = []
    rows.extend(
        build_case("baseline", index, source, source["query"])
        for index, source in enumerate(baseline_sources[:20], start=1)
    )
    rows.extend(
        build_case("paraphrase", index, source, paraphrase(source["query"]))
        for index, source in enumerate(baseline_sources[20:40], start=1)
    )
    typo_sources = (oral_sources + baseline_sources[40:] + sources)[:20]
    rows.extend(
        build_case("typo_colloquial", index, source, colloquialize(source["query"]))
        for index, source in enumerate(typo_sources, start=1)
    )
    rows.extend(
        build_case(
            "direction_conflict",
            index,
            source,
            f"请按我描述的角色方向判断，不要把双方弄反：{source['query']}",
        )
        for index, source in enumerate(direction_sources, start=1)
    )

    multi_rows = [
        build_case("multi_intent", index, source, source["query"])
        for index, source in enumerate(multi_sources, start=1)
    ]
    for offset in range(20 - len(multi_rows)):
        left = baseline_sources[offset * 2]
        right = baseline_sources[offset * 2 + 1]
        multi_rows.append(
            build_case(
                "multi_intent",
                len(multi_rows) + 1,
                left,
                f"{left['query']}；另外，{right['query']}",
                [left["expected_intent"], right["expected_intent"]],
            )
        )
    rows.extend(multi_rows)

    high_risk_sources = risky_sources + [
        item for item in sources
        if any(term in item["query"] for term in ("异物", "手机号", "验证码", "赔偿"))
        and item not in risky_sources
    ]
    for index, source in enumerate(high_risk_sources[:20], start=1):
        hint = detect_intent_hint(source["query"])
        expected_intents = list(dict.fromkeys(filter(None, (hint, source["expected_intent"]))))
        rows.append(
            build_case(
                "high_risk",
                index,
                source,
                source["query"],
                expected_intents=expected_intents,
                risk_level="high",
            )
        )

    counts = {case_type: sum(row["case_type"] == case_type for row in rows) for case_type in {
        "baseline", "paraphrase", "typo_colloquial", "direction_conflict", "multi_intent", "high_risk"
    }}
    if len(rows) != 120 or any(count != 20 for count in counts.values()):
        raise RuntimeError(f"invalid retrieval v2 quota: total={len(rows)}, counts={counts}")
    OUTPUT_PATH.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
