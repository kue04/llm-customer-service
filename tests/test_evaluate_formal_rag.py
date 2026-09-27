from scripts.evaluate_formal_rag import aggregate_formal_report
from scripts.evaluate_formal_rag import compare_quality, score_evidence, summarize_quality
from scripts.evaluate_formal_rag import evaluate_intent_labels
import copy
import pytest


def test_intent_audit_preserves_unsupported_labels_and_full_denominator():
    cases = [
        {'id': 'supported', 'query': '退款进度', 'expected_intent': '退款进度'},
        {'id': 'unsupported', 'query': '退款进度', 'expected_intent': '退款失败'},
    ]
    report = evaluate_intent_labels(cases)
    assert report['count'] == 2
    assert report['primary_accuracy'] == 0.5
    assert report['taxonomy']['coverage'] == 0.5
    assert report['taxonomy']['supported_accuracy'] == 1
    assert report['details'][1]['expected'] == '退款失败'
    assert report['details'][1]['failure_type'] == 'taxonomy_gap'
    assert cases[1]['expected_intent'] == '退款失败'


def test_intent_audit_empty_is_not_success():
    report = evaluate_intent_labels([])
    assert report['primary_accuracy'] is None
    assert report['taxonomy']['coverage'] is None


def test_multi_intent_requires_every_evidence_span():
    result = score_evidence([{'chunk_id': 'a', 'text': '退款需审核'}],
                            ['退款需审核', '优惠券规则'], {'a', 'b'})
    assert result['query_hit'] == 1
    assert result['query_all_evidence'] == 0
    assert result['evidence_coverage'] == 0.5
    assert result['chunk_precision'] == 1
    assert result['chunk_recall'] == 0.5


def test_duplicate_chunks_do_not_inflate_recall():
    evidence = [{'chunk_id': 'a', 'text': '答案'}, {'chunk_id': 'a', 'text': '答案'},
                {'chunk_id': 'b', 'text': '无关'}]
    result = score_evidence(evidence, ['答案'], {'a', 'c'})
    assert result['chunk_recall'] == 0.5
    assert result['chunk_precision'] == 0.5


def test_missing_corpus_gold_is_not_silently_removed_from_query_denominator():
    result = score_evidence([], ['不存在'], set())
    assert result['query_hit'] == 0
    assert result['query_all_evidence'] == 0
    assert result['chunk_recall'] is None


def test_empty_blind_group_has_no_manufactured_score():
    assert summarize_quality([]) == {'count': 0, 'status': 'not_evaluated', 'metrics': None}


def test_cutoff_does_not_count_correct_evidence_below_budget():
    items = [{'chunk_id': str(i), 'text': '错'} for i in range(3)]
    items.append({'chunk_id': 'correct', 'text': '答案'})
    assert score_evidence(items, ['答案'], {'correct'}, k=3)['query_hit'] == 0
    assert score_evidence(items, ['答案'], {'correct'}, k=5)['query_hit'] == 1


def _comparison_fixture():
    scores = score_evidence([{'chunk_id': 'a', 'text': '答案'}], ['答案'], {'a'})
    row = {'id': 'q1', 'query_type': 'title', 'failure_stage': '',
        'scores': {name: scores.copy() for name in ('rewrite_only', 'selected_raw', 'production_plan')},
        'expected_intents': None}
    return {'snapshot': {}, 'fusion': {}, 'budgets': {}, 'visible_chunk_count': 1,
        'intent_classification': {'sha256': 'i', 'count': 1, 'primary_accuracy': 1},
        'datasets': {'title': {**summarize_quality([row]), 'sha256': 'd', 'details': [row]}}}


def test_comparison_blocks_blind_and_rejects_changed_data():
    before = _comparison_fixture()
    assert compare_quality(before, before)['blind']['gate'] == 'blocked'
    after = copy.deepcopy(before)
    after['datasets']['title']['sha256'] = 'changed'
    with pytest.raises(ValueError, match='changed dataset'):
        compare_quality(before, after)


def test_comparison_detects_per_case_regression():
    before = _comparison_fixture()
    after = copy.deepcopy(before)
    after['datasets']['title']['details'][0]['scores']['production_plan']['query_all_evidence'] = 0
    result = compare_quality(before, after)['groups']['title']
    assert result['suggested_non_regression_gate'] == 'fail'
    assert result['paired_all_evidence']['production_plan']['lost'] == ['q1']


def test_aggregate_formal_report_keeps_index_and_route_metrics():
    report = aggregate_formal_report(
        {"index": {"index_version": 3}, "datasets": {"title": {"metrics": {"hybrid": {"mrr": 0.8}}}}},
        {"run_id": "g1", "summary": {"route_counts": {"clarify": 2}, "clarify_count": 2, "human_handoff_count": 1}},
    )
    assert report["retrieval_path"] == "chunk-index"
    assert report["index"]["index_version"] == 3
    assert report["retrieval"]["metrics"]["title"]["hybrid"]["mrr"] == 0.8
    assert report["evidence_and_grounding"]["clarify_count"] == 2
    assert report["evidence_and_grounding"]["human_handoff_count"] == 1
