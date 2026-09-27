from decimal import Decimal
import json

import pytest
from sqlalchemy import select

import runtime_fixtures
from services.call_ledger import calls, call_scope, model_call, capture_usage, ledger_summary
from services.ingestion.db import get_engine, dispose_engines
from services.request_budget import BudgetExceeded

runtime_db = runtime_fixtures.runtime_db


def test_fail_cancel_success_and_restart_reconcile_without_unknown_zero(runtime_db):
    with call_scope('a', request_id='request1'):
        with model_call('local', 'local-model', 'generation'):
            capture_usage({'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10, 'counting_method': 'local_tokenizer'})
        with pytest.raises(ValueError):
            with model_call('local', 'local-model', 'generation'):
                raise ValueError('password=do-not-persist')
        with pytest.raises(BudgetExceeded):
            with model_call('local', 'local-model', 'generation'):
                raise BudgetExceeded('request_cancelled')
    dispose_engines()
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(calls)).mappings()]
    assert len(rows) == 3
    assert {r['status'] for r in rows} == {'succeeded', 'failed', 'cancelled'}
    assert all(r['amount'] is None and r['price_version'] is None for r in rows)
    assert all(r['request_id'] == 'request1' for r in rows)
    assert 'do-not-persist' not in str(rows)
    assert ledger_summary('a')['attempts'] == 3
    assert ledger_summary('a')['unknown_cost_calls'] == 3
    assert ledger_summary('b')['attempts'] == 0


def test_confirmed_price_uses_decimal_and_retry_attempts_are_separate(runtime_db, tmp_path, monkeypatch):
    path = tmp_path/'test-prices.json'
    path.write_text(json.dumps({'online/test-model': {'confirmed': True, 'version': 'test-only', 'currency': 'TEST',
                                                    'input_per_million': '2', 'output_per_million': '4'}}))
    monkeypatch.setenv('RAG_MODEL_PRICES_FILE', str(path))
    for attempt in (1, 2):
        with call_scope('a', job_id='job', attempt=attempt):
            with model_call('online', 'test-model', 'generation'):
                capture_usage({'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110, 'counting_method': 'provider_usage'})
    summary = ledger_summary('a')
    assert summary['attempts'] == 2 and summary['unknown_cost_calls'] == 0
    assert summary['priced_totals'][0]['amount'] == Decimal('.000480')
    with get_engine().connect() as conn:
        assert set(conn.execute(select(calls.c.attempt)).scalars()) == {1, 2}
