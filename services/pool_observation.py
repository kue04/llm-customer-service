"""Opt-in pool evidence for isolated load runs; disabled in normal operation."""
import json
import os
from pathlib import Path
from threading import Lock
import time

from sqlalchemy import event

_lock = Lock()


def observe_pool(engine):
    directory = os.getenv('RAG_POOL_OBSERVATION_DIR', '')
    if not directory or not hasattr(engine.pool, 'checkedout'):
        return
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{os.getpid()}.jsonl'

    def record(phase):
        # SQLAlchemy's checkin event fires before the queue receives the
        # returned connection, so subtract the connection being returned.
        count = max(0, engine.pool.checkedout()-(phase == 'checkin'))
        row = {'at': time.time(), 'phase': phase, 'checked_out': count, 'pool_size': engine.pool.size()}
        with _lock:
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row)+'\n')

    event.listen(engine, 'checkout', lambda *args: record('checkout'))
    event.listen(engine, 'checkin', lambda *args: record('checkin'))
