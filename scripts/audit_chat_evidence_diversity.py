"""审计：聊天链路 Top-K 的**证据多样性**（B17 的验收探针）。

## 这个脚本回答什么问题

聊天链路的证据预算是 ``limit=3``。它拿到的是 **3 条不同内容**，
还是 3 个名额被「同一段内容」的副本占掉（同一份文档的 md/html 孪生、
父子块同文本）？

走**真实聊天路径**，不模拟：

    retrieve_chunk_items_for_chat  →  adapt_chunk_items_for_prompt
                                   →  build_prompt_context_items

最后一列 ``effective_evidence_after_downstream_dedupe`` 才是结论 ——
``build_prompt_context_items`` 自己会按文本去重，**聊天实际拿到的有效证据条数**
才是影响答案质量的量。

## 输出文件名必须显式区分「去重前 / 去重后」

2026-09-25 实际踩到：本脚本第一版把输出路径**硬编码**成
``reports/rag_ingestion_auth_review/b17_topk_diversity_20260925.json``，
结果改完代码重跑时**把「接线前 2.14/3」的那份证据原地覆盖成了「接线后 3.00/3」**，
台账里引用的数字与文件内容当场对不上。

所以现在：
* ``--label`` 必填语义（``before`` / ``after`` / 自定义），进文件名；
* ``--out`` 可显式指定，默认按 ``B17_chat_evidence_diversity_<label>_<date>.json`` 生成；
* 文件已存在时**默认拒绝覆盖**（``--force`` 才覆盖）。

## 用法

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 RAG_JWT_SECRET=... \\
        ./venv/Scripts/python.exe scripts/audit_chat_evidence_diversity.py --label after

只读，不改任何数据。需要真实 embedding 模型 → 必须带 ``HF_HUB_OFFLINE=1``。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.auth_context import AuthContext  # noqa: E402
from services.chat_service import (  # noqa: E402
    adapt_chunk_items_for_prompt,
    retrieve_chunk_items_for_chat,
)
from services.ingestion import db  # noqa: E402
from utils.rag_context import build_prompt_context_items  # noqa: E402

QUERIES = [
    "外卖配送超时怎么赔偿",
    "消费者七天无理由退货的规定",
    "电子商务经营者应当履行的义务",
    "食品经营许可证怎么办",
    "广告不得含有哪些内容",
    "个人信息处理者收集个人信息需要遵守什么原则",
    "网络交易平台经营者对平台内经营者的管理义务",
]

TENANT = "tenant-dev"
USER = "dev_user"
CHAT_LIMIT = 3
REPORT_DIR = PROJECT_ROOT / "reports" / "rag_ingestion_auth_review"


def heading_of(item: dict) -> str:
    return " > ".join(item.get("heading_path") or [])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计聊天链路的证据多样性（B17）")
    parser.add_argument(
        "--label",
        default="after",
        help="这批数据代表哪个状态（如 before / after），进默认文件名",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="显式指定输出路径；不传则按 B17_chat_evidence_diversity_<label>_<date>.json 生成",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已存在的证据文件（默认拒绝，避免把前后两次结果混成一份）",
    )
    return parser.parse_args(argv)


def resolve_out_path(args: argparse.Namespace) -> Path:
    if args.out:
        return Path(args.out) if Path(args.out).is_absolute() else PROJECT_ROOT / args.out
    stamp = date.today().strftime("%Y%m%d")
    return REPORT_DIR / f"B17_chat_evidence_diversity_{args.label}_{stamp}.json"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_path = resolve_out_path(args)
    if out_path.exists() and not args.force:
        print(
            f"拒绝覆盖已存在的证据文件：{out_path}\n"
            f"（这是刻意的：先确认要覆盖的是哪一批数据；确需覆盖请加 --force）",
            file=sys.stderr,
        )
        return 2

    auth = AuthContext(tenant_id=TENANT, user_id=USER, roles=frozenset({"admin"}))
    report: list[dict] = []

    with db.session_scope():
        print("=" * 80)
        for query in QUERIES:
            raw = retrieve_chunk_items_for_chat(query, auth, CHAT_LIMIT)
            context_items = build_prompt_context_items(adapt_chunk_items_for_prompt(raw))

            ids = [item["chunk_id"] for item in raw]
            headings = [heading_of(item) for item in raw]
            texts = [" ".join(str(item.get("text", "")).split()) for item in raw]

            dup_ids = len(ids) - len(set(ids))
            dup_headings = len(headings) - len(set(headings))
            dup_texts = len(texts) - len(set(texts))

            print(f"\n【{query}】")
            print(f"  原始命中 {len(raw)} 条 → 下游组装出 **{len(context_items)} 条有效证据**")
            print(
                f"  重复情况：chunk_id 重复 {dup_ids} · heading 重复 {dup_headings} · "
                f"文本完全相同 {dup_texts}"
            )
            if len(context_items) < len(raw):
                print(f"  ↳ 被下游按文本去重吃掉 {len(raw) - len(context_items)} 条")
            for rank, item in enumerate(raw, start=1):
                text = " ".join(str(item.get("text", "")).split())[:56]
                print(f"    {rank}. score={item['score']:.4f} chunk={item['chunk_id'][:14]}")
                print(f"        {heading_of(item)[:70]}")
                print(f"        {text}")

            report.append(
                {
                    "query": query,
                    "raw_count": len(raw),
                    "distinct_chunk_ids": len(set(ids)),
                    "distinct_headings": len(set(headings)),
                    "distinct_texts": len(set(texts)),
                    "duplicate_chunk_ids": dup_ids,
                    "duplicate_headings": dup_headings,
                    "identical_texts": dup_texts,
                    "effective_evidence_after_downstream_dedupe": len(context_items),
                    "hits": [
                        {
                            "rank": rank,
                            "chunk_id": item["chunk_id"],
                            "document_id": item["document_id"],
                            "document_title": item["document_title"],
                            "heading_path": list(item.get("heading_path") or []),
                            "score": round(float(item["score"]), 4),
                        }
                        for rank, item in enumerate(raw, start=1)
                    ],
                }
            )

    slots = len(report) * CHAT_LIMIT
    wasted = sum(row["raw_count"] - row["effective_evidence_after_downstream_dedupe"] for row in report)
    dup_id_total = sum(row["duplicate_chunk_ids"] for row in report)
    effective_total = sum(row["effective_evidence_after_downstream_dedupe"] for row in report)

    print("\n" + "=" * 80)
    print(f"结论（{len(report)} 条 query，每条取前 {CHAT_LIMIT} 条 = {slots} 个名额）")
    print(f"  · chunk_id 级别的重复：{dup_id_total} 条（{dup_id_total / slots:.0%} 名额）")
    print(f"  · 被下游按文本去重吃掉：{wasted} 条")
    print(f"  · 平均有效证据数：{effective_total / len(report):.2f} / {CHAT_LIMIT}")
    print(f"  · 标签：{args.label}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "generated_by": "scripts/audit_chat_evidence_diversity.py",
                "label": args.label,
                "chat_limit": CHAT_LIMIT,
                "slots": slots,
                "duplicate_chunk_ids_total": dup_id_total,
                "wasted_slots_by_downstream_dedupe": wasted,
                "effective_evidence_total": effective_total,
                "avg_effective_evidence": round(effective_total / len(report), 4),
                "rows": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"证据已落盘：{out_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
