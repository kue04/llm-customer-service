import json

from services.ingestion.db import create_db_engine


def test_pool_occupancy_records_actual_checkout_and_return(tmp_path, monkeypatch):
    monkeypatch.setenv('RAG_POOL_OBSERVATION_DIR', str(tmp_path/'events'))
    engine = create_db_engine('sqlite:///'+(tmp_path/'pool.db').as_posix())
    try:
        with engine.connect(), engine.connect():
            pass
        rows = [json.loads(line) for p in (tmp_path/'events').glob('*.jsonl') for line in p.read_text().splitlines()]
        assert max(row['checked_out'] for row in rows) == 2
        assert rows[-1]['phase'] == 'checkin' and rows[-1]['checked_out'] == 0
    finally:
        engine.dispose()
