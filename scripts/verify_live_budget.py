"""Real local generation cancellation and bounded admission evidence."""
from concurrent.futures import ThreadPoolExecutor
import time

import httpx

from scripts.accept_live_rag import require, poll


def verify_budget(client, auth, foreign, question, checkpoint):
    recoveries = []

    def recovered():
        start = time.monotonic()
        poll(lambda: client.get('/ops/capacity', headers=auth, timeout=2).json()['chat']['tenant_active'] == 0,
             timeout=10, label='actual_thread_exit_and_slot_release')
        recoveries.append(time.monotonic()-start)
    def chat(headers=auth, timeout=15):
        return client.post('/chat/prompt', headers=headers, timeout=timeout,
                           json={'message': question, 'session_id': f'budget-{time.time_ns()}'})
    start = time.monotonic()
    response = chat()
    require(response.status_code == 504, 'real_model_deadline_not_enforced')
    require(response.json()['detail']['code'] == 'request_timeout', 'wrong_timeout_code')
    require(time.monotonic()-start < 5, 'http_deadline_exceeded')
    recovered()
    response = chat()
    require(response.status_code == 504, 'slot_not_released_after_timeout')
    recovered()
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: chat(), range(4)))
    codes = [r.status_code for r in responses]
    require(429 in codes and 504 in codes, 'bounded_admission_not_observed')
    require(all(c in (429, 504) for c in codes), 'unexpected_burst_result')
    recovered()
    try:
        chat(timeout=.15)
    except httpx.TimeoutException:
        pass
    else:
        raise AssertionError('disconnect_probe_completed_unexpectedly')
    recovered()
    response = chat()
    require(response.status_code == 504, 'slot_not_released_after_disconnect')
    with ThreadPoolExecutor(max_workers=1) as executor:
        inflight = executor.submit(chat)
        start = time.monotonic()
        require(client.get('/health/live', timeout=1).status_code == 200, 'event_loop_blocked')
        health_ms = (time.monotonic()-start)*1000
        other = chat(foreign)
        require(other.status_code != 429, 'tenant_capacity_starved_other_tenant')
        require(client.post('/retrieval/search', headers=foreign, json={'query': question}).status_code == 200, 'other_tenant_retrieval_unavailable')
        inflight.result()
    recovered()
    rows = client.get('/ops/model-calls', headers=auth, params={'limit': 500}).json()['items']
    outcomes = {row['status'] for row in rows}
    require('timeout' in outcomes and 'cancelled' in outcomes, 'timeout_or_cancellation_missing_from_ledger')
    require(all(row['amount'] is None for row in rows), 'unknown_local_cost_became_zero')
    checkpoint('real_budget_and_cancellation', timeout_http_status=504, burst_statuses=codes,
               disconnected_request_followup_http_status=504, other_tenant_http_status=other.status_code,
               health_during_generation_ms=health_ms, model='real-local-qwen', resource_recovery_seconds=recoveries,
               ledger_outcomes=sorted(outcomes), ledger_attempts=len(rows))
