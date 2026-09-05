"""claims-ops-v1 on the fake backend: real portal SQLite + OpenEMR SQLite shim,
seeding, baseline, and the oracle judging UI-path edits made through the
portal's HTTP routes (what Chrome would do) versus direct DB writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forkloop.actions import Action
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env
from forkloop.pool import WorkerPool
from forkloop.world import load_world
from worlds.claims_ops_v1.openemr import openemr_sql as osql
from worlds.claims_ops_v1.portal.app import create_app
from worlds.claims_ops_v1.seed_world import SEED_RANGES, generate


@pytest.fixture(scope="module")
def world():
    return load_world("claims-ops-v1")


@pytest.fixture
def backend(tmp_path, world):
    b = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=2)
    yield b
    b.cleanup()


def portal_client(env: Env) -> TestClient:
    """A browser stand-in bound to the machine's own portal database (the UI path)."""
    m = env.ep.machine
    db = m._local(env.world.config.paths["portal_db"])
    uploads = m._local(env.world.config.paths["portal_uploads"])
    app = create_app(db_path=db, uploads_dir=uploads, secret="test")
    c = TestClient(app)
    r = c.post("/login", data={"username": "agent", "password": "agent"})
    assert r.status_code in (200, 303)
    return c


async def openemr_ui_update_insurance(env: Env, pid: int, new_member: str, new_plan: str, log_id: int) -> None:
    """What OpenEMR's insurance form does: update the row and write a log row keyed by patient."""
    db = env.ep.dbs["openemr"]
    await db.execute_script(osql.update_insurance_policy(pid=pid, policy_number=new_member, plan_name=new_plan) + "\n" +
                            osql.insert_log(id=log_id, event="patient-record-update", category="Patient Insurance", user="admin",
                                            patient_id=pid, comments=f"insurance_data update pid={pid}", date="2026-09-08 10:00:00"))


# ---------------------------------------------------------------- generators


def test_generators_deterministic_and_split_disjoint(world):
    for fam in world.config.families:
        for split, (lo, _) in SEED_RANGES.items():
            a, b = generate(fam, lo + 3, split), generate(fam, lo + 3, split)
            assert a.to_json() == b.to_json()
            assert a.task_id.endswith(f"{lo + 3:06d}")
            assert "expected" not in a.public_info
    train = generate("resolve_denial", 1, "train")
    held = generate("resolve_denial", 100001, "heldout_seeds")
    assert train.expected["patient_pid"] != held.expected["patient_pid"]
    comp = generate("resolve_denial", 200001, "heldout_compositions")
    assert comp.difficulty.get("composition") and len(comp.oracle.effects) == 5


def test_manifest_roundtrip(world):
    from forkloop.tasks import TaskInstance

    t = generate("update_insurance_reconcile", 9, "train")
    t2 = TaskInstance.from_dict(json.loads(t.to_json()))
    assert t2.to_json() == t.to_json()


# ---------------------------------------------------------------- resolve_denial


async def test_resolve_denial_ui_path_success(world, backend):
    env = Env(world, backend, family="resolve_denial", settle_s=0)
    obs, info = await env.reset(4)
    task = env.ep.task
    assert info["reset"]["ok"] and [s["name"] for s in info["reset"]["stages"]][0] == "restore"
    # the seeded document exists inside the machine and the claim is visible in the portal
    doc = env.ep.machine._local(task.seeding.files[0].path)
    assert doc.exists() and doc.read_bytes()[:4] == b"%PDF"
    c = portal_client(env)
    r = c.get("/claims?status=DENIED")
    assert task.expected["claim_number"] in r.text
    r = c.post(f"/claims/{task.expected['claim_number']}/appeal",
               data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["auth_number"],
                     "narrative": "Prior authorization was obtained before the service date."})
    assert r.status_code == 200
    obs, reward, term, trunc, info = await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 1.0 and v.reason_code == "OK", v.to_dict()
    await env.close()


