"""Match completed paired live cells; keep infrastructure attempts and missing cells explicit."""
import argparse, hashlib, json
from pathlib import Path
from scripts.evaluation_contract import validate_identity
from scripts.lambda_score_development import score

TASK_KEYS = ['seed', 'instruction', 'initial_screen', 'seeding', 'oracle', 'budget']
COMPLETED = 'completed'


def reset_equivalence(a, b):
    """Task-state equivalence of two resets, independent of machine ids and timings."""
    da, db = a.get('baseline_digest') or {}, b.get('baseline_digest') or {}
    tables = sorted(set(da.get('tables', {})) | set(db.get('tables', {})))
    differing = [t for t in tables if da.get('tables', {}).get(t) != db.get('tables', {}).get(t)]
    ra, rb = a.get('reset_report') or {}, b.get('reset_report') or {}
    return {'baseline_tables_compared': len(tables), 'baseline_tables_differing': differing,
            'watermarks_equal': da.get('watermarks') == db.get('watermarks'),
            'preserved_rows_equal': da.get('preserved_rows_sha256') == db.get('preserved_rows_sha256'),
            'initial_observation_equal': a.get('initial_observation_sha256') == b.get('initial_observation_sha256') and a.get('initial_observation_sha256') is not None,
            'reset_methods': [ra.get('method'), rb.get('method')], 'reset_ok': [ra.get('ok'), rb.get('ok')],
            'reset_stage_names_equal': [s.get('name') for s in ra.get('stages', [])] == [s.get('name') for s in rb.get('stages', [])],
            'machines': [a.get('machine'), b.get('machine')],
            'expected_differences': 'machine ids, restore timings, and pixel-level screenshot differences do not affect task state'}


def compare(out_dir, base_run, trained_run):
    results = json.loads((Path(out_dir) / 'live-results.json').read_text())
    identities = {l: validate_identity(results['model_identity'][l], l) for l in ['base', 'trained']}
    for k in ['base_files_sha256', 'serving_source_sha256']:
        if identities['base'][k] != identities['trained'][k]:
            raise ValueError(f'identity mismatch: {k}')
    runs = {'base': Path(base_run), 'trained': Path(trained_run)}
    metas = {l: json.loads((r / 'run.json').read_text()) for l, r in runs.items()}
    for k in ['policy_options', 'budget_override', 'seeds', 'best_of', 'concurrency', 'reset_mode']:
        if k not in metas['base'] or metas['base'][k] != metas['trained'].get(k):
            raise ValueError(f'configuration mismatch: {k}')
    scores = {l: score(r) for l, r in runs.items()}
    final = {}
    for a in results['attempts']:
        final[(a['label'], a['seed'])] = a
    seeds = sorted({int(c.split(':')[1]) for c in results['plan']})
    pairs, unmatched = [], []
    for seed in seeds:
        cells = {l: final.get((l, seed)) for l in ['base', 'trained']}
        metrics = {l: next((r for r in scores[l]['episodes'] if r['seed'] == seed), None) for l in ['base', 'trained']}
        if any(c is None or c['status'] != COMPLETED or not c.get('verdict') for c in cells.values()) or any(m is None for m in metrics.values()):
            unmatched.append({'seed': seed, 'reason': 'missing/error/incomplete cell; not a model task failure',
                              'cells': {l: None if c is None else {k: c.get(k) for k in ['status', 'error', 'calls', 'attempt']} for l, c in cells.items()}})
            continue
        fingerprints = {}
        for l, c in cells.items():
            m = json.loads((runs[l] / 'episodes' / c['episode_id'] / 'manifest.json').read_text())
            fingerprints[l] = hashlib.sha256(json.dumps({k: m[k] for k in TASK_KEYS}, sort_keys=True).encode()).hexdigest()
        equivalence = reset_equivalence(cells['base'], cells['trained'])
        if fingerprints['base'] != fingerprints['trained']:
            unmatched.append({'seed': seed, 'reason': 'task/reset input mismatch', 'fingerprints': fingerprints, 'reset_equivalence': equivalence})
            continue
        pairs.append({'seed': seed, 'task_sha256': fingerprints['base'], 'reset_equivalence': equivalence,
                      'base': metrics['base'], 'trained': metrics['trained']})
    attempts = [{k: a.get(k) for k in ['label', 'seed', 'attempt', 'status', 'error', 'calls', 'machine', 'creates_before', 'creates_after', 'setup_and_episode_seconds', 'recovery_scheduled']} for a in results['attempts']]
    completed = [c for c in results['plan'] if c in results['completed_cells']]
    return {'planned_cells': results['plan'], 'planned_pairs': len(seeds), 'completed_cells': completed, 'missing_cells': results['missing_cells'],
            'matched_pairs': len(pairs), 'unmatched_seeds': [u['seed'] for u in unmatched],
            'base_successes': sum(p['base']['task_success'] for p in pairs), 'trained_successes': sum(p['trained']['task_success'] for p in pairs),
            'pairs': pairs, 'unmatched': unmatched, 'infrastructure_attempts': attempts, 'stop_reason': results.get('stop_reason'),
            'infrastructure_events': results.get('infrastructure_events', []), 'all_model_metrics': scores,
            'scope': 'Best-of-one development pairs on a reverted golden machine; one or two pairs cannot establish reliable general workflow success.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out-dir', required=True, help='harness --out directory containing live-results.json')
    p.add_argument('--base-run', required=True)
    p.add_argument('--trained-run', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    r = compare(a.out_dir, a.base_run, a.trained_run)
    with Path(a.out).open('x') as f:
        json.dump(r, f, indent=2)
