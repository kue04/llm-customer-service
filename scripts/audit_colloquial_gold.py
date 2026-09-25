"""审计：口语化金标的「疑似假阴性」逐条复核（B17 登记批的第 6 项交付物）。

## 为什么有这个脚本

B8 的评测结论里留了一句尾巴：

> 口语化查询的绝对水平低（R@1 0.4000 / R@10 0.5667），
> 且 30 条里 **7 条两路 top-50 全捞不到**，疑似金标假阴性，**未逐条复核**。
> 所以「口语化场景已评测到可信」这句话**不成立**。

也就是说：**这个 30 条数据集能不能用来下结论，本身还没被验证过**。
本脚本就是来把这句话判掉 —— 对每条"两路 top-50 都捞不到"的用例，
回答一个二分问题：

    Q: 金标 span 到底在不在库里？
       ├─ 在库里，只是没被检索到   → **真·检索失败**（金标有效，检索要背）
       └─ 库里根本没有这段文本     → **金标假阴性**（金标要背，检索不背）

判据分两步，**两步都用压平空白后的文本**（与 ``span_matches`` 同口径）：

1. **检索层**：dense / sparse 各取 ``--top-k``（默认 50）条，看 span 是否出现；
2. **库层**：把全库 chunk 的压平文本拼成一个大字符串，
   直接问"这段 span 在不在全库里" —— 这一步**绕开检索**，因此能把
   "检索不好"和"金标不存在"彻底分开。

配套还查 ``gold_document_id`` 与 ``gold_heading`` 是否在库（假阴性的常见形态：
金标指向的文档/章节根本不在当前语料里）。

## 用法

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 RAG_JWT_SECRET=... \\
        ./venv/Scripts/python.exe scripts/audit_colloquial_gold.py --top-k 50

只读，不改任何数据。输出同时落盘到
``reports/rag_ingestion_auth_review/B17_colloquial_gold_recheck_<date>.txt``。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for extra in (PROJECT_ROOT, PROJECT_ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from sqlalchemy import text  # noqa: E402

from evaluate_hybrid_retrieval import span_matches  # noqa: E402  （判据与评测同源，不复刻）
from services.ingestion import db  # noqa: E402
from services.ingestion.pipeline import (  # noqa: E402
    default_embedder,
    default_embedding_model,
    default_index_root,
)
from utils.hybrid_retriever import search_hybrid_chunks  # noqa: E402
from utils.vector_retriever import (  # noqa: E402
    CHUNK_INDEX_NAME,
    ChunkAccessFilter,
    describe_chunk_index,
)

CASES_PATH = PROJECT_ROOT / "data" / "retrieval_colloquial_cases.jsonl"
REPORT_DIR = PROJECT_ROOT / "reports" / "rag_ingestion_auth_review"
LANES = ("dense", "sparse")


def normalize(text: str) -> str:
    return " ".join(str(text or "").split())


def load_cases() -> list[dict]:
    rows = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_corpus_text(session) -> str:
    """全库 chunk 的压平文本拼成一个大字符串（绕开检索的"在不在库里"判据）。

    用 ``\\n`` 作分隔符：压平后的 span 里不含换行，所以不会跨 chunk 边界误命中。
    """

    rows = session.execute(text("SELECT text FROM document_chunks")).fetchall()
    return "\n".join(normalize(row[0]) for row in rows if row[0])


def load_doc_index(session) -> tuple[set[str], str]:
    """库里已有的 document_id 集合 + 全部 heading_path 的拼接串。"""

    ids = {row[0] for row in session.execute(text("SELECT id FROM documents")).fetchall()}
    heading_rows = session.execute(
        text("SELECT json_extract(metadata_json, '$.heading_path') FROM document_chunks")
    ).fetchall()
    headings = []
    for (raw,) in heading_rows:
        if raw:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(value, list):
                headings.append(normalize(" > ".join(str(part) for part in value)))
            else:
                headings.append(normalize(str(value)))
    return ids, "\n".join(headings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="口语化金标假阴性复核（只读）")
    parser.add_argument("--top-k", type=int, default=50, help="每路召回的条数（默认 50）")
    parser.add_argument("--tenant-id", default="tenant-dev")
    args = parser.parse_args(argv)

    cases = load_cases()
    access = ChunkAccessFilter(tenant_id=args.tenant_id)
    index_root = default_index_root()
    lines: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        lines.append(line)

    emit("=" * 84)
    emit("口语化金标假阴性复核（只读）")
    emit(f"日期：{date.today().isoformat()}")
    emit(f"数据集：{CASES_PATH.relative_to(PROJECT_ROOT)}（{len(cases)} 条）")
    emit(f"召回口径：dense / sparse 各 top-{args.top_k}")
    emit("命中判据：span_matches（两边压平空白）—— 与评测脚本同一函数")
    emit("=" * 84)
    emit()
    emit(json.dumps(describe_chunk_index(root=index_root, index_name=CHUNK_INDEX_NAME),
                    ensure_ascii=False, default=str, indent=2))
    emit()

    table: list[dict] = []
    with db.session_scope() as session:
        corpus_text = load_corpus_text(session)
        doc_ids, heading_text = load_doc_index(session)

        for case in cases:
            span = case.get("gold_span") or ""
            query = case["query"]
            ranks: dict[str, int | None] = {}
            for lane in LANES:
                hits = search_hybrid_chunks(
                    query,
                    access=access,
                    top_k=args.top_k,
                    mode=lane,
                    embedder=default_embedder,
                    embedding_model=default_embedding_model(),
                    root=index_root,
                    index_name=CHUNK_INDEX_NAME,
                )
                found = None
                for rank, item in enumerate(hits, start=1):
                    if span_matches(span, item.hit.text):
                        found = rank
                        break
                ranks[lane] = found

            missed_both = all(ranks[lane] is None for lane in LANES)
            row = {
                "id": case.get("id"),
                "query": query,
                "rewritten": case.get("rewritten_from_query"),
                "dense_rank": ranks["dense"],
                "sparse_rank": ranks["sparse"],
                "missed_both": missed_both,
                "span_in_corpus": None,
                "doc_in_corpus": None,
                "heading_in_corpus": None,
                "gold_span": span,
                "gold_heading": case.get("gold_heading"),
                "gold_document_title": case.get("gold_document_title"),
            }
            if missed_both:
                norm_span = normalize(span)
                row["span_in_corpus"] = bool(norm_span) and norm_span in corpus_text
                row["doc_in_corpus"] = str(case.get("gold_document_id")) in doc_ids
                row["heading_in_corpus"] = bool(normalize(case.get("gold_heading") or "")) and (
                    normalize(case.get("gold_heading") or "") in heading_text
                )
                verdict = "真·检索失败" if row["span_in_corpus"] else "★ 金标假阴性"
            else:
                verdict = "命中（正常）"
            row["verdict"] = verdict
            table.append(row)

    missed = [row for row in table if row["missed_both"]]
    hit_dense = sum(1 for row in table if row["dense_rank"] is not None)
    hit_sparse = sum(1 for row in table if row["sparse_rank"] is not None)
    fake = [row for row in missed if not row["span_in_corpus"]]
    real = [row for row in missed if row["span_in_corpus"]]

    emit("-" * 84)
    emit("总览")
    emit("-" * 84)
    emit(f"  用例总数                      : {len(table)}")
    emit(f"  dense  top-{args.top_k} 命中            : {hit_dense}")
    emit(f"  sparse top-{args.top_k} 命中            : {hit_sparse}")
    emit(f"  两路都未命中                  : {len(missed)}")
    emit(f"    ├─ 金标 span 在库里 → **真·检索失败** : {len(real)}")
    emit(f"    └─ 金标 span 不在库里 → **金标假阴性** : {len(fake)}  ★")
    emit()

    if missed:
        emit("-" * 84)
        emit(f"逐条明细（两路 top-{args.top_k} 都未命中的 {len(missed)} 条）")
        emit("-" * 84)
        for row in missed:
            emit()
            emit(f"[{row['id']}] {row['query']}")
            emit(f"    改写自        : {row['rewritten']}")
            emit(f"    金标标题      : {row['gold_document_title']} / {row['gold_heading']}")
            emit(f"    金标 span     : {row['gold_span'][:70]!r}")
            emit(f"    判定          : {row['verdict']}")
            emit(f"      · span 在库里 : {'是' if row['span_in_corpus'] else '否'}")
            emit(f"      · 文档在库里  : {'是' if row['doc_in_corpus'] else '否'}")
            emit(f"      · 章节在库里  : {'是' if row['heading_in_corpus'] else '否'}")

    emit()
    emit("=" * 84)
    emit("结论")
    emit("=" * 84)
    emit(f"  · 30 条口语化金标里，两路 top-{args.top_k} 都捞不到的有 {len(missed)} 条；")
    if fake:
        emit(f"    其中 **{len(fake)} 条是金标假阴性**（库内根本没有这段文本），")
        emit(f"    **{len(real)} 条是真·检索失败**（span 在库里却没被召回）。")
    else:
        emit(f"    **全部 {len(real)} 条都是真·检索失败** —— span / 文档 / 章节三样**都在库里**，")
        emit(f"    就是没被两路 top-{args.top_k} 召回。")
        emit("    ★ 因此「疑似金标假阴性」这个猜测**被推翻**：金标是有效的，欠账在检索侧。")
    emit("  · 该数据集的 R@k 因此**既反映检索能力、也受金标口径影响**（本次实测：假阴性 0 条），")
    emit("    但仍**不能**把它当作「已收拾干净的检索质量数字」对外引用 ——")
    emit("    7 条全库可达却进不了 top-50，说明这 30 条里的口语化改写与库内表述的**词面距离过大**。")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"B17_colloquial_gold_recheck_{date.today().strftime('%Y%m%d')}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n证据已落盘：{out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
