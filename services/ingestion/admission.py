"""Database-serialized admission: bounds durable jobs across API replicas."""
from fastapi import HTTPException
from sqlalchemy import select, func, text

from services.ingestion.models import IngestionJob
from services.request_budget import capacity


def admit_job(session, tenant_id):
    # Held until the caller commits job creation; no count/check race between
    # replicas. This lock is separate from runtime business tenant locks.
    if session.bind.dialect.name == 'postgresql':
        session.execute(text('SELECT pg_advisory_xact_lock(54002026)'))
    active = ('pending', 'running', 'retrying')
    total = session.scalar(select(func.count()).select_from(IngestionJob).where(IngestionJob.status.in_(active)))
    tenant = session.scalar(select(func.count()).select_from(IngestionJob).where(
        IngestionJob.status.in_(active), IngestionJob.tenant_id == tenant_id))
    if total >= capacity('RAG_INGESTION_CAPACITY', 64) or tenant >= capacity('RAG_INGESTION_TENANT_CAPACITY', 16):
        raise HTTPException(429, detail={'code': 'ingestion_capacity_rejected', 'retryable': True}, headers={'Retry-After': '2'})
