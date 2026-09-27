"""Bounded, read-only dependency probes. Never expose connection strings/errors."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib import request

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from config.runtime_config import development_degradation_enabled
from services.ingestion import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_HEARTBEAT_TTL = 60


def migration_heads() -> set[str]:
    config = Config(str(PROJECT_ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(PROJECT_ROOT / 'alembic'))
    return set(ScriptDirectory.from_config(config).get_heads())


def require_database_ready() -> dict:
    with db.get_engine().connect() as connection:
        connection.execute(text('SELECT 1'))
        current = set(MigrationContext.configure(connection).get_current_heads())
        if current != migration_heads():
            raise RuntimeError('database_migrations_pending')
    return {'status': 'ok', 'revision': ','.join(sorted(current))}


def redis_client():
    import redis

    url = os.getenv('RAG_REDIS_STREAM_URL', '').strip()
    if not url:
        raise RuntimeError('redis_not_configured')
    return redis.Redis.from_url(url, decode_responses=True,
                               socket_connect_timeout=2, socket_timeout=2)


def worker_heartbeat_key() -> str:
    # Queue deployments sharing Redis must not accept another queue's worker.
    stream = os.getenv('RAG_INGESTION_STREAM_KEY', 'rag:ingestion:jobs')
    group = os.getenv('RAG_INGESTION_CONSUMER_GROUP', 'rag-workers')
    return f'{stream}:{group}:worker-heartbeat'


def check_redis() -> dict:
    with redis_client() as client:
        client.ping()
    return {'status': 'ok'}


def check_worker() -> dict:
    with redis_client() as client:
        value = client.get(worker_heartbeat_key())
    if value is None or not 0 <= time.time() - float(value) < WORKER_HEARTBEAT_TTL:
        raise RuntimeError('worker_heartbeat_missing')
    return {'status': 'ok'}


def check_index() -> dict:
    from services.ingestion.index_manifest import load_active_manifest, read_manifest, verify_index_file
    from services.ingestion.pipeline import default_index_root, default_embedding_model
    from services.ingestion.sparse_index import declared_sparse_filename, verify_sparse_index

    manifest, pointer, path = load_active_manifest(default_index_root())
    # A cached query manifest must not hide a removed/corrupt on-disk artifact.
    manifest = read_manifest(default_index_root() / pointer.manifest_path)
    if manifest.fingerprint() != pointer.fingerprint:
        raise RuntimeError('manifest_fingerprint_mismatch')
    if manifest.embedding_model != default_embedding_model():
        raise RuntimeError('embedding_model_mismatch')
    verify_index_file(path, manifest)
    verify_sparse_index(path.parent / declared_sparse_filename(manifest), manifest)
    return {'status': 'ok', 'version': pointer.index_version}


def check_models() -> dict:
    from config.rag_config import get_rag_config
    from services import chat_service
    from utils import vector_retriever

    config = get_rag_config()
    if vector_retriever._EMBEDDING_MODEL is None:
        raise RuntimeError('embedding_model_not_loaded')
    if config.model_rerank_weight > 0 and vector_retriever._RERANKER_MODEL is None:
        raise RuntimeError('reranker_not_loaded')
    if config.generation_provider == 'local':
        if chat_service.model is None or chat_service.tokenizer is None:
            raise RuntimeError('generation_model_not_loaded')
    elif config.generation_provider == 'online':
        key = os.getenv(config.online_api_key_env, '').strip()
        if not key or not config.online_model_name:
            raise RuntimeError('online_model_not_configured')
        base = config.online_api_base_url.rstrip('/')
        if base.endswith('/chat/completions'):
            base = base[:-len('/chat/completions')]
        probe = request.Request(base + '/models', headers={'Authorization': f'Bearer {key}'})
        with request.urlopen(probe, timeout=3) as response:
            models = json.loads(response.read(1024 * 1024))
        if not any(item.get('id') == config.online_model_name for item in models.get('data', [])):
            raise RuntimeError('online_model_unavailable')
    else:
        raise RuntimeError('generation_provider_invalid')
    return {'status': 'ok', 'provider': config.generation_provider}


def warm_models() -> None:
    from config.rag_config import get_rag_config
    from services.chat_service import load_local_model
    from utils.vector_retriever import get_embedding_model, get_reranker_model

    config = get_rag_config()
    get_embedding_model()
    if config.model_rerank_weight > 0:
        get_reranker_model()
    if config.generation_provider == 'local':
        load_local_model()


def check_auth() -> dict:
    from services.auth_context import load_auth_config

    load_auth_config()
    return {'status': 'ok'}


def dependency_health() -> dict:
    checks = {}
    probes = {'database': require_database_ready, 'redis': check_redis,
              'worker': check_worker, 'index': check_index, 'models': check_models, 'auth': check_auth}
    for name, probe in probes.items():
        started = time.perf_counter()
        try:
            result = probe()
        except Exception:
            result = {'status': 'unavailable', 'code': f'{name}_unavailable'}
        checks[name] = {**result, 'latency_ms': round((time.perf_counter() - started) * 1000, 1)}
    failed = [name for name, value in checks.items() if value['status'] != 'ok']
    # Development degradation never permits a broken/unmigrated database or auth.
    degraded = bool(failed) and development_degradation_enabled() and not {'database', 'auth'}.intersection(failed)
    return {'status': 'degraded' if degraded else ('not_ready' if failed else 'ready'),
            'ready': not failed or degraded, 'checks': checks}
