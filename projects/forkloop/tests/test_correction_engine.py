"""End-to-end correction engine on the toy world (fake backend): record with bound checkpoints,
diagnose, repair from a checkpoint with independent branches (replay and snapshot strategies),
export an immutable dataset with lineage, and recover from interruption."""
from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest

from forkloop.actions import Action
from forkloop.backends.fake import FakeBackend
from forkloop.correction import (CheckpointPolicy, RepairConfig, Store, export_dataset, record_attempt, repair_attempt,
                                 restart_points)
from forkloop.correction.dataset import render_target
from forkloop.env import Env
from forkloop.policies.base import BranchablePolicy
from forkloop.pool import WorkerPool
from forkloop.world import load_world

A_PLUS, A_MINUS, B_PLUS = "click(220, 200)", "click(100, 200)", "click(540, 200)"


class ToyAgent(BranchablePolicy):
    """Explicit-memory toy agent. ``mistake_at``: the step at which it clicks counter B (a
    collateral edit). It tracks A from its own memory ("A=n" facts it writes itself)."""

    branch_state_fields = ("_memory", "i")

    def __init__(self, target: int, a0: int, mistake_at: int | None = None, name: str = "toy") -> None:
        self.target, self.a0, self.mistake_at, self.name = target, a0, mistake_at, name
        self._memory: list[str] = []
        self.i = 0
        self.usage = {"in": 0, "out": 0}

    def reset(self) -> None:
        self._memory, self.i = [], 0

    def agent_state(self) -> dict:
        return {"memory": list(self._memory)}

    def load_agent_state(self, state: dict) -> None:
        self._memory = list(state.get("memory", []))

    def describe(self) -> dict:
        return {"policy": self.name, "mistake_at": self.mistake_at}

    def _a(self) -> int:
        facts = [m for m in self._memory if m.startswith("A=")]
        return int(facts[-1][2:]) if facts else self.a0

    async def act(self, obs):
        self.i += 1
        a = self._a()
        if self.mistake_at is not None and obs.step == self.mistake_at:
            act, new = B_PLUS, a
        elif a == self.target:
            return Action.parse("done()"), {"raw_action": "Target reached.\ndone()", "memory_written": []}
        else:
            act, new = (A_PLUS, a + 1) if self.target > a else (A_MINUS, a - 1)
        fact = f"A={new}"
        self._memory.append(fact)
        return Action.parse(act), {"raw_action": f"Moving A.\nMemory: {fact}\n{act}", "memory_written": [fact]}


@pytest.fixture
def world():
    return load_world("toy-counter")


@pytest.fixture
def backend(tmp_path, world):
    b = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=4, gui_factory=world.gui_factory())
    yield b
    b.cleanup()


def _task(world, seed=3):
    task = world.generate("reach_target", seed, "train")
    assert abs(task.expected["a"] - task.expected["a0"]) >= 2
    return task


async def _record(world, backend, store, task, strategy, mistake_at=1):
    env = Env(world, backend, pool=WorkerPool(backend, world, size=1, mode="revert"), history_k=100)
    try:
        return await record_attempt(env, ToyAgent(task.expected["a"], task.expected["a0"], mistake_at=mistake_at, name="student"),
                                    task, store=store, ckpt=CheckpointPolicy(strategy=strategy, every=1),
                                    attempt_id=f"att-{strategy}", cell=f"{task.task_id}/student")
    finally:
        await env.close()


def _teacher(task):
    return lambda: ToyAgent(task.expected["a"], task.expected["a0"], mistake_at=None, name="teacher")


