"""Derive capacity comparisons and evidence references; never fill missing data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.measure_capacity import summarize  # noqa: E402


def read_runs(directory):
    return [(path, json.loads(path.read_text(encoding='utf-8'))) for path in sorted(directory.glob('*.json'))]


def generate(root, junit):
    sides = {}
    metadata = {}
    for side, directory in [('before', root/'before_v3'), ('after', root/'after')]:
        sides[side] = {}
        metadata[side] = []
        for path, report in read_runs(directory):
            if report['status'] != 'passed':
                continue
            config = next(s for s in report['steps'] if s['name'] == 'capacity_configuration')
            observation = next(s for s in report['steps'] if s['name'] == 'capacity_observations')
            observation = dict(observation)
            observation['launcher_only_peak_rss_bytes'] = observation.pop('peak_rss_bytes', None)
            observation['actual_model_process_peak_rss_bytes'] = None
            observation['resource_measurement_limit'] = 'These runs sampled Windows launcher PIDs; model process RSS/CPU is unavailable. Pool events are independently recoverable by actual PID. No rerun requested.'
            pool_root = Path(config.get('pool_observation_dir') or '')
            observation['pool_peaks_by_actual_pid'] = {p.stem: max((json.loads(line)['checked_out'] for line in p.read_text().splitlines()), default=0)
                                                       for p in pool_root.glob('*.jsonl')}
            seed = next(s for s in report['steps'] if s['name'] == 'upload_to_published')
            metadata[side].append({'path': str(path), 'run_id': report['run_id'],
                                   'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'fixture': config['fixture'], 'seed_sha256': seed['markdown_sha256'],
                                   'cpu': config['cpu'], 'memory_bytes': config['memory_bytes'],
                                   'logical_cpu': config['logical_cpu'], 'physical_cpu': config['physical_cpu'],
                                   'startup_to_ready_seconds': config['warm_startup_seconds'],
                                   'threads': config['thread_configuration'], 'observations': observation})
            for batch in report['steps']:
                if batch['name'] != 'capacity_batch':
                    continue
                success = [r for r in batch['rows'] if r['outcome'] == 'success']
                sides[side][batch['phase']+':'+str(batch['concurrency'])] = {
                    'attempts': batch['attempts'], 'outcomes': batch['outcomes'],
                    'successful_per_second': batch['successful_per_second'],
                    'all_attempts_latency_ms': batch['latency_all_attempts_ms'],
                    'success_latency_ms': summarize(success, batch['wall_seconds'])['latency_all_attempts_ms'],
                    'stopped_increasing_concurrency': batch['stop']}
    if not metadata['before'] or not metadata['after']:
        raise ValueError('missing_completed_capacity_runs')
    seed_hashes = {r['seed_sha256'] for side in metadata.values() for r in side}
    if len(seed_hashes) != 1:
        raise ValueError('seed_workload_hash_mismatch')
    compare = [{'workload': key, 'before': sides['before'].get(key), 'after': sides['after'].get(key)}
               for key in sorted(set(sides['before']) | set(sides['after']))]
    tests = ET.parse(junit).getroot()
    suites = list(tests.iter('testsuite'))
    results = {key: sum(int(s.attrib.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    recovery = json.loads((root/'recovery/20260927_verified.json').read_text(encoding='utf-8'))
    summary = {'created_at': datetime.now(timezone.utc).isoformat(), 'capacity': compare, 'runs': metadata,
               'same_seed_payload_verified': True, 'tests': results, 'test_junit': str(junit),
               'recovery': recovery, 'currency_cost': None,
               'limits': ['small diagnostic corpus, not the historical 9229 chunks',
                          'closed-loop concurrency 1/2/4, only 2*concurrency attempts per phase',
                          'no production QPS/SLA or externally confirmed model prices',
                          'backup taken while source was quiescent; not online PITR']}
    (root/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'tests': results, 'recovery': recovery['status'], 'rows': compare}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('reports/stability_cost'))
    parser.add_argument('--junit', type=Path, required=True)
    args = parser.parse_args()
    generate(args.root, args.junit)
