"""Quiescent backup/restore drill into a NEW database and NEW file trees.

Uses installed PostgreSQL CLI and real local models. No database is dropped,
no source is overwritten, and Redis is isolated using a new stream/group.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.accept_live_rag import Child, headers, poll, redact, require, tree_hashes, validate_chat  # noqa: E402


def run(args):
    import httpx
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    source = json.loads(args.source_report.read_text(encoding='utf-8'))
    require(source['status'] == 'passed', 'source_run_incomplete')
    steps = {r['name']: r for r in source['steps']}
    original_root = Path(steps['bootstrap']['run_root']).resolve()
    owner, foreign = steps['bootstrap']['identities']
    document_id = steps['upload_to_published']['document_id']
    database = make_url(os.environ['RAG_DATABASE_URL'])
    require(database.get_backend_name() == 'postgresql', 'postgresql_required')
    require(database.host in ('127.0.0.1', 'localhost', '::1'), 'drill_requires_local_isolated_source')
    require(not args.run_root.exists(), 'restore_root_must_be_new')
    args.run_root.mkdir(parents=True)
    root = args.run_root.resolve()
    (root/'logs').mkdir()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target_name = 's54_restore_'+stamp.lower()+'_'+secrets.token_hex(3)
    target = database.set(database=target_name)
    private = [database.render_as_string(hide_password=False), database.password, os.getenv('RAG_REDIS_STREAM_URL')]
    report = {'status': 'running', 'source_run_id': source['run_id'], 'target_database': target_name,
              'run_root': str(root), 'backup_started_at': datetime.now(timezone.utc).isoformat()}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database, connect_args={'connect_timeout': 3})

    def counts(selected_engine):
        tables = sorted(inspect(selected_engine).get_table_names())
        with selected_engine.connect() as conn:
            return {name: conn.execute(text('SELECT count(*) FROM '+conn.dialect.identifier_preparer.quote(name))).scalar_one() for name in tables}

    def command(name, extra, dbname):
        executable = args.pg_bin/(name+('.exe' if os.name == 'nt' else ''))
        env = {**os.environ, 'PGPASSWORD': database.password or '', 'PGCONNECT_TIMEOUT': '3'}
        result = subprocess.run([str(executable), '-h', database.host, '-p', str(database.port), '-U', database.username,
                                 *extra, dbname], env=env, capture_output=True, text=True, timeout=120)
        require(result.returncode == 0, name+'_failed:'+redact(result.stderr, private))

    try:
        with engine.connect() as conn:
            require(conn.execute(text("SELECT count(*) FROM ingestion_jobs WHERE status IN ('pending','running','retrying')")).scalar_one() == 0, 'source_not_quiescent')
            require(conn.execute(text('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()')).scalar_one() == 0, 'stop_source_api_worker_before_backup')
        before = counts(engine)
        hashes = {name: tree_hashes(original_root/name) for name in ('objects', 'faiss')}
        require(all(hashes.values()), 'source_artifacts_missing')
        dump = root/'metadata.dump'
        command('pg_dump', ['-Fc', '-f', str(dump)], database.database)
        for name in hashes:
            shutil.copytree(original_root/name, root/('backup-'+name))
        require(counts(engine) == before and all(tree_hashes(original_root/n) == h for n, h in hashes.items()), 'source_changed_during_backup')
        report.update(backup_finished_at=datetime.now(timezone.utc).isoformat(),
                      dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest(), table_counts_before=before,
                      backup_file_counts={n: len(h) for n, h in hashes.items()})
        started = time.monotonic()
        report['restore_started_at'] = datetime.now(timezone.utc).isoformat()
        command('createdb', [], target_name)
        command('pg_restore', ['--no-owner', '--exit-on-error', '-d', target_name], str(dump))
        for name in hashes:
            shutil.copytree(root/('backup-'+name), root/name)
            require(tree_hashes(root/name) == hashes[name], 'restored_artifact_hash_mismatch')
        restored = create_engine(target, connect_args={'connect_timeout': 3})
        try:
            require(counts(restored) == before, 'restored_table_counts_mismatch')
            from services.ingestion.object_store import build_object_key, filename_from_source_uri, LocalObjectStore
            with restored.connect() as conn:
                documents = conn.execute(text('SELECT id, tenant_id, source_uri FROM documents')).mappings().all()
            store = LocalObjectStore(root/'objects')
            require(all(store.exists(build_object_key(d['tenant_id'], d['id'], filename_from_source_uri(d['source_uri']))) for d in documents), 'restored_document_object_reference_missing')
            report['verified_document_objects'] = len(documents)
        finally:
            restored.dispose()
        secret = secrets.token_urlsafe(48)
        private += [secret, target.render_as_string(hide_password=False)]
        env = {**os.environ, 'RAG_DATABASE_URL': target.render_as_string(hide_password=False),
               'RAG_ENV': 'production', 'RAG_JWT_SECRET': secret, 'RAG_JWT_ALGORITHM': 'HS256',
               'RAG_JWT_ISSUER': 'llm-customer-service', 'RAG_JWT_AUDIENCE': 'customer-service-api',
               'RAG_FAISS_STORE_DIR': str(root/'faiss'), 'RAG_OBJECT_STORE_DRIVER': 'local',
               'RAG_OBJECT_STORE_LOCAL_ROOT': str(root/'objects'), 'RAG_GENERATION_PROVIDER': 'local',
               'RAG_INGESTION_STREAM_KEY': target_name+':jobs', 'RAG_INGESTION_CONSUMER_GROUP': target_name,
               'RAG_WARM_MODELS_ON_STARTUP': 'true', 'PYTHONUTF8': '1', 'HF_HUB_OFFLINE': '1'}
        env['REDIS_URL'] = env['RAG_REDIS_STREAM_URL']
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with ExitStack() as stack:
            for name, command_line in [('worker', [sys.executable, '-m', 'services.ingestion.worker', '--consumer-name', target_name]),
                                        ('api', [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port), '--no-access-log'])]:
                child = Child(name, command_line, env, root/'logs', private)
                stack.callback(child.stop)
            client = stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=60, trust_env=False))

            def ready():
                try:
                    return client.get('/health/ready', timeout=3).status_code == 200
                except httpx.TransportError:
                    return False

            poll(ready, timeout=120, label='restored_real_runtime_ready')
            auth = headers(owner, secret)
            response = client.post('/chat/prompt', headers=auth, json={'message': args.question, 'session_id': 'restored-'+stamp})
            require(response.status_code == 200, 'restored_chat_failed')
            body = response.json()
            (root/'restored-response.json').write_text(redact(json.dumps(body, ensure_ascii=False, indent=2), private), encoding='utf-8')
            primary = [item for item in body.get('evidence_citations', []) if item.get('evidence_role') == 'primary']
            require(len(primary) == 1 and primary[0]['document_id'] == document_id, 'restored_primary_evidence_mismatch')
            references = body.get('citations', [])+body.get('evidence_citations', [])
            verified = set()
            with engine.connect() as conn:
                for citation in references:
                    original_text = conn.execute(text(
                        'SELECT c.text FROM document_chunks c JOIN document_versions v ON v.id=c.document_version_id '
                        "WHERE c.tenant_id=:tenant AND v.tenant_id=:tenant AND v.document_id=:document AND c.chunk_id=:chunk AND v.status='published'"),
                        {'tenant': owner['tenant_id'], 'document': citation['document_id'], 'chunk': citation['chunk_id']}).scalar_one()
                    quote = citation.get('snippet') or citation.get('quote')
                    require(bool(quote) and ' '.join(quote.split()) in ' '.join(original_text.split()), 'restored_quote_not_in_source_chunk')
                    verified.add(citation['document_id'])
            report['restored_chat'] = validate_chat(body, document_id, ['订单', '售后'], expected_document_ids=verified)
            report['source_citation_verification'] = {'primary_document_id': document_id, 'references_checked': len(references),
                                                     'every_quote_matches_source_published_tenant_chunk': True,
                                                     'note': 'Multi-document corpus may include supporting diagnostic documents; this verifies recovery, not 5.3 semantic relevance.'}
            other = client.post('/retrieval/search', headers=headers(foreign, secret), json={'query': args.question})
            require(other.status_code == 200 and not other.json()['results'], 'restored_tenant_isolation_failed')
            require(client.get('/documents/'+document_id, headers=headers(foreign, secret)).status_code == 404, 'restored_document_acl_failed')
            report.update(status='passed', rto_seconds=time.monotonic()-started, observed_rpo_seconds=0,
                          rpo_basis='quiesced source, zero committed rows lost, identical table counts and artifact hashes',
                          sla_target_confirmed=False, tenant_isolation=True)
        require(counts(engine) == before and all(tree_hashes(original_root/n) == h for n, h in hashes.items()), 'source_modified_by_drill')
        report['source_unchanged'] = True
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=redact(str(error), private))
    finally:
        engine.dispose()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        args.report.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2, default=str), private), encoding='utf-8')
        print(report['status'].upper(), args.report)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--question', default='星桥优享服务退款申请审核通过后，应通过哪个渠道查看进度？')
    raise SystemExit(run(parser.parse_args()))
