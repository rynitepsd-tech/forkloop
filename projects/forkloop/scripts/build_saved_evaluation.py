"""Offline candidate discovery and frozen, portable development assets. No inference."""
import argparse, hashlib, json, shutil
from pathlib import Path
from train.make_sft import Episode, episode_records
from forkloop.policies.action_parse import parse_compact

ROOT=Path(__file__).resolve().parents[1]
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def readl(p): return [json.loads(s) for s in Path(p).read_text().splitlines()]
def dump(p,data): Path(p).write_text(json.dumps(data,indent=2)+'\n')

def discover(out):
    training=readl(ROOT/'data/sft_f3_25_v3.jsonl')
    train_seeds={r['seed'] for r in training};train_eps={r['episode_id'] for r in training}
    train_auth={parse_compact(r['target'])[0]['text'] for r in training if r['target'].startswith('type("AUTH-')}
    train_images={digest(p) for p in {p for r in training for p in r['images']}}
    report=[];candidates=[];paths=set()
    for ep in sorted((ROOT/'runs/luna-v5-f3-s100-139/episodes').iterdir()):
        if not ep.is_dir(): continue
        m=json.loads((ep/'manifest.json').read_text());v=json.loads((ep/'verdict.json').read_text()) if (ep/'verdict.json').exists() else {}
        row={'episode_id':ep.name,'seed':m['seed'],'reward':v.get('reward'),'reasons':[]};report.append(row)
        if m.get('superseded'):row['reasons'].append('superseded')
        if v.get('reward')!=1:row['reasons'].append('not_successful')
        if m['seed'] in train_seeds or ep.name in train_eps:row['reasons'].append('training_seed_or_episode_overlap')
        if m['family']!='resolve_denial' or not 100<=m['seed']<=139:row['reasons'].append('out_of_scope')
        if row['reasons']: continue
        expected=m['expected']['auth_number']
        if expected in train_auth:row['reasons'].append('training_target_overlap');continue
        try:
            steps=readl(ep/'steps.jsonl')
            if any(s['i']!=i for i,s in enumerate(steps)):raise ValueError('noncontiguous steps')
            records=episode_records(Episode(ep.parent.parent,ep,m,v,steps),history_k=8,keep_invalid=False)
            auth=next(r for r in records if parse_compact(r['target'])[0]=={'type':'type','text':expected})
            navs=[]
            for r in records:
                a=parse_compact(r['target'])[0];s=steps[r['step']]
                if 10<=r['step']<=auth['step']-3 and a and a['type'] in ['click','scroll'] and a.get('y',0)>=120:
                    if any((t.get('action') or {}).get('type')=='type' and t['action'].get('text') in ['admin','pass'] for t in steps[max(0,r['step']-2):r['step']+3]): continue
                    if s.get('shot_after') and digest(ep/s['shot_before'])!=digest(ep/s['shot_after']):navs.append(r)
            if not navs:raise ValueError('no meaningful navigation candidate')
            nav=min(navs,key=lambda r:(abs(r['step']-auth['step']/2),r['step']))
            for r in [nav,auth]:
                if expected in json.dumps({'instruction':r['instruction'],'history':r['history']}):raise ValueError('expected authorization leaks in input text')
                if train_images.intersection(digest(p) for p in r['images']):raise ValueError('exact training image overlap')
            row.update(auth_step=auth['step'],navigation_step=nav['step'])
            candidate={'seed':m['seed'],'episode_id':ep.name,'expected':expected,'rows':[nav,auth],
                       'provenance':{str(p.relative_to(ROOT)):digest(p) for p in [ep/'manifest.json',ep/'verdict.json',ep/'steps.jsonl']},
                       'separation':{'seed':True,'episode':True,'target_value':True,'exact_images':True,'input_text_no_answer':True}}
            candidates.append(candidate);paths.update(auth['images'])
        except (ValueError,StopIteration,OSError,KeyError) as e:row['reasons'].append(str(e) or type(e).__name__)
    dump(out/'candidate-inventory.json',report);dump(out/'candidates.json',candidates);dump(out/'ocr-paths.json',sorted(paths))
    dump(out/'training-separation-reference.json',{'dataset':'data/sft_f3_25_v3.jsonl','sha256':digest(ROOT/'data/sft_f3_25_v3.jsonl'),'seeds':sorted(train_seeds),'episode_count':len(train_eps),'auth_target_count':len(train_auth),'image_count':len(train_images),'image_set_sha256':hashlib.sha256('\n'.join(sorted(train_images)).encode()).hexdigest()})
    print({'candidates':len(candidates),'ocr_images':len(paths),'episodes':len(report)})

