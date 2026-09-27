"""Read-only before/after chunking audit; never publishes or indexes rejected documents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def audit(paths: list[Path], baseline_ref: str) -> dict:
    from config.chunking_config import build_config
    from services.ingestion.chunkers import ChunkContext
    from services.ingestion.chunkers import chunker
    from services.ingestion.parser_registry import parse_document
    from services.ingestion.parse_quality import evaluate

    # Execute the actual trusted repository baseline module in this diagnostic
    # process only, rather than approximating its algorithm or editing files.
    source_path = 'services/ingestion/chunkers/cleaner.py'
    source = subprocess.check_output(['git', 'show', f'{baseline_ref}:{source_path}'], cwd=ROOT).decode('utf-8')
    name = 'services.ingestion.chunkers._audit_baseline_cleaner'
    legacy = types.ModuleType(name)
    sys.modules[name] = legacy
    exec(compile(source, f'{baseline_ref}:{source_path}', 'exec'), legacy.__dict__)
    current = chunker.clean_blocks
    documents = []
    try:
        for path in paths:
            raw = path.read_bytes()
            parsed = parse_document(raw, path.name)
            context = ChunkContext(tenant_id='offline-audit', document_id=path.name,
                document_version_id=path.name, document_version=1)
            metrics = {}
            for phase, cleaner in [('before', legacy.clean_blocks), ('after', current)]:
                chunker.clean_blocks = cleaner
                result = chunker.chunk_document(parsed, context=context, config=build_config())
                bare = [c for c in result.chunks if c.heading_path and c.text.strip() == c.heading_path[-1].strip()]
                question_bare = [c for c in bare if '?' in c.text or '？' in c.text]
                body_blocks = [b for b in parsed.blocks if b.type == 'paragraph' and b.heading_path and b.text.strip()]
                lost = [b.heading_path[-1] for b in body_blocks if not any(
                    c.heading_path == b.heading_path and ' '.join(b.text.split()) in ' '.join(c.text.split())
                    for c in result.chunks)]
                metrics[phase] = {'chunk_count': len(result.chunks), 'bare_heading_chunks': len(bare),
                    'question_only_chunks': len(question_bare), 'dropped_duplicate': result.stats.dropped_duplicate,
                    'source_answer_blocks': len(body_blocks), 'answer_blocks_without_matching_chunk': len(lost),
                    'lost_answer_headings': lost}
            documents.append({'source_file': path.as_posix(), 'source_sha256': hashlib.sha256(raw).hexdigest(),
                'parse_quality': evaluate(parsed), 'metrics': metrics})
    finally:
        chunker.clean_blocks = current
        sys.modules.pop(name, None)
    return {'scope': 'offline parser/chunker replay, no publication or retrieval quality claim',
        'baseline_ref': baseline_ref, 'baseline_cleaner_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'current_cleaner_sha256': hashlib.sha256((ROOT / source_path).read_bytes()).hexdigest(),
        'chunker_version': chunker.CHUNKER_VERSION, 'documents': documents}


def verify_live_fixture(source_path: Path, run_root: Path) -> dict:
    """Validate a source-derived fixture, not republication of the rejected source."""
    from collections import defaultdict
    from functools import partial
    from services.auth_context import AuthContext
    from services.ingestion import repository
    from services.ingestion.db import session_scope
    from services.ingestion.index_builder import rebuild_index
    from services.ingestion.object_store import LocalObjectStore, build_object_key, logical_source_uri
    from services.ingestion.parser_registry import parse_document
    from services.ingestion.parse_quality import evaluate
    from services.ingestion.pipeline import IngestionPipeline, default_embedder, default_embedding_model
    from services.retrieval_access import build_chunk_access_filter
    from utils.hybrid_retriever import search_hybrid_chunks

    if run_root.exists():
        raise ValueError('run-root must not exist')
    parsed = parse_document(source_path.read_bytes(), source_path.name)
    groups = defaultdict(list)
    for block in parsed.blocks:
        if block.type == 'paragraph' and block.heading_path:
            groups[block.text].append(block.heading_path[-1])
    answer, questions = next((text, qs) for text, qs in groups.items() if len(set(qs)) >= 2)
    other_answer, other_questions = next((text, qs) for text, qs in groups.items() if text != answer)
    pairs = [(q, answer) for q in list(dict.fromkeys(questions))[:2]] + [(other_questions[0], other_answer)]
    nl = chr(10)
    raw = ('# FAQ diagnostic subset' + nl * 2 + (nl * 2).join(f'## {q}{nl}{nl}{a}' for q, a in pairs) + nl).encode('utf-8')
    quality = evaluate(parse_document(raw, 'fixture.md'))
    if quality['status'] != 'passed':
        raise ValueError('diagnostic fixture rejected by parse gate; do not override')
    run_root.mkdir(parents=True)
    (run_root / 'fixture.md').write_bytes(raw)
    store = LocalObjectStore(run_root / 'objects')
    index_root = run_root / 'index'
    with session_scope() as session:
        tenant = repository.create_tenant(session, name='faq-chunking-diagnostic')
        user = repository.create_user(session, tenant_id=tenant.id, external_id='faq-diagnostic')
        kb = repository.create_knowledge_base(session, tenant_id=tenant.id, slug='diagnostic', created_by=user.id)
        repository.add_knowledge_base_member(session, tenant_id=tenant.id, knowledge_base_id=kb.id, user_id=user.id, member_role='owner')
        doc = repository.create_document(session, tenant_id=tenant.id, knowledge_base_id=kb.id,
            source_uri=logical_source_uri('fixture.md'), source_type='md', title='Source-derived diagnostic fixture', created_by=user.id)
        store.put(build_object_key(tenant.id, doc.id, 'fixture.md'), raw)
        job = repository.create_ingestion_job(session, tenant_id=tenant.id, document_id=doc.id, status='pending', stage='received')
        session.commit()
        outcome = IngestionPipeline(session=session, store=store, index_root=index_root,
            index_runner=partial(rebuild_index, all_tenants=False)).process_job(tenant_id=tenant.id, job_id=job.id)
        if not outcome.succeeded:
            raise RuntimeError(f'fixture pipeline failed: {outcome.error_code}')
        auth = AuthContext(user_id=user.external_id, tenant_id=tenant.id, roles=frozenset({'admin'}))
        access = build_chunk_access_filter(session, auth)
        records = repository.list_chunks(session, tenant.id, outcome.document_version_id)
        for question, expected in pairs:
            exact = [r for r in records if (r.metadata_json or {}).get('heading_path', [])[-1:] == [question]]
            if not exact or not all(expected in r.text for r in exact):
                raise RuntimeError('answer lost from its own FAQ section')
        retrieval = []
        for question, expected in pairs:
            hits = search_hybrid_chunks(question, access=access, top_k=5, root=index_root,
                embedder=default_embedder, embedding_model=default_embedding_model())
            hit = any(expected in item.hit.text for item in hits)
            if not hit:
                raise RuntimeError('fixture answer absent from real-model top5')
            retrieval.append({'query': question, 'answer_hit_at_5': hit})
        return {'scope': 'three-FAQ diagnostic fixture; real PostgreSQL/pipeline/local BGE; no HTTP/worker/generation',
            'source_file': source_path.as_posix(), 'source_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'fixture_sha256': hashlib.sha256(raw).hexdigest(), 'fixture_parse_quality': quality,
            'original_parse_quality': evaluate(parsed), 'tenant_id': tenant.id,
            'pipeline': outcome.to_dict(), 'own_section_answers_preserved': True, 'retrieval': retrieval}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--baseline-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live-run-root', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; refusing to replace evidence')
    report = audit(args.source, args.baseline_ref)
    if args.live_run_root:
        report['live_fixture'] = verify_live_fixture(args.source[0], args.live_run_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for doc in report['documents']:
        print(doc['source_file'], doc['parse_quality']['status'],
              {phase: {k: v for k, v in m.items() if k != 'lost_answer_headings'} for phase, m in doc['metrics'].items()})