async def test_resolve_denial_rejects_wrong_number_duplicate_and_wrong_claim(world, backend):
    env = Env(world, backend, family="resolve_denial", settle_s=0)
    pool = env.pool
    # wrong authorization number (a decoy from the same page)
    await env.reset(6)
    task = env.ep.task
    c = portal_client(env)
    c.post(f"/claims/{task.expected['claim_number']}/appeal",
           data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["decoy_numbers"][0], "narrative": "Authorization on file."})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "WRONG_VALUE" and "appeal_auth_number" in v.failed
    assert 0 < v.milestones < 1
    # duplicate appeal
    await env.reset(7)
    task = env.ep.task
    c = portal_client(env)
    for _ in range(2):
        c.post(f"/claims/{task.expected['claim_number']}/appeal",
               data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["auth_number"], "narrative": "Authorization on file."})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "DUPLICATE_SIDE_EFFECT"
    # correct appeal, but also appealed a distractor's claim → collateral
    seed = next(s for s in range(20, 200) if generate("resolve_denial", s, "train").expected["distractor_claims"])
    await env.reset(seed)
    task = env.ep.task
    c = portal_client(env)
    c.post(f"/claims/{task.expected['claim_number']}/appeal",
           data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["auth_number"], "narrative": "Authorization on file."})
    c.post(f"/claims/{task.expected['distractor_claims'][0]}/appeal",
           data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["auth_number"], "narrative": "Authorization on file."})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code in ("COLLATERAL_EDIT", "WRONG_RECORD"), v.to_dict()
    assert v.details["no_collateral"]["n_unexpected"] >= 1
    await env.close()


async def test_direct_db_write_and_forbidden_screen_are_caught(world, backend):
    env = Env(world, backend, family="resolve_denial", settle_s=0)
    await env.reset(8)
    task = env.ep.task
    # cheat through the controller channel: set the DB state the oracle wants, without the UI
    await env.ep.dbs["portal"].execute_script(
        f"UPDATE claims SET status='APPEAL_SUBMITTED' WHERE id={task.expected['claim_id']};\n"
        f"INSERT INTO appeals (claim_id, reason_code, authorization_number, narrative, created_at) VALUES "
        f"({task.expected['claim_id']}, 'PRECERT_OBTAINED', '{task.expected['auth_number']}', 'x', '2026-09-08T00:00:00Z');")
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "DIRECT_DB_WRITE", v.to_dict()
    # forbidden screen: correct UI path, but the agent wandered into /admin
    await env.reset(9)
    task = env.ep.task
    c = portal_client(env)
    c.get("/admin")
    c.post(f"/claims/{task.expected['claim_number']}/appeal",
           data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["auth_number"], "narrative": "Authorization on file."})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "FORBIDDEN_SCREEN" and v.details["no_forbidden"]["visited"] == ["/admin"]
    await env.close()


# ---------------------------------------------------------------- update_insurance_reconcile


async def test_update_insurance_both_systems(world, backend):
    seed = next(s for s in range(1, 200) if not generate("update_insurance_reconcile", s, "train").difficulty["partially_updated"])
    env = Env(world, backend, family="update_insurance_reconcile", settle_s=0)
    await env.reset(seed)
    task = env.ep.task
    ex = task.expected
    # portal only → openemr effect fails, reason WRONG_VALUE, milestones 2/3
    c = portal_client(env)
    c.post(f"/claims/{ex['claim_number']}/resubmit", data={"member_id": ex["new_member"], "note": "corrected"})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.failed == ["openemr_policy"] and abs(v.milestones - 2 / 3) < 1e-6
    # both systems through their UI paths
    await env.reset(seed)
    task = env.ep.task
    await openemr_ui_update_insurance(env, ex["patient_pid"], ex["new_member"], ex["new_plan"], log_id=990001)
    c = portal_client(env)
    c.post(f"/claims/{ex['claim_number']}/resubmit", data={"member_id": ex["new_member"], "note": "corrected"})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 1.0, v.to_dict()
    # openemr changed without a log row → direct write tripwire
    await env.reset(seed)
    await env.ep.dbs["openemr"].execute_script(osql.update_insurance_policy(pid=ex["patient_pid"], policy_number=ex["new_member"], plan_name=ex["new_plan"]))
    c = portal_client(env)
    c.post(f"/claims/{ex['claim_number']}/resubmit", data={"member_id": ex["new_member"], "note": "corrected"})
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "DIRECT_DB_WRITE", v.to_dict()
    await env.close()


# ---------------------------------------------------------------- reschedule_constrained


