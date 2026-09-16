"""One reserved, no-model diagnostic: golden fork, then repeated golden reverts.

Tests the exact reuse contract of the paired live harness: each cycle runs the
real reset pipeline (restore, seeding, health, baseline, initial screen, stable
screen) on the same machine after ``revert(golden)``, then a harmless GUI Escape
and screenshot. Also exercises the kill-on-timeout refresh the harness re-arms
before every episode. Records readiness redials so a dropped control channel is
visible even when recovery succeeds. Seeds 140-142 are historical diagnostics.
"""
import asyncio, datetime, hashlib, importlib.metadata, io, json, os, time
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from forkloop.actions import Action
from forkloop.backends.base import apply_action
from forkloop.backends.solari import SolariBackend
from forkloop.reset import ResetController
from forkloop.spending import SessionLedger
from forkloop.world import load_world
from scripts.evaluation_watchdog import cleanup
from scripts.lambda_development_eval import baseline_digest

OUT = Path(os.environ['FORKLOOP_DIAGNOSTIC_OUT']).resolve()
SEEDS = [140, 141, 142]


def log(event, **kw):
    row = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': event, **kw}
    with (OUT / 'events.jsonl').open('a') as f:
        f.write(json.dumps(row, default=str) + '\n')
    print(json.dumps(row, default=str), flush=True)


async def timed(event, awaitable, limit):
    start = time.monotonic()
    try:
        async with asyncio.timeout(limit):
            value = await awaitable
        log(event, ok=True, seconds=time.monotonic() - start)
        return value
    except BaseException as e:
        log(event, ok=False, seconds=time.monotonic() - start, error_type=type(e).__name__, error=str(e))
        raise


async def main():
    ledger = SessionLedger(os.environ['FORKLOOP_SESSION_LEDGER'])
    b = SolariBackend(ready_timeout_s=90, call_timeout_ms=120000)
    world = load_world('claims-ops-v1')
    golden = world.golden_snapshot_id()
    watchdog = json.loads((OUT.parent / 'live-guard' / 'watchdog-heartbeat.json').read_text())
    assert watchdog['deadline'] > time.time() + 900 and time.time() - watchdog['utc'] < 30
    log('environment', versions={k: importlib.metadata.version(k) for k in ['solari-core', 'solari-sandbox', 'solari-desktop', 'websockets', 'httpx']},
        golden=golden, lifetime_ms=1800000, watchdog_deadline=watchdog['deadline'], seeds=SEEDS)
    before = await timed('snapshot_inventory_before', b.list_snapshots(), 20)
    (OUT / 'snapshots-before.json').write_text(json.dumps([r.__dict__ for r in before], indent=2))
    row = {'cycles': [], 'model_calls': 0}
    m = None
    try:
        m = await timed('backend_create_golden_fork', b.create(from_snapshot=golden, metadata={'run_id': OUT.name, 'diagnostic': 'paired-reuse'}, timeout_ms=1800000), 240)
        log('resource_identity', id=m.id, metadata=m.metadata, readiness_redials=getattr(m, 'readiness_redials', None))
        row['machine'] = m.id
        row['lifetime_refresh'] = await timed('lifetime_refresh', m.refresh_lifetime(1800000), 20)
        for cycle, seed in enumerate(SEEDS):
            async def restore():
                if cycle:
                    await m.revert(golden)
                return m
            worker = SimpleNamespace(pool=SimpleNamespace(mode='revert' if cycle else 'fork'), restore=restore)
            task = world.generate('resolve_denial', seed, 'train')
            outcome = await timed(f'reset_{seed}', ResetController(world).reset(worker, task), 400)
            (OUT / f'golden-reset-{seed}.png').write_bytes(outcome.screenshot)
            await timed('normal_ui_escape', apply_action(m, Action.key('Escape')), 30)
            aftershot = await timed('post_action_screenshot', m.screenshot(), 30)
            Image.open(io.BytesIO(aftershot)).verify()
            row['cycles'].append({'seed': seed, 'reset': outcome.report.to_dict(), 'baseline_digest': baseline_digest(outcome.baseline),
                                  'observation_sha256': hashlib.sha256(outcome.screenshot).hexdigest(),
                                  'post_action_sha256': hashlib.sha256(aftershot).hexdigest(),
                                  'readiness_redials': getattr(m, 'readiness_redials', None), 'reconnects': m.reconnects})
            (OUT / 'diagnostic-results.json').write_text(json.dumps(row, indent=2, default=str))
        # Same seed twice on the same machine: task state must be identical after a golden revert.
        cycle = len(SEEDS)
        async def restore_again():
            await m.revert(golden)
            return m
        worker = SimpleNamespace(pool=SimpleNamespace(mode='revert'), restore=restore_again)
        outcome = await timed(f'reset_{SEEDS[0]}_repeat', ResetController(world).reset(worker, world.generate('resolve_denial', SEEDS[0], 'train')), 400)
        repeat = baseline_digest(outcome.baseline)
        first = row['cycles'][0]['baseline_digest']
        row['repeat_equivalence'] = {'seed': SEEDS[0], 'tables_differing': [t for t in first['tables'] if first['tables'][t] != repeat['tables'].get(t)],
                                     'watermarks_equal': first['watermarks'] == repeat['watermarks'],
                                     'preserved_rows_equal': first['preserved_rows_sha256'] == repeat['preserved_rows_sha256'],
                                     'observation_equal': hashlib.sha256(outcome.screenshot).hexdigest() == row['cycles'][0]['observation_sha256'],
                                     'reset': outcome.report.to_dict()}
        (OUT / f'golden-reset-{SEEDS[0]}-repeat.png').write_bytes(outcome.screenshot)
        row['ok'] = True
    except Exception as e:
        row['error'] = f'{type(e).__name__}: {e}'
        row['ok'] = False
    finally:
        if m:
            try:
                await timed('direct_kill', m.kill(), 30)
            except Exception:
                pass
        row['remaining'] = await cleanup(b, ledger.path)
        row['resources'] = dict(b.resources)
        after = await timed('snapshot_inventory_after', b.list_snapshots(), 20)
        (OUT / 'snapshots-after.json').write_text(json.dumps([r.__dict__ for r in after], indent=2))
        row['golden_present'] = any(r.id == golden for r in after)
        row['snapshot_inventory_unchanged'] = sorted(r.id for r in before) == sorted(r.id for r in after)
        (OUT / 'diagnostic-results.json').write_text(json.dumps(row, indent=2, default=str))
        (OUT / 'session-spend.json').write_text(json.dumps(ledger.summary(), indent=2))
        log('diagnostic_end', ok=row.get('ok'), error=row.get('error'), remaining=row['remaining'])
        await b.close()


if __name__ == '__main__':
    asyncio.run(main())
