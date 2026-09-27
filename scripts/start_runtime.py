"""Non-container launcher: migration failure prevents API/worker startup."""
from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from services.ingestion.db import get_database_url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service', choices=['api', 'worker'])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args, remaining = parser.parse_known_args(argv)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    config.set_main_option('sqlalchemy.url', get_database_url().replace('%', '%%'))
    command.upgrade(config, 'head')
    if args.service == 'worker':
        from services.ingestion.worker import main as worker_main
        return worker_main(remaining)
    if remaining:
        parser.error('unknown API arguments')
    import uvicorn
    uvicorn.run('main:app', host=args.host, port=args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
