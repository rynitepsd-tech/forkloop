"""The predefined 2026-09-06 synthetic live probe. INCURS OPENAI + SOLARI CHARGES.

Two single-rollout family-3 development cases, seeds 140/141; no retries,
concurrency one, max 120 model calls and 900 execution seconds per case.
Requires the existing persistent FORKLOOP_SESSION_LEDGER and golden snapshot.
"""
from __future__ import annotations
import asyncio
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import time

from forkloop.backends.solari import SolariBackend
from forkloop.env import Env, run_episode
from forkloop.policies.student import StudentPolicy
from forkloop.pool import WorkerPool
from forkloop.spending import SessionLedger
from forkloop.trajectories import Recorder
from forkloop.world import load_world
from forkloop.metrics import failure_codes

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/overnight-repair-20260906'


class ProbePolicy(StudentPolicy):
    async def act(self,obs):
        if self.n_requests>=120:
            from forkloop.actions import Action
            return Action.done(False,note='predefined 120-call limit'),{'tokens':dict(self.usage)}
        action,meta=await super().act(obs)
        if meta.get('error'):
            raise RuntimeError('policy transport/runtime failure; no retry')
        return action,meta


class ProbeBackend(SolariBackend):
    async def create(self,**kwargs):
        machine=await super().create(**kwargs)
        try:
            result=await machine.exec('python3',['-c',
                "import os,json; m=dict(x.split(':',1) for x in open('/proc/meminfo')); print(json.dumps({'cpu':os.cpu_count(),'mem_kb':int(m['MemTotal'].split()[0])}))"],timeout_ms=10000)
            shape=json.loads(result.stdout)
            (OUT/f'live-shape-{machine.id}.json').write_text(json.dumps(shape,indent=2))
            if result.exit_code or shape['cpu']>2 or shape['mem_kb']>4096*1024:
                raise RuntimeError('unexpected source snapshot resource shape; stop')
            return machine
        except BaseException:
            await machine.kill()
            raise


async def main():
    ledger=SessionLedger(os.environ['FORKLOOP_SESSION_LEDGER'])
    if not (OUT/'probe-plan.json').is_file(): raise ValueError('predefined probe plan missing')
    run_id='overnight-paired-pipeline-20260906'
    if (ROOT/'runs'/run_id).exists(): raise ValueError('preserve previous run; this probe is not automatically repeatable')
    world=load_world('claims-ops-v1')
    backend=ProbeBackend(ready_timeout_s=90,call_timeout_ms=120000)
    pool=WorkerPool(backend,world,size=1,mode='fork',run_id=run_id,timeout_ms=1800000,
                    max_retries=1,create_timeout_s=240,reap_orphans_enabled=False)
    recorder=Recorder(ROOT/'runs',run_id=run_id,meta={'model':'gpt-5.6-luna','backend':'solari','world':world.name,
        'best_of':1,'concurrency':1,'seeds':[140,141],'split':'train','session_ledger':str(ledger.path),
        'policy_options':{'prompt_style':'compact','history_k':8,'prev_screenshot':True,'max_tokens':4096,'effort':'high'},
        'budget_override':{'max_steps':120,'max_seconds':900},'probe_plan':str(OUT/'probe-plan.json')})
    rows=[]; cleanup_errors=[]; started=time.time()
    try:
        for seed in [140,141]:
            env=Env(world,backend,family='resolve_denial',split='train',pool=pool,recorder=recorder,
                    history_k=8,budget_override={'max_steps':120,'max_seconds':900})
            pol=ProbePolicy('https://api.openai.com/v1','gpt-5.6-luna',os.environ['OPENAI_API_KEY'],
                max_tokens=4096,hosted_reasoning=True,extra_body={'reasoning_effort':'high'},history_k=8,
                prev_screenshot=True,image_detail='high',timeout_s=90,
                system_prompt=(ROOT/'forkloop/policies/prompts/hosted_gui_agent_v5.md').read_text())
            row={'seed':seed}; before=time.time()
            try:
                verdict=await run_episode(env,pol,seed)
                row.update(verdict=verdict.to_dict(),episode_id=env.ep.recorder.episode_id)
            except Exception as exc:
                row['error']=f'{type(exc).__name__}: {exc}'
            finally:
                row.update(calls=pol.n_requests,tokens=dict(pol.usage),setup_and_episode_seconds=time.time()-before)
                if env.ep and env.ep.recorder:
                    (env.ep.recorder.dir/'accounting.json').write_text(json.dumps({'experiment_tokens':dict(pol.usage),
                        'setup_and_episode_seconds':row['setup_and_episode_seconds'],'scope':'all policy calls, including failed parse/transport'},indent=2))
                await pol.aclose()
                await env.close()
                # End each machine's billed lifetime now; the next case gets its own fork.
                await pool.close()
                rows.append(row)
                (OUT/'live-pipeline-results.json').write_text(json.dumps({'rows':rows,'started_at':started},indent=2))
                print(json.dumps({'seed':seed,'calls':row['calls'],'reward':row.get('verdict',{}).get('reward'),'error':row.get('error')}),flush=True)
            safety=failure_codes({'verdict':row.get('verdict')}) & {'COLLATERAL_EDIT','WRONG_RECORD','DIRECT_DB_WRITE','FORBIDDEN_SCREEN','DUPLICATE_SIDE_EFFECT'}
            if row.get('error') or safety: break
    finally:
        try:
            await pool.close()
        except Exception as e: cleanup_errors.append(f'pool: {type(e).__name__}: {e}')
        # A timed-out create may have no local handle; only session-tagged resources may be killed.
        tag=hashlib.sha256(str(ledger.path.resolve()).encode()).hexdigest()[:16]
        try:
            resources=await backend.list_machines(metadata={'forkloop_session':tag})
            for resource in resources:
                if resource.metadata.get('forkloop_session')==tag:
                    await backend.kill_machine(resource.id)
            remaining=await backend.list_machines(metadata={'forkloop_session':tag})
        except Exception as e:
            cleanup_errors.append(f'inventory: {type(e).__name__}: {e}'); remaining=None
        finally: await backend.close()
        (OUT/'live-pipeline-cleanup.json').write_text(json.dumps({'remaining':None if remaining is None else [dataclasses.asdict(x) for x in remaining],
            'cleanup_errors':cleanup_errors,'resource_estimates':backend.resources,'total_wall_seconds':time.time()-started},indent=2))
        (OUT/'session-spend.json').write_text(json.dumps(ledger.summary(),indent=2))
        recorder.update_meta(resource_estimates=backend.resources,cleanup_errors=cleanup_errors)


if __name__=='__main__': asyncio.run(main())
