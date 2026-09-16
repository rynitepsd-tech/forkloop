"""Paired best-of-one live development evaluation; incurs only reserved Solari usage.

Runs on the Mac against two loopback model servers (base, trained). A predeclared
cell order completes one seed's pair before advancing (``--plan
trained:200,base:200,base:201,trained:201``). One session-owned golden machine is
reverted to the golden snapshot before every episode; a setup failure before any
model call gets at most one reserved replacement allocation. Once a model has
acted its episode is never rerun. Seeds 200-202 only; no training or sealed seeds.
"""
from __future__ import annotations
import asyncio
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

from forkloop.backends.solari import SolariBackend
from forkloop.env import Env, run_episode
from forkloop.pool import WorkerPool
from forkloop.reset import ResetError
from forkloop.spending import SessionLedger
from forkloop.trajectories import Recorder
from forkloop.world import load_world
from scripts.evaluation_contract import AuditedPolicy, validate_identity, make_policy
from scripts.lambda_score_development import observed_safety_failures

ROOT = Path(__file__).resolve().parents[1]
OUT = None
LABELS = ('base', 'trained')
SEEDS = (200, 201, 202)
#: 900 s execution + 120 s request overrun + 240 s setup; cleanup is one kill plus inventory.
CELL_S, CLEANUP_S = 1260, 600
COMPLETED, SETUP_FAILED, INCOMPLETE = 'completed', 'setup_failed_before_model', 'incomplete_model_episode'


class ProbePolicy(AuditedPolicy):
    async def act(self, obs):
        if self.n_requests >= 120:
            from forkloop.actions import Action
            return Action.done(False, note='predefined 120-call limit'), {'tokens': dict(self.usage)}
        action, meta = await super().act(obs)
        if meta.get('error'):
            raise RuntimeError('policy transport/runtime failure; no retry')
        return action, meta


class ProbeBackend(SolariBackend):
    async def create(self, **kwargs):
        machine = await super().create(**kwargs)
        try:
            result = await machine.exec('python3', ['-c',
                "import os,json; m=dict(x.split(':',1) for x in open('/proc/meminfo')); print(json.dumps({'cpu':os.cpu_count(),'mem_kb':int(m['MemTotal'].split()[0])}))"], timeout_ms=10000)
            shape = json.loads(result.stdout)
            (OUT / f'live-shape-{machine.id}.json').write_text(json.dumps(shape, indent=2))
            if result.exit_code or shape['cpu'] > 2 or shape['mem_kb'] > 4096 * 1024:
                raise RuntimeError('unexpected source snapshot resource shape; stop')
            return machine
        except BaseException:
            async with asyncio.timeout(5):
                await machine.kill()
            raise


def parse_plan(text):
    """``label:seed,...``; each seed's two cells are adjacent and cover both labels."""
    cells = []
    for item in text.split(','):
        label, seed = item.strip().split(':')
        if label not in LABELS or int(seed) not in SEEDS:
            raise ValueError(f'plan cell {item!r} outside base/trained x {SEEDS}')
        cells.append((label, int(seed)))
    if len(cells) != len(set(cells)):
        raise ValueError('plan repeats a cell')
    for i in range(0, len(cells), 2):
        group = cells[i:i + 2]
        if len(group) != 2 or group[0][1] != group[1][1]:
            raise ValueError('plan must pair both models on one seed before the next seed')
    return cells


