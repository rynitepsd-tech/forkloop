import asyncio
import json
import multiprocessing
from pathlib import Path

import httpx
import pytest

from forkloop.spending import SessionLedger,BudgetExceeded
from forkloop.policies.student import StudentPolicy
from forkloop.types import Observation
from forkloop.metrics import summarize_episodes,episode_tokens
from tests.test_metrics_cost import _ep
from tests.test_observation_contract import png


def _reserve_in_process(path):
    try:
        SessionLedger(path).reserve('openai',1,label='parallel')
        return True
    except BudgetExceeded: return False


def test_process_safe_reservations_and_nontransferable_services(tmp_path):
    p=tmp_path/'ledger.sqlite'
    ledger=SessionLedger.create(p,limits={'openai':{'ceiling':4,'stop':3},'solari':{'ceiling':10,'stop':8},'gpu':{'ceiling':0,'stop':0}})
    with multiprocessing.get_context('spawn').Pool(4) as pool:
        assert sum(pool.map(_reserve_in_process,[str(p)]*8))==3
    s=ledger.summary()['services']; assert s['openai']['pending_upper_usd']==3
    with pytest.raises(BudgetExceeded): ledger.reserve('openai',.01,label='over stop')
    with pytest.raises(BudgetExceeded): ledger.reserve('gpu',.01,label='not authorized')
    ledger.reserve('openai',1,label='cleanup',cleanup=True)
    with pytest.raises(BudgetExceeded): ledger.reserve('openai',.01,label='over ceiling',cleanup=True)
    with pytest.raises(FileExistsError): SessionLedger.create(p)


def test_uncertain_calls_retain_reservation_and_settlement_is_idempotent(tmp_path):
    l=SessionLedger.create(tmp_path/'ledger.sqlite')
    op=l.reserve('openai',.5,label='one')
    l.reconcile(op,None,status='network_timeout')
    assert l.summary()['services']['openai']['pending_upper_usd']==.5
    l.reconcile(op,.03,status='response_usage')
    l.reconcile(op,.03,status='response_usage')
    assert l.summary()['services']['openai']['actual_usd']==.03
    with pytest.raises(ValueError): l.reconcile(op,0,status='cannot_refund')
    for bound in [float('nan'),float('inf'),-1,0]:
        with pytest.raises(ValueError): l.reserve('openai',bound,label='bad')


def test_empty_authorization_does_not_grant_default_budgets(tmp_path):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite", limits={})
    with pytest.raises(BudgetExceeded):
        ledger.reserve("openai", .01, label="unauthorized")


def test_observed_exposure_exceeding_bound_stays_pending_and_blocks_service(tmp_path):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite", limits={
        "solari": {"ceiling": 10, "stop": 10}, "openai": {"ceiling": 1, "stop": 1},
    })
    operation = ledger.reserve("solari", .5, label="machine")
    ledger.retain_exposure(operation, 2, evidence={"lifetime_seconds": 9 * 3600})
    ledger.retain_exposure(operation, .25)
    summary = ledger.summary()
    assert summary["services"]["solari"]["pending_upper_usd"] == 2
    assert summary["services"]["solari"]["actual_usd"] == 0
    assert summary["services"]["solari"]["ceiling_usd"] == 10
    assert json.loads(summary["operations"][0]["evidence"])["lifetime_seconds"] == 9 * 3600
    assert ledger.headroom()["solari"]["blocked"] is True
    ledger.reserve("openai", .1, label="unaffected-service")
    with pytest.raises(BudgetExceeded):
        ledger.reserve("solari", .01, label="next", cleanup=True)
    ledger.reconcile(operation, 1.5, status="authoritative_invoice")
    with pytest.raises(BudgetExceeded):
        ledger.reserve("solari", .01, label="settlement-does-not-repair-bound")
    with pytest.raises(ValueError):
        ledger.retain_exposure(operation, 3)


async def test_unpriced_service_tier_is_rejected_before_request(tmp_path):
    calls = []
    policy = StudentPolicy("https://api.openai.com/v1", "gpt-5.6-luna",
                           session_ledger=str(tmp_path / "unused.sqlite"),
                           transport=httpx.MockTransport(lambda request: calls.append(request)))
    try:
        with pytest.raises(ValueError, match="service_tier"):
            await policy._post({"model": "gpt-5.6-luna", "max_tokens": 100, "service_tier": "priority"})
        assert calls == []
    finally:
        await policy.aclose()


async def test_malformed_provider_usage_never_releases_reservation(tmp_path):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite")

    def respond(request):
        assert json.loads(request.content)["service_tier"] == "default"
        return httpx.Response(200, json={"choices": [], "usage": {
            "prompt_tokens": 1000.5, "completion_tokens": 20,
        }})

    policy = StudentPolicy("https://api.openai.com./v1", "gpt-5.6-luna",
                           session_ledger=str(ledger.path), transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ValueError):
            await policy._post({"model": "gpt-5.6-luna", "max_tokens": 100})
        summary = ledger.summary()["services"]["openai"]
        assert summary["actual_usd"] == 0
        assert summary["pending_upper_usd"] == pytest.approx((1_050_000 * .5 + 100 * 1.8) / 1e6)
        assert policy.usage == {"in": 0, "out": 0}
    finally:
        await policy.aclose()


