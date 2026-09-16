"""Six fixed synthetic observations, current-only versus paired. No GUI actions execute.

Local endpoints are offline inference. api.openai.com incurs charges and requires
FORKLOOP_SESSION_LEDGER. Inputs use only the instruction, history and screenshots.
Expected values are read only after each response, for controller-side scoring.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from forkloop.policies.student import StudentPolicy
from forkloop.types import Observation


async def probe(args):
    import os
    records=[json.loads(line) for line in Path(args.data).read_text().splitlines()]
    template=Path(args.system_prompt_file).read_text()
    selected=[records[i] for i in (44,139,218)]
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists(): raise ValueError('preserve existing probe output; choose a new path')
    results=[]
    for rec in selected:
        for paired in [False,True]:
            pol=StudentPolicy(args.base_url,args.model,api_key=os.environ.get('OPENAI_API_KEY') if args.hosted else None,
                prompt_style='compact' if args.hosted else 'fara',coord_space='image' if args.hosted else 'norm1000',
                max_tokens=4096 if args.hosted else 256,hosted_reasoning=args.hosted,
                image_max_side=1280,history_k=8,prev_screenshot=paired,system_prompt=template,nav_macro=not args.hosted,
                extra_body={'reasoning_effort':'high'} if args.hosted else {},image_detail='high' if args.hosted else None,
                instruction_note=None if args.hosted else 'Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, click the Password field, type pass, click Login.',
                timeout_s=90)
            obs=Observation(Path(rec['images'][-1]).read_bytes(),rec['instruction'],rec['step'],rec['history'],
                            *rec['screen_size'],Path(rec['images'][0]).read_bytes() if paired else b'')
            try:
                request,_=pol.build_request(obs)
                action,meta=await pol.act(obs)
            finally: await pol.aclose()
            # Controller-only comparison; target/oracle never enters Observation.
            expected=json.loads((Path('runs')/rec['run_id']/'episodes'/rec['episode_id']/'manifest.json').read_text())['expected']['auth_number']
            results.append({'seed':rec['seed'],'step':rec['step'],'paired':paired,'action':action.to_dict() if action else None,
                            'correct_authorization_entry':bool(action and action.type=='type' and action.text==expected),
                            'meta':meta,'request_sha256':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),
                            'image_sha256':[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in rec['images'][-(2 if paired else 1):]]})
            out.write_text(json.dumps({'model':args.model,'base_url':args.base_url,'results':results,'max_calls':6,
                                      'max_output_tokens':4096 if args.hosted else 256,'training_performed':False},indent=2))
            print(json.dumps({k:results[-1][k] for k in ['seed','paired','action','correct_authorization_entry']}),flush=True)
            if meta.get('error'):
                break  # stop that pair after transport/runtime errors; do not retry
        if results[-1]['meta'].get('error'): break


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='data/sft_f3_25_v3.jsonl');p.add_argument('--base-url',required=True)
    p.add_argument('--model',required=True);p.add_argument('--out',required=True);p.add_argument('--hosted',action='store_true')
    p.add_argument('--system-prompt-file',required=True)
    asyncio.run(probe(p.parse_args()))
if __name__=='__main__': main()