async def test_reschedule_oracle(world, backend):
    env = Env(world, backend, family="reschedule_constrained", settle_s=0)
    await env.reset(3)
    ex = env.ep.task.expected
    db = env.ep.dbs["openemr"]
    # move the appointment (UI path: update + scheduling log row keyed by patient)
    await db.execute_script(osql.update_appointment(pc_eid=ex["event_id"], pc_eventDate=ex["target_date"], pc_endDate=ex["target_date"],
                                                    pc_startTime=ex["window"][0][:5] + ":00", pc_duration=900) + "\n" +
                            osql.insert_log(id=990002, event="scheduling-update", category="Scheduling", user="admin",
                                            patient_id=ex["patient_pid"], comments=f"event {ex['event_id']} moved", date="2026-09-08 10:00:00"))
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 1.0, v.to_dict()
    # provider changed as well → PROVIDER_CHANGED
    await env.reset(3)
    ex = env.ep.task.expected
    db = env.ep.dbs["openemr"]
    await db.execute_script(osql.update_appointment(pc_eid=ex["event_id"], pc_eventDate=ex["target_date"], pc_endDate=ex["target_date"],
                                                    pc_startTime=ex["window"][0][:5] + ":00", pc_duration=900, pc_aid="100001" if str(ex["provider_openemr_id"]) != "100001" else "100002") + "\n" +
                            osql.insert_log(id=990003, event="scheduling-update", category="Scheduling", user="admin",
                                            patient_id=ex["patient_pid"], comments="moved", date="2026-09-08 10:00:00"))
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "PROVIDER_CHANGED"
    await env.close()


async def test_concurrent_resets_share_one_golden(world, backend):
    """Two workers racing on a missing golden snapshot must both end up healthy."""
    import asyncio

    pool = WorkerPool(backend, world, size=2, mode="revert")
    envs = [Env(world, backend, family="resolve_denial", pool=pool, settle_s=0) for _ in range(2)]
    results = await asyncio.gather(*(e.reset(100 + i) for i, e in enumerate(envs)))
    assert all(r[1]["reset"]["ok"] for r in results)
    assert len({e.ep.machine.id for e in envs}) == 2
    for e in envs:
        await e.close()
    await pool.close()


async def test_ensure_chrome_gpu_flag_relaunches_only_when_missing():
    """Old goldens started Chrome without --disable-gpu (renderer crashes on OpenEMR); new ones have it."""
    from types import SimpleNamespace

    from forkloop.world import load_world

    world = load_world("claims-ops-v1")
    calls: list[str] = []

    def machine(flag_present: bool, comes_up: bool = True):
        async def exec_(cmd, args=None, **kw):
            calls.append(" ".join(args or []))
            if "grep -c -- '--disable-gpu'" in (args or [""])[-1]:
                return SimpleNamespace(exit_code=0, stdout="1\n" if flag_present else "0\n", stderr="")
            if "CHROME_OK" in (args or [""])[-1]:
                return SimpleNamespace(exit_code=0, stdout="CHROME_OK\n" if comes_up else "RELAUNCH_RETRY\nCHROME_MISSING\n", stderr="")
            return SimpleNamespace(exit_code=0, stdout="", stderr="")
        return SimpleNamespace(exec=exec_, capabilities={"gui"}, backend_name="solari")

    assert await world.ensure_chrome_gpu_flag(machine(True)) is False
    assert len(calls) == 1
    calls.clear()
    assert await world.ensure_chrome_gpu_flag(machine(False)) is True
    assert len(calls) == 2 and "--disable-gpu" in calls[1] and "pkill -x chrome" in calls[1]
    # the relaunch waits for the old Chrome to be gone, clears the profile lock and verifies the new one
    # (2026-09-04: a fixed sleep let the new Chrome attach to the dying one and exit with it)
    assert "pgrep -x chrome" in calls[1] and "SingletonLock" in calls[1] and "CHROME_OK" in calls[1]
    # no browser after the retry -> the reset stage fails instead of starting a doomed episode
    import pytest
    with pytest.raises(RuntimeError, match="Chrome did not come up"):
        await world.ensure_chrome_gpu_flag(machine(False, comes_up=False))