async def test_openai_reserves_before_request_counts_discarded_output_and_failed_call(tmp_path):
    ledger=SessionLedger.create(tmp_path/'ledger.sqlite'); calls=[]
    def respond(req):
        assert ledger.summary()['services']['openai']['pending_upper_usd']>0
        calls.append(req)
        if len(calls)==1:
            return httpx.Response(200,json={'id':'test','choices':[],'usage':{'prompt_tokens':1000,'completion_tokens':20}})
        raise httpx.ReadTimeout('test')
    pol=StudentPolicy('https://api.openai.com/v1','gpt-5.6-luna',hosted_reasoning=True,max_tokens=100,
                      session_ledger=str(ledger.path),transport=httpx.MockTransport(respond))
    obs=Observation(png('white'),'synthetic',0,[],1280,720)
    try:
        await pol.act(obs); await pol.act(obs)
        s=ledger.summary()['services']['openai']
        assert s['attempts']==2 and s['actual_usd']>0 and s['pending_upper_usd']>0
        assert pol.usage=={'in':1000,'out':20}
    finally: await pol.aclose()


async def test_openai_cache_writes_are_reserved_and_charged_at_long_context_rate(tmp_path):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite")
    expected = (1_000_000 * .4 * 1.25 + 100 * 1.8) / 1e6

    def respond(request):
        assert ledger.summary()["services"]["openai"]["pending_upper_usd"] >= expected
        return httpx.Response(200, json={
            "choices": [], "usage": {
                "prompt_tokens": 1_000_000, "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1_000_000},
            },
        })

    policy = StudentPolicy("https://api.openai.com/v1", "gpt-5.6-luna",
                           hosted_reasoning=True, max_tokens=100,
                           session_ledger=str(ledger.path), transport=httpx.MockTransport(respond))
    try:
        await policy._post({"model": "gpt-5.6-luna", "max_completion_tokens": 100})
        assert ledger.summary()["services"]["openai"]["actual_usd"] == pytest.approx(expected)
        assert policy.usage["in"] == 0
        assert policy.usage["cache_write"] == 1_000_000
    finally:
        await policy.aclose()


async def test_gpt6_luna_is_reserved_and_charged_at_its_own_rates(tmp_path):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite")
    reserved = (1_050_000 * .2 * 1.25 + 100 * .75) / 1e6

    def respond(request):
        assert ledger.summary()["services"]["openai"]["pending_upper_usd"] == pytest.approx(reserved)
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 10_000, "completion_tokens": 100}})

    policy = StudentPolicy("https://api.openai.com/v1", "gpt-6-luna", hosted_reasoning=True, max_tokens=100,
                           session_ledger=str(ledger.path), transport=httpx.MockTransport(respond))
    try:
        await policy._post({"model": "gpt-6-luna", "max_completion_tokens": 100})
        assert ledger.summary()["services"]["openai"]["actual_usd"] == pytest.approx((10_000 * .1 + 100 * .5) / 1e6)
        with pytest.raises(ValueError, match="no verified conservative bound"):
            await policy._post({"model": "gpt-6-sol", "max_completion_tokens": 100})
    finally:
        await policy.aclose()


def test_measurements_count_secondary_failures_setup_and_losing_branches(tmp_path):
    ep=_ep([{'in':100,'out':10}],reward=0,wall=100)
    ep['dir']=tmp_path
    ep['reset']={'ok':True,'total_seconds':50}
    ep['verdict'].update(reason_code='WRONG_VALUE',failed=['target','collateral','forbidden'],details={
        'target':{'passed':False,'reason_code':'WRONG_VALUE'},
        'collateral':{'passed':False,'reason_code':'COLLATERAL_EDIT'},
        'forbidden':{'passed':False,'reason_code':'FORBIDDEN_SCREEN'}})
    child=tmp_path/'branches/loser'; child.mkdir(parents=True)
    (child/'steps.jsonl').write_text(json.dumps({'tokens':{'in':900,'out':50}})+'\n')
    assert episode_tokens(ep)['in']==900
    (tmp_path/'accounting.json').write_text(json.dumps({'experiment_tokens':{'in':1200,'out':90},'branch_resource_seconds':70}))
    s=summarize_episodes([ep],model='gpt-5.6-luna',vm_hour_usd=.134)
    assert s['tokens']['in']==1200
    assert s['collateral_edit_rate']['k']==1 and s['safety_failure_rate']['k']==1
    assert s['all_failure_codes']['FORBIDDEN_SCREEN']==1
    assert s['setup_seconds']==50 and s['branch_resource_seconds']==70
    assert s['cost_vm_usd']==round(220/3600*.134,4)
    assert s['cost_authoritative'] is False
