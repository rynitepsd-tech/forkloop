"""Offline provenance audit for v3 paired observations. Controller report only.

Keeps original run/dataset files unchanged; records their SHA-256 digests and
checks every image boundary, source step and executed-action history.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from collections import Counter
from PIL import Image

from forkloop.actions import Action
from forkloop.policies.observation import OBSERVATION_SCHEMA


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(data: Path, original: Path, out: Path):
    records=[json.loads(line) for line in data.read_text().splitlines()]
    prior=[json.loads(line) for line in original.read_text().splitlines()]
    ids=lambda rows: {(r['run_id'],r['episode_id']) for r in rows}
    assert ids(records)==ids(prior), 'source episode set changed'
    assert {(r['run_id'],r['episode_id'],r['step']) for r in records}=={(r['run_id'],r['episode_id'],r['step']) for r in prior}
    allowed={'instruction','task_id','family','seed','split','episode_id','run_id','reward','schema_version','images',
             'image_roles','image_steps','screen_size','history_coordinate_space','history','target','step','reasoning','recipe'}
    image_hashes={}; episodes={}; sources={}; auth_entries=[]
    root=data.resolve().parent.parent
    for r in records:
        assert not (set(r)-allowed), set(r)-allowed
        assert r['schema_version']==OBSERVATION_SCHEMA
        assert r['split']=='train' and r['seed'] not in range(200,230) and r['seed']<100000
        key=(r['run_id'],r['episode_id']); ep=root/'runs'/r['run_id']/'episodes'/r['episode_id']
        if key not in episodes:
            steps=[json.loads(line) for line in (ep/'steps.jsonl').read_text().splitlines()]
            manifest=json.loads((ep/'manifest.json').read_text())
            verdict=json.loads((ep/'verdict.json').read_text()); assert verdict['reward']==1
            episodes[key]=(steps,manifest)
            for p in [ep/'manifest.json',ep/'steps.jsonl',ep/'verdict.json',root/'runs'/r['run_id']/'run.json']:
                sources[str(p.relative_to(root))]=digest(p)
        steps,manifest=episodes[key]; i=r['step']; assert steps[i]['i']==i
        selected=[i-1,i] if i else [0]
        assert r['image_steps']==selected
        assert r['image_roles']==(['previous','current'] if i else ['current'])
        assert r['images']==[str((ep/steps[j]['shot_before']).resolve()) for j in selected]
        expected_history=[Action.parse(s['action']).to_compact() if s.get('action') else s['raw_action'] for s in steps[:i]]
        assert r['history']==expected_history[-8:]
        assert r['instruction']==manifest['instruction']
        assert r['target']==Action.parse(steps[i]['action']).to_compact()
        for name in r['images']:
            p=Path(name); assert p.is_relative_to(ep) and p.is_file()
            if name not in image_hashes:
                with Image.open(p) as im: assert list(im.size)==r['screen_size']; im.verify()
                image_hashes[name]=digest(p)
    by_episode=[]
    for (run,eid),(steps,m) in sorted(episodes.items()):
        auth=m['expected']['auth_number']
        entry=next(r for r in records if r['run_id']==run and r['episode_id']==eid and auth in r['target'])
        auth_entries.append({'record_index':records.index(entry),'run_id':run,'episode_id':eid,'seed':m['seed'],'step':entry['step'],
                             'answer_in_instruction':auth in entry['instruction'],'answer_in_history':auth in '\n'.join(entry['history'])})
        by_episode.append({'run_id':run,'episode_id':eid,'seed':m['seed'],'task_id':m['task_id'],'world_version':m.get('world_version')})
    result={'schema_version':OBSERVATION_SCHEMA,'dataset':str(data.resolve()),'dataset_sha256':digest(data),
            'original_dataset':str(original.resolve()),'original_sha256':digest(original),'records':len(records),
            'episodes':by_episode,'source_files_sha256':sources,'images_sha256':image_hashes,
            'image_references':sum(len(r['images']) for r in records),'unique_images':len(image_hashes),
            'configuration':{'history_k':8,'history_coordinate_space':'screen','image_order':['previous','current'],
                             'first_step_images':['current'],'reasoning':'assistant target only'},
            'auth_entries':auth_entries,'policy_input_fields':['instruction','history','step','screen_size','images'],
            'all_files_exist':True,'source_boundaries_verified':True,'original_episode_set_preserved':True,
            'reserved_final_evaluation':{'split':'heldout_seeds','seeds':'100500-100529','used_in_this_session':False},
            'source_checkout':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
    out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ['records','unique_images','image_references','dataset_sha256']}))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--original',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args(); audit(args.data,args.original,args.out)
if __name__=='__main__': main()