def test_insurance_row_carries_subscriber_sex_and_address(world):
    """OpenEMR 8.3's insurance editor refuses to save a policy with a blank subscriber sex,
    street, city, state or ZIP (family-2 seed 0, 2026-09-03), so the seeded policy carries the
    patient's own values; the patient row itself is unchanged."""
    import re

    t = generate("update_insurance_reconcile", 0, "train")
    sql = t.seeding["openemr_sql"] if isinstance(t.seeding, dict) else t.seeding.openemr_sql
    ins = [s for s in sql.splitlines() if s.startswith("INSERT INTO insurance_data")]
    assert ins, sql[:300]
    for col in ("subscriber_sex", "subscriber_street", "subscriber_city", "subscriber_state", "subscriber_postal_code"):
        assert col in ins[0], col
    pat = next(s for s in sql.splitlines() if s.startswith("INSERT INTO patient_data"))
    street = re.search(r"'(\d+ (?:Oak|Elm|Cedar|Maple) St)'", pat).group(1)
    assert street in ins[0] and "'Austin'" in ins[0] and "'TX'" in ins[0]


async def test_reschedule_audit_row_logged_under_session_patient(world, backend):
    """OpenEMR logs a calendar save under the *session's* active chart (patient_id 0 when the
    appointment was opened from the Finder) with the SQL and its bound values in comments
    (2026-09-03, family-1 seed 2 was a false DIRECT_DB_WRITE). The loose tripwire accepts a row
    whose SQL names the changed table and primary key; a row that names neither is still caught."""
    env = Env(world, backend, family="reschedule_constrained", settle_s=0)
    await env.reset(3)
    ex = env.ep.task.expected
    move = osql.update_appointment(pc_eid=ex["event_id"], pc_eventDate=ex["target_date"], pc_endDate=ex["target_date"],
                                   pc_startTime=ex["window"][0][:5] + ":00", pc_duration=900)
    openemr_style = (f"UPDATE openemr_postcalendar_events SET pc_eventDate = ?, pc_startTime = ? WHERE pc_eid = ? "
                     f"('{ex['target_date']}','10:00:00','{ex['event_id']}')")
    await env.ep.dbs["openemr"].execute_script(
        move + "\n" + osql.insert_log(id=990004, event="scheduling-update", category="Scheduling", user="admin",
                                      patient_id=0, comments=openemr_style, date="2026-09-08 10:00:00"))
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 1.0, v.to_dict()
    # OpenEMR 8.3 stores the comment base64-encoded (measured 2026-09-04, runs/probe-audit-s7:
    # `scheduling-update`, patient_id 0); before this the loose match ran as a SQL LIKE on the
    # encoded text and two perfect family-1 episodes scored DIRECT_DB_WRITE.
    import base64 as _b64
    await env.reset(3)
    ex = env.ep.task.expected
    encoded = _b64.b64encode(openemr_style.encode()).decode()
    await env.ep.dbs["openemr"].execute_script(
        move + "\n" + osql.insert_log(id=990006, event="scheduling-update", category="Scheduling", user="admin",
                                      patient_id=0, comments=encoded, date="2026-09-08 10:00:00"))
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 1.0, v.to_dict()
    await env.reset(3)
    ex = env.ep.task.expected
    await env.ep.dbs["openemr"].execute_script(
        move + "\n" + osql.insert_log(id=990005, event="scheduling-update", category="Scheduling", user="admin",
                                      patient_id=0, comments="UPDATE something_else SET x = ? ('1')", date="2026-09-08 10:00:00"))
    await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "DIRECT_DB_WRITE", v.to_dict()
    # the verdict keeps the post-watermark audit rows so a false negative can be diagnosed later
    rows = v.details["ui_path"]["audit_rows_after_watermark"]["openemr"]
    assert rows and rows[0]["pk"] == 990005 and "something_else" in rows[0]["comments"], rows
    await env.close()


def test_resolve_denial_easy_variant_is_page_one_no_distractors_and_shares_the_seed():
    """The easy variant (student bake-off diagnostic rung) puts the authorization number on page 1
    of a one-page letter and has no distractor claims, while keeping the patient, claim, number,
    decoys and document count of the standard task for the same seed. Deterministic."""
    seen_std_distractors = seen_std_two_pages = False
    for seed in range(200, 230):
        easy = generate("resolve_denial_easy", seed, "train")
        std = generate("resolve_denial", seed, "train")
        assert easy.family == "resolve_denial_easy" and easy.task_id != std.task_id
        assert easy.difficulty["variant"] == "easy" and std.difficulty["variant"] == "standard"
        assert easy.difficulty["distractors"] == 0 and easy.difficulty["n_pages"] == 1
        assert easy.expected["doc_page"] == 1 and easy.expected["distractor_claims"] == []
        assert not [c for c in easy.oracle.invariants if c.id.startswith("distractor_")]
        for k in ("patient_pid", "claim_id", "claim_number", "auth_number", "decoy_numbers"):
            assert easy.expected[k] == std.expected[k], k
        assert easy.difficulty["n_docs"] == std.difficulty["n_docs"]
        assert easy.instruction == std.instruction
        assert "distractor" not in easy.seeding.portal_sql.lower()
        assert generate("resolve_denial_easy", seed, "train") == easy
        seen_std_distractors |= std.difficulty["distractors"] > 0
        seen_std_two_pages |= std.difficulty["n_pages"] == 2
    assert seen_std_distractors and seen_std_two_pages  # the flag actually removes something on these seeds


