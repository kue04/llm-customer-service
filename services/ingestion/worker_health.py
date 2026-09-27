"""Expiring worker heartbeat; runs while a long ingestion job is in flight."""
from contextlib import contextmanager
import logging
from threading import Event, Thread
import time

from services.health_service import WORKER_HEARTBEAT_TTL, redis_client, worker_heartbeat_key

logger = logging.getLogger(__name__)


@contextmanager
def worker_heartbeat():
    stop = Event()

    def publish():
        try:
            with redis_client() as client:
                client.set(worker_heartbeat_key(), str(time.time()), ex=WORKER_HEARTBEAT_TTL)
        except Exception:
            logger.warning('worker heartbeat unavailable')

    def loop():
        while not stop.wait(WORKER_HEARTBEAT_TTL / 3):
            publish()

    publish()
    thread = Thread(target=loop, name='worker-heartbeat', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        # Do not delete the shared key: another worker may still be publishing.
