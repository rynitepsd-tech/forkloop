"""Two distinct, reserved, no-model diagnostics. Clean desktop then golden fork."""
import asyncio, datetime, hashlib, importlib.metadata, io, json, os, time
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from forkloop.backends.solari import SolariBackend, SolariMachine
from forkloop.spending import SessionLedger
from forkloop.world import load_world
from forkloop.reset import ResetController
from scripts.evaluation_watchdog import cleanup

OUT=Path(os.environ['FORKLOOP_DIAGNOSTIC_OUT']).resolve()
def log(event,**kw):
    row={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':event,**kw}
    with (OUT/'events.jsonl').open('a') as f: f.write(json.dumps(row)+'\n')
    print(json.dumps(row),flush=True)

async def timed(event,awaitable,limit=10):
    start=time.monotonic()
    try:
        async with asyncio.timeout(limit): value=await awaitable
        log(event,ok=True,seconds=time.monotonic()-start);return value
    except BaseException as e:
        log(event,ok=False,seconds=time.monotonic()-start,error_type=type(e).__name__,error=str(e));raise

original_connect=SolariMachine.connect
async def diagnostic_connect(self,*,wait_ready_s=60):
    log('resource_identity',id=self.id,metadata=self.metadata)
    # Inspect protocol shapes, never credentials or screenshot payloads.
    channel=self._d._channel;original_message=channel._on_message
    def message(chunk):
        log('ws_received',bytes=len(chunk),newline=chunk.endswith('\n'))
        original_message(chunk)
    channel._on_message=message
    async with asyncio.timeout(wait_ready_s):
        await timed('sdk_connect',self._d.connect(),15)
        try:
            h=await timed('sdk_health',self._d.health())
        except Exception:
            # SDK reconnect alone is known to be a no-op on a connected socket.
            await timed('sdk_force_close',self._d.close(),5)
            await timed('sdk_force_reconnect',self._d.reconnect(),15)
            h=await timed('sdk_health_after_reconnect',self._d.health())
        log('health_fields',ready=h.ready,display=h.display,vnc=h.vnc)
        if not h.ready: raise RuntimeError('SDK health did not declare ready')
        await timed('forkloop_wrapper_health',original_connect(self,wait_ready_s=10),10)

async def main():
    ledger=SessionLedger(os.environ['FORKLOOP_SESSION_LEDGER']);b=SolariBackend(ready_timeout_s=65,call_timeout_ms=10000)
    world=load_world('claims-ops-v1');golden=world.golden_snapshot_id()
    watchdog=json.loads((OUT/'watchdog-heartbeat.json').read_text())
    assert watchdog['deadline']>time.time()+600
    log('environment',versions={k:importlib.metadata.version(k) for k in ['solari-core','solari-sandbox','solari-desktop','websockets','httpx']},golden=golden,lifetime_ms=600000,watchdog_deadline=watchdog['deadline'])
    before=await timed('snapshot_inventory_before',b.list_snapshots(),20)
    (OUT/'snapshots-before.json').write_text(json.dumps([r.__dict__ for r in before],indent=2))
    original_create=b._client.create_desktop
    async def create(**kw): return await timed('provider_create',original_create(**kw),130)
    b._client.create_desktop=create
    SolariMachine.connect=diagnostic_connect
    results=[]
    try:
        for label,snapshot in [('clean',None),('golden',golden)]:
            row={'variant':label,'model_calls':0,'cycles':[]};m=None
            log('variant_start',variant=label)
            try:
                m=await timed('backend_create',b.create(from_snapshot=snapshot,metadata={'run_id':OUT.name,'diagnostic':label},timeout_ms=600000),210)
                shot=await timed('screenshot',m.screenshot());Image.open(io.BytesIO(shot)).verify();(OUT/f'{label}-desktop.png').write_bytes(shot)
                r=await timed('controller_exec',m.exec('true',timeout_ms=10000));assert r.exit_code==0
                row['desktop_ready']=True
                if label=='golden':
                    for cycle,seed in enumerate([140,141]):
                        async def restore():
                            if cycle: await m.revert(golden)
                            return m
                        worker=SimpleNamespace(pool=SimpleNamespace(mode='revert' if cycle else 'fork'),restore=restore)
                        outcome=await timed(f'reset_{seed}',ResetController(world).reset(worker,world.generate('resolve_denial',seed,'train')),180)
                        (OUT/f'golden-reset-{seed}.png').write_bytes(outcome.screenshot)
                        # Harmless Escape through the ordinary GUI action implementation.
                        from forkloop.actions import Action
                        from forkloop.types import Observation
                        obs=Observation(outcome.screenshot,world.generate('resolve_denial',seed,'train').instruction,0,[],1280,720)
                        from forkloop.backends.base import apply_action
                        await timed('normal_ui_escape',apply_action(m,Action.key('Escape')))
                        aftershot=await timed('post_action_screenshot',m.screenshot());Image.open(io.BytesIO(aftershot)).verify()
                        row['cycles'].append({'seed':seed,'reset':outcome.report.to_dict(),'observation_sha256':hashlib.sha256(obs.screenshot).hexdigest(),'post_action_sha256':hashlib.sha256(aftershot).hexdigest()})
            except Exception as e: row['error']=f'{type(e).__name__}: {e}'
            finally:
                if m:
                    try: await timed('direct_kill',m.kill(),15)
                    except Exception: pass
                row['remaining']=await cleanup(b,ledger.path)
                row['resources']=dict(b.resources)
                results.append(row);(OUT/'diagnostic-results.json').write_text(json.dumps(results,indent=2))
                log('variant_end',variant=label,desktop_ready=row.get('desktop_ready',False),error=row.get('error'),remaining=row['remaining'])
                if row['remaining']: break
    finally:
        SolariMachine.connect=original_connect
        remaining=await cleanup(b,ledger.path)
        after=await timed('snapshot_inventory_after',b.list_snapshots(),20)
        (OUT/'snapshots-after.json').write_text(json.dumps([r.__dict__ for r in after],indent=2))
        (OUT/'cleanup.json').write_text(json.dumps({'remaining':remaining,'resources':b.resources,'golden_present':any(r.id==golden for r in after)},indent=2))
        (OUT/'session-spend.json').write_text(json.dumps(ledger.summary(),indent=2))
        (OUT/'cleanup-requested').touch();await b.close()

if __name__=='__main__': asyncio.run(main())
