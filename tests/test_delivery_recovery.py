from datetime import datetime, timedelta, timezone

import test_ingestion_pipeline as fixtures
from test_ingestion_pipeline import TestWorker as WorkerHarness, RecordingQueue, worker_module
from services.ingestion import repository
from services.ingestion.admission import admit_job
from fastapi import HTTPException
import pytest

harness = fixtures.harness
db_session = fixtures.db_session
store = fixtures.store
index_root = fixtures.index_root


def test_dependency_retry_exhaustion_is_durable_and_terminal(harness, monkeypatch):
    queue = RecordingQueue()
    worker = WorkerHarness()._worker(harness, queue)
    job, _ = harness.upload()
    monkeypatch.setattr(worker_module, 'process_job', lambda *a, **kw: (_ for _ in ()).throw(ConnectionError('secret-body')))
    for attempt in range(1, 4):
        queue.publish(job.id)
        worker.run_once()
        harness.session.expire_all()
        value = repository.get_ingestion_job_unscoped(harness.session, job.id)
        assert value.delivery_attempts == attempt
        assert 'secret-body' not in (value.error_message or '')
        if attempt < 3:
            assert value.status == 'retrying' and not queue.acked
            assert value.next_retry_at is not None and value.dead_letter_at is None
            value.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(seconds=1)
            harness.session.commit()
        else:
            assert value.status == 'failed' and value.dead_letter_at is not None
            assert len(queue.acked) == 1
    queue.publish(job.id)
    worker.run_once()
    harness.session.expire_all()
    assert repository.get_ingestion_job_unscoped(harness.session, job.id).delivery_attempts == 3
    assert len(queue.acked) == 2


def test_retry_due_time_does_not_consume_attempt(harness, monkeypatch):
    queue = RecordingQueue()
    worker = WorkerHarness()._worker(harness, queue)
    job, _ = harness.upload()
    job.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(seconds=60)
    harness.session.commit()
    queue.publish(job.id)
    assert worker.run_once()[0].skip_reason == 'retry_not_due'
    harness.session.expire_all()
    assert repository.get_ingestion_job_unscoped(harness.session, job.id).delivery_attempts == 0
    assert not queue.acked


def test_quarantined_jobs_do_not_consume_admission_and_other_tenant_has_room(harness, monkeypatch):
    monkeypatch.setenv('RAG_INGESTION_TENANT_CAPACITY', '1')
    job, _ = harness.upload()
    with pytest.raises(HTTPException) as caught:
        admit_job(harness.session, job.tenant_id)
    assert caught.value.status_code == 429
    admit_job(harness.session, 'another-tenant')
    job.status = 'requires_review'
    harness.session.commit()
    admit_job(harness.session, job.tenant_id)