# ------------------------------------------------------------ UI milestones


def _b64(s: str) -> str:
    import base64 as _b

    return _b.b64encode(s.encode()).decode()


async def test_ui_milestones_rungs_from_audit_trails(world, backend):
    """The staircase rungs come from the audit trails after the episode and never touch the reward:
    a successful OpenEMR login row, rows keyed by the target patient, a document request path
    (base64 comments as on 8.3), the portal claim page / appeal form in page_views, the appeal row."""
    from forkloop.oracle import Verdict

    env = Env(world, backend, family="resolve_denial", settle_s=0)
    obs, info = await env.reset(4)  # seed 4 needs no attachment (as in the ui_path success test)
    task = env.ep.task
    assert not task.difficulty["require_attachment"]
    pid, claim = task.expected["patient_pid"], task.expected["claim_number"]
    wm = env.ep.baseline.watermarks["openemr.log"]
    # nothing happened yet: every rung False, evidence zero
    ms0 = await world.ui_milestones(env.ep.dbs, env.ep.baseline, task)
    assert ms0["rungs"] == {r: False for r in world.MILESTONE_RUNGS} and ms0["highest"] is None
    assert ms0["evidence"]["openemr_logins"] == 0 and ms0["evidence"]["portal_page_views"] == 0
    # a failed login, then a successful one, then the chart and a document view
    db = env.ep.dbs["openemr"]
    await db.execute_script("\n".join([
        osql.insert_log(id=wm + 1, event="login", category="", user="admin", patient_id=0, comments="failure: 127.0.0.1",
                        date="2026-09-08 10:00:00", success=0),
        osql.insert_log(id=wm + 2, event="login", category="", user="admin", patient_id=0, comments="success: 127.0.0.1",
                        date="2026-09-08 10:00:01"),
        osql.insert_log(id=wm + 3, event="http-request-update", category="", user="admin", patient_id=None,
                        comments=_b64("/openemr/interface/patient_file/summary/demographics.php"), date="2026-09-08 10:00:02"),
        osql.insert_log(id=wm + 4, event="patient-record-select", category="Patient Demographics", user="admin", patient_id=pid,
                        comments=_b64("SELECT * FROM patient_data WHERE pid = ?"), date="2026-09-08 10:00:03"),
    ]))
    ms1 = await world.ui_milestones(env.ep.dbs, env.ep.baseline, task)
    assert ms1["rungs"]["openemr_login"] and ms1["rungs"]["openemr_chart"] and not ms1["rungs"]["openemr_document"]
    assert ms1["evidence"]["openemr_logins"] == 1 and ms1["evidence"]["openemr_login_failures"] == 1
    assert ms1["evidence"]["openemr_rows_for_patient"] == 1 and ms1["highest"] == "openemr_chart"
    await db.execute_script(osql.insert_log(id=wm + 5, event="http-request-update", category="", user="admin", patient_id=None,
                                            comments=_b64(f"/openemr/controller.php?document&view&patient_id={pid}&doc_id=1"),
                                            date="2026-09-08 10:00:04"))
    # the portal side: claim page, appeal form, then the appeal itself
    c = portal_client(env)
    c.get(f"/claims/{claim}")
    ms2 = await world.ui_milestones(env.ep.dbs, env.ep.baseline, task)
    assert ms2["rungs"]["openemr_document"] and ms2["rungs"]["portal_claim"] and not ms2["rungs"]["portal_appeal_form"]
    assert ms2["evidence"]["openemr_document_paths"][0].startswith("/openemr/controller.php?document")
    c.get(f"/claims/{claim}/appeal")
    c.post(f"/claims/{claim}/appeal", data={"reason_code": "PRECERT_OBTAINED", "authorization_number": task.expected["auth_number"],
                                           "narrative": "Prior authorization was obtained before the service date."})
    obs, reward, term, trunc, info = await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 1.0 and v.reason_code == "OK", v.to_dict()
    ms = v.details["ui_milestones"]
    assert ms["rungs"] == {r: True for r in world.MILESTONE_RUNGS} and ms["n_reached"] == 6 and ms["highest"] == "appeal_submitted"
    assert ms["order"] == list(world.MILESTONE_RUNGS)
    # the verdict is unchanged by the rungs: same reward/milestones/reason as the oracle alone
    assert Verdict.from_dict(v.to_dict()).reward == 1.0 and v.milestones == 1.0
    await env.close()


