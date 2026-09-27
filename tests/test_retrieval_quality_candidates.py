import json
from pathlib import Path

import pytest

from scripts.build_colloquial_cases import build_quality_candidates
from scripts.build_colloquial_cases import build_annotation_tasks


def test_candidates_are_traceable_development_data_not_blind_gold(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'data/retrieval_colloquial_cases.jsonl'
    original = source.read_bytes()
    output = tmp_path / 'candidates.jsonl'
    rows = build_quality_candidates(source, output)
    originals = {row['id']: row for row in map(json.loads, original.decode('utf-8').splitlines())}
    assert len(rows) == 20
    assert {r['query_type'] for r in rows} == {'colloquial', 'typo', 'coreference', 'multi_intent'}
    for row in rows:
        assert row['annotation_status'] == 'candidate_pending_human_review'
        assert row['split'] == 'development'
        assert row['gold_spans'] == [originals[key]['gold_span'] for key in row['source_case_ids']]
        if row['query_type'] == 'coreference':
            assert row['context']['messages']
        if row['query_type'] == 'multi_intent':
            assert len(row['evidence_requirements']) == 2
            assert all(e['sub_question'] for e in row['evidence_requirements'])
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match='overwrite'):
        build_quality_candidates(source, output)


def test_annotation_queue_preserves_labels_and_requires_human_review(tmp_path):
    source = tmp_path / 'sources.jsonl'
    source.write_text(json.dumps({'id': 'x', 'query': '退款失败怎么办',
        'expected_intent': '退款失败', 'gold_span': '原文', 'query_type': 'coreference',
        'context': {'messages': [{'role': 'user', 'content': '退款被驳回了'}]}}, ensure_ascii=False) + '\n', encoding='utf-8')
    output = tmp_path / 'tasks.jsonl'
    rows = build_annotation_tasks(source, source, source, output)
    assert len({r['task_id'] for r in rows}) == 3
    intent = next(r for r in rows if r['review_type'] == 'intent')
    assert intent['reference_intent'] == '退款失败'
    assert intent['reference_intent_supported'] is False
    assert intent['context']['messages']
    assert all(r['status'] == 'pending' and r['human_intents'] is None and r['reviewer'] is None for r in rows)
    assert all(r['split'] == 'development_not_blind' for r in rows)
    with pytest.raises(ValueError, match='overwrite'):
        build_annotation_tasks(source, source, source, output)
