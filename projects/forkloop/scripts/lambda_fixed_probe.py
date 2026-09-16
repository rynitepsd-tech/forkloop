"""Predefined, externally scored paired-observation student probes."""
import argparse,asyncio,hashlib,json,math
from pathlib import Path
from forkloop.policies.student import StudentPolicy
from forkloop.policies.action_parse import parse_compact
from forkloop.types import Observation
from forkloop.fixed_metrics import score
NOTE='Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, click the Password field, type pass, click Login.'
async def run(a):
 rows=[json.loads(x) for x in Path(a.data).read_text().splitlines()];indices=[int(i) for i in a.indices.split(',')]
 out=Path(a.out);assert not out.exists();results=[]
 def make_policy(): return StudentPolicy(a.base_url,'fara-v3',prompt_style='fara',coord_space='norm1000',max_tokens=512,image_max_side=1280,history_k=8,prev_screenshot=not getattr(a,"current_only",False),system_prompt=Path('forkloop/policies/prompts/fara_no_user_v1.md').read_text(),nav_macro=True,instruction_note=NOTE,timeout_s=120)
 policy=None
 try:
  for i in indices:
   policy=make_policy()
   r=rows[i];obs=Observation(Path(r['images'][-1]).read_bytes(),r['instruction'],r['step'],r['history'],*r['screen_size'],Path(r['images'][0]).read_bytes() if len(r['images'])==2 else b'')
   request,_=policy.build_request(obs);action,meta=await policy.act(obs);await policy.aclose()
   # Labels and target values enter only this external scorer after generation.
   expected,err=parse_compact(r['target']);assert expected is not None,err
   got=action.to_dict() if action else None
   correct=bool(got and got['type']==expected['type'])
   if correct:
    if expected['type']=='click':correct=math.hypot(got['x']-expected['x'],got['y']-expected['y'])<=20
    elif expected['type']=='type':correct=got['text']==expected['text']
    elif expected['type']=='key':correct=got['keys']==expected['keys']
    else:correct=all(got.get(k)==v for k,v in expected.items())
   result={'index':i,'seed':r['seed'],'step':r['step'],'action':got,'expected_action':expected,'correct_action':correct,'authorization_example':expected['type']=='type' and expected.get('text','').startswith('AUTH-'),'meta':meta,'request_sha256':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),'image_sha256':[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (r['images'][-1:] if getattr(a,'current_only',False) else r['images'])]}
   result['metrics']=score(meta.get('raw_action',''),got,meta,{'kind':'authorization' if result['authorization_example'] else 'navigation','expected_action':expected,'expected_authorization':expected.get('text') if result['authorization_example'] else None})
   results.append(result);report={'label':a.label,'condition':'current-only' if getattr(a,'current_only',False) else 'paired','results':results,'correct_actions':sum(x['correct_action'] for x in results),'exact_authorizations':sum(x['correct_action'] and x['authorization_example'] for x in results),'decoding':{'temperature':0,'max_tokens':512},'data_sha256':hashlib.sha256(Path(a.data).read_bytes()).hexdigest()}
   out.write_text(json.dumps(report,indent=2));print(json.dumps({k:result[k] for k in ['index','action','correct_action','authorization_example']}),flush=True)
   if meta.get('error'):break
 finally:
  if policy is not None: await policy.aclose()
def main():
 p=argparse.ArgumentParser();p.add_argument('--data',default='data/sft_f3_25_v3.jsonl');p.add_argument('--indices',default='0,1,10,44,60,100,139,218');p.add_argument('--base-url',default='http://127.0.0.1:8011/v1');p.add_argument('--out',required=True);p.add_argument('--label',required=True);p.add_argument('--current-only',action='store_true');asyncio.run(run(p.parse_args()))
if __name__=='__main__':main()
