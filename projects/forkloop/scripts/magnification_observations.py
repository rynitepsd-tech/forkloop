"""Controller-prepared paired observations for the document-magnification diagnostic.

One session-owned golden fork, reverted before every seed (the repaired paired-live reuse
contract: bounded readiness redials, per-seed lifetime refresh, independent watchdog).
For each seed the controller reproduces the teacher-reached state from recorded
provenance only (patient pid, claim number, document name, document page), captures
the document at the viewer's default rendering, then at the magnified rendering, then
the appeal form with the authorization field focused. No model is called and the
expected authorization value is never read by this script.
"""
from __future__ import annotations

import argparse, asyncio, datetime, hashlib, importlib.metadata, io, json, os, re, sys, time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from forkloop.actions import Action
from forkloop.backends.base import apply_action
from forkloop.backends.solari import SolariBackend
from forkloop.observe import wait_stable
from forkloop.reset import ResetController
from forkloop.spending import SessionLedger
from forkloop.world import load_world
from scripts.evaluation_watchdog import cleanup
from scripts.lambda_development_eval import baseline_digest
from scripts.magnification_common import (NEUTRAL_CLICK, PANE_CLICK, detect_pdf_toolbar, inner_position_actions, load_rgb,
                                          measure_text, outer_scroll_actions, page_actions, zoom_actions)

ROOT = Path(__file__).resolve().parents[1]
OPENEMR_LOGIN = {'user_field': (690, 431), 'pass_field': (680, 487)}
SEARCH_BOX = (1050, 145)
FIRST_ROW = (73, 557)
FINDER_ROW_DY = 41
ALERT_OK = (811, 390)
ALERT_OK_RGB = (13, 80, 187)
DOCUMENTS_LINK = (429, 405)
MEDICAL_RECORD_TOGGLE = (26, 617)
DOC_ROW_X, DOC_ROW_Y0, DOC_ROW_DY = 167, 637, 20
PORTAL_TAB = (161, 46)
OMNIBOX = (640, 90)
REASON_SELECT = (348, 382)
REASON_OPTION = (190, 441)
AUTH_FIELD = (336, 465)
CONTROLLER_NOTE = 'document/form state supplied by the diagnostic harness from recorded provenance (pid, claim number, document name, page); not agent navigation'
CRASHES: list[dict] = []          # every renderer crash observed during preparation (evidence; session-wide cap)
MAX_SESSION_CRASHES = 12


def utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class RendererCrash(RuntimeError):
    pass


def crash_page_visible(png: bytes) -> bool:
    """Chrome's 'Aw, Snap!' error page: blue Reload button at (870,440), its pale halo, white body."""
    a = load_rgb(png)
    return bool(np.abs(a[440, 870] - np.array([13, 80, 187])).max() <= 10 and np.abs(a[451, 896] - np.array([226, 234, 247])).max() <= 10
                and a[300, 600].min() >= 250 and a[500, 20].min() >= 250)


