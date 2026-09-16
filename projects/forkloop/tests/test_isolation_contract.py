import asyncio
import copy
import json
from pathlib import Path

import pytest

from forkloop.actions import Action
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env
from forkloop.policies.base import BranchablePolicy
from forkloop.pool import WorkerPool
from forkloop.search import best_of_n,SearchStats
from forkloop.trajectories import Recorder
from forkloop.world import load_world
from train.eval import EvalConfig,run_eval


class Stateful(BranchablePolicy):
    branch_state_fields=('notes','queue','calls')
    name='stateful-test'
    def __init__(self):
        self.notes=[]; self.queue=[]; self.calls=0; self.usage={'in':0,'out':0}; self.events=[]
    async def act(self,obs):
        self.calls+=1; self.usage['in']+=10; self.usage['out']+=1
        if obs.step==0:
            self.notes.append('initial'); self.queue=['initial-only']
            return Action.click(1,1),{'confidence':0.1,'tokens':dict(self.usage)}
        self.events.append((list(self.notes),list(self.queue),self.calls,obs.step))
        self.notes.append('branch-rollout'); self.queue.clear()
        await asyncio.sleep(0)
        return Action.done(),{'tokens':dict(self.usage)}
    async def propose(self,obs,n):
        assert self.calls==0 and self.notes==[]  # before initial act, not after it
        self.calls=1; self.notes=['alternative']; self.queue=['alternative-only']
        self.usage['in']+=20; self.usage['out']+=2
        return [(Action.click(2,2),{'tokens':dict(self.usage),'_policy_state':self.snapshot_state()})]


@pytest.mark.parametrize('mode',['fork','revert'])
async def test_branch_state_independent_and_all_spend_survives_adoption(tmp_path,mode):
    world=load_world('toy-counter'); b=FakeBackend(base_dir=tmp_path/'fake',concurrency_cap=3,gui_factory=world.gui_factory())
    rec=Recorder(tmp_path/'runs',run_id=mode)
    env=Env(world,b,family='reach_target',settle_s=0,recorder=rec,budget_override={'max_steps':5,'max_seconds':20})
    policy=Stateful(); stats=SearchStats()
    try:
        await best_of_n(env,policy,2,1,mode=mode,branch_prob=1,stats=stats)
        assert sorted((e[0],e[1]) for e in policy.events)==[(['alternative'],['alternative-only']),(['initial'],['initial-only'])]
        assert all(e[2]==2 for e in policy.events)
        assert stats.experiment_tokens=={'in':50,'out':5}
        assert len(policy.notes)==2  # selected state, not both branches' mutations
        accounting=json.loads((rec.episodes()[0]/'accounting.json').read_text())
        assert accounting['experiment_tokens']==stats.experiment_tokens
        assert stats.snapshots_deleted==stats.snapshots and not stats.snapshot_delete_errors
        assert len(await b.list_machines())==1  # parent only
    finally: await env.close(); b.cleanup()


async def test_checkpoint_restores_charged_actions_waits_and_visual_history(tmp_path):
    w=load_world('toy-counter'); b=FakeBackend(base_dir=tmp_path/'fake',gui_factory=w.gui_factory())
    e=Env(w,b,settle_s=0,history_k=0,budget_override={'max_steps':10})
    try:
        await e.reset(1)
        for a in [Action.wait(.001),Action.click(1,1),Action.wait(.001)]: await e.step(a)
        cp=await e.checkpoint()
        assert cp.step==3 and cp.budget_steps==1
        await e.step(Action.click(2,2)); await e.step(None)
        obs=await e.restore(cp)
        assert e.ep.budget_steps==1 and e.ep.step==3 and e.ep.invalid==0
        assert e.ep.history==cp.history and obs.history==[]
        assert obs.previous_screenshot==cp.previous_screenshot
        assert not e.ep.terminated and not e.ep.truncated
        await b.delete_snapshot(cp.snapshot_id)
    finally: await e.close(); b.cleanup()


