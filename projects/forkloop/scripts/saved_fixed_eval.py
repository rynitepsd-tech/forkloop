"""Inference-only matched saved observations. No Solari or hosted model calls."""
import argparse, asyncio, hashlib, json, time
from pathlib import Path
import httpx
from forkloop.types import Observation
from forkloop.fixed_metrics import score
from scripts.evaluation_contract import make_policy, verify_dataset, validate_identity, sha

async def run(a):
    package=Path(a.package).resolve();manifest,cases,labels=verify_dataset(package)
    out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    # Each result directory is an exclusive, auditable attempt; no automatic resume/retry.
    report={'label':a.label,'dataset_manifest_sha256':sha(package/'manifest.json'),'cases_sha256':sha(package/'cases.jsonl'),
            'labels_sha256':sha(package/'labels.jsonl'),'planned_case_ids':[c['case_id'] for c in cases],
            'decoding':{'temperature':0,'max_tokens':512,'best_of':1},'started_at':time.time(),'results':[]}
    deadline=time.monotonic()+a.max_seconds
    stop_reason=None
    def save(): (out/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    policy=make_policy(a.base_url,a.label)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            h=(await client.get(a.base_url.removesuffix('/v1')+'/health')).json()
        report['server_health']=h
        validate_identity(h.get('model_identity'),a.label)
    except Exception as e:stop_reason=f'identity_preflight: {type(e).__name__}: {e}'
    finally:await policy.aclose()
    for c in cases:
        row={'case_id':c['case_id'],'status':'missing','model_identity':report.get('server_health',{}).get('model_identity')}
        if not stop_reason and time.monotonic()+125>deadline:stop_reason='experiment_deadline_reserve'
        if stop_reason:
            row['error']=stop_reason;report['results'].append(row);save();continue
        policy=make_policy(a.base_url,a.label)
        try:
            images=[(package/p).read_bytes() for p in c['images']]
            obs=Observation(images[-1],c['instruction'],c['step'],c['history'],*c['screen_size'],images[0])
            request,_=policy.build_request(obs)
            row.update(request_sha256=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),image_sha256=[hashlib.sha256(b).hexdigest() for b in images])
            async with asyncio.timeout(min(120,deadline-time.monotonic())):
                action,meta=await policy.act(obs)
            got=action.to_dict() if action else None
            row.update(status='error' if meta.get('error') else 'completed',action=got,meta=meta,
                       raw_response=policy.last_response,metrics=score(meta.get('raw_action',''),got,meta,labels[c['case_id']]))
            if policy.last_response:row['model_identity']=validate_identity(policy.last_response.get('model_identity'),a.label)
            row['calls']=policy.n_requests
            if meta.get('error'):stop_reason='policy_transport_or_identity_failure; no retries'
        except Exception as e:
            row.update(status='error',error=f'{type(e).__name__}: {e}',raw_response=policy.last_response)
            stop_reason='runtime_failure; no retries'
        finally:await policy.aclose()
        report['results'].append(row);save()
        print(json.dumps({'case_id':row['case_id'],'status':row['status']}),flush=True)
    report.update(finished_at=time.time(),stop_reason=stop_reason);save()
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',required=True);p.add_argument('--out',required=True);p.add_argument('--label',choices=['base','trained'],required=True);p.add_argument('--base-url',default='http://127.0.0.1:8011/v1');p.add_argument('--max-seconds',type=float,required=True)
    asyncio.run(run(p.parse_args()))
