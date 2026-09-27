"""Execution lease + durable finite retry, with PostgreSQL as authority.

Redis idle time nominates a candidate; it never authorizes concurrent execution.
A dedicated PostgreSQL connection holds a session advisory lock, and the same
connection runs every pipeline transaction. Crash releases this lock.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from threading import Event, Thread
from pathlib import Path

from sqlalchemy import text

from services.ingestion import repository
from services.ingestion.pipeline import PipelineOutcome
from services.request_budget import positive

logger = logging.getLogger(__name__)
TERMINAL = {'succeeded', 'requires_review', 'cancelled'}
TRANSIENT = {'dependency_unavailable', 'dependency_timeout'}


@contextmanager
def heartbeat(queue, message):
    stop = Event()

    def renew():
        while not stop.wait(positive('RAG_WORKER_LEASE_SECONDS', 30)/3):
            try:
                queue.renew(message.message_id)
            except Exception:
                logger.warning('delivery_heartbeat_unavailable job=%s', message.job_id)

    thread = Thread(target=renew, daemon=True) if hasattr(queue, 'renew') else None
    if thread:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join(timeout=6)


def handle(worker, message):
    with worker.session_factory() as probe:
        engine = probe.get_bind()
    # Serialize publication for this index root in addition to each job. This
    # prevents two workers racing a shared manifest while recovery is in flight.
    from services.ingestion.pipeline import default_index_root
    root = str(Path(worker.pipeline_options.get('index_root') or default_index_root()).resolve())
    keys = [int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)
            for value in ('ingestion:'+root, 'ingestion-job:'+message.job_id)]
    with engine.connect() as connection:
        locked = []
        try:
            if connection.dialect.name == 'postgresql':
                for key in keys:
                    acquired = connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}).scalar_one()
                    connection.commit()
                    if not acquired:
                        return PipelineOutcome(job_id=message.job_id, tenant_id='', skipped=True, skip_reason='execution_lease_busy'), False
                    locked.append(key)
            with worker.session_factory(bind=connection) as session:
                job = repository.get_ingestion_job_unscoped(session, message.job_id)
                if job is None:
                    return PipelineOutcome(job_id=message.job_id, tenant_id='', skipped=True, skip_reason='job_not_found'), True
                if job.status in TERMINAL or job.dead_letter_at:
                    return PipelineOutcome(job_id=job.id, tenant_id=job.tenant_id, status=job.status, skipped=True, skip_reason='already_terminal'), True
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if job.next_retry_at and job.next_retry_at > now:
                    return PipelineOutcome(job_id=job.id, tenant_id=job.tenant_id, skipped=True, skip_reason='retry_not_due'), False
                if job.delivery_attempts >= int(positive('RAG_WORKER_MAX_ATTEMPTS', 3)):
                    job.status, job.error_code, job.error_message = 'failed', 'delivery_attempts_exhausted', 'Delivery attempts exhausted'
                    job.dead_letter_at, job.next_retry_at = now, None
                    session.commit()
                    return PipelineOutcome(job_id=job.id, tenant_id=job.tenant_id, status='failed', error_code=job.error_code), True
                job.delivery_attempts += 1
                job.next_retry_at = None
                session.commit()
            with heartbeat(worker.queue, message):
                try:
                    from services.call_ledger import call_scope
                    with call_scope(job.tenant_id, job_id=job.id, attempt=job.delivery_attempts):
                        outcome = worker._process(message.job_id, connection=connection)
                except Exception as error:
                    from services.ingestion.pipeline import IngestionPipeline
                    code, _ = IngestionPipeline._translate(error)
                    outcome = PipelineOutcome(job_id=job.id, tenant_id=job.tenant_id, status='failed', error_code=code)
            with worker.session_factory(bind=connection) as session:
                job = repository.get_ingestion_job_unscoped(session, message.job_id)
                ack = True
                if outcome.failed:
                    job.status, job.error_code = 'failed', outcome.error_code
                    if outcome.error_code in TRANSIENT and job.delivery_attempts < int(positive('RAG_WORKER_MAX_ATTEMPTS', 3)):
                        delay = min(positive('RAG_WORKER_RETRY_MAX_SECONDS', 60), positive('RAG_WORKER_RETRY_BASE_SECONDS', 2)*2**(job.delivery_attempts-1))
                        job.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(seconds=delay)
                        job.status, ack = 'retrying', False
                    else:
                        job.dead_letter_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    # Never copy arbitrary provider/parser exception text into a dead letter.
                    job.error_message = 'Processing failed; see error_code'
                session.commit()
            return outcome, ack
        finally:
            if locked and not connection.invalidated:
                connection.rollback()
                for key in reversed(locked):
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                connection.commit()
