"""聚合正式 chunk RAG 评测报告。

该脚本不重新实现检索，只聚合 ``evaluate_hybrid_retrieval.py`` 的 chunk/span
指标和 grounding 报告，统一记录数据来源、索引版本、证据覆盖与路由统计。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "formal_rag"


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def aggregate_formal_report(retrieval: dict, grounding: dict | None = None) -> dict:
    index = dict(retrieval.get("index") or {})
    datasets = retrieval.get("datasets") or {}
    grounding_payload = grounding or {}
    summary = grounding_payload.get("summary") or {}
    return {
        "report_type": "formal_chunk_rag",
        "retrieval_path": "chunk-index",
        "data_source": "formal_chunk_corpus",
        "index": index,
        "retrieval": {
            "datasets": datasets,
            "metrics": {label: value.get("metrics", {}) for label, value in datasets.items()},
        },
        "evidence_and_grounding": {
            "source_report": grounding_payload.get("run_id", ""),
            "summary": summary,
            "route_counts": summary.get("route_counts", {}),
            "clarify_count": summary.get("clarify_count", 0),
            "human_handoff_count": summary.get("human_handoff_count", 0),
            "citation_quality": summary.get("citation_quality", {}),
        },
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate formal chunk RAG evaluation reports.")
    parser.add_argument("--retrieval-report", type=Path, required=True)
    parser.add_argument("--grounding-report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    report = aggregate_formal_report(load_json(args.retrieval_report), load_json(args.grounding_report) if args.grounding_report else None)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
