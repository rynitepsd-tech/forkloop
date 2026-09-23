import datetime as dt
import json
from types import SimpleNamespace

import pytest

from forkloop.backends.solari import BackendError, SolariBackend, SolariMachine
from forkloop.doctor import doctor, render_doctor
from forkloop.spending import BudgetExceeded, SessionLedger, load_solari_pricing


def test_pricing_renewal_cannot_authorize_unbounded_compute(tmp_path, monkeypatch):
    monkeypatch.delenv('FORKLOOP_SOLARI_PRICING_FILE', raising=False)
    with pytest.raises(ValueError):
        load_solari_pricing(today=dt.date(2026, 10, 1))
    original = load_solari_pricing(today=dt.date(2026, 9, 15))
    data = original.public_info()
    data.update(acknowledged=True, reviewed_on='2026-10-01', valid_until='2026-11-01')
    path = tmp_path / 'pricing.json'
    path.write_text(json.dumps(data))
    renewed = load_solari_pricing(path, today=dt.date(2026, 10, 1))
    with pytest.raises(BudgetExceeded):
        renewed.reservation(renewed.hourly(2, 4096, desktop=True))
    with pytest.raises(ValueError):
        load_solari_pricing(path, today=dt.date(2026, 11, 1))


@pytest.mark.parametrize('change', [
    {'acknowledged': False}, {'plan': 'professional'}, {'cpu_hour_usd': float('nan')},
    {'memory_gb_hour_usd': -1}, {'screen_hour_usd': True},
    {'valid_until': '2027-01-01'}, {'storage_starts_on': '2027-01-01'},
    {'reviewed_on': '2026-10-02'}, {'source': 'https://example.invalid/pricing'},
])
def test_invalid_or_unbounded_pricing_review_is_refused(tmp_path, monkeypatch, change):
    monkeypatch.delenv('FORKLOOP_SOLARI_PRICING_FILE', raising=False)
    data = load_solari_pricing(today=dt.date(2026, 9, 15)).public_info()
    data.update(acknowledged=True, reviewed_on='2026-10-01', valid_until='2026-11-01')
    data.update(change)
    path = tmp_path / 'pricing.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_solari_pricing(path, today=dt.date(2026, 10, 1))


async def test_fractional_memory_cannot_underreserve_before_create(tmp_path, reviewed_solari_pricing, simulated_solari_lifetime):
    class Client:
        async def create_desktop(self, **kwargs):
            pytest.fail('invalid shape reached paid create')
    ledger = SessionLedger.create(tmp_path / 'ledger.sqlite')
    backend = SolariBackend.__new__(SolariBackend)
    backend.session_ledger = str(ledger.path)
    backend.plan = 'starter'
    backend.kind = 'desktop'
    backend._client = Client()
    with pytest.raises(BackendError):
        await backend.create(cpu=2, mem_mb=4607)
    assert ledger.summary()['operations'] == []


async def test_compute_review_cannot_authorize_unbounded_storage(tmp_path, monkeypatch):
    monkeypatch.delenv('FORKLOOP_SOLARI_PRICING_FILE', raising=False)
    pricing = load_solari_pricing(today=dt.date(2026, 9, 15))
    data = pricing.public_info()
    data.update(acknowledged=True, reviewed_on=dt.date.today().isoformat(),
                valid_until=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
                storage_starts_on=min(dt.date.today(), dt.date(2026, 10, 1)).isoformat())
    path = tmp_path / 'pricing.json'
    path.write_text(json.dumps(data))
    class Desktop:
        id = 'synthetic'
        async def snapshot(self, name=None):
            pytest.fail('unbounded retained storage reached provider')
    machine = SolariMachine(Desktop(), SimpleNamespace(pricing_file=str(path)), (1280, 720), {})
    with pytest.raises(BackendError):
        await machine.snapshot()


async def test_offline_doctor_never_constructs_client_or_leaks_private_evidence(tmp_path, monkeypatch, reviewed_solari_pricing):
    import solari_sandbox
    def forbidden(*args, **kwargs):
        pytest.fail('offline doctor constructed a network client')
    monkeypatch.setattr(solari_sandbox.SandboxClient, '__init__', forbidden)
    monkeypatch.setenv('SOLARI_API_KEY', 'synthetic-private-key')
    monkeypatch.setenv('FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1', 'synthetic-private-snapshot')
    monkeypatch.setenv('SOLARI_BASE_URL', 'https://user:synthetic-auth@provider.invalid')
    ledger = SessionLedger.create(tmp_path / 'ledger.sqlite')
    ledger.reserve('solari', 1, label='test', evidence={'snapshot': 'synthetic-private-snapshot'})
    before = ledger.path.read_bytes()
    result = await doctor(session_ledger=ledger.path)
    text = json.dumps(result) + render_doctor(result)
    for private in ('synthetic-private-key', 'synthetic-private-snapshot', 'synthetic-auth', 'provider.invalid'):
        assert private not in text
    assert ledger.path.read_bytes() == before
    amounts = next(c for c in result['checks'] if c['name'] == 'ledger.solari')['details']['amounts']
    assert amounts['headroom_usd'] == 7
    assert result['mode'] == 'offline'


async def test_remote_doctor_failure_redacts_provider_error(monkeypatch, reviewed_solari_pricing):
    import solari_sandbox
    async def refused(self, **kwargs):
        raise RuntimeError('synthetic-secret snapshot-account-id https://u:p@private.invalid')
    monkeypatch.setattr(solari_sandbox.SandboxClient, 'list_snapshots', refused)
    monkeypatch.setenv('SOLARI_API_KEY', 'synthetic-secret')
    result = await doctor(remote=True, build=True)
    assert not result['ready']
    assert 'synthetic-secret' not in json.dumps(result)
    assert any(c['name'] == 'remote.solari' and c['status'] == 'fail' for c in result['checks'])


async def test_doctor_exposes_service_hold_without_private_operation_evidence(tmp_path, reviewed_solari_pricing):
    ledger = SessionLedger.create(tmp_path / "ledger.sqlite")
    operation = ledger.reserve("solari", .5, label="machine")
    ledger.retain_exposure(operation, 1.5, evidence={"machine_id": "private-machine-id"})
    result = await doctor(session_ledger=ledger.path)
    check = next(item for item in result["checks"] if item["name"] == "ledger.solari")
    assert check["status"] == "fail"
    assert check["details"]["amounts"]["blocked"] is True
    assert check["details"]["amounts"]["accounted_upper_usd"] == 1.5
    assert "private-machine-id" not in json.dumps(result)


async def test_doctor_refuses_new_ledger_without_inventing_a_cost_bound(tmp_path, reviewed_solari_pricing):
    ledger = SessionLedger.create(tmp_path / "fresh.sqlite")
    result = await doctor(session_ledger=ledger.path)
    check = next(item for item in result["checks"] if item["name"] == "solari.lifetime")
    assert check["status"] == "fail"
    assert not result["ready"]
    pricing = next(item for item in result["checks"] if item["name"] == "solari.pricing")
    assert "create_upper_usd" not in pricing["details"]
    assert ledger.summary()["operations"] == []
