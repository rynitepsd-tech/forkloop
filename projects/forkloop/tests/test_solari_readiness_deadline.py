import asyncio
import time
from types import SimpleNamespace
import pytest
from forkloop.backends.solari import SolariMachine, SolariBackend, BackendError
from forkloop.spending import SessionLedger

class Desktop:
    id = 'disposable'
    def __init__(self, hang): self.hang=hang; self.closed=0; self.killed=0; self.canceled=[]
    async def op(self, name):
        if self.hang == name:
            try: await asyncio.sleep(20)
            finally: self.canceled.append(name)
    async def connect(self): await self.op('connect')
    async def reconnect(self): await self.op('reconnect')
    async def close(self): self.closed+=1; await self.op('close')
    async def health(self):
        await self.op('health')
        if self.hang in ('close','reconnect'): raise ConnectionError('lost')
        return SimpleNamespace(ready=True)
    async def kill(self): self.killed+=1; await self.op('kill')

@pytest.mark.parametrize('hang',['connect','health','close','reconnect'])
async def test_deadline_bounds_every_readiness_phase(hang):
    d=Desktop(hang);m=SolariMachine(d,None,(1280,720),{})
    start=time.monotonic()
    with pytest.raises(BackendError): await m.connect(wait_ready_s=.05)
    assert time.monotonic()-start < .25
    assert hang in d.canceled

async def test_external_cancel_propagates():
    d=Desktop('health');m=SolariMachine(d,None,(1280,720),{})
    task=asyncio.create_task(m.connect(wait_ready_s=20));await asyncio.sleep(.01);task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert d.canceled == ['health']

class DroppingDesktop:
    """Guest closes the control channel after each dial (_on_close → _ws=None) for `drops` dials."""
    id = 'disposable'
    def __init__(self, drops, dial_failures=0, health_error=None):
        self.drops=drops; self.dial_failures=dial_failures; self.health_error=health_error
        self.dials=0; self.closed=0; self.connected=False
    async def connect(self): await self._dial()
    async def reconnect(self): await self._dial()
    async def _dial(self):
        if self.dial_failures:
            self.dial_failures-=1; raise ConnectionError('WebSocket connect failed: 502 guest_unreachable')
        self.dials+=1; self.connected=self.dials>self.drops
    async def close(self): self.closed+=1; self.connected=False
    async def health(self):
        if self.health_error: raise self.health_error
        if not self.connected: raise ConnectionError('Not connected — call connect() first')
        return SimpleNamespace(ready=True)

async def test_channel_dropped_after_redial_is_redialed_again():
    # 2026-09-06 seed 201: one redial, then 85 s of "Not connected" polling. Two drops must now pass.
    d=DroppingDesktop(drops=2);m=SolariMachine(d,None,(1280,720),{})
    await m.connect(wait_ready_s=5)
    assert d.dials==3 and d.closed==2

async def test_failed_dial_is_retried_within_deadline():
    d=DroppingDesktop(drops=0,dial_failures=2);m=SolariMachine(d,None,(1280,720),{})
    await m.connect(wait_ready_s=5)
    assert d.dials==1

async def test_non_transport_health_failure_redials_once_only():
    d=DroppingDesktop(drops=0,health_error=RuntimeError('display not ready'));m=SolariMachine(d,None,(1280,720),{})
    with pytest.raises(BackendError, match='redials=1'): await m.connect(wait_ready_s=1.4)
    assert d.dials==2

async def test_lifetime_refresh_is_bounded():
    class D:
        id='disposable'
        async def set_timeout(self,ms): self.ms=ms; return {'ok':True}
    d=D();m=SolariMachine(d,None,(1280,720),{})
    assert await m.refresh_lifetime(1_800_000)=={'ok':True} and d.ms==1_800_000
    with pytest.raises(BackendError): await m.refresh_lifetime(1_800_001)

async def test_canceled_create_kills_and_preserves_reservation(tmp_path,reviewed_solari_pricing,simulated_solari_lifetime):
    d=Desktop('health')
    class Client:
        async def create_desktop(self,**kw): return d
    ledger=SessionLedger.create(tmp_path/'ledger.sqlite')
    b=SolariBackend.__new__(SolariBackend)
    b.session_ledger=str(ledger.path);b.plan='starter';b.kind='desktop';b._client=Client()
    b.counters={'create':0};b.resources={};b.ready_timeout_s=1
    task=asyncio.create_task(b.create(timeout_ms=60000));await asyncio.sleep(.02);task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert d.killed == 1 and b.resources[d.id]['closed']
    assert ledger.summary()['services']['solari']['pending_upper_usd']>0

async def test_failure_cleanup_also_bounded(tmp_path,reviewed_solari_pricing,simulated_solari_lifetime):
    d=Desktop('kill')
    async def bad_health(): raise RuntimeError('unready')
    d.health=bad_health
    class Client:
        async def create_desktop(self,**kw): return d
    ledger=SessionLedger.create(tmp_path/'ledger.sqlite');b=SolariBackend.__new__(SolariBackend)
    b.session_ledger=str(ledger.path);b.plan='starter';b.kind='desktop';b._client=Client()
    b.counters={'create':0};b.resources={};b.ready_timeout_s=.08
    start=time.monotonic()
    with pytest.raises(BackendError): await b.create(timeout_ms=60000)
    assert time.monotonic()-start < .3
    assert b.resources[d.id]['cleanup_error']=='TimeoutError'
    assert not b.resources[d.id]['closed']
