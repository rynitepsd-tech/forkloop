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
