"""聚合正式 chunk RAG 评测报告。

该脚本不重新实现检索，只聚合 ``evaluate_hybrid_retrieval.py`` 的 chunk/span
指标和 grounding 报告，统一记录数据来源、索引版本、证据覆盖与路由统计。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from collections import Counter
import hashlib


def score_evidence(evidence: list[dict], spans: list[str], relevant_ids: set[str], k: int = 5) -> dict:
    """Separate query success, required evidence coverage, and chunk relevance.

    Relevance is a silver span label, not exhaustive human chunk judgments.
    Duplicate chunks cannot inflate either denominator. Empty labels are N/A.
    """
    from scripts.evaluate_hybrid_retrieval import span_matches

    selected = list({item['chunk_id']: item for item in evidence[:k]}.values())
    ranks = [next((i for i, item in enumerate(evidence, 1)
                   if span_matches(span, item.get('text', ''))), None) for span in spans]
    covered = sum(rank is not None and rank <= k for rank in ranks)
    retrieved_ids = {item['chunk_id'] for item in selected}
    true_positives = len(retrieved_ids & relevant_ids)
    return {
        'query_hit': int(covered > 0) if spans else None,
        'query_all_evidence': int(covered == len(spans)) if spans else None,
        'evidence_coverage': covered / len(spans) if spans else None,
        'chunk_precision': true_positives / len(selected) if selected else 0.0,
        'chunk_recall': true_positives / len(relevant_ids) if relevant_ids else None,
        'gold_chunk_count': len(relevant_ids), 'ranks': ranks,
    }


def summarize_quality(rows: list[dict]) -> dict:
    if not rows:
        return {'count': 0, 'status': 'not_evaluated', 'metrics': None}
    variants = list(rows[0]['scores'])
    metrics = {}
    for variant in variants:
        metrics[variant] = {}
        cutoff = 3 if variant.endswith('default3') else 5
        for key in ('query_hit', 'query_all_evidence', 'evidence_coverage', 'chunk_precision', 'chunk_recall'):
            values = [row['scores'][variant][key] for row in rows if row['scores'][variant][key] is not None]
            metrics[variant][key + f'@{cutoff}'] = sum(values) / len(values) if values else None
            metrics[variant][key + '_denominator'] = len(values)
    labeled = [row for row in rows if row.get('expected_intents') is not None]
    tp = fp = fn = 0
    for row in labeled:
        expected, predicted = set(row['expected_intents']), set(row['predicted_intents'])
        tp += len(expected & predicted)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    return {'count': len(rows), 'status': 'evaluated', 'metrics': metrics,
        'intent': {'labeled_count': len(labeled), 'unlabeled_count': len(rows) - len(labeled),
            'exact_set_accuracy': sum(set(r['expected_intents']) == set(r['predicted_intents']) for r in labeled) / len(labeled) if labeled else None,
            'micro_precision': tp / (tp + fp) if tp + fp else None,
            'micro_recall': tp / (tp + fn) if tp + fn else None},
        'failures': dict(Counter(row['failure_stage'] for row in rows if row['failure_stage'])),
        'rewrite_effect': {
            'gained': [r['id'] for r in rows if r['scores']['rewrite_only']['query_hit'] > r['scores']['selected_raw']['query_hit']],
            'lost': [r['id'] for r in rows if r['scores']['rewrite_only']['query_hit'] < r['scores']['selected_raw']['query_hit']]},
        'plan_effect': {
            'gained': [r['id'] for r in rows if r['scores']['production_plan']['query_hit'] > r['scores']['selected_raw']['query_hit']],
            'lost': [r['id'] for r in rows if r['scores']['production_plan']['query_hit'] < r['scores']['selected_raw']['query_hit']]}}


def compare_quality(before: dict, after: dict) -> dict:
    """Suggested offline non-regression gates; never represent an online SLA."""
    for key in ('snapshot', 'fusion', 'budgets', 'visible_chunk_count'):
        if before[key] != after[key]:
            raise ValueError(f'incomparable evaluation configuration: {key}')
    groups = {}
    if set(before['datasets']) != set(after['datasets']):
        raise ValueError('incomparable dataset groups')
    for name, old in before['datasets'].items():
        new = after['datasets'][name]
        if old['sha256'] != new['sha256']:
            raise ValueError(f'changed dataset: {name}')
        old_rows = {r['id']: r for r in old['details']}
        new_rows = {r['id']: r for r in new['details']}
        if set(old_rows) != set(new_rows) or len(old_rows) != old['count'] or len(new_rows) != new['count']:
            raise ValueError(f'changed or duplicate case ids: {name}')
        paired = {}
        for variant in old['metrics']:
            gained, lost = [], []
            for case_id, row in old_rows.items():
                a = row['scores'][variant]['query_all_evidence']
                b = new_rows[case_id]['scores'][variant]['query_all_evidence']
                if b > a:
                    gained.append(case_id)
                elif b < a:
                    lost.append(case_id)
            paired[variant] = {'gained': gained, 'lost': lost}
        groups[name] = {'count': old['count'], 'before': old['metrics'], 'after': new['metrics'],
            'paired_all_evidence': paired,
            'suggested_non_regression_gate': 'pass' if not any(p['lost'] for p in paired.values()) else 'fail',
            'by_query_type': {kind: {
                'before': summarize_quality([r for r in old['details'] if r['query_type'] == kind]),
                'after': summarize_quality([r for r in new['details'] if r['query_type'] == kind])}
                for kind in sorted({r['query_type'] for r in old['details']})}}
    intent_before, intent_after = before['intent_classification'], after['intent_classification']
    if intent_before['sha256'] != intent_after['sha256']:
        raise ValueError('changed intent dataset')
    return {'gate_scope': 'suggested offline paired non-regression; not SLA', 'groups': groups,
        'intent': {'before': intent_before['primary_accuracy'], 'after': intent_after['primary_accuracy'],
            'count': intent_after['count'], 'gate': 'pass' if intent_after['primary_accuracy'] >= intent_before['primary_accuracy'] else 'fail'},
        'blind': {'gate': 'blocked', 'reason': 'no independent human-labeled retrieval blind set'},
        'release_quality': 'not_established',
        'known_gaps': ['silver span labels, not exhaustive chunk relevance',
            'title/colloquial have no intent labels', 'candidate labels require human review',
            'historical corpus includes question-only chunks and legacy ungated versions']}


def run_retrieval_quality(snapshot_path: Path, output_path: Path, candidate_path: Path | None = None) -> dict:
    """Evaluate the existing production retrieval functions, without answer generation.

    CLI must set RAG_FAISS_STORE_DIR to the prepared snapshot before imports.
    Title/colloquial gold is never edited or silently filtered. Blind is absent
    until independently labeled; existing answer-grounding blind data is unopened.
    """
    from dataclasses import asdict
    from scripts.evaluate_hybrid_retrieval import GOLD_PATH, COLLOQUIAL_PATH, load_jsonl, span_matches
    from services.auth_context import AuthContext
    from services.chat_service import retrieve_chunk_items_for_chat, retrieve_with_query_plan
    from services.ingestion.db import session_scope
    from services.ingestion.pipeline import default_embedder, default_embedding_model, default_index_root
    from services.intent_service import analyze_intents
    from services.query_resolution import resolve_query
    from services.retrieval_access import build_chunk_access_filter
    from utils.hybrid_retriever import FusionConfig, search_hybrid_chunks
    from utils.vector_retriever import describe_chunk_index, load_chunk_index

    snapshot = load_json(snapshot_path)
    root = default_index_root()
    if root.resolve() != Path(snapshot['index_root']).resolve():
        raise ValueError('RAG_FAISS_STORE_DIR must match snapshot index_root')
    if output_path.exists():
        raise ValueError('refusing to overwrite evaluation evidence')
    auth = AuthContext(user_id=snapshot['user_id'], tenant_id=snapshot['tenant_id'], roles=frozenset({'admin'}))
    with session_scope() as session:
        access = build_chunk_access_filter(session, auth)
    manifest, _ = load_chunk_index(root=root)
    entries = [e for e in manifest.entries if e.tenant_id == auth.tenant_id and e.chunk_id in access.allowed_chunk_ids]
    if not entries:
        raise ValueError('no published, ACL-visible evaluation chunks')
    paths = {'title': GOLD_PATH, 'colloquial': COLLOQUIAL_PATH}
    if candidate_path:
        paths['synthetic_candidates'] = candidate_path
    report = {'report_type': 'retrieval_quality_5_3', 'generated_at': datetime.now().astimezone().isoformat(),
        'snapshot': snapshot, 'index': describe_chunk_index(root=root), 'fusion': asdict(FusionConfig()),
        'paths': {'recall': 'search_hybrid_chunks + production access filter',
            'selection': 'retrieve_chunk_items_for_chat', 'plan': 'retrieve_with_query_plan',
            'rewrite_only': 'resolve_query without intent hints', 'intent': 'analyze_intents'},
        'scope': 'real local query embedding; cached real corpus vectors; no generation or HTTP/worker rerun',
        'visible_chunk_count': len(entries), 'datasets': {},
        'budgets': {'diagnostic_top_k': 10, 'diagnostic_scoring_k': 5, 'chat_default_k': 3},
        'chunk_label_scope': 'silver span-derived relevance over visible corpus, not exhaustive semantic judgments',
        'blind': {'count': 0, 'status': 'not_evaluated_missing_independent_retrieval_labels', 'metrics': None}}
    intent_path = PROJECT_ROOT / 'data/chat_grounding_cases.jsonl'
    report['intent_classification'] = {**evaluate_intent_labels(load_jsonl(intent_path)), 'source': str(intent_path),
        'annotation_status': 'legacy primary-intent labels; independent human provenance unverified',
        'sha256': hashlib.sha256(intent_path.read_bytes()).hexdigest(),
        'multilabel_metrics': None, 'multilabel_gap': 'legacy dataset supplies primary intent only'}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for group, path in paths.items():
        cases, rows = load_jsonl(path), []
        for case in cases:
            query, context = case['query'], case.get('context') or {}
            spans = case.get('gold_spans') or [case['gold_span']]
            relevant = {e.chunk_id for e in entries if any(span_matches(span, e.text) for span in spans)}
            span_presence = [any(span_matches(span, e.text) for e in entries) for span in spans]
            hits = {}
            for mode in ('dense', 'sparse', 'hybrid'):
                hits[mode] = [h.hit.to_dict() for h in search_hybrid_chunks(query, access=access, top_k=10, mode=mode,
                    embedder=default_embedder, embedding_model=default_embedding_model(), root=root)]
            analysis = analyze_intents(query, context)
            rewrite = resolve_query(query, {}, context)
            plan = resolve_query(query, analysis, context)
            hits['selected_raw'] = retrieve_chunk_items_for_chat(query, auth, 10)
            hits['rewrite_only'] = retrieve_chunk_items_for_chat(rewrite.resolved_query, auth, 10)
            hits['production_plan'], coverage = retrieve_with_query_plan(plan.to_dict(), auth, 10)
            hits['production_default3'], _ = retrieve_with_query_plan(plan.to_dict(), auth, 3)
            hits['selected_default3'] = retrieve_chunk_items_for_chat(query, auth, 3)
            for item in [*hits['production_plan'], *hits['production_default3']]:
                item.setdefault('text', item.get('answer', ''))
            scores = {name: score_evidence(items, spans, relevant, 3 if name.endswith('default3') else 5) for name, items in hits.items()}
            if not all(span_presence):
                failure = 'gold_not_in_visible_corpus'
            elif not scores['hybrid']['query_all_evidence']:
                failure = 'ranking_below_5' if all(r is not None for r in scores['hybrid']['ranks']) else 'recall_or_ranking_below_10'
            elif not scores['selected_raw']['query_all_evidence']:
                failure = 'evidence_selection'
            elif not scores['production_plan']['query_all_evidence']:
                failure = 'query_plan_regression'
            else:
                failure = ''
            row = {'id': case['id'], 'query': query, 'query_type': case.get('query_type'), 'context': context,
                'annotation_status': case.get('annotation_status', 'weak_supervision' if group == 'title' else 'legacy_handwritten_unverified'),
                'gold_spans': spans, 'gold_span_presence': span_presence, 'scores': scores,
                'rewrite_only': rewrite.to_dict(), 'production_plan': plan.to_dict(),
                'predicted_intents': [analysis['primary_intent'], *analysis['secondary_intents']],
                'expected_intents': case.get('expected_intents'), 'failure_stage': failure,
                'coverage': coverage, 'top10': {name: [{'chunk_id': item['chunk_id'],
                    'document_id': item['document_id'], 'text': item.get('text', '')} for item in items] for name, items in hits.items()}}
            rows.append(row)
        report['datasets'][group] = {**summarize_quality(rows), 'source': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'details': rows}
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(group, json.dumps({key: val for key, val in summarize_quality(rows).items() if key != 'metrics'}, ensure_ascii=False), flush=True)
    return report


def evaluate_intent_labels(cases: list[dict]) -> dict:
    """Report classifier errors separately from unavailable target labels.

    The runtime rules are the only canonical vocabulary. No aliasing, collapsing
    an unsupported label into fallback, or removal from the original denominator.
    """
    from services.intent_service import FALLBACK_INTENT_NAME, INTENT_RULES, analyze_intents

    names = {rule.name for rule in INTENT_RULES} | {FALLBACK_INTENT_NAME}
    rows = []
    for case in cases:
        expected = case['expected_intent']
        predicted = analyze_intents(case['query'], case.get('context'))['primary_intent']
        supported = expected in names
        correct = predicted == expected
        rows.append({'id': case['id'], 'case_type': case.get('case_type', 'unspecified'),
            'expected': expected, 'predicted': predicted, 'correct': correct,
            'label_supported': supported,
            'failure_type': '' if correct else ('classification_mismatch' if supported else 'taxonomy_gap')})
    supported_rows = [r for r in rows if r['label_supported']]
    correct = sum(r['correct'] for r in rows)
    return {'count': len(rows), 'primary_accuracy': correct / len(rows) if rows else None,
        'taxonomy': {'source': 'services.intent_service.INTENT_RULES',
            'canonical_labels': sorted(names), 'fallback_label': FALLBACK_INTENT_NAME,
            'supported_count': len(supported_rows), 'unsupported_count': len(rows) - len(supported_rows),
            'coverage': len(supported_rows) / len(rows) if rows else None,
            'supported_accuracy': sum(r['correct'] for r in supported_rows) / len(supported_rows) if supported_rows else None,
            'unsupported_labels': sorted({r['expected'] for r in rows if not r['label_supported']}),
            'label_policy': 'preserve original labels; no automatic aliases; unresolved targets need human adjudication'},
        'by_type': {kind: {'count': sum(r['case_type'] == kind for r in rows),
            'primary_accuracy': sum(r['correct'] for r in rows if r['case_type'] == kind) / sum(r['case_type'] == kind for r in rows)}
            for kind in sorted({r['case_type'] for r in rows})}, 'details': rows}

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
    parser.add_argument("--retrieval-report", type=Path)
    parser.add_argument("--grounding-report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--quality-before', type=Path)
    parser.add_argument('--quality-after', type=Path)
    args = parser.parse_args()
    if args.quality_before or args.quality_after:
        if not (args.quality_before and args.quality_after):
            parser.error('quality comparison requires both --quality-before and --quality-after')
        report = compare_quality(load_json(args.quality_before), load_json(args.quality_after))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output = args.output_dir / 'comparison.json'
        if output.exists():
            parser.error('comparison.json already exists; use a new output directory')
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Saved comparison: {output}')
        return 0
    if not args.retrieval_report:
        parser.error('--retrieval-report is required for aggregation')
    report = aggregate_formal_report(load_json(args.retrieval_report), load_json(args.grounding_report) if args.grounding_report else None)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