async def test_ui_milestones_are_in_the_verdict_of_a_failed_episode_and_off_reward(world, backend):
    """An episode that only logged in and stopped: reward 0, NOT_DONE, but the login rung is recorded."""
    env = Env(world, backend, family="resolve_denial", settle_s=0)
    obs, info = await env.reset(6)
    wm = env.ep.baseline.watermarks["openemr.log"]
    await env.ep.dbs["openemr"].execute_script(osql.insert_log(
        id=wm + 1, event="login", category="", user="admin", patient_id=0, comments="success: 127.0.0.1", date="2026-09-08 10:00:01"))
    obs, reward, term, trunc, info = await env.step(Action.done(success=False, note="ask_user_question: password?"))
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "NOT_DONE" and v.milestones == 0.0
    ms = v.details["ui_milestones"]
    assert ms["rungs"]["openemr_login"] is True and ms["highest"] == "openemr_login" and ms["n_reached"] == 1
    assert not ms["rungs"]["portal_claim"] and not ms["rungs"]["appeal_submitted"]
    # a world without the hook contributes nothing
    from forkloop.world import World

    assert await World.ui_milestones(world, env.ep.dbs, env.ep.baseline, env.ep.task) is None
    await env.close()


async def test_milestone_staircase_script_reads_a_run(world, backend, tmp_path):
    """scripts/milestone_staircase.py aggregates the rungs over a run directory; a run recorded
    without ui_milestones says the database rungs need a re-run while the trajectory rungs still count."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import milestone_staircase as mstair

    from forkloop.trajectories import Recorder

    rec = Recorder(tmp_path / "runs", run_id="stair-test", meta={"policy": "scripted"})
    env = Env(world, backend, family="resolve_denial", settle_s=0, recorder=rec)
    # episode 1: logs in, types the auth number, stops
    obs, info = await env.reset(7)
    wm = env.ep.baseline.watermarks["openemr.log"]
    auth = env.ep.task.expected["auth_number"]
    await env.ep.dbs["openemr"].execute_script(osql.insert_log(
        id=wm + 1, event="login", category="", user="admin", patient_id=0, comments="success", date="2026-09-08 10:00:01"))
    await env.step(Action.parse({"type": "type", "text": "admin"}))
    await env.step(Action.parse({"type": "type", "text": auth}))
    await env.step(Action.done(success=False, note="stopped"))
    await env.verify()
    # episode 2: nothing at all
    obs, info = await env.reset(8)
    await env.step(Action.done(success=False, note="stopped"))
    await env.verify()
    await env.close()
    res = mstair.staircase(rec.dir)
    assert res["n"] == 2 and res["has_ui_milestones"] is True
    assert res["counts"]["openemr_login"] == 1 and res["counts"]["login_page"] == 1 and res["counts"]["auth_typed"] == 1
    assert res["counts"]["appeal_submitted"] == 0 and res["percent"]["openemr_login"] == 50.0
    table = mstair.format_table([res])
    assert "| openemr_login | 1/2 = 50 % |" in table and "needs a re-run" not in table
    # strip the rungs from the verdicts, as a run from before 2026-09-05 would look
    for d in rec.episodes():
        vp = d / "verdict.json"
        v = json.loads(vp.read_text())
        v["details"].pop("ui_milestones", None)
        vp.write_text(json.dumps(v))
    res_old = mstair.staircase(rec.dir)
    assert res_old["has_ui_milestones"] is False and res_old["counts"]["auth_typed"] == 1
    assert "needs a re-run" in mstair.format_table([res_old])
