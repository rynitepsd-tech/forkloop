"""Gateway cleanup of this ledger's resources only; never creates a resource."""
import argparse, asyncio, dataclasses, hashlib, json, time
from pathlib import Path
from forkloop.backends.solari import SolariBackend

async def cleanup(backend, ledger):
    tag=hashlib.sha256(str(Path(ledger).resolve()).encode()).hexdigest()[:16]
    async with asyncio.timeout(40):
        owned=[r for r in await backend.list_machines(metadata={'forkloop_session':tag}) if r.metadata.get('forkloop_session')==tag]
        for r in owned: await backend.kill_machine(r.id)
        remaining=[dataclasses.asdict(r) for r in await backend.list_machines(metadata={'forkloop_session':tag}) if r.metadata.get('forkloop_session')==tag]
    return remaining

async def run(a):
    b=SolariBackend(session_ledger=a.ledger,call_timeout_ms=10000)
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    try:
        while True:
            expired=time.time()>=a.deadline or (out/'cleanup-requested').exists()
            row={'utc':time.time(),'deadline':a.deadline,'expired':expired,'ledger_sha256_tag':hashlib.sha256(str(Path(a.ledger).resolve()).encode()).hexdigest()[:16]}
            if expired:
                try:
                    row['remaining']=await cleanup(b,a.ledger)
                    if not row['remaining']:
                        (out/'watchdog-cleanup.json').write_text(json.dumps(row,indent=2));return
                except Exception as e: row['error']=f'{type(e).__name__}: {e}'
            (out/'watchdog-heartbeat.json').write_text(json.dumps(row,indent=2))
            await asyncio.sleep(10)
    finally: await b.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--ledger',required=True);p.add_argument('--out',required=True);p.add_argument('--deadline',type=float,required=True)
    asyncio.run(run(p.parse_args()))