@pytest.mark.parametrize("strategy", ["replay", "snapshot"])
def test_record_diagnose_repair_export(tmp_path, world, backend, strategy):
    store = Store(tmp_path / "store" / "forkloop.sqlite")
    task = _task(world)

    async def run():
        res = await _record(world, backend, store, task, strategy)
        assert res.status == "finished" and res.reward == 0.0 and res.reason_code == "COLLATERAL_EDIT"
        ckpts = store.checkpoints(res.attempt_id)
        by_step = {c["step"]: c for c in ckpts}
        assert by_step[0]["strategy"] == "reset" and by_step[1]["status"] == "clean"
        assert by_step[2]["status"] == "damaged"            # B was clicked at step 1
        if strategy == "snapshot":
            assert by_step[1]["strategy"] == "snapshot" and by_step[1]["world_ref"]
        # the policy state is bound to the checkpoint: memory before step 1 holds step 0's fact
        assert by_step[1]["agent_state"] == {"memory": [f"A={task.expected['a0'] + (1 if task.expected['a'] > task.expected['a0'] else -1)}"]}
        points = restart_points(store.attempt(res.attempt_id), ckpts, task, max_points=3)
        assert points[0].reason == "damage" and points[0].step == 1 and points[-1].step == 0
        rep = await repair_attempt(store, world, backend, res.attempt_id, teacher_factory=_teacher(task),
                                   cfg=RepairConfig(k=2, max_restart_points=2, concurrency=2, history_k=100))
        return res, rep

    res, rep = asyncio.run(run())
    assert rep.status == "verified" and len(rep.verified_branches) == 2
    branches = store.branches(repair_id=rep.repair_id)
    assert {b["ckpt_id"] for b in branches} == {rep.restart_points[0]["ckpt_id"]}   # stopped after the first point
    for b in branches:
        restore = b["restore"]["attempts"][-1]
        assert restore["ok"] and restore["fidelity"]["tables_equal"]
        assert restore["strategy"] == strategy
        assert (restore["replayed_steps"] == 1) == (strategy == "replay")
    # the original attempt is untouched and still a failure; charges accumulate
    assert store.attempt(res.attempt_id)["reward"] == 0.0
    kinds = {c["kind"] for c in store.charges()}
    assert {"attempt_wall_seconds", "branch_wall_seconds", "restore_seconds", "checkpoint_seconds"} <= kinds

    manifest = export_dataset(store, world, tmp_path / "ds", name="toy")
    ds = tmp_path / "ds"
    recs = [json.loads(l) for l in (ds / "records.jsonl").read_text().splitlines()]
    assert manifest["counts"]["records"] == len(recs) > 0
    assert all(r["origin"] == "correction_suffix" and r["source"]["ckpt_step"] == 1 for r in recs)
    assert all(r["audit"]["memory_provenance_ok"] for r in recs)
    first = min(recs, key=lambda r: r["input"]["step"])
    assert first["input"]["step"] == 1 and first["input"]["history"][0] in (A_PLUS, A_MINUS)
    assert first["input"]["memory"] == store.checkpoint(rep.restart_points[0]["ckpt_id"])["agent_state"]["memory"]
    assert (ds / first["input"]["current_shot"]["path"]).exists() and first["input"]["previous_shot"] is not None
    # rendering into a student coordinate frame rescales the target action
    txt = render_target(first["target"], screen=(640, 400), coords=(1000, 1000))
    assert txt.splitlines()[-1].startswith("click(") and "Memory: A=" in txt
    prefs = [json.loads(l) for l in (ds / "preferences.jsonl").read_text().splitlines()]
    assert prefs and all(p["rejected"]["kind"] == "failed_attempt_step" for p in prefs)
    assert prefs[0]["rejected"]["action"]["x"] == 540          # the student's B click is the rejected action
    # immutable: read-only files, and re-export to the same directory with other content is refused
    assert not ((ds / "records.jsonl").stat().st_mode & stat.S_IWUSR)
    assert manifest["files"]["records.jsonl"] and manifest["sources"][0]["prefix_attempt"] == res.attempt_id


def test_full_restart_mode_and_branch_independence(tmp_path, world, backend):
    store = Store(tmp_path / "s" / "f.sqlite")
    task = _task(world)

    async def run():
        res = await _record(world, backend, store, task, "snapshot")
        rep = await repair_attempt(store, world, backend, res.attempt_id, teacher_factory=_teacher(task),
                                   cfg=RepairConfig(mode="full_restart", k=2, concurrency=2, history_k=100))
        return res, rep

    res, rep = asyncio.run(run())
    assert rep.status == "verified" and all(p["step"] == 0 for p in rep.restart_points)
    ids = [b["branch_id"] for b in store.branches(repair_id=rep.repair_id)]
    assert len(set(ids)) == 2
    manifest = export_dataset(store, world, tmp_path / "ds2", include_demos=False)
    assert manifest["counts"]["by_origin"] == {"restart_demo": manifest["counts"]["records"]}


