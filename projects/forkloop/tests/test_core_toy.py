"""End-to-end tests of the core loop on the toy world with the fake backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forkloop.actions import Action, InvalidAction
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env, run_episode
from forkloop.exporters import export_jsonl, export_osworld, export_sft_pairs
from forkloop.metrics import summarize_run, wilson
from forkloop.policies.scripted import CallbackPolicy, RandomPolicy, ScriptedPolicy
from forkloop.pool import WorkerPool
from forkloop.search import best_of_n
from forkloop.trajectories import Recorder
from forkloop.world import load_world, list_worlds

A_PLUS, A_MINUS, B_PLUS = "click(220, 200)", "click(100, 200)", "click(540, 200)"


@pytest.fixture
def world():
    return load_world("toy-counter")


@pytest.fixture
def backend(tmp_path, world):
    b = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=2, gui_factory=world.gui_factory())
    yield b
    b.cleanup()


def solve(task):
    """Scripted solution derived from the controller-only expected values (tests only)."""
    a0, target = task.expected["a0"], task.expected["a"]
    delta = target - a0
    return [A_PLUS if delta > 0 else A_MINUS] * abs(delta)


# ---------------------------------------------------------------- actions


def test_action_parse_roundtrip():
    for s in ["click(1, 2)", 'type("hi")', 'key("ctrl+l")', 'scroll(3, 4, "up", 2)', "wait(0.5)", "done()", "drag(1, 2, 3, 4)"]:
        a = Action.parse(s)
        assert Action.parse(a.to_dict()).to_compact() == a.to_compact()
        assert Action.parse(a.to_json()).to_compact() == a.to_compact()
    with pytest.raises(InvalidAction):
        Action.parse("click(2000, 10)", width=1280, height=720)
    with pytest.raises(InvalidAction):
        Action.parse("fly(1)")
    assert Action.parse({"type": "left_click", "coordinate": [5, 6]}).to_compact() == "click(5, 6)"
    assert Action.key("Enter").keys == ("Return",)


# ---------------------------------------------------------------- world


def test_world_registry():
    assert "toy-counter" in list_worlds()
    w = load_world("toy-counter")
    t = w.generate("reach_target", 1, "train")
    t2 = w.generate("reach_target", 1, "train")
    assert t.to_json() == t2.to_json()
    assert t.task_id == "reach_target-train-000001"
    assert "expected" not in t.public_info


# ---------------------------------------------------------------- env loop


async def test_scripted_success_and_recorder(world, backend, tmp_path):
    rec = Recorder(tmp_path / "runs", run_id="t1", meta={"policy": "scripted"})
    env = Env(world, backend, family="reach_target", recorder=rec, settle_s=0)
    task = world.generate("reach_target", 7, "train")
    obs, info = await env.reset(7, task=task)
    assert obs.width == 640 and obs.screenshot[:4] == b"\x89PNG"
    pol = ScriptedPolicy(solve(task))
    while True:
        a, meta = await pol.act(obs)
        obs, r, term, trunc, info = await env.step(a, meta=meta)
        if term or trunc:
            break
    v = await env.verify()
    assert v.reward == 1.0 and v.reason_code == "OK", v.to_dict()
    ep_dir = rec.episodes()[0]
    steps = [json.loads(l) for l in (ep_dir / "steps.jsonl").read_text().splitlines()]
    assert len(steps) == len(solve(task)) + 1
    assert (ep_dir / "verdict.json").exists() and (ep_dir / "reset.json").exists()
    assert (ep_dir / steps[0]["shot_before"]).exists()
    manifest = json.loads((ep_dir / "manifest.json").read_text())
    assert manifest["expected"]["a"] == task.expected["a"]
    await env.close()
    # exporters + metrics
    n = export_jsonl(rec.dir, tmp_path / "eps.jsonl")
    assert n == 1
    stats = export_sft_pairs(rec.dir, tmp_path / "sft.jsonl")
    assert stats["episodes"] == 1 and stats["examples"] == len(steps)
    assert export_osworld(rec.dir, tmp_path / "osworld") == 1
    s = summarize_run(rec.dir)
    assert s["success_rate"]["value"] == 1.0 and s["n_episodes"] == 1


async def test_oracle_rejects_collateral_and_wrong_value(world, backend):
    env = Env(world, backend, family="reach_target", settle_s=0)
    task = world.generate("reach_target", 3, "train")
    obs, _ = await env.reset(3, task=task)
    actions = solve(task) + [B_PLUS]  # correct A, but also touches B
    pol = ScriptedPolicy(actions)
    while True:
        a, meta = await pol.act(obs)
        obs, r, term, trunc, info = await env.step(a, meta=meta)
        if term or trunc:
            break
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "COLLATERAL_EDIT" and "b_untouched" in v.failed
    assert v.milestones == 1.0  # effect satisfied, invariant broken
    await env.close()


async def test_oracle_direct_db_write_tripwire(world, backend):
    env = Env(world, backend, family="reach_target", settle_s=0)
    task = world.generate("reach_target", 5, "train")
    obs, _ = await env.reset(5, task=task)
    # cheat: set the value directly in the DB (controller channel), then say done
    await env.ep.dbs["state"].execute_script(f"UPDATE counters SET value = {task.expected['a']} WHERE id = 1;")
    obs, r, term, trunc, info = await env.step(Action.done())
    v = await env.verify()
    assert v.reward == 0.0 and v.reason_code == "DIRECT_DB_WRITE", v.to_dict()
    await env.close()


async def test_budget_truncation_and_invalid_actions(world, backend):
    env = Env(world, backend, family="reach_target", settle_s=0, max_invalid=3)
    obs, _ = await env.reset(11)
    # invalid actions consume steps without touching the machine
    for _ in range(3):
        obs, r, term, trunc, info = await env.step("click(9999, 9999)")
    assert trunc and info["end_reason"] == "invalid_actions"
    v = await env.verify()
    assert v.reason_code in ("WRONG_VALUE", "INVALID_ACTION_LIMIT")
    await env.close()


async def test_revert_restores_state_between_episodes(world, backend):
    pool = WorkerPool(backend, world, size=1, mode="revert")
    env = Env(world, backend, family="reach_target", pool=pool, settle_s=0)
    obs, _ = await env.reset(1)
    m1 = env.ep.machine.id
    a0_first = env.ep.task.expected["a0"]
    await env.step(A_PLUS)
    await env.step(A_PLUS)
    v_before = await env.ep.dbs["state"].scalar("SELECT value FROM counters WHERE id = 1")
    await env.step(Action.done())
    obs, _ = await env.reset(2)
    assert env.ep.machine.id == m1  # same machine, reverted
    v_after = await env.ep.dbs["state"].scalar("SELECT value FROM counters WHERE id = 1")
    assert int(v_before) == a0_first + 2
    assert int(v_after) == env.ep.task.expected["a0"]  # golden state + seed 2's seeding, nothing from episode 1
    assert backend.counters["create"] == 1
    assert env.last_reset_report["ok"] and env.last_reset_report["method"] == "revert"
    assert [s["name"] for s in env.last_reset_report["stages"]][:3] == ["restore", "seed", "before_episode"]
    await env.close()
    await pool.close()


async def test_fork_mode_creates_fresh_machines(world, backend):
    pool = WorkerPool(backend, world, size=1, mode="fork")
    env = Env(world, backend, family="reach_target", pool=pool, settle_s=0)
    await env.reset(1)
    m1 = env.ep.machine.id
    await env.step(Action.done())
    await env.reset(2)
    assert env.ep.machine.id != m1
    assert backend.counters["create"] >= 2
    await env.close()
    await pool.close()


async def test_best_of_n_revert_mode_finds_solution(world, backend, tmp_path):
    """First action is uncertain and wrong; the proposed alternative is right. Search must keep the right branch."""
    rec = Recorder(tmp_path / "runs", run_id="search")
    env = Env(world, backend, family="reach_target", recorder=rec, settle_s=0)
    task = world.generate("reach_target", 21, "train")
    delta = task.expected["a"] - task.expected["a0"]
    step = A_PLUS if delta > 0 else A_MINUS

    class TwoHeaded:
        """Greedy plan: wrong first click, then the right clicks. propose() offers the right first click."""

        name = "two"

        def __init__(self):
            self.calls = 0

        async def act(self, obs):
            self.calls += 1
            if obs.step == 0:
                a = Action.parse(B_PLUS)
                return a, {"raw_action": a.to_compact(), "confidence": 0.1}
            # count how many A-clicks happened so far from the visible history
            done_a = sum(1 for h in obs.history if h == step)
            if done_a < abs(delta):
                a = Action.parse(step)
                return a, {"raw_action": a.to_compact(), "confidence": 0.9}
            a = Action.done()
            return a, {"raw_action": a.to_compact(), "confidence": 0.9}

        async def propose(self, obs, n):
            a = Action.parse(step)
            return [(a, {"raw_action": a.to_compact()})][:n]

    v = await best_of_n(env, TwoHeaded(), 2, 21, family="reach_target", branch_prob=0.0, confidence_threshold=0.5, mode="revert")
    assert v.reward == 1.0, v.to_dict()
    ep = rec.episodes()[0]
    steps = [json.loads(l) for l in (ep / "steps.jsonl").read_text().splitlines()]
    assert all("adopted_from" in (s.get("search") or {}) for s in steps)
    assert steps[0]["action"]["x"] == Action.parse(step).x  # the winning branch's first action
    assert (ep / "branches").exists() and len(list((ep / "branches").iterdir())) == 2
    branch_verdicts = sorted(json.loads((b / "verdict.json").read_text())["reward"] for b in (ep / "branches").iterdir())
    assert branch_verdicts == [0.0, 1.0]
    await env.close()


async def test_random_policy_runs(world, backend):
    env = Env(world, backend, family="reach_target", settle_s=0)
    v = await run_episode(env, RandomPolicy(seed=1, p_done=0.2), 4)
    assert v.reward in (0.0, 1.0)
    await env.close()


def test_wilson():
    p, lo, hi = wilson(0, 10)
    assert p == 0 and lo == 0 and hi < 0.35
    p, lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo > 0.65
