"""Bounded real HTTP load probe; run against an isolated acceptance runtime.

All attempts (including failures/timeouts) participate in latency distributions.
Closed-loop concurrency measures this fixture only, not production capacity.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import math
import time

import httpx


def summarize(rows, seconds):
    values = sorted(row['elapsed_ms'] for row in rows)
    return {
        'attempts': len(rows), 'outcomes': dict(Counter(row['outcome'] for row in rows)),
        'wall_seconds': seconds, 'completed_per_second': len(rows) / seconds if seconds else None,
        'successful_per_second': sum(r['outcome'] == 'success' for r in rows) / seconds if seconds else None,
        'latency_all_attempts_ms': {f'p{p}': values[max(0, math.ceil(len(values)*p/100)-1)]
                                    if values else None for p in (50, 95, 99)},
        'rows': rows,
    }


def measure(client, owner, auth, question, *, concurrency, count, timeout, phase):
    """No hidden client retry; a timed-out batch stops the enclosing sweep."""
    def attempt(i):
        kind = 'ingestion' if phase == 'ingestion' or (phase == 'mixed' and i % 2) else 'chat'
        start = time.monotonic()
        row = {'kind': kind, 'outcome': 'failure', 'http_status': None}
        try:
            if kind == 'chat':
                response = client.post('/chat/prompt', headers=auth, timeout=timeout,
                                       json={'message': question, 'session_id': f'capacity-{time.time_ns()}-{i}'})
            else:
                marker = f'{phase}-{concurrency}-{i}'
                payload = f'# 容量测试 {marker}\n\n问题：容量诊断记录如何查看？\n\n答复：请在服务页面查看诊断记录 {marker}，并联系官方客服核对。'
                response = client.post(f"/knowledge-bases/{owner['kb_id']}/documents", headers=auth,
                                       files={'file': (f'capacity-{marker}.md', payload.encode(), 'text/markdown')}, timeout=timeout)
            row['http_status'] = response.status_code
            row['admission_ms'] = (time.monotonic()-start)*1000
            if response.status_code in (429, 503):
                row['outcome'] = 'rejected'
            elif response.status_code == 504:
                row['outcome'] = 'timeout'
            elif response.is_success:
                body = response.json()
                if kind == 'ingestion':
                    row['job_id'] = body['job_id']
                    while time.monotonic()-start < timeout:
                        detail = client.get(f"/ingestion-jobs/{body['job_id']}", headers=auth,
                                            timeout=min(5, max(.1, timeout-(time.monotonic()-start))))
                        detail.raise_for_status()
                        job = detail.json()
                        if job['status'] in {'succeeded', 'failed', 'requires_review', 'cancelled'}:
                            row.update(outcome='success' if job['status'] == 'succeeded' else 'failure',
                                       terminal=job['status'], stage=job['stage'], error_code=job.get('error_code'))
                            break
                        time.sleep(.2)
                    else:
                        row['outcome'] = 'timeout'
                else:
                    trace = body.get('trace', {})
                    generated = any(s.get('step') == 'generation_completed' and s.get('status') == 'success' for s in body.get('full_trace', []))
                    row.update(outcome='success' if generated and not trace.get('degraded') and body.get('citations') else 'failure',
                               request_id=body.get('request_id'), token_usage=body.get('token_usage'),
                               stages=[{'step': s.get('step'), 'latency_ms': s.get('latency_ms'), 'status': s.get('status')}
                                       for s in body.get('full_trace', [])], trace=trace)
        except httpx.TimeoutException:
            row['outcome'] = 'timeout'
        except Exception as error:
            row['error_type'] = type(error).__name__
        row['elapsed_ms'] = (time.monotonic()-start)*1000
        return row

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        rows = list(executor.map(attempt, range(count)))
    return summarize(rows, time.monotonic()-started)
