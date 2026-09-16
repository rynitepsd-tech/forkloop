"""Freeze the paired document-magnification observations into a saved-evaluation package.

Reads the original frozen package (instruction, history, step, labels) and the controller-prepared
captures (standard / magnified document screenshots and the shared appeal-form screenshot), applies
the frozen eligibility rule symmetrically, and writes a package that `scripts.saved_fixed_eval`
accepts unchanged. The expected authorization is copied only into labels.jsonl (controller side).
"""
import argparse, hashlib, json, shutil
from pathlib import Path

from scripts.evaluation_contract import verify_dataset

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ('standard', 'magnified')


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def readl(p): return [json.loads(s) for s in Path(p).read_text().splitlines()]


def eligibility(ver: dict) -> list[str]:
    reasons = []
    prov = ver['provenance']
    if not ver.get('prepared'):
        reasons.append('harness reported not prepared')
    if not ver.get('viewed_doc_ids') or ver['viewed_doc_ids'][-1] != ver['target_doc_id']:
        reasons.append('viewed document id != recorded document id')
    doc = (ver.get('identity') or {}).get('openemr_document') or [{}]
    if doc[0].get('hash') != prov.get('doc_hash'):
        reasons.append('document hash mismatch')
    if not (ver.get('form') or {}).get('appeal_form_visited'):
        reasons.append('appeal form for the recorded claim not visited')
    if int((ver.get('identity') or {}).get('appeals_for_claim') or 0) != 0:
        reasons.append('an appeal already exists')
    for c in CONDITIONS:
        if (ver.get(c) or {}).get('toolbar_y') is None:
            reasons.append(f'viewer toolbar not detected in {c}')
    s, m = (ver.get('standard') or {}).get('text') or {}, (ver.get('magnified') or {}).get('text') or {}
    if not (s.get('median_row_height_px') and m.get('median_row_height_px') and m['median_row_height_px'] > s['median_row_height_px']):
        reasons.append('magnified text row height not larger than standard')
    return reasons


def main(a):
    orig = Path(a.original).resolve()
    manifest0, cases0, labels0 = verify_dataset(orig)
    obs = Path(a.observations).resolve()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'images').mkdir()
    auth = [(c, labels0[c['case_id']]) for c in cases0 if labels0[c['case_id']]['kind'] == 'authorization']
    auth.sort(key=lambda cl: cl[1]['seed'])
    cases, labels, assets, exclusions = [], [], {}, []
    for idx, (c, l) in enumerate(auth):
        seed = l['seed']
        sd = obs / f'seed-{seed:03d}'
        summary = json.loads((sd / 'summary.json').read_text()) if (sd / 'summary.json').exists() else None
        ad = sd / summary['attempt_dir'] if summary else None
        ver = json.loads((ad / 'verification.json').read_text()) if ad and (ad / 'verification.json').exists() else None
        if ver is None:
            exclusions.append({'seed': seed, 'source_case_id': c['case_id'], 'reasons': ['no prepared observation'], 'attempts': (summary or {}).get('attempts')})
            continue
        reasons = eligibility(ver)
        if reasons:
            exclusions.append({'seed': seed, 'source_case_id': c['case_id'], 'reasons': reasons, 'attempts': summary.get('attempts')})
            continue
        images = {}
        for name in ('standard', 'magnified', 'form'):
            b = (ad / f'{name}.png').read_bytes()
            h = hashlib.sha256(b).hexdigest()
            assert h == ver[name]['sha256'], f'{seed} {name} capture changed since verification'
            rel = f'images/{h}.png'
            if not (out / rel).exists():
                (out / rel).write_bytes(b)
            assets[rel] = {'sha256': h, 'source': str(ad.relative_to(ROOT) / f'{name}.png'), 'bytes': len(b), 'seed': seed, 'role': name}
            images[name] = rel
        order = CONDITIONS if idx % 2 == 0 else tuple(reversed(CONDITIONS))
        for cond in order:
            case_id = hashlib.sha256(f"{l['episode_id']}:{c['step']}:magnification-v1:{cond}".encode()).hexdigest()[:20]
            cases.append({'case_id': case_id, 'schema_version': c['schema_version'], 'instruction': c['instruction'], 'history': c['history'],
                          'step': c['step'], 'screen_size': c['screen_size'], 'images': [images[cond], images['form']],
                          'image_roles': c['image_roles'], 'image_steps': c['image_steps'], 'history_coordinate_space': c['history_coordinate_space']})
            labels.append({**l, 'case_id': case_id, 'condition': cond, 'source_case_id': c['case_id'], 'diagnostic': 'document-magnification-v1',
                           'observation': {'dir': str(ad.relative_to(ROOT)), 'document_sha256': ver[cond]['sha256'], 'form_sha256': ver['form']['sha256'],
                                           'toolbar_y': ver[cond]['toolbar_y'], 'text': ver[cond]['text'], 'controller_note': ver['controller_note'],
                                           'controller_actions': ver.get('controller_actions'), 'navigation_attempts': (ver.get('steps') or {}).get('openemr_navigation_attempts')},
                           'visible_evidence': [{'role': 'previous', 'condition': cond, 'sha256': ver[cond]['sha256'], 'path': str(ad.relative_to(ROOT) / f'{cond}.png')}]})
    for name, rows in (('cases.jsonl', cases), ('labels.jsonl', labels)):
        (out / name).write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
    (out / 'exclusions.json').write_text(json.dumps(exclusions, indent=2))
    shutil.copyfile(obs.parent / 'protocol.md', out / 'protocol.md')
    geometry = json.loads((obs / 'geometry-frozen.json').read_text()) if (obs / 'geometry-frozen.json').exists() else None
    manifest = {'schema': 'forkloop.saved-evaluation.v1', 'diagnostic': 'document-magnification-v1', 'cases': len(cases), 'episodes': len(cases) // 2,
                'authorization_cases': len(cases), 'navigation_cases': 0, 'conditions': list(CONDITIONS), 'planned_seeds': [l['seed'] for _, l in auth],
                'prepared_seeds': sorted({l['seed'] for l in labels}), 'excluded': exclusions, 'geometry': geometry,
                'original_package': {'path': str(orig.relative_to(ROOT)), 'manifest_sha256': digest(orig / 'manifest.json'), 'cases_sha256': digest(orig / 'cases.jsonl'), 'labels_sha256': digest(orig / 'labels.jsonl')},
                'assets': assets, 'files': {p.name: digest(p) for p in out.iterdir() if p.is_file()}, 'training': manifest0.get('training'),
                'visual_review': 'pending; review.json required before execution'}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'cases': len(cases), 'prepared_seeds': manifest['prepared_seeds'], 'excluded': [(e['seed'], e['reasons']) for e in exclusions]}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--original', default='runs/evaluation-readiness-20260906/saved-dev-v3')
    p.add_argument('--observations', required=True)
    p.add_argument('--out', required=True)
    main(p.parse_args())
