"""Real API/worker acceptance against dedicated PostgreSQL, Redis and local models.

Read model settings and RAG_DATABASE_URL/RAG_REDIS_STREAM_URL from the environment.
Own subprocesses, stream/group, object/index directories and transparent dependency
proxies. Fault injection disconnects only those proxies, never external servers.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class AcceptanceFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceFailure(message)


def redact(value: str, private_values=()) -> str:
    for private in sorted((str(v) for v in private_values if v), key=len, reverse=True):
        value = value.replace(private, '[redacted]')
    value = re.sub(r'''(?i)(?:postgres(?:ql)?(?:\+\w+)?|rediss?)://[^\s"'<>]+''', '[connection-redacted]', value)
    value = re.sub(r'''(?i)Bearer\s+[^\s"']+''', 'Bearer [redacted]', value)
    return re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[jwt-redacted]', value)


def export_markdown(export: dict) -> bytes:
    rows = [json.loads(line) for line in export['jsonl'].splitlines() if line.strip()]
    require(len(rows) == 1 and export['count'] == 1, 'expected_one_approved_export')
    row = rows[0]
    require(bool(row.get('question')) and bool(row.get('answer')), 'invalid_approved_export')
    # Keep each FAQ in one section: separate headings split its question from
    # the answer and can make a question-only chunk the primary evidence.
    return f"# {row.get('title') or row['question']}\n\n问题：{row['question']}\n\n官方答复：{row['answer']}\n".encode('utf-8')


def tree_hashes(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def poll(probe, *, timeout: float, label: str):
    deadline = time.monotonic() + timeout
    event = threading.Event()
    while True:
        result = probe()
        if result:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AcceptanceFailure(f'timeout:{label}')
        # Recheck a concrete condition until its deadline; no guessed startup sleeps.
        event.wait(min(0.5, remaining))


class DependencyProxy:
    """Transparent forwarding with fault injection confined to owned sockets."""

    def __init__(self, host: str, port: int):
        self.target = (host, port)
        self.enabled = threading.Event()
        self.enabled.set()
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.connections: set[socket.socket] = set()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(64)
        self.listener.settimeout(0.25)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._forward, args=(client,), daemon=True).start()

    def _forward(self, client):
        upstream = None
        try:
            if not self.enabled.is_set():
                return
            upstream = socket.create_connection(self.target, timeout=3)
            upstream.settimeout(3)
            client.settimeout(3)
            with self.lock:
                self.connections.update((client, upstream))
            while self.enabled.is_set() and not self.closed.is_set():
                ready, _, _ = select.select([client, upstream], [], [], 0.25)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.connections.discard(client)
                self.connections.discard(upstream)
            client.close()
            if upstream is not None:
                upstream.close()

    def isolate(self):
        self.enabled.clear()
        with self.lock:
            for connection in tuple(self.connections):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

    def restore(self):
        self.enabled.set()

    def close(self):
        self.closed.set()
        self.isolate()
        self.listener.close()
        self.thread.join(timeout=3)


class Child:
    def __init__(self, name, command, env, log_dir, private_values):
        self.log_path = log_dir / f'{name}.log'
        self.process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )

        def drain():
            with self.log_path.open('w', encoding='utf-8') as handle:
                for line in self.process.stdout:
                    handle.write(redact(line, private_values))
                    handle.flush()

        self.reader = threading.Thread(target=drain, daemon=True)
        self.reader.start()

    def alive(self):
        require(self.process.poll() is None, f'child_exited:{self.log_path.name}')

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.reader.join(timeout=5)


def bootstrap():
    from services.ingestion.db import session_scope
    from services.ingestion import repository

    identities = []
    with session_scope() as session:
        for suffix in ('owner', 'foreign'):
            tenant = repository.create_tenant(session, name=f'live-acceptance-{suffix}')
            user = repository.create_user(session, tenant_id=tenant.id, external_id=f'live-{suffix}')
            role = repository.create_role(session, tenant_id=tenant.id, code='admin')
            repository.assign_role(session, tenant_id=tenant.id, user_id=user.id, role_id=role.id)
            kb = repository.create_knowledge_base(session, tenant_id=tenant.id, slug='live-acceptance', created_by=user.id)
            repository.add_knowledge_base_member(session, tenant_id=tenant.id, knowledge_base_id=kb.id, user_id=user.id, member_role='owner')
            identities.append({'tenant_id': tenant.id, 'user_id': user.id,
                               'external_id': user.external_id, 'kb_id': kb.id})
    return identities


def headers(identity: dict, secret: str) -> dict:
    import jwt

    now = int(time.time())
    token = jwt.encode({'sub': identity['external_id'], 'tenant_id': identity['tenant_id'],
                        'roles': ['admin'], 'iss': 'llm-customer-service',
                        'aud': 'customer-service-api', 'iat': now, 'exp': now + 14400}, secret, algorithm='HS256')
    return {'Authorization': f'Bearer {token}'}


def validate_chat(body: dict, document_id: str, keywords: list[str], *, expected_document_ids=None) -> dict:
    usage = body.get('token_usage', {})
    trace = body.get('trace', {})
    require(usage.get('provider') == 'local' and usage.get('completion_tokens', 0) > 0, 'local_generation_not_exercised')
    require(usage.get('counting_method') == 'local_tokenizer', 'nonlocal_token_accounting')
    require(trace.get('failure_stage') == 'none' and not trace.get('degraded'), 'chat_degraded')
    require(trace.get('retrieval_path') == 'chunk-index', 'wrong_chat_retrieval_path')
    generated = [step for step in body.get('full_trace', []) if step.get('step') == 'generation_completed']
    require(len(generated) == 1 and generated[0].get('status') == 'success', 'generation_trace_missing_or_failed')
    require(generated[0].get('metadata', {}).get('token_usage', {}).get('provider') == 'local', 'generation_trace_not_local')
    require(all(word in body.get('reply', '') for word in keywords), 'answer_content_mismatch')
    citations = body.get('citations', []) + body.get('evidence_citations', [])
    ids = {str(item.get('document_id', '')) for item in citations if item.get('document_id')}
    require(ids == (set(expected_document_ids) if expected_document_ids is not None else {document_id}), 'citation_document_mismatch')
    return {'reply': body['reply'], 'document_ids': sorted(ids), 'token_usage': usage,
            'trace': trace, 'citation_count': len(citations), 'generation_evidence': generated[0],
            'raw_generation_text_available': False,
            'generation_evidence_note': 'API exposes local generation token counts and character count; final reply may include answer composition.'}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True, help='New isolated directory; must not exist.')
    parser.add_argument('--report-dir', type=Path, default=ROOT / 'reports' / 'live_rag_acceptance')
    parser.add_argument('--timeout', type=float, default=900, help='Maximum seconds per startup/ingestion condition.')
    parser.add_argument('--question', default='星桥优享服务退款申请审核通过后，应通过哪个渠道查看进度？')
    parser.add_argument('--answer', default='请打开星桥优享服务 App 的订单详情页，在售后进度中查看结果。请以官方页面显示为准。')
    parser.add_argument('--expected-keyword', action='append', default=None)
    parser.add_argument('--verify-parse-quality', action='store_true', help='Also exercise quarantine with real worker and rebuild.')
    parser.add_argument('--capacity', action='store_true', help='Measure bounded capacity instead of repeating fault acceptance.')
    parser.add_argument('--budget-checks', action='store_true', help='Run real timeout/disconnect/admission probes with a short configured deadline.')
    parser.add_argument('--worker-checks', action='store_true')
    parser.add_argument('--ledger-checks', action='store_true')
    parser.add_argument('--capacity-phases', nargs='+', choices=['chat', 'ingestion', 'mixed'], default=['chat', 'ingestion', 'mixed'])
    return parser.parse_args(argv)


def run(args) -> int:
    import httpx
    from sqlalchemy.engine import make_url

    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
    report = {'run_id': run_id, 'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'execution': 'real-subprocess-api-worker-postgresql-redis-local-model', 'steps': []}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f'{run_id}.json'
    private_values = [os.getenv('RAG_DATABASE_URL', ''), os.getenv('RAG_REDIS_STREAM_URL', '')]
    stage = 'configuration'

    def checkpoint(name, **evidence):
        report['steps'].append({'name': name, 'status': 'passed', **evidence})
        report_path.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2), private_values), encoding='utf-8')
        print(f'PASS {name}', flush=True)

    try:
        database = make_url(os.environ['RAG_DATABASE_URL'])
        redis = urlsplit(os.environ['RAG_REDIS_STREAM_URL'])
        require(database.get_backend_name() == 'postgresql', 'postgresql_required')
        require(database.host and database.port, 'explicit_database_tcp_host_port_required')
        require(redis.scheme == 'redis' and redis.hostname and redis.port, 'explicit_plain_redis_tcp_endpoint_required')
        require(args.timeout > 0, 'positive_timeout_required')
        require(not args.run_root.exists(), 'run_root_must_be_new')
        args.run_root.mkdir(parents=True)
        run_root = args.run_root.resolve()
        logs, index, objects = (run_root / name for name in ('logs', 'faiss', 'objects'))
        for directory in (logs, index, objects):
            directory.mkdir()
        secret = secrets.token_urlsafe(48)
        private_values.extend([secret, database.password, redis.password])
        with ExitStack() as stack:
            db_proxy = DependencyProxy(database.host, database.port)
            stack.callback(db_proxy.close)
            redis_proxy = DependencyProxy(redis.hostname, redis.port)
            stack.callback(redis_proxy.close)
            proxied_db = database.set(host='127.0.0.1', port=db_proxy.port).render_as_string(hide_password=False)
            redis_auth = redis.netloc.rpartition('@')[0] + '@' if '@' in redis.netloc else ''
            proxied_redis = urlunsplit(redis._replace(netloc=f'{redis_auth}127.0.0.1:{redis_proxy.port}'))
            private_values.extend([proxied_db, proxied_redis])
            env = dict(os.environ)
            env.update(RAG_DATABASE_URL=proxied_db, RAG_REDIS_STREAM_URL=proxied_redis, REDIS_URL=proxied_redis,
                       RAG_ENV='production', RAG_ALLOW_DEGRADED_READINESS='false', RAG_ENABLE_DEMO_ENDPOINTS='false',
                       RAG_ALLOW_TEST_JWT_SECRET='false', RAG_JWT_SECRET=secret, RAG_JWT_ALGORITHM='HS256',
                       RAG_JWT_ISSUER='llm-customer-service', RAG_JWT_AUDIENCE='customer-service-api',
                       RAG_FAISS_STORE_DIR=str(index), RAG_OBJECT_STORE_DRIVER='local', RAG_OBJECT_STORE_LOCAL_ROOT=str(objects),
                       RAG_INGESTION_STREAM_KEY=f'live-acceptance:{run_id}:jobs', RAG_INGESTION_CONSUMER_GROUP=f'live-{run_id}',
                       RAG_GENERATION_PROVIDER='local', RAG_WARM_MODELS_ON_STARTUP='true', PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
            original = dict(os.environ)
            stack.callback(lambda: (os.environ.clear(), os.environ.update(original)))
            os.environ.update(env)
            from services.ingestion.db import dispose_engines, get_engine
            stack.callback(dispose_engines)
            stage = 'migrations'
            migration = Child('migration', [sys.executable, '-m', 'alembic', 'upgrade', 'head'], env, logs, private_values)
            stack.callback(migration.stop)
            require(migration.process.wait(timeout=args.timeout) == 0, 'migration_failed')
            owner, foreign = bootstrap()
            owner_headers, foreign_headers = headers(owner, secret), headers(foreign, secret)
            checkpoint('bootstrap', identities=[owner, foreign], database_backend='postgresql', queue_backend='redis-streams',
                       run_root=str(run_root), stream_key=env['RAG_INGESTION_STREAM_KEY'])
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                api_port = reservation.getsockname()[1]
            base = f'http://127.0.0.1:{api_port}'
            client = stack.enter_context(httpx.Client(base_url=base, timeout=httpx.Timeout(args.timeout, connect=3), trust_env=False))
            generation = 0

            def launch(kind):
                nonlocal generation
                generation += 1
                command = ([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(api_port), '--no-access-log']
                           if kind == 'api' else [sys.executable, '-m', 'services.ingestion.worker', '--consumer-name', f'live-{run_id}'])
                child = Child(f'{kind}-{generation}', command, env, logs, private_values)
                stack.callback(child.stop)
                return child

            def request(method, path, *, auth=owner_headers, expected=200, **kwargs):
                response = client.request(method, path, headers=auth, **kwargs)
                require(response.status_code == expected, f'http_status:{method}:{path}:{response.status_code}:expected_{expected}')
                return response.json()

            def health(expected=200, failed=None):
                api.alive()
                try:
                    response = client.get('/health/ready', timeout=20)
                except httpx.TransportError:
                    return False
                if response.status_code != expected:
                    return False
                detail = client.get('/health/dependencies', headers=owner_headers, timeout=20)
                if detail.status_code != expected:
                    return False
                body = detail.json()
                body['public_ready_http_status'] = response.status_code
                if failed and body.get('checks', {}).get(failed, {}).get('status') != 'unavailable':
                    return False
                return body

            def live():
                api.alive()
                try:
                    return client.get('/health/live', timeout=5).status_code == 200
                except httpx.TransportError:
                    return False

            stage = 'api_worker_startup'
            startup_started = time.monotonic()
            worker, api = launch('worker'), launch('api')
            poll(live, timeout=args.timeout, label='api_live')
            checkpoint('api_worker_running', api_pid=api.process.pid, worker_pid=worker.process.pid)
            stage = 'operations_snapshot'
            marker = 'capacity-fixture-v2' if args.capacity else f'live-proof-{run_id}'
            item = request('POST', '/knowledge/items', json={'title': marker, 'question': args.question, 'answer': args.answer,
                                                           'category': '服务说明', 'intent': '进度查询'})
            request('POST', f"/knowledge/items/{item['id']}/review", json={'status': 'approved', 'review_note': 'isolated live acceptance'})
            exported = request('GET', '/knowledge/export-approved')
            markdown = export_markdown(exported)
            require(args.answer in markdown.decode('utf-8'), 'export_content_mismatch')
            before_index = tree_hashes(index)
            published = request('POST', '/knowledge/publish-approved')
            require(published['item_ids'] == [item['id']] and published['status'] == 'succeeded', 'snapshot_publish_mismatch')
            require(tree_hashes(index) == before_index, 'operations_publish_modified_formal_index')
            from sqlalchemy import text
            with get_engine().connect() as connection:
                count = connection.execute(text('SELECT count(*) FROM documents WHERE tenant_id=:tenant'), {'tenant': owner['tenant_id']}).scalar_one()
                status = connection.execute(text('SELECT status FROM runtime_knowledge_items WHERE id=:id AND tenant_id=:tenant'),
                                            {'id': item['id'], 'tenant': owner['tenant_id']}).scalar_one()
            require(count == 0 and status == 'published', 'operations_publish_not_snapshot_only')
            require(request('GET', '/knowledge/items', auth=foreign_headers)['total'] == 0, 'cross_tenant_operations_leak')
            checkpoint('operations_publish_is_database_snapshot', item_id=item['id'], publish_id=published['publish_id'],
                       formal_document_count=count, index_unchanged=True, export_order='approved_export_before_snapshot_publish')
            stage = 'upload_and_worker_pipeline'
            uploaded = request('POST', f"/knowledge-bases/{owner['kb_id']}/documents", expected=202,
                               files={'file': (f'{marker}.md', markdown, 'text/markdown')})
            document_id, job_id = uploaded['document_id'], uploaded['job_id']

            def job_complete():
                worker.alive()
                job = request('GET', f'/ingestion-jobs/{job_id}')
                require(job['status'] not in {'failed', 'cancelled'}, f"ingestion_failed:{job.get('error_code', '')}")
                return job if job['status'] == 'succeeded' and job['stage'] == 'published' else False

            job = poll(job_complete, timeout=args.timeout, label='ingestion_published')
            checkpoint('upload_to_published', document_id=document_id, job_id=job_id, job=job, markdown_sha256=hashlib.sha256(markdown).hexdigest())
            stage = 'real_retrieval_and_generation'
            readiness = poll(health, timeout=args.timeout, label='all_dependencies_ready')
            checkpoint('ready_with_real_models', health=readiness)
            if args.worker_checks:
                from scripts.verify_live_delivery import verify_delivery
                verify_delivery(client, owner, owner_headers, foreign_headers, worker, launch, env, run_root, checkpoint)
                report['status'] = 'passed'
                return 0
            if args.budget_checks:
                from scripts.verify_live_budget import verify_budget
                verify_budget(client, owner_headers, foreign_headers, args.question, checkpoint)
                report['status'] = 'passed'
                return 0
            if args.capacity:
                from scripts.capacity_observer import capacity_sweep
                capacity_sweep(client, owner, owner_headers, args.question, api, worker, env,
                               run_root, checkpoint, time.monotonic() - startup_started, args.capacity_phases)
                report['status'] = 'passed'
                return 0

            def verify_answers(phase):
                retrieval = request('POST', '/retrieval/search', json={'query': args.question, 'retrieval_mode': 'hybrid', 'limit': 5})
                results = retrieval['results']
                require(results and {row['document_id'] for row in results} == {document_id}, 'retrieval_document_mismatch')
                require(all(row['tenant_id'] == owner['tenant_id'] for row in results), 'retrieval_tenant_mismatch')
                require(any(args.answer in row['text'] for row in results), 'retrieval_content_mismatch')
                body = request('POST', '/chat/prompt', json={'message': args.question, 'session_id': f'{run_id}-{phase}', 'channel': 'live_acceptance'})
                (run_root / f'{phase}.json').write_text(
                    redact(json.dumps(body, ensure_ascii=False, indent=2), private_values), encoding='utf-8')
                evidence = validate_chat(body, document_id, args.expected_keyword or ['订单', '售后'])
                checkpoint(phase, retrieval_index=retrieval['index'], retrieval_document_ids=[r['document_id'] for r in results], chat=evidence)

            verify_answers('query_and_chat_before_restart')
            stage = 'process_restart'
            old_pids = [api.process.pid, worker.process.pid]
            api.stop()
            worker.stop()
            worker, api = launch('worker'), launch('api')
            poll(live, timeout=args.timeout, label='restarted_api_live')
            poll(health, timeout=args.timeout, label='restarted_ready')
            checkpoint('api_worker_restarted', old_pids=old_pids, new_pids=[api.process.pid, worker.process.pid])
            verify_answers('query_and_chat_after_restart')
            if args.ledger_checks:
                from scripts.verify_live_ledger import verify_ledger
                verify_ledger(client, owner_headers, foreign_headers, run_root, checkpoint)
                report['status'] = 'passed'
                return 0
            stage = 'cross_tenant_isolation'
            request('GET', f'/documents/{document_id}', auth=foreign_headers, expected=404)
            foreign_search = request('POST', '/retrieval/search', auth=foreign_headers, json={'query': args.question})
            require(not foreign_search['results'], 'cross_tenant_retrieval_leak')
            foreign_chat = request('POST', '/chat/prompt', auth=foreign_headers, json={'message': args.question})
            serialized = json.dumps(foreign_chat, ensure_ascii=False)
            require(document_id not in serialized and marker not in serialized, 'cross_tenant_chat_leak')
            require(not foreign_chat.get('citations') and not foreign_chat.get('evidence_citations'), 'cross_tenant_citation_leak')
            checkpoint('cross_tenant_isolation', document_http_status=404, retrieval_count=0, citation_count=0)
            stage = 'worker_outage_readiness'
            worker.stop()
            failure = poll(lambda: health(503, 'worker'), timeout=95, label='worker_heartbeat_expired')
            require(live(), 'worker_outage_killed_api')
            checkpoint('worker_outage_returns_503', health=failure, api_live=True)
            worker = launch('worker')
            poll(health, timeout=args.timeout, label='worker_recovered')
            for name, proxy in [('database', db_proxy), ('redis', redis_proxy)]:
                stage = f'{name}_outage_readiness'
                proxy.isolate()
                try:
                    failure = poll(lambda: health(503, name), timeout=45, label=f'{name}_unavailable')
                    require(live(), f'{name}_outage_killed_api')
                    checkpoint(f'{name}_outage_returns_503', health=failure, api_live=True, fault_scope='run_owned_tcp_proxy_only')
                finally:
                    proxy.restore()
                poll(health, timeout=args.timeout, label=f'{name}_recovered')
            checkpoint('dependencies_recovered', health=health())
            if args.verify_parse_quality:
                stage = 'parse_quality_quarantine'
                before_quality = tree_hashes(index)
                rejected = []

                def review_job(job_id):
                    worker.alive()
                    value = request('GET', f'/ingestion-jobs/{job_id}')
                    require(value['status'] not in {'failed', 'succeeded', 'cancelled'}, 'quality_job_wrong_terminal_state')
                    return value if value['status'] == 'requires_review' else False

                bad_inputs = {
                    'empty_text': '   ',
                    'garbled_text': '�' * 100,
                    'repeated_content': ('这是一段错误重复提取的售后流程说明，请联系官方客服检查。\n\n') * 8,
                    'structure_anomaly': '# 只有标题没有正文',
                }
                for reason, payload in bad_inputs.items():
                    bad = request('POST', f"/knowledge-bases/{owner['kb_id']}/documents", expected=202,
                                  files={'file': (f'{marker}-{reason}.md', payload.encode('utf-8'), 'text/markdown')})
                    detail = poll(lambda: review_job(bad['job_id']), timeout=args.timeout, label=reason)
                    require(reason in detail['parse_quality']['reasons'], f'quality_reason_missing:{reason}')
                    retry = request('POST', f"/documents/{bad['document_id']}/reprocess", expected=202)
                    poll(lambda: review_job(retry['job_id']), timeout=args.timeout, label='quality_reprocess')
                    duplicate = request('POST', f"/knowledge-bases/{owner['kb_id']}/documents", expected=202,
                                        files={'file': (f'{marker}-{reason}-duplicate.md', payload.encode('utf-8'), 'text/markdown')})
                    poll(lambda: review_job(duplicate['job_id']), timeout=args.timeout, label='quality_duplicate')
                    rejected.extend([bad['document_id'], duplicate['document_id']])
                # Reject a new version of the good document while preserving its old published version.
                bad_update = request('POST', f"/knowledge-bases/{owner['kb_id']}/documents", expected=202,
                                     files={'file': (f'{marker}.md', ('�' * 101).encode('utf-8'), 'text/markdown')})
                require(bad_update['document_id'] == document_id, 'quality_update_not_same_document')
                poll(lambda: review_job(bad_update['job_id']), timeout=args.timeout, label='quality_bad_update')
                require(tree_hashes(index) == before_quality, 'quarantine_modified_active_index')
                request('POST', '/ingestion/indexes/rebuild')
                from services.ingestion.index_manifest import load_active_manifest, DEFAULT_INDEX_NAME
                manifest, _, _ = load_active_manifest(index, DEFAULT_INDEX_NAME)
                require(not {entry.document_id for entry in manifest.entries}.intersection(rejected), 'quarantine_rebuild_leak')
                with get_engine().connect() as connection:
                    states = connection.execute(text('SELECT status FROM document_versions WHERE tenant_id=:tenant AND document_id=:document'),
                                                {'tenant': owner['tenant_id'], 'document': document_id}).scalars().all()
                require(sorted(states) == ['published', 'requires_review'], 'quality_old_version_not_preserved')
                verify_answers('query_and_chat_after_quarantine_rebuild')
                checkpoint('parse_quality_quarantine', reasons=list(bad_inputs), rejected_document_ids=rejected,
                           retry_and_duplicate_blocked=True, index_unchanged_before_rebuild=True,
                           rebuild_excludes_quarantine=True, old_published_version_preserved=True)
            report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', failure={'stage': stage, 'type': type(error).__name__,
                                               'message': redact(str(error), private_values)})
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2), private_values), encoding='utf-8')
        print(f"{report['status'].upper()} report={report_path}", flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(run(parse_args()))
