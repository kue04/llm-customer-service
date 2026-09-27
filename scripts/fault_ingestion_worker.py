"""Dedicated acceptance child; fault injection never exists in runtime code."""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from services.ingestion.worker import build_worker
    from services.ingestion.queue import QueueUnavailableError
    from services.ingestion.worker_health import worker_heartbeat
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['crash', 'transient', 'poison', 'ack'])
    parser.add_argument('marker', type=Path)
    args = parser.parse_args()
    worker = build_worker(consumer_name='fault-'+args.mode, batch=1)
    original = worker.store.get

    def get(key):
        count = int(args.marker.read_text()) if args.marker.exists() else 0
        args.marker.write_text(str(count+1))
        if args.mode == 'crash':
            time.sleep(120)
        elif args.mode == 'transient' and count < 2:
            raise ConnectionError('injected dependency outage')
        elif args.mode == 'poison':
            raise ValueError('injected permanent format failure')
        return original(key)

    worker.store.get = get
    if args.mode == 'crash':
        # Force the transport lease to expire while the task is alive. The
        # second worker must still respect the PostgreSQL execution lock.
        worker.queue.renew = lambda _: None
    if args.mode == 'ack':
        def ack(_):
            args.marker.write_text('1')
            raise QueueUnavailableError('injected_ack_failure')
        worker.queue.ack = ack
    with worker_heartbeat():
        worker.run_forever(poll_interval=.1)


if __name__ == '__main__':
    main()