class SeedSession:
    def __init__(self, out: Path, machine, dbs, task, watermarks: dict, log, world, initial_sha: str):
        self.out, self.m, self.dbs, self.task, self.wm, self.log, self.world = out, machine, dbs, task, watermarks, log, world
        self.i = 0
        self.last_sha = initial_sha
        (out / 'shots').mkdir(parents=True, exist_ok=True)
        self.actions_path = out / 'actions.jsonl'

    async def do(self, action: Action, *, settle: float = 0.6, note: str | None = None, allow_crash: bool = False) -> bytes:
        """Execute one controller action, record it with the after screenshot (one grab per action)."""
        t0 = time.monotonic()
        await apply_action(self.m, action)
        await asyncio.sleep(max(settle, 0.8))
        after = await self.m.screenshot()
        crash = crash_page_visible(after)
        row = {'i': self.i, 'utc': utc(), 'action': action.to_compact(), 'seconds': round(time.monotonic() - t0, 3),
               'before_sha256': self.last_sha, 'after_sha256': sha(after), 'note': note, 'renderer_crash_visible': crash}
        self.last_sha = row['after_sha256']
        (self.out / 'shots' / f'{self.i:03d}_after.png').write_bytes(after)
        with self.actions_path.open('a') as f:
            f.write(json.dumps(row) + '\n')
        self.i += 1
        if crash and not allow_crash:
            CRASHES.append({'seed': self.task.seed, 'utc': row['utc'], 'action': row['action']})
            # Evidence only: the preparation attempt is abandoned and retried from a fresh golden revert.
            self.log('renderer_crash_visible', seed=self.task.seed, action_index=self.i - 1)
            try:
                diag = await asyncio.wait_for(self.world.diagnostics(self.m), 60)
                (self.out / 'chrome-crash-diagnostics.json').write_text(json.dumps(diag, indent=2))
            except Exception as e:  # noqa: BLE001
                self.log('crash_diagnostics_failed', error=str(e))
            raise RendererCrash(f'Chrome "Aw, Snap!" page visible after controller action {self.i - 1} ({action.to_compact()})')
        return after

    async def run_all(self, actions: list[Action], *, settle: float = 0.5, note: str | None = None) -> bytes:
        shot = b''
        for a in actions:
            shot = await self.do(a, settle=settle, note=note)
        return shot

    async def stable(self, name: str, timeout: float = 25.0) -> bytes:
        shot, n = await wait_stable(self.m, timeout_s=timeout, interval_s=1.0, required=3)
        (self.out / f'{name}.png').write_bytes(shot)
        self.log('capture', seed=self.task.seed, name=name, sha256=sha(shot), shots_until_stable=n)
        return shot

    async def viewed_doc_ids(self) -> list[int]:
        from forkloop.oracle import _comment_texts
        from worlds.claims_ops_v1.world import document_view_path
        wm_log = int(self.wm.get('openemr.log', 0) or 0)
        rows = await self.dbs['openemr'].query("SELECT comments AS c FROM log WHERE id > ? AND event LIKE 'http-request%' ORDER BY id LIMIT 2000", [wm_log])
        ids = []
        for r in rows:
            for text in _comment_texts(r.get('c')):
                if (text.startswith('/') or '://' in text) and document_view_path(text):
                    m = re.search(r'(?:doc_id|document_id)=(\d+)', text)
                    if m:
                        ids.append(int(m.group(1)))
                    break
        return ids

    async def portal_paths(self) -> list[str]:
        wm_pv = int(self.wm.get('portal.page_views', 0) or 0)
        rows = await self.dbs['portal'].query('SELECT path AS p FROM page_views WHERE id > ? ORDER BY id', [wm_pv])
        return [str(r['p']) for r in rows]


def alert_present(png: bytes) -> bool:
    a = load_rgb(png)
    return bool(np.abs(a[ALERT_OK[1], ALERT_OK[0]] - np.array(ALERT_OK_RGB)).max() <= 12)