async def sequence(cells, run_cell, *, remaining_seconds, max_recoveries=1):
    """Drive the predeclared cells; returns (attempts, stop_reason).

    ``run_cell(label, seed, attempt)`` returns a row whose ``status`` is
    completed / setup_failed_before_model / incomplete_model_episode. A setup
    failure is retried at most ``max_recoveries`` times per session; a model
    episode is never retried. A seed group starts only with time for both cells
    plus cleanup; its second cell runs only if the first produced model evidence;
    a later seed starts only when every earlier cell completed.
    """
    attempts, recoveries, stop = [], 0, None
    final = {}  # (label, seed) -> status of the last attempt
    for i, (label, seed) in enumerate(cells):
        first_of_group = i % 2 == 0
        if first_of_group and any(status != COMPLETED for status in final.values()):
            stop = f'earlier cell not completed; secondary seed {seed} not started'
            break
        if not first_of_group and attempts[-1]['status'] == SETUP_FAILED:
            stop = f'no model evidence for {cells[i - 1]}; partner cell {label}:{seed} not started'
            break
        need = (2 if first_of_group else 1) * CELL_S + CLEANUP_S
        if remaining_seconds() < need:
            stop = f'budget stop before {label}:{seed}: need {need}s, remaining {remaining_seconds():.0f}s'
            break
        attempt = 1
        while True:
            row = await run_cell(label, seed, attempt)
            row.update(label=label, seed=seed, attempt=attempt)
            attempts.append(row)
            final[(label, seed)] = row['status']
            if row['status'] != SETUP_FAILED or recoveries >= max_recoveries or remaining_seconds() < need:
                break
            recoveries += 1
            attempt += 1
            row['recovery_scheduled'] = True
        if attempts[-1]['status'] == SETUP_FAILED and not first_of_group:
            stop = f'setup failed for {label}:{seed} after {recoveries} recovery attempt(s)'
            break
    return attempts, stop


def baseline_digest(baseline):
    d = baseline.to_dict()
    tables = {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest() for k, v in d['tables'].items()}
    return {'tables': tables, 'watermarks': d['watermarks'],
            'preserved_rows_sha256': hashlib.sha256(json.dumps(d['preserved_rows'], sort_keys=True).encode()).hexdigest(),
            'baseline_sha256': hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()}


