from contextlib import nullcontext
import time
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect

from auth_helpers import auth_headers
from services import health_service
from services.ingestion import db


@pytest.mark.parametrize('environment,enabled', [('production', 'true'), ('production', 'false'), ('development', 'false')])
def test_demo_not_registered_outside_explicit_development(monkeypatch, environment, enabled):
    from main import create_app
    monkeypatch.setenv('RAG_ENV', environment)
    monkeypatch.setenv('RAG_ENABLE_DEMO_ENDPOINTS', enabled)
    app = create_app()
    client = TestClient(app)
    for path in ['/retrieval/search-demo', '/retrieval/prompt-preview']:
        assert path not in app.openapi()['paths']
        assert client.post(path, json={'query': 'test'}, headers=auth_headers()).status_code == 404


def test_demo_requires_extra_server_derived_scope(monkeypatch):
    from main import create_app
    from routers import retrieval
    monkeypatch.setenv('RAG_ENV', 'development')
    monkeypatch.setenv('RAG_ENABLE_DEMO_ENDPOINTS', 'true')
    monkeypatch.setattr(retrieval, 'retrieve_by_real_vector', lambda *a, **k: [])
    client = TestClient(create_app())
    path = '/retrieval/search-demo'
    assert client.post(path, json={'query': 'test'}, headers=auth_headers(roles=['agent'])).status_code == 403
    assert client.post(path, json={'query': 'test'}, headers=auth_headers(roles=['admin'])).status_code == 200
    # Changing runtime configuration also shuts a previously registered handler.
    monkeypatch.setenv('RAG_ENV', 'production')
    assert client.post(path, json={'query': 'test'}, headers=auth_headers()).status_code == 404


PROBES = ['require_database_ready', 'check_redis', 'check_worker', 'check_index', 'check_models', 'check_auth']


@pytest.fixture
def healthy_probes(monkeypatch):
    for name in PROBES:
        monkeypatch.setattr(health_service, name, lambda: {'status': 'ok'})
    monkeypatch.setenv('RAG_ENV', 'production')
    monkeypatch.delenv('RAG_ALLOW_DEGRADED_READINESS', raising=False)


@pytest.mark.parametrize('probe', PROBES)
def test_required_dependency_failure_is_503_without_secrets(monkeypatch, healthy_probes, probe):
    from main import create_app
    def fail():
        raise RuntimeError('postgres://private-user:secret-password@private-host/db')
    monkeypatch.setattr(health_service, probe, fail)
    client = TestClient(create_app())
    assert client.get('/health/live').status_code == 200
    assert client.get('/health').status_code == 200
    assert client.get('/health/ready').status_code == 503
    assert client.get('/health/dependencies').status_code == 401
    response = client.get('/health/dependencies', headers=auth_headers())
    assert response.status_code == 503
    assert 'secret-password' not in response.text
    assert 'private-host' not in response.text
    assert all('latency_ms' in value for value in response.json()['checks'].values())


def test_healthy_ready_and_explicit_development_degradation(monkeypatch, healthy_probes):
    assert health_service.dependency_health()['status'] == 'ready'
    def fail():
        raise RuntimeError
    monkeypatch.setattr(health_service, 'check_redis', fail)
    monkeypatch.setenv('RAG_ALLOW_DEGRADED_READINESS', 'true')
    assert health_service.dependency_health()['ready'] is False
    monkeypatch.setenv('RAG_ENV', 'development')
    assert health_service.dependency_health()['status'] == 'degraded'
    monkeypatch.setattr(health_service, 'require_database_ready', fail)
    assert health_service.dependency_health()['ready'] is False


@pytest.mark.parametrize('environment', ['production', 'development'])
def test_runtime_rejects_sqlite_and_missing_database(monkeypatch, environment):
    monkeypatch.setenv('RAG_ENV', environment)
    monkeypatch.delenv('RAG_DATABASE_URL', raising=False)
    with pytest.raises(ValueError):
        db.get_database_url()
    with pytest.raises(ValueError):
        db.create_db_engine('sqlite:///:memory:')


