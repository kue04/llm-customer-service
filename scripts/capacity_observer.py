"""Observations for the bounded capacity sweep, without shared-service mutations."""
import hashlib
import json
import os
import platform
import threading
import time
from pathlib import Path

import psutil
import redis
from sqlalchemy import text

from scripts.measure_capacity import measure
from services.ingestion.db import get_engine


def capacity_sweep(client, owner, auth, question, api, worker, env, run_root, checkpoint, startup_seconds, phases):
    queue = redis.Redis.from_url(env['RAG_REDIS_STREAM_URL'], decode_responses=True, socket_timeout=2)
    samples, stop = [], threading.Event()

    def sample():
        while not stop.is_set():
            row = {'at': time.time(), 'rss_bytes': {}}
            try:
                for name, child in [('api', api), ('worker', worker)]:
                    proc = psutil.Process(child.process.pid)
                    processes = [proc, *proc.children(recursive=True)]
                    row['rss_bytes'][name] = sum(p.memory_info().rss for p in processes)
                    row[name+'_cpu_seconds'] = sum(sum(p.cpu_times()[:2]) for p in processes)
                with get_engine().connect() as conn:
                    row['database'] = dict(conn.execute(text("SELECT count(*) AS connections, count(*) FILTER (WHERE wait_event_type='Lock') AS lock_waiters, max(extract(epoch from clock_timestamp()-query_start)) FILTER (WHERE wait_event_type='Lock') AS max_lock_wait_seconds FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()")).mappings().one())
                pending = queue.xpending(env['RAG_INGESTION_STREAM_KEY'], env['RAG_INGESTION_CONSUMER_GROUP'])
                row['pending'] = pending['pending']
                row['oldest_pending_age_ms'] = max(0, int(time.time()*1000)-int(pending['min'].split('-')[0])) if pending['min'] else 0
                groups = queue.xinfo_groups(env['RAG_INGESTION_STREAM_KEY'])
                row['lag'] = next(g.get('lag') for g in groups if g['name'] == env['RAG_INGESTION_CONSUMER_GROUP'])
            except Exception as error:
                row['sample_error'] = type(error).__name__
            samples.append(row)
            stop.wait(.5)

    source = {}
    source_root = Path(__file__).resolve().parents[1]
    for directory in ('services', 'routers', 'config'):
        for path in sorted((source_root/directory).rglob('*.py')):
            source[str(path.relative_to(source_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    checkpoint('capacity_configuration', cpu=platform.processor(), logical_cpu=os.cpu_count(),
               physical_cpu=psutil.cpu_count(logical=False), memory_bytes=psutil.virtual_memory().total,
               python=platform.python_version(), api_processes=1, worker_processes=1,
               model='qwen2.5-1.5b-instruct', embedding='BAAI/bge-small-zh-v1.5',
               thread_configuration={k: env.get(k, 'library_default') for k in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS')},
               warm_startup_seconds=startup_seconds, source_sha256=source,
               fixture='capacity-fixture-v2; deterministic phase-concurrency-index Markdown; one approved FAQ seed',
               concurrency_levels=[1, 2, 4], attempts_per_level='2 * concurrency',
               request_timeout_seconds=90, stop_on='any failure/timeout or P95 > 30 seconds or RSS > 16 GiB',
               pool_observation='SQLAlchemy checkout/checkin event files in RAG_POOL_OBSERVATION_DIR',
               pool_observation_dir=env.get('RAG_POOL_OBSERVATION_DIR'))
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        cold = measure(client, owner, auth, question, concurrency=1, count=1, timeout=90, phase='chat')
        checkpoint('capacity_first_chat', **cold)
        if cold['outcomes'] != {'success': 1}:
            return
        for phase in phases:
            for concurrency in (1, 2, 4):
                result = measure(client, owner, auth, question, concurrency=concurrency, count=concurrency*2, timeout=90, phase=phase)
                failed = any(k in result['outcomes'] for k in ('failure', 'timeout'))
                high_latency = result['latency_all_attempts_ms']['p95'] > 30000
                high_memory = bool(samples and sum(samples[-1]['rss_bytes'].values()) > 16*1024**3)
                checkpoint('capacity_batch', phase=phase, concurrency=concurrency, stop=failed or high_latency or high_memory, **result)
                if failed or high_latency or high_memory:
                    break
            if failed:
                break  # timed-out work might still occupy the old synchronous API
    finally:
        stop.set()
        thread.join(timeout=10)
        path = run_root / 'capacity_samples.json'
        path.write_text(json.dumps(samples, indent=2, default=float), encoding='utf-8')
        pool_peaks = {}
        for name, child in [('api', api), ('worker', worker)]:
            pids = [child.process.pid, *[p.pid for p in psutil.Process(child.process.pid).children(recursive=True)]]
            values = []
            for pid in pids:
                pool_path = Path(env.get('RAG_POOL_OBSERVATION_DIR', run_root)) / f'{pid}.jsonl'
                if pool_path.exists():
                    values.extend(json.loads(line)['checked_out'] for line in pool_path.read_text().splitlines())
            pool_peaks[name] = max(values) if values else None
        checkpoint('capacity_observations', path=str(path), samples=len(samples),
                   pool_checked_out_peaks=pool_peaks,
                   peak_rss_bytes={name: max((r['rss_bytes'].get(name, 0) for r in samples), default=0) for name in ('api', 'worker')},
                   max_lock_waiters=max((r.get('database', {}).get('lock_waiters', 0) for r in samples), default=0),
                   max_pending=max((r.get('pending', 0) for r in samples), default=0),
                   sample_errors=sum('sample_error' in r for r in samples))