async def main(args):
    global OUT
    OUT = Path(args.out).resolve()
    deadline = datetime.datetime.fromisoformat(args.deadline).timestamp()
    if datetime.datetime.fromisoformat(args.deadline).tzinfo is None or deadline <= time.time() + 2 * CELL_S + CLEANUP_S:
        raise ValueError('explicit future timezone-aware deadline needs at least one pair plus cleanup')
    cells = parse_plan(args.plan)
    ledger = SessionLedger(os.environ['FORKLOOP_SESSION_LEDGER'])
    # A separately running session watchdog must precede every paid create.
    watchdog = json.loads(Path(args.watchdog_heartbeat).read_text())
    if watchdog.get('ledger_sha256_tag') != hashlib.sha256(str(ledger.path.resolve()).encode()).hexdigest()[:16] or time.time() - watchdog['utc'] > 30 or watchdog['deadline'] > deadline or watchdog['deadline'] < time.time() + 2 * CELL_S + CLEANUP_S:
        raise ValueError('missing/stale or incompatible provider watchdog')
    urls = {'base': args.base_url, 'trained': args.trained_url}
    identity, health = {}, {}
    for label, url in urls.items():
        check = make_policy(url, label)
        await check.aclose()
        async with httpx.AsyncClient(timeout=15) as client:
            health[label] = (await client.get(url.removesuffix('/v1') + '/health')).json()
        identity[label] = validate_identity(health[label].get('model_identity'), label)
    for k in ['base_files_sha256', 'serving_source_sha256']:
        if identity['base'][k] != identity['trained'][k]:
            raise ValueError(f'serving identity mismatch between variants: {k}')
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / 'server-health.json').write_text(json.dumps(health, indent=2))
    seeds = sorted({s for _, s in cells})
    plan = {'cells': [f'{l}:{s}' for l, s in cells], 'seeds': seeds, 'best_of': 1, 'max_steps': 120, 'max_calls': 120,
            'max_seconds': 900, 'deadline': deadline, 'reset_mode': 'revert', 'max_setup_recoveries': 1,
            'cell_reserve_seconds': CELL_S, 'cleanup_reserve_seconds': CLEANUP_S, 'model_identity': identity}
    (OUT / 'experiment-plan.json').write_text(json.dumps(plan, indent=2))
    run_id = args.run_id
    if Path(run_id).name != run_id:
        raise ValueError('run_id must be one directory name')
    for label in LABELS:
        if (ROOT / 'runs' / f'{run_id}-{label}').exists():
            raise ValueError('preserve previous run; this probe is not automatically repeatable')
    world = load_world('claims-ops-v1')
    backend = ProbeBackend(ready_timeout_s=90, call_timeout_ms=120000)
    # One worker, reverted to the golden snapshot before each episode. A refused
    # or timed-out revert fails the cell's setup instead of silently switching the
    # run to per-episode forks; recovery is a bounded replacement allocation.
    pool = WorkerPool(backend, world, size=1, mode='revert', run_id=run_id, timeout_ms=1800000, fallback_to_fork=False,
                      max_retries=1, create_timeout_s=240, reap_orphans_enabled=False)
    meta = {'model': 'fara-v3', 'backend': 'solari', 'world': world.name, 'best_of': 1, 'concurrency': 1, 'seeds': seeds,
            'split': 'train', 'session_ledger': str(ledger.path), 'reset_mode': 'revert', 'plan': plan['cells'],
            'policy_options': {'prompt_style': 'fara', 'history_k': 8, 'prev_screenshot': True, 'max_tokens': 512, 'temperature': 0, 'nav_macro': True, 'coord_space': 'norm1000'},
            'budget_override': {'max_steps': 120, 'max_seconds': 900}, 'probe_plan': str(OUT / 'experiment-plan.json')}
    recorders = {label: Recorder(ROOT / 'runs', run_id=f'{run_id}-{label}', meta={**meta, 'weights_label': label, 'model_identity': identity[label]}) for label in LABELS}
    started = time.time()
    cleanup_errors, infrastructure = [], []
    remaining = lambda: deadline - time.time()

    def infra(event, **kw):
        row = {'utc': time.time(), 'event': event, **kw}
        infrastructure.append(row)
        with (OUT / 'infrastructure-events.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')

    async def run_cell(label, seed, attempt):
        worker = pool.workers[0]
        row = {'model_identity': identity[label], 'machine_before': worker.machine.id if worker.machine else None,
               'creates_before': backend.counters['create']}
        live = worker.machine is not None and await worker.machine.healthy()
        if not live and backend.counters['create'] >= 1 + plan['max_setup_recoveries']:
            # The initial allocation plus one replacement is the whole infrastructure budget.
            row.update(status=SETUP_FAILED, error='replacement allocation budget exhausted; no machine created',
                       calls=0, tokens={}, setup_and_episode_seconds=0.0, creates_after=backend.counters['create'])
            infra('replacement_refused', label=label, seed=seed, attempt=attempt, creates=backend.counters['create'])
            return row
        if live:
            try:
                async with asyncio.timeout(20):
                    row['lifetime_refresh'] = json.loads(json.dumps(await worker.machine.refresh_lifetime(1800000), default=str))
            except Exception as exc:
                row['lifetime_refresh'] = f'{type(exc).__name__}: {exc}'
        env = Env(world, backend, family='resolve_denial', split='train', pool=pool, recorder=recorders[label],
                  history_k=8, budget_override={'max_steps': 120, 'max_seconds': 900})
        pol = ProbePolicy(urls[label], 'fara-v3', weights_label=label, max_tokens=512, prompt_style='fara', coord_space='norm1000',
                          image_max_side=1280, history_k=8, prev_screenshot=True, nav_macro=True, timeout_s=120,
                          instruction_note='Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, click the Password field, type pass, click Login.',
                          system_prompt=(ROOT / 'forkloop/policies/prompts/fara_no_user_v1.md').read_text())
        before = time.time()
        try:
            async with asyncio.timeout(min(CELL_S, remaining() - CLEANUP_S)):
                verdict = await run_episode(env, pol, seed)
            row.update(verdict=verdict.to_dict(), status=COMPLETED)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
            row['status'] = SETUP_FAILED if pol.n_requests == 0 else INCOMPLETE
            row['reset_error'] = isinstance(exc, ResetError)
            infra('cell_failed', label=label, seed=seed, attempt=attempt, status=row['status'], error=row['error'],
                  machine=worker.machine.id if worker.machine else None)
        finally:
            row.update(calls=pol.n_requests, tokens=dict(pol.usage), setup_and_episode_seconds=time.time() - before,
                       reset_report=env.last_reset_report, creates_after=backend.counters['create'],
                       machine=env.ep.machine.id if env.ep else None, pool_events=list(pool.events))
            if env.ep:
                row.update(episode_id=env.ep.recorder.episode_id if env.ep.recorder else None,
                           baseline_digest=baseline_digest(env.ep.baseline), run_dir=str(recorders[label].dir))
                if env.ep.recorder:
                    (env.ep.recorder.dir / 'accounting.json').write_text(json.dumps({
                        'experiment_tokens': dict(pol.usage), 'setup_and_episode_seconds': row['setup_and_episode_seconds'],
                        'scope': 'all policy calls, including failed parse/transport'}, indent=2))
                    (env.ep.recorder.dir / 'baseline-digest.json').write_text(json.dumps(row['baseline_digest'], indent=2))
                    first = env.ep.recorder.dir / 'shots' / '000_before.png'
                    row['initial_observation_sha256'] = hashlib.sha256(first.read_bytes()).hexdigest() if first.exists() else None
            await pol.aclose()
            try:
                async with asyncio.timeout(30):
                    await env.close()  # releases the worker; the machine stays for the next golden revert
            except Exception as exc:
                cleanup_errors.append(f'env {label}:{seed}: {type(exc).__name__}: {exc}')
            if row['status'] == COMPLETED and 'SAFETY_CHECK_ERROR' in observed_safety_failures(row.get('verdict') or {}):
                row['status'] = INCOMPLETE
                row['error'] = 'oracle safety check error'
            print(json.dumps({'cell': f'{label}:{seed}', 'attempt': attempt, 'status': row['status'], 'calls': row['calls'],
                              'reward': (row.get('verdict') or {}).get('reward'), 'error': row.get('error')}), flush=True)
        return row

    attempts, stop = [], None
    try:
        attempts, stop = await sequence(cells, run_cell, remaining_seconds=remaining)
    except Exception as exc:
        stop = f'harness failure: {type(exc).__name__}: {exc}'
        infra('harness_failure', error=stop)
    finally:
        try:
            async with asyncio.timeout(30):
                await pool.close()
        except Exception as e:
            cleanup_errors.append(f'pool: {type(e).__name__}: {e}')
        # A timed-out create may have no local handle; only session-tagged resources may be killed.
        try:
            from scripts.evaluation_watchdog import cleanup
            remaining_machines = await cleanup(backend, ledger.path)
        except Exception as e:
            cleanup_errors.append(f'inventory: {type(e).__name__}: {e}')
            remaining_machines = None
        finally:
            await backend.close()
        (OUT / 'live-cleanup.json').write_text(json.dumps({'remaining': remaining_machines, 'cleanup_errors': cleanup_errors,
            'resource_estimates': backend.resources, 'creates': backend.counters['create'], 'total_wall_seconds': time.time() - started}, indent=2))
        (OUT / 'session-spend.json').write_text(json.dumps(ledger.summary(), indent=2))
        for label, rec in recorders.items():
            rec.update_meta(resource_estimates=backend.resources, cleanup_errors=cleanup_errors, pool_events=pool.events)
        done = {(a['label'], a['seed']) for a in attempts if a['status'] == COMPLETED}
        (OUT / 'live-results.json').write_text(json.dumps({
            'plan': plan['cells'], 'attempts': attempts, 'stop_reason': stop, 'started_at': started, 'model_identity': identity,
            'completed_cells': [f'{l}:{s}' for l, s in cells if (l, s) in done],
            'missing_cells': [f'{l}:{s}' for l, s in cells if (l, s) not in done],
            'infrastructure_events': infrastructure, 'cleanup_errors': cleanup_errors}, indent=2))
        print(json.dumps({'stop_reason': stop, 'completed': sorted(f'{l}:{s}' for l, s in done)}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--plan', default='trained:200,base:200,base:201,trained:201')
    p.add_argument('--base-url', default='http://127.0.0.1:8011/v1')
    p.add_argument('--trained-url', default='http://127.0.0.1:8012/v1')
    p.add_argument('--out', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--deadline', required=True)
    p.add_argument('--watchdog-heartbeat', required=True)
    asyncio.run(main(p.parse_args()))
