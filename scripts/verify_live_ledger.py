"""Reconcile API model attempts, persisted ops totals and real token usage."""
import json

from scripts.accept_live_rag import require


def verify_ledger(client, auth, foreign, root, checkpoint):
    per_request = []
    for phase in ('query_and_chat_before_restart', 'query_and_chat_after_restart'):
        body = json.loads((root/(phase+'.json')).read_text(encoding='utf-8'))
        request_id = body['request_id']
        response = client.get('/ops/model-calls', headers=auth, params={'request_id': request_id})
        require(response.status_code == 200, 'ledger_read_failed')
        rows = response.json()['items']
        generation = [r for r in rows if r['kind'] == 'generation']
        require(len(generation) == 1, 'generation_attempt_count_mismatch')
        require(generation[0]['total_tokens'] == body['token_usage']['total_tokens'], 'ledger_tokens_do_not_match_response')
        require(all(r['status'] == 'succeeded' and r['ended_at'] for r in rows), 'unfinished_success_call')
        require(all(r['amount'] is None for r in rows), 'local_cost_not_unknown')
        other = client.get('/ops/model-calls', headers=foreign, params={'request_id': request_id})
        require(other.status_code == 200 and other.json()['items'] == [], 'ledger_tenant_leak')
        per_request.append({'request_id': request_id, 'calls': rows})
    rows = client.get('/ops/model-calls', headers=auth, params={'limit': 500}).json()['items']
    summary = client.get('/ops/metrics', headers=auth).json()['model_calls']
    require(len(rows) < 500 and summary['attempts'] == len(rows), 'ledger_summary_count_mismatch')
    require(sum(r['tokens'] or 0 for r in summary['outcomes']) == sum(r['total_tokens'] or 0 for r in rows), 'ledger_summary_tokens_mismatch')
    require(len({r['id'] for r in rows}) == len(rows), 'duplicate_billing_id')
    checkpoint('persistent_call_ledger_reconciliation', requests=per_request, summary=summary,
               details_reconcile=True, survives_restart=True, tenant_isolation=True, currency_cost=None)