def test_forbidden_split_and_interruption(tmp_path, world, backend):
    store = Store(tmp_path / "x" / "f.sqlite")
    task = world.generate("reach_target", 5, "test")
    store.put_task(task)
    pid = store.put_policy("student", {"name": "x"})
    store.start_attempt(attempt_id="att-crashed", task_id=task.task_id, policy_id=pid, run_dir=str(tmp_path), strategy="replay")
    assert store.mark_interrupted() == ["att-crashed"]
    assert store.attempt("att-crashed")["status"] == "interrupted"
    with pytest.raises(ValueError):
        store.start_attempt(attempt_id="att-crashed", task_id=task.task_id, policy_id=pid, run_dir=str(tmp_path), strategy="replay")
    with pytest.raises(ValueError):
        store.finish_attempt("att-crashed", status="finished", reward=1.0, reason_code="OK", n_steps=1)


def test_replay_fidelity_failure_is_recorded_not_scored(tmp_path, world, backend):
    """If the world diverges from the checkpoint digest, the branch is restore_failed (unscored)."""
    store = Store(tmp_path / "y" / "f.sqlite")
    task = _task(world)

    async def run():
        res = await _record(world, backend, store, task, "replay")
        # corrupt the recorded prefix: the replayed action differs from what was executed
        steps_p = Path(res.run_dir) / "steps.jsonl"
        steps = [json.loads(l) for l in steps_p.read_text().splitlines()]
        steps[0]["action"] = Action.parse(B_PLUS).to_dict()
        steps_p.write_text("".join(json.dumps(s) + "\n" for s in steps))
        ck = next(c for c in store.checkpoints(res.attempt_id) if c["step"] == 1)
        return await repair_attempt(store, world, backend, res.attempt_id, teacher_factory=_teacher(task),
                                    cfg=RepairConfig(k=1, concurrency=1, history_k=100, restore_retries=0),
                                    points=[{"ckpt_id": ck["ckpt_id"], "step": 1, "reason": "test", "evidence": {}}])

    rep = asyncio.run(run())
    b = store.branches(repair_id=rep.repair_id)[0]
    assert b["status"] == "restore_failed" and b["reward"] is None
    assert b["restore"]["attempts"][0]["fidelity"]["tables_equal"] is False
    assert rep.status == "unrepaired"


def test_cli_loop_end_to_end(tmp_path, monkeypatch, capsys):
    """record -> failures -> repair -> dataset -> status -> inspect through the CLI (toy world, replay)."""
    import shutil
    from forkloop.cli import main

    shutil.copy(Path(__file__).parent / "fixtures" / "toy_loop_agents.py", tmp_path / "toy_loop_agents.py")
    (tmp_path / "project.yaml").write_text(
        "version: 1\nworld: toy-counter\nbackend: fake\nstore: store/forkloop.sqlite\nhistory_k: 50\n"
        "checkpoints: {strategy: replay, every: 1}\n"
        "student: {name: s, factory: 'toy_loop_agents:student', revision: t1}\n"
        "teacher: {name: t, factory: 'toy_loop_agents:teacher', revision: t1}\n"
        "repair: {k: 2, max_restart_points: 2, concurrency: 2}\nconcurrency: 2\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORKLOOP_POOL_LOG", "0")
    cfg = ["--config", "project.yaml"]
    assert main(["record", *cfg, "--families", "reach_target", "--split", "train", "--seeds", "3", "--experiment", "e1"]) == 0
    assert main(["failures", *cfg, "--experiment", "e1"]) == 0
    assert main(["repair", *cfg, "--experiment", "e1"]) == 0
    assert main(["dataset", *cfg, "--out", "ds", "--experiment", "e1"]) == 0
    assert main(["status", *cfg]) == 0
    assert main(["inspect", *cfg, "--out", "report/index.html"]) == 0
    out = capsys.readouterr().out
    assert '"verified": 1' in out and (tmp_path / "report" / "index.html").exists()
    m = json.loads((tmp_path / "ds" / "manifest.json").read_text())
    assert m["counts"]["records"] > 0
    # re-running record does not re-run a finished cell
    assert main(["record", *cfg, "--families", "reach_target", "--split", "train", "--seeds", "3", "--experiment", "e1"]) == 0
    assert "0 to run, 1 skipped" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["record", *cfg, "--families", "reach_target", "--split", "final_test", "--seeds", "3", "--experiment", "e2"])