def freeze(out):
    import re
    candidates=json.loads((out/'candidates.json').read_text());inventory=json.loads((out/'candidate-inventory.json').read_text())
    ocr={x['path']:x for x in json.loads((out/'authorization-ocr-verified.json').read_text())};eligible=[]
    if any('error' in x for x in ocr.values()): raise ValueError('OCR failed; do not classify unavailable OCR as absent evidence')
    for c in candidates:
        # Require a labeled authorization-number line, not a decoy/reference mention.
        evidence=[]
        for role,p in zip(c['rows'][1]['image_roles'],c['rows'][1]['images']):
            for line in ocr[p].get('lines',[]):
                if re.search(r'authorization\s+number\s*:\s*'+re.escape(c['expected'])+r'(?![A-Z0-9])',line,re.I):
                    evidence.append({'role':role,'path':p,'sha256':digest(p),'line':line})
        if not evidence:
            for review in json.loads((out/'manual-ocr-review.json').read_text()):
                if review['seed']==c['seed'] and review['manual_transcription']==c['expected']:
                    for role,p in zip(c['rows'][1]['image_roles'],c['rows'][1]['images']):
                        if digest(p)==review['image_sha256'] and role==review['role']:
                            evidence.append({'role':role,'path':p,'sha256':digest(p),'line':'Authorization number: '+review['manual_transcription'],'manual_review':review})
        record=next(x for x in inventory if x['episode_id']==c['episode_id'])
        if not evidence:record['reasons'].append('adjacent_images_lack_OCR_supported_authorization_number');continue
        c['visible_evidence']=evidence
        c['rank']=hashlib.sha256(f"forkloop-saved-dev-v1:{c['seed']}".encode()).hexdigest();eligible.append(c)
    chosen=sorted(eligible,key=lambda c:(c['rank'],c['episode_id']))[:20]
    selected={c['episode_id'] for c in chosen}
    for c in eligible:
        r=next(x for x in inventory if x['episode_id']==c['episode_id']);r['eligible']=True;r['selected']=c['episode_id'] in selected
        if not r['selected']:r['reasons'].append('eligible_outside_first_20_hash_rank')
    package=out/'saved-dev-v3';package.mkdir(exist_ok=False);(package/'images').mkdir()
    inputs=[];labels=[];assets={}
    fields=['schema_version','instruction','history','step','screen_size','image_roles','image_steps','history_coordinate_space']
    for c in sorted(chosen,key=lambda c:c['seed']):
        for kind,r in zip(['navigation','authorization'],c['rows']):
            cid=hashlib.sha256(f"{c['episode_id']}:{r['step']}".encode()).hexdigest()[:20]
            inp={k:r[k] for k in fields};inp['case_id']=cid;inp['images']=[]
            for p in r['images']:
                h=digest(p);dest=f'images/{h}.png';shutil.copyfile(p,package/dest);inp['images'].append(dest)
                assets[dest]={'sha256':h,'source':str(Path(p).relative_to(ROOT)),'bytes':Path(p).stat().st_size}
            inputs.append(inp)
            labels.append({'case_id':cid,'kind':kind,'seed':c['seed'],'episode_id':c['episode_id'],'step':r['step'],'expected_action':parse_compact(r['target'])[0],
                           'expected_authorization':c['expected'] if kind=='authorization' else None,'provenance':c['provenance'],'separation':c['separation'],
                           'visible_evidence':[{**e,'path':str(Path(e['path']).relative_to(ROOT))} for e in c['visible_evidence']] if kind=='authorization' else [],'rank':c['rank']})
    for name,rows in [('cases.jsonl',inputs),('labels.jsonl',labels)]:
        (package/name).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    dump(package/'exclusions.json',inventory);shutil.copyfile(out/'manual-ocr-review.json',package/'manual-ocr-review.json');shutil.copyfile(out/'selection-rule-v3.json',package/'selection-rule.json')
    manifest={'schema':'forkloop.saved-evaluation.v1','cases':len(inputs),'episodes':len(chosen),'authorization_cases':len(chosen),'navigation_cases':len(chosen),'eligible_episodes':len(eligible),'source_successful_episodes':len(candidates),'selected_seeds':sorted(c['seed'] for c in chosen),'assets':assets,
              'files':{p.name:digest(p) for p in package.iterdir() if p.is_file()},'training':json.loads((out/'training-separation-reference.json').read_text()),'visual_review':'pending; freeze requires review.json before GPU execution'}
    dump(package/'manifest.json',manifest);print({k:v for k,v in manifest.items() if k not in ['assets','files','training']})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['discover','freeze']);p.add_argument('--out',type=Path,required=True);a=p.parse_args();globals()[a.mode](a.out)