def test_unmigrated_database_does_not_create_runtime_tables(monkeypatch, tmp_path):
    monkeypatch.setenv('RAG_ENV', 'test')
    monkeypatch.setenv('RAG_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'health.db'))
    try:
        with pytest.raises(RuntimeError, match='migrations_pending'):
            health_service.require_database_ready()
        assert inspect(db.get_engine()).get_table_names() == []
        command.upgrade(Config('alembic.ini'), 'head')
        assert health_service.require_database_ready()['status'] == 'ok'
    finally:
        db.dispose_engines()


@pytest.mark.parametrize('age,valid', [(0, True), (61, False), (-10, False), (None, False)])
def test_worker_probe_rejects_missing_or_stale_heartbeat(monkeypatch, age, valid):
    value = None if age is None else str(time.time() - age)
    monkeypatch.setattr(health_service, 'redis_client', lambda: nullcontext(SimpleNamespace(get=lambda _: value)))
    if valid:
        assert health_service.check_worker()['status'] == 'ok'
    else:
        with pytest.raises(RuntimeError):
            health_service.check_worker()


def test_startup_rejects_unmigrated_database(monkeypatch):
    import main
    def fail():
        raise RuntimeError('database_migrations_pending')
    monkeypatch.setattr(main, 'require_database_ready', fail)
    with pytest.raises(RuntimeError, match='migrations_pending'):
        with TestClient(main.create_app()):
            pass


def test_production_queue_never_silently_uses_memory(monkeypatch):
    from services.ingestion.queue import build_queue, QueueUnavailableError
    monkeypatch.setenv('RAG_ENV', 'production')
    monkeypatch.setenv('RAG_ALLOW_DEGRADED_READINESS', 'true')
    with pytest.raises(QueueUnavailableError):
        build_queue({})


def test_model_probe_checks_loaded_runtime_objects(monkeypatch):
    from config import rag_config
    from services import chat_service
    from utils import vector_retriever
    monkeypatch.setattr(rag_config, 'get_rag_config',
                        lambda: SimpleNamespace(model_rerank_weight=0, generation_provider='local'))
    monkeypatch.setattr(vector_retriever, '_EMBEDDING_MODEL', object())
    monkeypatch.setattr(chat_service, 'tokenizer', object())
    monkeypatch.setattr(chat_service, 'model', None)
    with pytest.raises(RuntimeError, match='generation_model_not_loaded'):
        health_service.check_models()
    monkeypatch.setattr(chat_service, 'model', object())
    assert health_service.check_models()['status'] == 'ok'
    monkeypatch.setattr(vector_retriever, '_EMBEDDING_MODEL', None)
    with pytest.raises(RuntimeError, match='embedding_model_not_loaded'):
        health_service.check_models()


def test_index_probe_does_not_trust_cached_missing_manifest(monkeypatch, tmp_path):
    from services.ingestion import index_manifest, pipeline
    manifest = SimpleNamespace(embedding_model='test')
    pointer = SimpleNamespace(manifest_path='missing.json', fingerprint='unused')
    monkeypatch.setattr(pipeline, 'default_index_root', lambda: tmp_path)
    monkeypatch.setattr(index_manifest, 'load_active_manifest',
                        lambda _: (manifest, pointer, tmp_path / 'vectors.faiss'))
    with pytest.raises(index_manifest.IndexManifestError):
        health_service.check_index()


@pytest.mark.parametrize('service', ['api', 'worker'])
def test_launcher_does_not_start_on_migration_failure(monkeypatch, service):
    from scripts import start_runtime
    import uvicorn
    from services.ingestion import worker
    calls = []
    monkeypatch.setattr(start_runtime, 'get_database_url', lambda: 'sqlite:///:memory:')
    def fail(*args):
        raise RuntimeError('migration failed')
    monkeypatch.setattr(start_runtime.command, 'upgrade', fail)
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: calls.append('api'))
    monkeypatch.setattr(worker, 'main', lambda *a, **k: calls.append('worker'))
    with pytest.raises(RuntimeError, match='migration failed'):
        start_runtime.main([service])
    assert calls == []


def test_worker_heartbeat_expires_without_a_running_process(monkeypatch):
    from services.ingestion import worker_health
    events = []
    client = SimpleNamespace(set=lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(worker_health, 'redis_client', lambda: nullcontext(client))
    with worker_health.worker_heartbeat():
        assert events[0][1]['ex'] == health_service.WORKER_HEARTBEAT_TTL
        assert events[0][0][0] == health_service.worker_heartbeat_key()


def test_cached_sqlite_engine_cannot_cross_into_production(monkeypatch):
    monkeypatch.setenv('RAG_ENV', 'test')
    db.get_engine('sqlite:///:memory:')
    monkeypatch.setenv('RAG_ENV', 'production')
    try:
        with pytest.raises(ValueError):
            db.get_engine('sqlite:///:memory:')
    finally:
        db.dispose_engines()
