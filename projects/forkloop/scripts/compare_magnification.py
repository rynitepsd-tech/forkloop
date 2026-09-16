"""Pair the standard and magnified results of the document-magnification package per seed.

`scripts.compare_saved_evaluation` requires identical request/image hashes and therefore cannot pair
two conditions whose document image differs by design; this comparator pairs by seed, checks that
everything else (instruction/history text, form image, model identity, server-side image grid and
token accounting) is identical, and reports the frozen metrics with exact denominators.
"""
import argparse, json
from pathlib import Path

from scripts.evaluation_contract import sha, verify_dataset

METRICS = ['exact_authorization_emitted', 'exact_authorization_type_selected', 'exact_authorization_runtime_type']
DIAG = ['ambiguous', 'structured_parse_error', 'runtime_parse_error', 'unsupported_action', 'truncated', 'transport_error']


def typed_value(metrics: dict) -> str | None:
    fields = metrics.get('authorization_fields') or []
    return fields[0].get('value') if fields else None


def main(a):
    package = Path(a.package).resolve()
    manifest, cases, labels = verify_dataset(package)
    report = json.loads(Path(a.results).read_text())
    assert report['label'] == 'trained' and report['dataset_manifest_sha256'] == sha(package / 'manifest.json')
    assert report['decoding'] == {'temperature': 0, 'max_tokens': 512, 'best_of': 1}
    rows = {r['case_id']: r for r in report['results']}
    historical = {}
    if a.historical:
        hist = json.loads(Path(a.historical).read_text())
        hlab = {l['case_id']: l for l in (json.loads(s) for s in Path(a.historical_labels).read_text().splitlines())}
        for r in hist['results']:
            l = hlab.get(r['case_id'])
            if l and l['kind'] == 'authorization':
                historical[l['seed']] = {'typed': typed_value(r.get('metrics') or {}), 'metrics': {m: (r.get('metrics') or {}).get(m) for m in METRICS}}
    by_seed: dict[int, dict] = {}
    for c in cases:
        l = labels[c['case_id']]
        by_seed.setdefault(l['seed'], {})[l['condition']] = (c, l, rows.get(c['case_id']))
    pairs, unmatched = [], []
    counts = {m: {'standard': 0, 'magnified': 0, 'corrected': 0, 'broken': 0, 'unchanged_correct': 0, 'unchanged_wrong': 0} for m in METRICS}
    diag = {c: {d: 0 for d in DIAG} for c in ('standard', 'magnified')}
    for seed in sorted(by_seed):
        cell = by_seed[seed]
        if set(cell) != {'standard', 'magnified'} or any(v[2] is None or v[2].get('status') != 'completed' for v in cell.values()):
            unmatched.append({'seed': seed, 'reason': 'missing or incomplete result', 'status': {k: (v[2] or {}).get('status') for k, v in cell.items()}})
            continue
        (cs, ls, rs), (cm, lm, rm) = cell['standard'], cell['magnified']
        problems = []
        if (cs['instruction'], cs['history'], cs['step']) != (cm['instruction'], cm['history'], cm['step']):
            problems.append('text input differs')
        if cs['images'][1] != cm['images'][1]:
            problems.append('form image differs')
        if rs.get('model_identity') != rm.get('model_identity'):
            problems.append('model identity differs')
        ts, tm = (rs.get('raw_response') or {}).get('evaluation_trace') or {}, (rm.get('raw_response') or {}).get('evaluation_trace') or {}
        for key in ('image_grid_thw', 'input_tokens', 'prompt_sha256'):
            if ts.get(key) is None or tm.get(key) is None:
                problems.append(f'server telemetry missing: {key}')
            elif key != 'prompt_sha256' and ts[key] != tm[key]:
                problems.append(f'{key} differs: {ts[key]} vs {tm[key]}')
        if problems:
            unmatched.append({'seed': seed, 'reason': problems})
            continue
        ms, mm = rs['metrics'], rm['metrics']
        pair = {'seed': seed, 'expected': ls['expected_authorization'], 'standard': {'typed': typed_value(ms), 'action': (rs.get('action') or {}).get('type'), 'metrics': {m: ms.get(m) for m in METRICS}, 'errors': ms.get('incorrect_authorizations'), 'note': (rs.get('meta') or {}).get('note')},
                'magnified': {'typed': typed_value(mm), 'action': (rm.get('action') or {}).get('type'), 'metrics': {m: mm.get(m) for m in METRICS}, 'errors': mm.get('incorrect_authorizations'), 'note': (rm.get('meta') or {}).get('note')},
                'historical_frozen_eval': historical.get(seed), 'image_grid_thw': ts.get('image_grid_thw'), 'input_tokens': ts.get('input_tokens'),
                'text_row_height_px': {'standard': (ls['observation']['text'] or {}).get('median_row_height_px'), 'magnified': (lm['observation']['text'] or {}).get('median_row_height_px')}}
        for m in METRICS:
            s, g = bool(ms.get(m)), bool(mm.get(m))
            counts[m]['standard'] += s
            counts[m]['magnified'] += g
            counts[m]['corrected' if (not s and g) else 'broken' if (s and not g) else 'unchanged_correct' if s else 'unchanged_wrong'] += 1
        for cond, mt in (('standard', ms), ('magnified', mm)):
            for d in DIAG:
                diag[cond][d] += bool(mt.get(d))
        pairs.append(pair)
    hist_wrong = {s for s, h in historical.items() if h['metrics'].get('exact_authorization_runtime_type') is False}
    out = {'package_manifest_sha256': sha(package / 'manifest.json'), 'results_stop_reason': report.get('stop_reason'),
           'denominators': {'planned_seeds': manifest['planned_seeds'], 'prepared_seeds': manifest['prepared_seeds'], 'excluded': manifest['excluded'],
                            'planned_requests': len(cases), 'completed_requests': sum(1 for r in report['results'] if r.get('status') == 'completed'),
                            'matched_pairs': len(pairs), 'unmatched': unmatched},
           'metrics': counts, 'diagnostics': diag, 'pairs': pairs,
           'historical_six_errors': {'seeds': sorted(hist_wrong), 'pairs': [p for p in pairs if p['seed'] in hist_wrong]} if historical else None,
           'gate': {'rule': 'magnified exact_authorization_type_selected > standard AND corrected > broken AND no validity problem',
                    'magnified_gt_standard': counts['exact_authorization_type_selected']['magnified'] > counts['exact_authorization_type_selected']['standard'],
                    'corrected_gt_broken': counts['exact_authorization_type_selected']['corrected'] > counts['exact_authorization_type_selected']['broken'],
                    'validity_problems': [u for u in unmatched if isinstance(u.get('reason'), list)]}}
    out['gate']['passed'] = bool(out['gate']['magnified_gt_standard'] and out['gate']['corrected_gt_broken'] and not out['gate']['validity_problems'] and report.get('stop_reason') is None)
    Path(a.out).write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({'matched_pairs': len(pairs), 'metrics': counts, 'gate': {k: v for k, v in out['gate'].items() if k != 'validity_problems'}}, indent=1))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--package', required=True)
    p.add_argument('--results', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--historical', default='runs/frozen-v3-eval-20260906/fixed-download/fixed-results/trained/results.json')
    p.add_argument('--historical-labels', default='runs/evaluation-readiness-20260906/saved-dev-v3/labels.jsonl')
    main(p.parse_args())
