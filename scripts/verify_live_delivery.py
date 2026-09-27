"""Crash/ACK failure/transient/dead-letter verification with actual Redis/PostgreSQL."""
from contextlib import ExitStack
import sys
import time

import redis

from scripts.accept_live_rag import Child, poll, require, tree_hashes


def verify_delivery(client, owner, auth, foreign, worker, launch, env, root, checkpoint):
    worker.stop()
    queue = redis.Redis.from_url(env['RAG_REDIS_STREAM_URL'], decode_responses=True)
    stream, group = env['RAG_INGESTION_STREAM_KEY'], env['RAG_INGESTION_CONSUMER_GROUP']

    def upload(label, bad=False):
        payload = '�'*100 if bad else f'# 恢复验收 {label}\n\n问题：如何核对恢复记录？\n\n答复：请到服务页面核对恢复记录 {label}，以官方页面为准。'
        response = client.post(f"/knowledge-bases/{owner['kb_id']}/documents", headers=auth,
                               files={'file': (label+'.md', payload.encode(), 'text/markdown')})
        require(response.status_code == 202, 'fault_upload_failed')
        return response.json()['job_id']

    def terminal(job_id, status):
        def probe():
            response = client.get('/ingestion-jobs/'+job_id, headers=auth)
            require(response.status_code == 200, 'job_read_failed')
            job = response.json()
            return job if job['status'] == status else False
        return poll(probe, timeout=90, label='job_'+status)

    def drained():
        return poll(lambda: queue.xpending(stream, group)['pending'] == 0, timeout=30, label='pending_drained')

    with ExitStack() as stack:
        def fault(mode):
            marker = root / (mode+'.marker')
            child = Child('fault-'+mode, [sys.executable, 'scripts/fault_ingestion_worker.py', mode, str(marker)],
                          env, root/'logs', [env['RAG_DATABASE_URL'], env['RAG_REDIS_STREAM_URL'], env['RAG_JWT_SECRET']])
            stack.callback(child.stop)
            return child, marker

        job_id = upload('killed-worker')
        child, marker = fault('crash')
        poll(marker.exists, timeout=30, label='executing_before_kill')
        # A second worker sees an idle message but cannot take the PostgreSQL
        # execution lease while the original process is alive.
        replacement = launch('worker')
        time.sleep(float(env.get('RAG_WORKER_LEASE_SECONDS', '30'))+1)
        running = client.get('/ingestion-jobs/'+job_id, headers=auth).json()
        require(running['delivery_attempts'] == 1, 'live_task_taken_over')
        child.stop()
        job = terminal(job_id, 'succeeded')
        require(job['delivery_attempts'] == 2, 'crashed_job_not_reclaimed')
        drained()
        checkpoint('worker_kill_recovery', job=job, live_task_not_taken_over=True)
        replacement.stop()

        job_id = upload('transient-dependency')
        child, _ = fault('transient')
        job = terminal(job_id, 'succeeded')
        require(job['delivery_attempts'] == 3 and job['retry_count'] == 2, 'retry_attempt_count_mismatch')
        drained()
        child.stop()
        checkpoint('finite_transient_retries', job=job)

        job_id = upload('ack-failure')
        child, marker = fault('ack')
        terminal(job_id, 'succeeded')
        poll(marker.exists, timeout=30, label='ack_failed')
        child.stop()
        before = tree_hashes(root/'faiss')
        replacement = launch('worker')
        drained()
        job = terminal(job_id, 'succeeded')
        require(job['delivery_attempts'] == 1 and tree_hashes(root/'faiss') == before, 'ack_replay_republished')
        replacement.stop()
        checkpoint('ack_failure_idempotent_recovery', job=job, index_unchanged=True)

        job_id = upload('permanent-failure')
        child, _ = fault('poison')
        job = terminal(job_id, 'failed')
        drained()
        require(job['dead_letter_at'] and job['delivery_attempts'] == 1, 'permanent_failure_not_dead_lettered')
        child.stop()
        response = client.post('/ingestion-jobs/'+job_id+'/redrive', headers=foreign)
        require(response.status_code == 404, 'foreign_redrive_not_denied')
        first = client.post('/ingestion-jobs/'+job_id+'/redrive', headers=auth)
        second = client.post('/ingestion-jobs/'+job_id+'/redrive', headers=auth)
        require(first.status_code == second.status_code == 202, 'authorized_redrive_failed')
        require(first.json()['job_id'] == second.json()['job_id'], 'redrive_not_idempotent')
        replacement = launch('worker')
        terminal(first.json()['job_id'], 'succeeded')
        drained()
        checkpoint('dead_letter_authorized_idempotent_redrive', source=job, replay_job_id=first.json()['job_id'], foreign_http_status=404)
        review_id = upload('quarantine', bad=True)
        review = terminal(review_id, 'requires_review')
        drained()
        require(review['delivery_attempts'] == 1 and not review['dead_letter_at'], 'review_was_retried_or_dead_lettered')
        require(client.post('/ingestion-jobs/'+review_id+'/redrive', headers=auth).status_code == 409, 'review_redrive_bypass')
        checkpoint('requires_review_ack_preserved', job=review)
        replacement.stop()
        capacity = int(env.get('RAG_INGESTION_TENANT_CAPACITY', '16'))
        for i in range(capacity):
            upload('admission-'+str(i))
        files_before = tree_hashes(root/'objects')
        response = client.post(f"/knowledge-bases/{owner['kb_id']}/documents", headers=auth,
                               files={'file': ('over-capacity.md', '容量拒绝验证，不应写入对象。'.encode(), 'text/markdown')})
        require(response.status_code == 429, 'ingestion_capacity_not_rejected')
        require(response.json()['detail']['code'] == 'ingestion_capacity_rejected', 'wrong_ingestion_rejection_code')
        require(tree_hashes(root/'objects') == files_before, 'rejected_upload_wrote_object')
        replacement = launch('worker')
        poll(lambda: (queue.xinfo_groups(stream)[0].get('lag') == 0 and queue.xpending(stream, group)['pending'] == 0),
             timeout=90, label='admission_jobs_drained')
        checkpoint('ingestion_bounded_admission', limit=capacity, rejected_http_status=429,
                   rejected_upload_object_unchanged=True, admitted_jobs_drained=True)
        replacement.stop()
