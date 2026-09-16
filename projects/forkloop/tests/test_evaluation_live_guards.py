import datetime,hashlib,json,time
from types import SimpleNamespace
import pytest
from forkloop.spending import SessionLedger
from scripts import lambda_development_eval as live

async def test_expired_deadline_refuses_before_ledger_or_network(tmp_path):
    args=SimpleNamespace(out=str(tmp_path/'never-created'),deadline='2026-01-01T00:00:00+00:00')
    with pytest.raises(ValueError,match='deadline'):await live.main(args)
    assert not (tmp_path/'never-created').exists()

async def test_unrelated_watchdog_cannot_authorize_vm(tmp_path,monkeypatch):
    ledger=SessionLedger.create(tmp_path/'ledger.sqlite');monkeypatch.setenv('FORKLOOP_SESSION_LEDGER',str(ledger.path))
    heartbeat=tmp_path/'watchdog.json';heartbeat.write_text(json.dumps({'utc':time.time(),'deadline':time.time()+5000,'ledger_sha256_tag':'unrelated'}))
    args=SimpleNamespace(out=str(tmp_path/'never-created'),plan='trained:200,base:200',deadline=datetime.datetime.fromtimestamp(time.time()+5100,datetime.timezone.utc).isoformat(),watchdog_heartbeat=str(heartbeat))
    with pytest.raises(ValueError,match='watchdog'):await live.main(args)
    assert ledger.summary()['services']['solari']['attempts']==0