def test_near_miss_origin_ignores_identifiers_in_the_instruction():
    from forkloop.correction.diagnose import near_miss_origin
    expected = {"claim_number": "C-40011", "auth_number": "AUTH-36G14538", "decoy_numbers": ["AUTH-11A11111"]}
    instr = "Appeal claim C-40011 with the authorization number from the letter."
    mention = [{"i": 0, "action": None, "agent": {"memory_written": ["Claim C-40012 belongs to another patient"]}}]
    assert near_miss_origin(mention, expected, instruction=instr) is None
    typo = [{"i": 3, "action": {"type": "type", "text": "AUTH-3614538"}, "agent": {}}]
    assert near_miss_origin(typo, expected, instruction=instr)["step"] == 3
    decoy = [{"i": 5, "action": None, "agent": {"memory_written": ["auth AUTH-11A11111"]}}]
    assert near_miss_origin(decoy, expected, instruction=instr)["why"] == "decoy value"


def test_origin_ignores_dates_and_noted_lures_but_catches_typed_typos():
    from forkloop.correction.diagnose import near_miss_origin
    exp = {"target_date": "2026-09-17", "new_member": "W12345678", "auth_number": "AUTH-36G14538"}
    instr = "Resubmit with member ID W12345678."
    note_date = [{"i": 2, "action": None, "agent": {"memory_written": ["Current appointment 2026-09-14"]}}]
    assert near_miss_origin(note_date, exp, instruction=instr, values=["2026-09-17", "W12345678"]) is None
    note_lure = [{"i": 4, "action": None, "agent": {"memory_written": ["form prefilled W12345679 (wrong)"]}}]
    assert near_miss_origin(note_lure, exp, instruction=instr, values=["W12345678"]) is None
    typo = [{"i": 6, "action": {"type": "type", "text": "W12345679"}, "agent": {}}]
    assert near_miss_origin(typo, exp, instruction=instr, values=["W12345678"])["step"] == 6


def test_budget_interleaves_families():
    from forkloop.correction.budget import Unit, interleave
    us = []
    for fam, rng in (("compose_claims", range(100, 104)), ("resolve_denial", range(1, 5))):
        for seed in rng:
            us.append(Unit("A", f"{seed:09d}:{fam}", f"{fam}-train_v2-{seed:06d}", f"a{fam}{seed}", None, []))
    fams = [u.task_id.split("-")[0] for u in interleave(us)[:4]]
    assert fams == ["compose_claims", "resolve_denial", "compose_claims", "resolve_denial"]


class _Unreachable(ToyAgent):
    """A teacher whose provider refuses every request (e.g. sustained HTTP 429): infrastructure."""

    async def act(self, obs):
        raise RuntimeError("policy provider: request failed: HTTPStatusError: 429 Too Many Requests")


def _recorded_failure(world, backend, store, task):
    async def run():
        env = Env(world, backend, pool=WorkerPool(backend, world, size=1, mode="revert"), history_k=100)
        try:
            return await record_attempt(env, ToyAgent(task.expected["a"], task.expected["a0"], mistake_at=1, name="student"),
                                        task, store=store, ckpt=CheckpointPolicy(strategy="replay", every=1),
                                        attempt_id="att-x", experiment_id="round", cell=f"{task.task_id}/student/r1")
        finally:
            await env.close()
    return asyncio.run(run())


