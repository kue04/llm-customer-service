"""Materialize verified pre-change source without reverting the working tree."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


def prepare(revision, report_path, destination):
    report = json.loads(report_path.read_text(encoding='utf-8'))
    recorded = next(s['source_sha256'] for s in report['steps'] if s['name'] == 'capacity_configuration')
    mismatches = []
    overrides = {}
    for name, expected in recorded.items():
        content = subprocess.check_output(['git', 'show', revision+':'+name.replace('\\', '/')])
        if expected not in {hashlib.sha256(content).hexdigest(), hashlib.sha256(content.replace(b'\n', b'\r\n')).hexdigest()}:
            path = name.replace('\\', '/')
            current = Path(path).read_bytes()
            if hashlib.sha256(current).hexdigest() == expected:
                overrides[path] = ('working-copy-matching-recorded-sha256', current)
                continue
            revisions = subprocess.check_output(['git', 'rev-list', '--max-count=30', revision, '--', path], text=True).splitlines()
            for candidate in revisions:
                historical = subprocess.check_output(['git', 'show', candidate+':'+path])
                if expected in {hashlib.sha256(historical).hexdigest(), hashlib.sha256(historical.replace(b'\n', b'\r\n')).hexdigest()}:
                    overrides[path] = (candidate, historical)
                    break
            else:
                mismatches.append(name)
    if mismatches:
        raise ValueError('baseline_source_mismatch:'+str(mismatches))
    if destination.exists():
        raise ValueError('destination_must_be_new')
    archive = subprocess.check_output(['git', 'archive', revision, 'services', 'routers', 'schemas', 'config',
                                       'utils', 'models/prompt.py', 'main.py', 'alembic', 'alembic.ini', 'data', 'scripts'])
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter='data')
    for name, (_, content) in overrides.items():
        (destination/name).write_bytes(content)
    (destination/'scripts').mkdir(exist_ok=True)
    for name in ('accept_live_rag.py', 'measure_capacity.py', 'capacity_observer.py'):
        shutil.copy2(Path('scripts')/name, destination/'scripts'/name)
    shutil.copy2('services/pool_observation.py', destination/'services/pool_observation.py')
    db = destination/'services/ingestion/db.py'
    source = db.read_text(encoding='utf-8')
    source = source.replace('    return engine', '    from services.pool_observation import observe_pool\n    observe_pool(engine)\n    return engine', 1)
    db.write_text(source, encoding='utf-8')
    (destination/'baseline_provenance.json').write_text(json.dumps({
        'revision': revision, 'source_files_verified': len(recorded), 'baseline_report': str(report_path),
        'historical_file_overrides': {name: revision for name, (revision, _) in overrides.items()},
        'only_runtime_instrumentation': 'pool checkout/checkin observer; no budgets, delivery or ledger changes',
    }, indent=2), encoding='utf-8')
    print('VERIFIED', len(recorded), destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.revision, args.report, args.destination)