async def prepare_seed(s: SeedSession, prov: dict, geometry: dict, log) -> dict:
    """The frozen preparation protocol. Returns the verification record for this seed."""
    m, seed = s.m, s.task.seed
    ver: dict = {'seed': seed, 'provenance': prov, 'geometry': geometry, 'controller_note': CONTROLLER_NOTE, 'steps': {}}
    pid, page = prov['patient_pid'], prov['doc_page']
    surname = await s.dbs['openemr'].scalar('SELECT lname AS v FROM patient_data WHERE pid = ?', [pid])
    docs = await s.dbs['openemr'].query('SELECT id, name, docdate FROM documents WHERE foreign_id = ? ORDER BY docdate DESC, id DESC', [pid])
    ver['openemr_documents'] = docs
    target = [d for d in docs if d['name'] == prov['doc_name']]
    if len(target) != 1:
        raise RuntimeError(f'document identity: {prov["doc_name"]} not unique in {docs}')
    target_id = int(target[0]['id'])
    ver['target_doc_id'] = target_id
    order = [int(d['id']) for d in docs]
    same_surname = await s.dbs['openemr'].query('SELECT pid FROM patient_data WHERE lname = ? ORDER BY pid', [surname])
    rank = [int(r['pid']) for r in same_surname].index(int(pid))
    ver['steps']['finder_rank'] = rank
    wm_log = int(s.wm.get('openemr.log', 0) or 0)

    async def openemr_navigate(nav_attempt: int) -> None:
        """Login -> finder -> chart -> Documents -> recorded document. Raises RendererCrash on 'Aw, Snap!'."""
        tag = f' (nav attempt {nav_attempt})'
        await s.stable('login_page', 20)
        await s.run_all([Action.click(*OPENEMR_LOGIN['user_field']), Action.type_text('admin'), Action.click(*OPENEMR_LOGIN['pass_field']),
                         Action.type_text('pass\n'), Action.wait(6.0)], note='login' + tag)
        shot = await s.stable('after_login', 25)
        if crash_page_visible(shot):
            raise RendererCrash('Chrome "Aw, Snap!" page visible after login')
        # 2. Patient by surname; the finder lists matches by ascending pid (verified on seeds 101 and 107),
        #    so the recorded pid's rank among same-surname patients selects the row; identity is then
        #    verified through OpenEMR's audit log for that pid.
        await s.run_all([Action.click(*SEARCH_BOX), Action.type_text(f'{surname}\n'), Action.wait(4.0)], note='search surname' + tag)
        await s.stable('finder', 20)
        shot = await s.do(Action.click(FIRST_ROW[0], FIRST_ROW[1] + FINDER_ROW_DY * rank), settle=4.0, note=f'open chart (row {rank})' + tag)
        shot = await s.stable('chart', 25)
        if alert_present(shot):
            shot = await s.do(Action.click(*ALERT_OK), settle=1.0, note='dismiss clinical reminders alert' + tag)
            ver['steps']['alert_dismissed'] = True
        chart_rows = await s.dbs['openemr'].scalar('SELECT COUNT(*) AS n FROM log WHERE id > ? AND patient_id = ?', [wm_log, pid])
        ver['steps']['openemr_rows_for_target_patient'] = int(chart_rows or 0)
        if int(chart_rows or 0) == 0:
            raise RuntimeError('patient identity: no OpenEMR log rows for the recorded pid after opening the chart')
        # 3. Documents tree -> Medical Record -> recorded document (verified by the audited view path).
        shot = await s.do(Action.click(*DOCUMENTS_LINK), settle=3.0, note='documents tab' + tag)
        if alert_present(shot):
            shot = await s.do(Action.click(*ALERT_OK), settle=1.0, note='dismiss clinical reminders alert' + tag)
            shot = await s.do(Action.click(*DOCUMENTS_LINK), settle=3.0, note='documents tab (again)' + tag)
            ver['steps']['alert_dismissed_after_documents'] = True
        await s.stable('documents', 20)
        await s.do(Action.click(*MEDICAL_RECORD_TOGGLE), settle=1.5, note='expand Medical Record' + tag)
        opened = None
        tried = []
        for k in [order.index(target_id)] + [i for i in range(len(order)) if i != order.index(target_id)]:
            await s.do(Action.click(DOC_ROW_X, DOC_ROW_Y0 + DOC_ROW_DY * k), settle=3.5, note=f'open document row {k}' + tag)
            ids = await s.viewed_doc_ids()
            tried.append({'row': k, 'viewed_doc_ids': ids})
            if ids and ids[-1] == target_id:
                opened = k
                break
        ver['steps']['document_rows_tried'] = tried
        if opened is None:
            raise RuntimeError(f'document identity: recorded document {target_id} never opened; tried {tried}')

    # 1. OpenEMR in a second tab. Chrome's renderer has repeatedly died on OpenEMR page loads in this
    #    environment ("Aw, Snap!", error code 5; docs/limitations.md). Controller preparation only:
    #    a crash abandons the navigation, reloads to the login page and repeats it, at most 4 times,
    #    without any VM revert; every attempt and crash is recorded in actions.jsonl / verification.json.
    await s.run_all([Action.key('ctrl+t'), Action.type_text('http://localhost/openemr\n'), Action.wait(3.0)], note='open OpenEMR')
    nav_attempts = []
    for nav_attempt in range(1, 5):
        try:
            await openemr_navigate(nav_attempt)
            nav_attempts.append({'attempt': nav_attempt, 'ok': True})
            break
        except RendererCrash as e:
            nav_attempts.append({'attempt': nav_attempt, 'ok': False, 'renderer_crash': str(e), 'utc': utc()})
            s.log('openemr_navigation_crash', seed=seed, attempt=nav_attempt, error=str(e))
            if nav_attempt == 4 or len(CRASHES) >= MAX_SESSION_CRASHES:
                ver['steps']['openemr_navigation_attempts'] = nav_attempts
                raise
            await s.do(Action.key('F5'), settle=4.0, note='reload after renderer crash', allow_crash=True)
            await s.do(Action.click(*OMNIBOX), settle=0.5, note='omnibox', allow_crash=True)
            await s.run_all([Action.key('ctrl+a'), Action.type_text('http://localhost/openemr\n'), Action.wait(3.0)], note='reopen OpenEMR login')
    ver['steps']['openemr_navigation_attempts'] = nav_attempts
    shot = await s.stable('as_opened')
    ty = detect_pdf_toolbar(shot)
    ver['as_opened'] = {'toolbar_y': ty, 'text': measure_text(shot, ty).__dict__ if ty else None, 'sha256': sha(shot)}
    if ty is None:
        raise RuntimeError('viewer toolbar not detected after opening the document')
    # 4. Page selection through the visible page field (identical for both conditions).
    await s.run_all(page_actions(ty, page), settle=0.6, note=f'page field -> {page}')
    await s.do(Action.wait(1.0))
    # 5. Standard condition: outer page scroll, pane focus click (symmetric with the magnified flow), capture.
    await s.run_all(outer_scroll_actions('down'), settle=0.8, note='outer scroll down')
    await s.run_all(inner_position_actions(0, 0), settle=0.35, note='inner positioning (standard: focus click only)')
    std = await s.stable('standard')
    ty_std = detect_pdf_toolbar(std)
    ver['standard'] = {'toolbar_y': ty_std, 'text': measure_text(std, ty_std).__dict__ if ty_std else None, 'sha256': sha(std)}
    # 6. Magnified condition: same outer scroll state (untouched), zoom through the visible readout at its
    #    current position, re-select the page through the page field (zooming moves the viewport), then the
    #    fixed inner positioning (pane focus click, Left x N to the left edge, Down x K), capture.
    if ty_std is None:
        raise RuntimeError('viewer toolbar not detected in the standard capture')
    await s.run_all(zoom_actions(ty_std, geometry['zoom']), settle=0.6, note=f'zoom readout -> {geometry["zoom"]}')
    await s.do(Action.wait(1.0))
    after_zoom = await s.stable('after_zoom')
    ver['after_zoom'] = {'toolbar_y': detect_pdf_toolbar(after_zoom), 'sha256': sha(after_zoom)}
    await s.run_all(page_actions(ty_std, page), settle=0.6, note=f'page field -> {page} (after zoom)')
    await s.do(Action.wait(1.0))
    await s.run_all(inner_position_actions(geometry['left'], geometry['down'], geometry.get('right', 0)), settle=0.35, note='inner positioning')
    mag = await s.stable('magnified')
    ty_mag = detect_pdf_toolbar(mag)
    ver['magnified'] = {'toolbar_y': ty_mag, 'text': measure_text(mag, ty_mag).__dict__ if ty_mag else None, 'sha256': sha(mag)}
    # 7. Appeal form for the recorded claim, reason selected, authorization field focused.
    url = f'http://localhost:8080/claims/{prov["claim_number"]}/appeal'
    await s.run_all([Action.click(*PORTAL_TAB), Action.wait(1.0), Action.click(*OMNIBOX), Action.key('ctrl+a'), Action.type_text(url + '\n'), Action.wait(2.5)], note='appeal form url')
    await s.run_all([Action.click(*REASON_SELECT), Action.wait(0.8), Action.click(*REASON_OPTION), Action.wait(0.8), Action.click(*AUTH_FIELD), Action.wait(0.6)], note='reason + focus authorization field')
    form = await s.stable('form')
    paths = await s.portal_paths()
    ver['form'] = {'sha256': sha(form), 'portal_paths': paths, 'appeal_form_visited': f'/claims/{prov["claim_number"]}/appeal' in paths}
    ver['viewed_doc_ids'] = await s.viewed_doc_ids()
    ver['identity'] = {
        'portal_claim': await s.dbs['portal'].query('SELECT id, claim_number, patient_id, status FROM claims WHERE claim_number = ?', [prov['claim_number']]),
        'openemr_patient': await s.dbs['openemr'].query('SELECT pid, fname, lname, DOB FROM patient_data WHERE pid = ?', [pid]),
        'openemr_document': await s.dbs['openemr'].query('SELECT id, name, hash, size, foreign_id FROM documents WHERE id = ?', [target_id]),
        'appeals_for_claim': await s.dbs['portal'].scalar('SELECT COUNT(*) AS n FROM appeals WHERE claim_id = ?', [prov['claim_id']]),
    }
    ok = (ty_std is not None and ty_mag is not None and ver['form']['appeal_form_visited'] and ver['viewed_doc_ids'][-1] == target_id
          and ver['identity']['openemr_document'][0]['hash'] == prov['doc_hash'] and int(ver['identity']['appeals_for_claim'] or 0) == 0)
    ver['prepared'] = bool(ok)
    return ver