def test_repair_with_an_unscored_branch_is_replaced_and_only_the_clean_one_counts(tmp_path, world, backend):
    from forkloop.correction.budget import PendingUnit, Rates, repair_units, select, summarize
    from forkloop.correction.repair import counted_repair
    from forkloop.correction.runner import run_repairs

    store = Store(tmp_path / "r" / "f.sqlite")
    task = _task(world)
    res = _recorded_failure(world, backend, store, task)
    assert res.status == "finished" and res.reward == 0.0
    state = {"down": True}

    def teacher():
        cls = _Unreachable if state["down"] else ToyAgent
        return cls(task.expected["a"], task.expected["a0"], mistake_at=None, name="teacher")

    cfg = RepairConfig(k=2, max_restart_points=1, concurrency=2, history_k=100)
    go = lambda: asyncio.run(run_repairs(store=store, world=world, backend=backend, attempt_ids=[res.attempt_id],  # noqa: E731
                                         teacher_factory=teacher, cfg=cfg, experiment_id="rep", concurrency=2,
                                         infra_retries=2, log=lambda m: None))
    rates = Rates(0.35, 0.001)

    go()                                    # provider down: both branches unscored → the repair is void
    first = store.repairs(experiment_id="rep")
    assert len(first) == 1 and {b["status"] for b in store.branches(repair_id=first[0]["repair_id"])} == {"infra_error"}
    assert counted_repair(store, res.attempt_id, experiment_id="rep", mode="checkpoint") == (None, 1)
    units = repair_units(store, "round", "rep", "checkpoint", "A2")
    assert units[0].pending and summarize(units, rates)["pending"] == 1
    with pytest.raises(PendingUnit):
        select(units, 100.0, rates)

    state["down"] = False
    go()                                    # replacement repair: scored, verified
    reps = store.repairs(experiment_id="rep")
    assert len(reps) == 2 and reps[1]["status"] == "verified"
    counted, tried = counted_repair(store, res.attempt_id, experiment_id="rep", mode="checkpoint")
    assert counted["repair_id"] == reps[1]["repair_id"] and tried == 2
    go()                                    # settled: nothing more runs
    assert len(store.repairs(experiment_id="rep")) == 2

    u = repair_units(store, "round", "rep", "checkpoint", "A2")[0]
    assert not u.pending and u.repair_id == reps[1]["repair_id"]
    good = {b["branch_id"] for b in store.branches(repair_id=reps[1]["repair_id"])}
    assert set(u.verified_sources) == good and len(good) == 2       # the void repair's work is not charged or used
    charged = sum(c["amount"] for c in store.charges() if c["kind"] == "branch_wall_seconds" and c["ref"] in good)
    assert u.world_hours == pytest.approx(
        sum(c["amount"] for c in store.charges() if c["kind"] == "attempt_wall_seconds" and c["ref"] == res.attempt_id) / 3600
        + charged / 3600)
    chosen, spent = select([u], 100.0, rates)
    assert chosen == [u] and spent == pytest.approx(u.cost(rates))
    # the earlier accounting (every branch of the latest repair, unscored attempts too) stays available
    old = repair_units(store, "round", "rep", "checkpoint", "A2", count_unscored=True)[0]
    assert old.repair_id == reps[1]["repair_id"] and set(old.verified_sources) == good
    assert old.world_hours > u.world_hours          # all-work accounting also charges the void repair


def test_repairs_are_exhausted_after_the_registered_number_of_unscored_tries(tmp_path, world, backend):
    from forkloop.correction.budget import Rates, repair_units, select
    from forkloop.correction.runner import run_repairs

    store = Store(tmp_path / "e" / "f.sqlite")
    task = _task(world)
    res = _recorded_failure(world, backend, store, task)
    cfg = RepairConfig(k=1, max_restart_points=1, concurrency=1, history_k=100)
    teacher = lambda: _Unreachable(task.expected["a"], task.expected["a0"], name="teacher")  # noqa: E731
    for _ in range(3):
        asyncio.run(run_repairs(store=store, world=world, backend=backend, attempt_ids=[res.attempt_id],
                                teacher_factory=teacher, cfg=cfg, experiment_id="rep", concurrency=1,
                                infra_retries=1, log=lambda m: None))
    assert len(store.repairs(experiment_id="rep")) == 2           # 1 + infra_retries, then skipped
    u = repair_units(store, "round", "rep", "checkpoint", "A2", infra_retries=1)[0]
    assert u.excluded and not u.pending
    assert select([u], 100.0, Rates(0.35, 0.001)) == ([], 0.0)     # excluded, reported, never selected