async def test_fork_inherits_limits_and_cleans_up_on_restore_failure(tmp_path,monkeypatch):
    import forkloop.search as search
    w=load_world('toy-counter'); b=FakeBackend(base_dir=tmp_path/'fake',concurrency_cap=3,gui_factory=w.gui_factory())
    e=Env(w,b,settle_s=0,stable_after_action=True,max_invalid=2,history_k=3,budget_override={'max_steps':7,'max_seconds':19})
    observed=[]
    async def rollout(sub,policy,obs,**kwargs):
        observed.append((sub.max_invalid,sub.stable_after_action,sub.history_k,dict(sub.budget_override),sub.ep.budget_steps))
        sub.budget_override['max_steps']=999
        raise RuntimeError('injected branch failure')
    monkeypatch.setattr(search,'_rollout',rollout)
    stats=SearchStats()
    try:
        with pytest.raises(RuntimeError,match='injected'):
            await best_of_n(e,Stateful(),2,1,mode='fork',branch_prob=1,stats=stats)
        assert len(observed)==2
        assert all(row==(2,True,3,{'max_steps':7,'max_seconds':19},0) for row in observed)
        assert e.budget_override['max_steps']==7
        assert len(await b.list_machines())==1
        assert stats.snapshots_deleted==stats.snapshots
    finally: await e.close(); b.cleanup()


async def test_concurrent_eval_factory_is_per_episode_and_failure_keeps_tokens():
    from tests.test_train import FakeEnv
    policies=[]; envs=[]; seen=set()
    class P:
        def __init__(self): self.queue=[]; self.usage={'in':0,'out':0}; self.closed=False; policies.append(self)
        async def act(self,obs):
            assert not self.queue
            self.queue.append(obs.instruction)
            seen.add(id(self)); self.usage={'in':12,'out':3}
            await asyncio.sleep(0)
            raise RuntimeError('after paid call')
        async def aclose(self): self.closed=True
    async def factory(family):
        e=FakeEnv(family=family,split='heldout_seeds',succeed_seeds=set()); envs.append(e); return e
    rows=[]
    cfg=EvalConfig(families=['resolve_denial'],seeds=[1,2,3],n_seeds=2,concurrency=3)
    result=await run_eval(cfg,factory,lambda repeat:P(),on_result=rows.append)
    assert len(policies)==6 and len(seen)==6
    assert all(p.closed for p in policies) and all(e.closed for e in envs)
    assert all(r['tokens_in']==12 and r['tokens_out']==3 and r['error'] for r in rows)
    assert result['n_errors']==6


async def test_unsupported_policy_fails_before_creating_resources(tmp_path):
    w=load_world('toy-counter'); b=FakeBackend(base_dir=tmp_path/'fake'); e=Env(w,b)
    try:
        with pytest.raises(TypeError,match='explicit'):
            await best_of_n(e,object(),2,1)
        assert not await b.list_machines()
    finally: await e.close(); b.cleanup()


async def test_pool_restart_has_one_slot_and_failed_cleanup_retains_handle(tmp_path):
    w=load_world('toy-counter'); b=FakeBackend(base_dir=tmp_path/'fake',gui_factory=w.gui_factory())
    pool=WorkerPool(b,w,size=1)
    try:
        worker=await pool.acquire(); await worker.restore(); await pool.release(worker)
        await pool.close(); await pool.start()
        worker=await pool.acquire()
        assert pool._free.empty()
        machine=await worker.restore(); real_kill=machine.kill
        async def fail(): raise RuntimeError('kill failed')
        machine.kill=fail
        with pytest.raises(Exception,match='cleanup failed'):
            await pool.close()
        assert worker.machine is machine
        machine.kill=real_kill
        await pool.close()
        assert worker.machine is None and not await b.list_machines()
    finally: await pool.close(); b.cleanup()


async def test_candidate_generation_obeys_trajectory_deadline(tmp_path):
    w=load_world('toy-counter'); b=FakeBackend(base_dir=tmp_path/'fake',gui_factory=w.gui_factory())
    e=Env(w,b,settle_s=0,budget_override={'max_seconds':.05})
    class Slow(Stateful):
        async def propose(self,obs,n):
            await asyncio.sleep(10)
            raise AssertionError('must be cancelled')
    stats=SearchStats()
    try:
        await asyncio.wait_for(best_of_n(e,Slow(),2,1,branch_prob=1,stats=stats),timeout=2)
        assert e.ep.truncated and stats.snapshots_deleted==stats.snapshots
    finally: await e.close(); b.cleanup()


async def test_eval_cli_carries_training_prompt_conventions(tmp_path):
    from train.eval import build_parser,make_policy_factory
    p=tmp_path/'prompt.md'; p.write_text('task system {fara_identity}\n{fara_tools}')
    args=build_parser().parse_args(['--prompt-style','fara','--coord-space','norm1000',
        '--system-prompt-file',str(p),'--instruction-note','visible credentials only','--nav-macro','--prev-shot'])
    factory,desc=make_policy_factory(args); pol=factory(0)
    try:
        assert pol.instruction_note=='visible credentials only' and pol.nav_macro
        assert desc['prev_screenshot'] and desc['system_prompt_file']==str(p)
    finally: await pol.aclose()