async def main(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    events = out / 'events.jsonl'

    def log(event, **kw):
        row = {'utc': utc(), 'event': event, **kw}
        with events.open('a') as f:
            f.write(json.dumps(row, default=str) + '\n')
        print(json.dumps(row, default=str), flush=True)

    ledger = SessionLedger(os.environ['FORKLOOP_SESSION_LEDGER'])
    heartbeat = Path(a.watchdog_heartbeat)
    watchdog = json.loads(heartbeat.read_text())
    assert watchdog['ledger_sha256_tag'] == hashlib.sha256(str(ledger.path.resolve()).encode()).hexdigest()[:16]
    assert time.time() - watchdog['utc'] < 30 and watchdog['deadline'] > time.time() + 1200, 'watchdog stale or deadline too close'
    deadline = watchdog['deadline'] - 600
    geometry = {'zoom': a.zoom, 'left': a.left, 'right': a.right, 'down': a.down}
    b = SolariBackend(ready_timeout_s=90, call_timeout_ms=120000)
    world = load_world('claims-ops-v1')
    golden = world.golden_snapshot_id()
    log('environment', versions={k: importlib.metadata.version(k) for k in ['solari-core', 'solari-sandbox', 'solari-desktop', 'websockets', 'httpx']},
        golden=golden, seeds=a.seeds, geometry=geometry, watchdog_deadline=watchdog['deadline'])
    before = await b.list_snapshots()
    (out / 'snapshots-before.json').write_text(json.dumps([r.__dict__ for r in before], indent=2))
    summary = {'machine': None, 'seeds': {}, 'creates': 0, 'calibration': []}
    state = {'m': None, 'fresh': False}

    async def ensure_machine():
        m = state['m']
        if m is None and a.attach_machine:
            m = await asyncio.wait_for(b.attach(a.attach_machine), 120)
            summary['machine'], summary['attached'] = m.id, True
            log('machine_attached', id=m.id)
            state['m'], state['fresh'] = m, False
            return m
        if m is None or not await m.healthy():
            if summary['creates'] >= a.max_creates:
                raise RuntimeError('replacement allocation budget exhausted')
            if m is not None:
                try:
                    await asyncio.wait_for(m.kill(), 30)
                except Exception as e:  # noqa: BLE001
                    log('kill_unhealthy_failed', error=str(e))
            summary['creates'] += 1
            m = await asyncio.wait_for(b.create(from_snapshot=golden, metadata={'run_id': out.name, 'diagnostic': 'magnification-observations'}, timeout_ms=1800000), 240)
            summary['machine'] = m.id
            log('machine_created', id=m.id, readiness_redials=getattr(m, 'readiness_redials', None))
            state['m'], state['fresh'] = m, True
        return state['m']

    async def run_seed(seed: int, sd: Path, geometry: dict) -> dict:
        sd.mkdir(parents=True)
        t_seed = time.monotonic()
        m = await ensure_machine()
        fresh = state['fresh']
        state['fresh'] = False
        refresh = await asyncio.wait_for(m.refresh_lifetime(1800000), 20)

        async def restore():
            if not fresh:
                await m.revert(golden)
            return m
        worker = SimpleNamespace(pool=SimpleNamespace(mode='fork' if fresh else 'revert'), restore=restore)
        task = world.generate('resolve_denial', seed, 'train')
        outcome = await asyncio.wait_for(ResetController(world).reset(worker, task), 400)
        (sd / 'initial.png').write_bytes(outcome.screenshot)
        prov = {k: v for k, v in dict(task.expected).items() if k not in ('auth_number', 'decoy_numbers')}
        s = SeedSession(sd, outcome.machine, outcome.dbs, task, dict(outcome.baseline.watermarks or {}), log, world, sha(outcome.screenshot))
        try:
            ver = await asyncio.wait_for(prepare_seed(s, prov, geometry, log), 600)
        finally:
            (sd / 'progress.json').write_text(json.dumps({'controller_actions': s.i, 'reset': outcome.report.to_dict()}, indent=2, default=str))
        ver.update(reset=outcome.report.to_dict(), baseline_digest=baseline_digest(outcome.baseline), lifetime_refresh=refresh,
                   initial_sha256=sha(outcome.screenshot), readiness_redials=getattr(m, 'readiness_redials', None), reconnects=m.reconnects,
                   seconds=round(time.monotonic() - t_seed, 1), controller_actions=s.i, task_id=task.task_id, instruction=task.instruction)
        (sd / 'verification.json').write_text(json.dumps(ver, indent=2, default=str))
        return ver

    try:
        if a.calibrate_seed is not None:
            # One neutral setup example outside the scored set: repeat only that seed until the
            # operator writes calibration/decision.json {"action": "proceed"|"retry"|"abort", "geometry": {...}}.
            attempt = 0
            while True:
                attempt += 1
                sd = out / 'calibration' / f'attempt-{attempt}'
                try:
                    ver = await run_seed(a.calibrate_seed, sd, geometry)
                    row = {'attempt': attempt, 'geometry': dict(geometry), 'prepared': ver['prepared'], 'standard': ver.get('standard'), 'magnified': ver.get('magnified')}
                except Exception as e:  # noqa: BLE001
                    row = {'attempt': attempt, 'geometry': dict(geometry), 'prepared': False, 'error': f'{type(e).__name__}: {e}'}
                    (sd / 'failure.json').write_text(json.dumps(row, indent=2, default=str))
                summary['calibration'].append(row)
                (out / 'summary.json').write_text(json.dumps(summary, indent=2, default=str))
                log('calibration_attempt', **{k: v for k, v in row.items() if k in ('attempt', 'geometry', 'prepared', 'error')})
                decision_path = out / 'calibration' / 'decision.json'
                last_refresh = time.monotonic()
                while not decision_path.exists():
                    if time.time() > deadline - 300:
                        raise RuntimeError('deadline reached while waiting for the calibration decision')
                    if time.monotonic() - last_refresh > 600 and state['m'] is not None:
                        await asyncio.wait_for(state['m'].refresh_lifetime(1800000), 20)
                        last_refresh = time.monotonic()
                    await asyncio.sleep(5)
                decision = json.loads(decision_path.read_text())
                decision_path.rename(out / 'calibration' / f'decision-{attempt}.json')
                geometry = dict(decision.get('geometry') or geometry)
                log('calibration_decision', attempt=attempt, action=decision['action'], geometry=geometry)
                if decision['action'] == 'proceed':
                    break
                if decision['action'] == 'abort':
                    raise RuntimeError('calibration aborted by operator')
            (out / 'geometry-frozen.json').write_text(json.dumps({'geometry': geometry, 'frozen_utc': utc(), 'calibration_attempts': attempt}, indent=2))
        retries_used = 0
        for seed in a.seeds:
            if len(CRASHES) >= MAX_SESSION_CRASHES:
                log('crash_budget_stop', seed=seed, crashes=len(CRASHES))
                summary['seeds'][str(seed)] = {'prepared': False, 'reason': f'session renderer-crash budget exhausted ({len(CRASHES)})'}
                continue
            if time.time() > deadline - 300:
                log('deadline_reserve_stop', seed=seed)
                summary['seeds'][str(seed)] = {'prepared': False, 'reason': 'session deadline reserve'}
                continue
            sd = out / f'seed-{seed:03d}'
            if sd.exists():
                log('skip_existing', seed=seed)
                continue
            sd.mkdir()
            attempts = []
            for attempt in (1, 2, 3):
                ad = sd / f'attempt-{attempt}'
                try:
                    ver = await run_seed(seed, ad, geometry)
                    attempts.append({'attempt': attempt, 'prepared': ver['prepared'], 'seconds': ver['seconds'], 'actions': ver['controller_actions']})
                    if ver['prepared']:
                        break
                    raise RuntimeError('preparation checks failed (see verification.json)')
                except Exception as e:  # noqa: BLE001
                    attempts.append({'attempt': attempt, 'prepared': False, 'error': f'{type(e).__name__}: {e}'})
                    (ad / 'failure.json').write_text(json.dumps(attempts[-1], indent=2, default=str))
                    log('seed_attempt_failed', seed=seed, attempt=attempt, error=f'{type(e).__name__}: {e}')
                    if attempt < 3 and retries_used < a.max_retries and time.time() < deadline - 600:
                        retries_used += 1
                        continue
                    break
            done = attempts[-1].get('prepared', False)
            summary['seeds'][str(seed)] = {'prepared': done, 'attempts': attempts, 'attempt_dir': f'attempt-{len(attempts)}'}
            (sd / 'summary.json').write_text(json.dumps(summary['seeds'][str(seed)], indent=2, default=str))
            log('seed_done', seed=seed, prepared=done, attempts=len(attempts))
            if not done and a.stop_on_failure:
                break
            (out / 'summary.json').write_text(json.dumps(summary, indent=2, default=str))
        if a.hold_file:
            # Keep the session-owned machine alive (lifetime re-armed) for a later attach until released.
            hold = Path(a.hold_file)
            log('hold_start', machine=summary['machine'], hold_file=str(hold))
            last_refresh = time.monotonic()
            while not hold.exists() and time.time() < deadline - 300:
                if time.monotonic() - last_refresh > 480 and state['m'] is not None:
                    await asyncio.wait_for(state['m'].refresh_lifetime(1800000), 20)
                    last_refresh = time.monotonic()
                    log('hold_refresh', machine=summary['machine'])
                await asyncio.sleep(5)
            log('hold_end', released=hold.exists())
    finally:
        m = state['m']
        if m is not None and not a.keep_machine:
            try:
                await asyncio.wait_for(m.kill(), 30)
                log('machine_killed', id=m.id)
            except Exception as e:  # noqa: BLE001
                log('machine_kill_failed', id=m.id, error=str(e))
        summary['remaining_after_cleanup'] = None if a.keep_machine else await cleanup(b, ledger.path)
        summary['resources'] = dict(b.resources)
        after = await b.list_snapshots()
        (out / 'snapshots-after.json').write_text(json.dumps([r.__dict__ for r in after], indent=2))
        summary['golden_present'] = any(r.id == golden for r in after)
        summary['snapshot_inventory_unchanged'] = sorted(r.id for r in before) == sorted(r.id for r in after)
        summary['renderer_crashes'] = list(CRASHES)
        (out / 'summary.json').write_text(json.dumps(summary, indent=2, default=str))
        (out / 'session-spend.json').write_text(json.dumps(ledger.summary(), indent=2))
        log('session_end', summary={k: v for k, v in summary.items() if k != 'seeds'})
        await b.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    p.add_argument('--seeds', type=lambda v: [int(x) for x in v.split(',')], required=True)
    p.add_argument('--watchdog-heartbeat', required=True)
    p.add_argument('--zoom', default='150')
    p.add_argument('--left', type=int, required=True)
    p.add_argument('--down', type=int, required=True)
    p.add_argument('--right', type=int, default=0)
    p.add_argument('--calibrate-seed', type=int, default=None)
    p.add_argument('--max-retries', type=int, default=4)
    p.add_argument('--attach-machine', default=None)
    p.add_argument('--hold-file', default=None)
    p.add_argument('--max-creates', type=int, default=1)
    p.add_argument('--stop-on-failure', action='store_true')
    p.add_argument('--keep-machine', action='store_true')
    asyncio.run(main(p.parse_args()))