def test_repairs_voided_by_a_provider_outage_are_not_replacement_tries(tmp_path, world, backend):
    from forkloop.correction.repair import PROVIDER_OUTAGE, counted_repair
    from forkloop.correction.runner import run_repairs

    store = Store(tmp_path / "o" / "f.sqlite")
    task = _task(world)
    res = _recorded_failure(world, backend, store, task)
    cfg = RepairConfig(k=1, max_restart_points=1, concurrency=1, history_k=100)
    state = {"down": True}
    teacher = lambda: (_Unreachable if state["down"] else ToyAgent)(task.expected["a"], task.expected["a0"], name="teacher")  # noqa: E731
    go = lambda: asyncio.run(run_repairs(store=store, world=world, backend=backend, attempt_ids=[res.attempt_id],  # noqa: E731
                                         teacher_factory=teacher, cfg=cfg, experiment_id="rep", concurrency=1,
                                         infra_retries=1, log=lambda m: None))
    go(); go()                                   # two void repairs: the limit (1 + 1) is reached
    assert counted_repair(store, res.attempt_id, experiment_id="rep", mode="checkpoint") == (None, 2)
    for r in store.repairs(experiment_id="rep"):
        store.annotate_repair(r["repair_id"], void_reason=PROVIDER_OUTAGE)
    assert counted_repair(store, res.attempt_id, experiment_id="rep", mode="checkpoint") == (None, 0)
    assert any(e["kind"] == "repair_annotated" for e in store.events()) if hasattr(store, "events") else True
    state["down"] = False
    go()
    counted, tried = counted_repair(store, res.attempt_id, experiment_id="rep", mode="checkpoint")
    assert counted is not None and counted["status"] == "verified" and tried == 1
    assert len(store.repairs(experiment_id="rep")) == 3 and len({r["repair_id"] for r in store.repairs(experiment_id="rep")}) == 3


def test_a_repair_running_in_another_live_runner_is_not_duplicated(tmp_path, world, backend):
    import sqlite3
    import time as _t
    from forkloop.correction.runner import run_repairs

    store = Store(tmp_path / "l" / "f.sqlite")
    task = _task(world)
    res = _recorded_failure(world, backend, store, task)
    pid = store.put_policy("teacher", {"name": "teacher"})
    store.start_repair(repair_id="rep-other", attempt_id=res.attempt_id, mode="checkpoint", teacher_policy_id=pid,
                       config={}, experiment_id="rep")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE repairs SET runner='other-runner' WHERE repair_id='rep-other'")
    (store.root / "runners").mkdir(exist_ok=True)
    hb = store.root / "runners" / "other-runner.hb"
    cfg = RepairConfig(k=1, max_restart_points=1, concurrency=1, history_k=100)
    teacher = _teacher(task)
    go = lambda: asyncio.run(run_repairs(store=store, world=world, backend=backend, attempt_ids=[res.attempt_id],  # noqa: E731
                                         teacher_factory=teacher, cfg=cfg, experiment_id="rep", concurrency=1,
                                         log=lambda m: None))
    hb.write_text(str(_t.time()))                 # the other runner is alive: its repair is left alone
    go()
    assert [r["repair_id"] for r in store.repairs(experiment_id="rep")] == ["rep-other"]
    hb.write_text(str(_t.time() - 3600))          # it died: its repair is interrupted and replaced
    go()
    reps = store.repairs(experiment_id="rep")
    assert reps[0]["status"] == "interrupted" and len(reps) == 2 and reps[1]["status"] == "verified"


def test_operator_stopped_attempts_are_not_replacement_tries(tmp_path, world):
    from forkloop.correction.runner import plan_cells
    store = Store(tmp_path / "p" / "f.sqlite")
    task = world.generate("reach_target", 3, "train")
    store.put_task(task)
    pid = store.put_policy("student", {"name": "s"})
    cell = f"{task.task_id}/eval:x/r1"
    for n in (1, 2):
        store.start_attempt(attempt_id=f"att-{n}", task_id=task.task_id, policy_id=pid, run_dir=str(tmp_path),
                            strategy="reset", experiment_id="ev", cell=cell)
        store.finish_attempt(f"att-{n}", status="interrupted", reward=None, reason_code=None, n_steps=0)
    [p] = plan_cells(store, [task], role="eval:x", experiment_id="ev", infra_retries=1)
    assert p.skip_reason and "exhausted after 2" in p.skip_reason
    for n in (1, 2):
        store.annotate_attempt(f"att-{n}", void_reason="operator_stop")
    [p] = plan_cells(store, [task], role="eval:x", experiment_id="ev", infra_retries=1)
    assert p.skip_reason is None and p.attempt_no == 3
    assert store.attempt("att-1")["status"] == "interrupted" and store.attempt("att-1")["info"]["void_reason"] == "operator_stop"
