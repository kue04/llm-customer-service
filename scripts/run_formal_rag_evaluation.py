"""一键运行正式 chunk RAG 评测并生成聚合报告。"""
from __future__ import annotations
import argparse, json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from scripts.evaluate_formal_rag import aggregate_formal_report
from scripts.evaluate_hybrid_retrieval import COLLOQUIAL_PATH, GOLD_PATH, evaluate_dataset, load_jsonl
from scripts.evaluate_chat_grounding import build_grounding_reports_from_rag, load_formal_chunk_grounding_cases, summarize_grounding_reports
from services.auth_context import AuthContext
from services.chat_service import get_answer_from_rag
from services.ingestion.pipeline import default_embedding_model, default_index_root
from utils.hybrid_retriever import ChunkAccessFilter
from utils.vector_retriever import CHUNK_INDEX_NAME, describe_chunk_index

def run_formal_evaluation(tenant_id: str, top_k: int = 10) -> dict:
    root = default_index_root(); embedding_model = default_embedding_model()
    index = describe_chunk_index(root=root, index_name=CHUNK_INDEX_NAME)
    access = ChunkAccessFilter(tenant_id=tenant_id)
    retrieval = {}
    for name, path in (("title", GOLD_PATH), ("colloquial", COLLOQUIAL_PATH)):
        metrics, outcomes, latencies = evaluate_dataset(load_jsonl(path), access=access, index_root=root, embedding_model=embedding_model, top_k=top_k)
        retrieval[name] = {"metrics": metrics, "latency_ms": {mode: {"count": len(values), "mean": (sum(values)/len(values)*1000 if values else 0.0), "max": (max(values)*1000 if values else 0.0)} for mode, values in latencies.items()}, "details": [{"id": item.case_id, "ranks": item.ranks} for item in outcomes]}
    auth = AuthContext(user_id="formal-eval", tenant_id=tenant_id, roles=frozenset({"admin"}))
    cases = load_formal_chunk_grounding_cases()
    metadata = [{k: case.get(k, "") for k in ("id", "scenario", "case_type", "expected_intent", "expected_evidence_keywords", "forbidden_keywords", "notes")} for case in cases]
    reports = build_grounding_reports_from_rag([case["query"] for case in cases], lambda query: get_answer_from_rag(SimpleNamespace(message=query, user_id="formal-eval", channel="evaluation"), auth=auth), metadata)
    return aggregate_formal_report({"tenant_id": tenant_id, "index": index, "datasets": retrieval}, {"summary": summarize_grounding_reports(reports), "reports": reports})

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--tenant-id", default="tenant-dev"); parser.add_argument("--top-k", type=int, default=10); parser.add_argument("--output-dir", type=Path, default=Path("reports/formal_rag")); args = parser.parse_args()
    report = run_formal_evaluation(args.tenant_id, args.top_k); args.output_dir.mkdir(parents=True, exist_ok=True); output = args.output_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"; output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"); print(f"Saved report: {output}"); return 0

if __name__ == "__main__": raise SystemExit(main())
