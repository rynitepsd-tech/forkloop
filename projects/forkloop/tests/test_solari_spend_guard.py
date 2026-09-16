import json
from types import SimpleNamespace
import pytest
from forkloop.backends.solari import SolariBackend,SolariMachine
from forkloop.spending import SessionLedger


async def test_resource_reserved_before_create_lifetime_includes_setup_and_cleanup(tmp_path,monkeypatch,reviewed_solari_pricing,simulated_solari_lifetime):
    ledger=SessionLedger.create(tmp_path/'ledger.sqlite'); requests=[]
    class Client:
        async def create_desktop(self,**kw):
            assert ledger.summary()['services']['solari']['pending_upper_usd']>0
            requests.append(kw)
            return SimpleNamespace(id='owned-test-machine')
    async def connect(self,**kwargs): return None
    monkeypatch.setattr(SolariMachine,'connect',connect)
    b=SolariBackend.__new__(SolariBackend)
    b.session_ledger=str(ledger.path);b.plan='starter';b.kind='desktop';b._client=Client()
    b.counters={'create':0};b.resources={};b.ready_timeout_s=1
    m=await b.create(cpu=2,mem_mb=4096,timeout_ms=60000,from_snapshot='synthetic-snapshot')
    assert requests[0]['lifecycle']=={'onTimeout':'kill'}
    assert requests[0]['timeout_ms']==60000 and requests[0]['metadata']['spend_operation']
    b.resources[m.id]['started_at']-=50
    b.record_resource_closed(m.id)
    report=ledger.summary(); op=report['operations'][0]
    assert op['status']=='resource_closed_usage_pending'
    evidence=json.loads(op['evidence'])
    assert evidence['lifetime_seconds']>=50 and evidence['estimated_compute_usd']>0
    assert report['services']['solari']['pending_upper_usd']>0  # not an invented invoice


async def test_failed_create_retains_charge_and_no_ledger_refuses_before_network(tmp_path,reviewed_solari_pricing,simulated_solari_lifetime):
    class Client:
        async def create_desktop(self,**kwargs): raise TimeoutError('unknown remote outcome')
    b=SolariBackend.__new__(SolariBackend);b.session_ledger=None;b.plan='starter';b.kind='desktop';b._client=Client()
    with pytest.raises(Exception,match='require'): await b.create()
    ledger=SessionLedger.create(tmp_path/'ledger.sqlite');b.session_ledger=str(ledger.path)
    with pytest.raises(TimeoutError): await b.create()
    report=ledger.summary()
    assert report['services']['solari']['pending_upper_usd']>0
    assert report['operations'][0]['status']=='create_uncertain'


async def test_observed_lifetime_overrun_retains_exposure_and_blocks_create(tmp_path, monkeypatch, reviewed_solari_pricing, simulated_solari_lifetime):
    from forkloop.spending import BudgetExceeded

    ledger = SessionLedger.create(tmp_path / "ledger.sqlite")
    operation = ledger.reserve("solari", 0.7, label="machine_create")
    backend = SolariBackend.__new__(SolariBackend)
    backend.session_ledger = str(ledger.path)
    backend.plan = "starter"
    backend.kind = "desktop"
    backend.resources = {"owned": {"operation": operation, "started_at": 1000,
                                   "hourly_usd": 0.134, "closed": False}}
    with monkeypatch.context() as clock:
        clock.setattr("forkloop.backends.solari.time.time", lambda: 1000 + 10 * 3600)
        backend.record_resource_closed("owned")
    service = ledger.summary()["services"]["solari"]
    assert service["actual_usd"] == 0
    assert service["pending_upper_usd"] == pytest.approx(1.34)
    assert service["blocked"] is True
    # No SDK client is present: the invalid bound must refuse before any request.
    with pytest.raises(BudgetExceeded):
        await backend.create()


@pytest.mark.parametrize("kind", ["desktop", "sandbox"])
async def test_new_ledger_cannot_bypass_unresolved_provider_lifetime(tmp_path, reviewed_solari_pricing, kind):
    from forkloop.spending import BudgetExceeded

    class Client:
        async def create(self, **kwargs):
            pytest.fail("unbounded allocation reached the provider")

        create_desktop = create

    ledger = SessionLedger.create(tmp_path / "fresh.sqlite")
    backend = SolariBackend.__new__(SolariBackend)
    backend.session_ledger = str(ledger.path)
    backend.plan = "starter"
    backend.kind = kind
    backend._client = Client()
    with pytest.raises(BudgetExceeded):
        await backend.create()
    assert ledger.summary()["operations"] == []


async def test_standalone_spike_cannot_allocate_outside_ledger_guard():
    from forkloop.spending import BudgetExceeded
    from spikes._common import create_desktop

    with pytest.raises(BudgetExceeded):
        await create_desktop(None)
