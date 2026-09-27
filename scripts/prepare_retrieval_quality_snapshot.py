"""Copy the historical formal corpus into a dedicated PostgreSQL evaluation tenant.

Preserve text, status, metadata and cached real embeddings; never reparse old data
or change shared indexes. No network/model download is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(source_db: Path, source_index: Path, run_root: Path) -> dict:
    import faiss
    import numpy as np
    from sqlalchemy import insert
    from services.ingestion import models, repository
    from services.ingestion.db import session_scope
    from services.ingestion.index_builder import rebuild_index

    if run_root.exists():
        raise ValueError('run-root must not exist')
    pointer = json.loads((source_index / 'chunk_index/document_chunks/current.json').read_text('utf-8'))
    manifest_path = source_index / pointer['manifest_path']
    vector_path = source_index / pointer['index_path']
    manifest = json.loads(manifest_path.read_text('utf-8'))
    source_tenant = manifest['tenant_id']
    vectors = faiss.read_index(str(vector_path))
    cache = {}
    for entry in manifest['entries']:
        vector = vectors.reconstruct(entry['row_id'])
        if entry['text'] in cache and not np.allclose(cache[entry['text']], vector, atol=1e-6):
            raise ValueError('inconsistent cached embeddings for identical text')
        cache[entry['text']] = vector
    with sqlite3.connect(source_db.resolve().as_uri() + '?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        if source.execute('SELECT count(*) FROM document_acl WHERE tenant_id=?', (source_tenant,)).fetchone()[0]:
            raise ValueError('snapshot only supports this historical public corpus; refusing to drop ACLs')
        rows = {name: [dict(row) for row in source.execute(
            f'SELECT * FROM {name} WHERE tenant_id=?', (source_tenant,)
        )] for name in ('knowledge_bases', 'documents', 'document_versions', 'document_chunks')}
    namespace = uuid.uuid4()
    def remap(value):
        return uuid.uuid5(namespace, value).hex if value else value
    run_root.mkdir(parents=True)
    with session_scope() as session:
        tenant = repository.create_tenant(session, name='retrieval-quality-' + namespace.hex[:8])
        user = repository.create_user(session, tenant_id=tenant.id, external_id='retrieval-quality')
        tenant_id, user_id = tenant.id, user.external_id
        for name, records in rows.items():
            table = models.Base.metadata.tables[name]
            converted = []
            for original in records:
                row = dict(original)
                for key, value in row.items():
                    if key in ('id', 'knowledge_base_id', 'document_id', 'document_version_id'):
                        row[key] = remap(value)
                    elif key == 'tenant_id':
                        row[key] = tenant_id
                    elif key == 'created_by':
                        row[key] = user.id
                    elif key.endswith('_json') and isinstance(value, str):
                        row[key] = json.loads(value)
                    elif key.endswith('_at') and isinstance(value, str):
                        row[key] = datetime.fromisoformat(value)
                converted.append(row)
            if converted:
                session.execute(insert(table), converted)
        session.flush()
        for kb in rows['knowledge_bases']:
            repository.add_knowledge_base_member(session, tenant_id=tenant_id,
                knowledge_base_id=remap(kb['id']), user_id=user.id, member_role='owner')
        session.commit()
        result = rebuild_index(session, tenant_id=tenant_id, root=run_root / 'index',
            embedder=lambda text: cache[text], embedding_model=manifest['embedding_model'],
            all_tenants=False)
    report = {
        'tenant_id': tenant_id, 'user_id': user_id, 'index_root': str((run_root / 'index').resolve()),
        'source_tenant': source_tenant, 'source_index_version': manifest['index_version'],
        'source_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'source_vectors_sha256': hashlib.sha256(vector_path.read_bytes()).hexdigest(),
        'source_database_sha256': hashlib.sha256(source_db.read_bytes()).hexdigest(),
        'document_id_map': {r['id']: remap(r['id']) for r in rows['documents']},
        'counts': {name: len(records) for name, records in rows.items()},
        'index': result.to_dict(),
        'embedding_provenance': 'cached real vectors from source FAISS; queries use real local model',
        'parse_quality': 'historical metadata preserved; no retroactive gate or status override',
    }
    (run_root / 'snapshot.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, default=ROOT / 'data/rag_metadata.db')
    parser.add_argument('--source-index', type=Path, default=ROOT / 'data/faiss_store')
    parser.add_argument('--run-root', type=Path, required=True)
    args = parser.parse_args()
    output = prepare(args.source_db, args.source_index, args.run_root)
    print(json.dumps({k: output[k] for k in ('tenant_id', 'counts', 'index_root')}, ensure_ascii=False))
