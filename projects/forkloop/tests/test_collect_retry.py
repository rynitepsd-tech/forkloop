"""`collect --retry-failed N` on the fake backend: failed seeds are re-run on a fresh reset, the
shortest verified attempt per seed is what exporters and metrics see, and every attempt is
recorded in run.json / collect_summary.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forkloop import cli
from forkloop.exporters import export_jsonl, export_sft_pairs
from forkloop.metrics import summarize_run
from forkloop.policies.scripted import ScriptedPolicy
from forkloop.trajectories import Recorder, iter_episode_dirs, select_attempts
from forkloop.world import load_world

A_PLUS, A_MINUS = "click(220, 200)", "click(100, 200)"


def _solve(task) -> list[str]:
    a0, target = task.expected["a0"], task.expected["a"]
    delta = target - a0
    return [A_PLUS if delta > 0 else A_MINUS] * abs(delta)


def _attempt_policy(succeed_on: dict[int, int]):
    """Factory for cli._policy: seed -> the attempt number on which it should verify (0 = never)."""
    world = load_world("toy-counter")
    calls: list[tuple[int, int]] = []

    def factory(spec, args, *, family=None, seed=None, attempt=1, **_):
        calls.append((seed, attempt))
        if succeed_on.get(seed, 0) == attempt:
            return ScriptedPolicy(_solve(world.generate(family, seed, "train")))
        return ScriptedPolicy([])  # done() at once: reward 0, NOT_DONE

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


def _collect(tmp_path: Path, monkeypatch, factory, *extra: str, run_id: str = "r") -> Path:
    monkeypatch.setenv("FORKLOOP_CONCURRENCY", "1")
    monkeypatch.delenv("FORKLOOP_GOLDEN_SNAPSHOT_TOY_COUNTER", raising=False)
    monkeypatch.setattr(cli, "_policy", factory)
    rc = cli.main(["collect", "--world", "toy-counter", "--backend", "fake", "--policy", "scripted",
                   "--families", "reach_target", "--seeds", "0-1", "--concurrency", "1", "--pool-mode", "fork", "--reset-retries", "0",
                   "--runs", str(tmp_path / "runs"), "--run-id", run_id, *extra])
    assert rc == 0
    return tmp_path / "runs" / run_id


def test_retry_failed_reruns_only_unverified_seeds(tmp_path, monkeypatch):
    factory = _attempt_policy({0: 2, 1: 0})  # seed 0 verifies on its 2nd attempt, seed 1 never
    run = _collect(tmp_path, monkeypatch, factory, "--retry-failed", "2")
    # seed 0: attempts 1-2 (stops once verified); seed 1: attempts 1-3
    assert sorted(factory.calls) == [(0, 1), (0, 2), (1, 1), (1, 2), (1, 3)]

    every = iter_episode_dirs(run, include_superseded=True)
    selected = iter_episode_dirs(run)
    assert len(every) == 5 and len(selected) == 2
    by_seed = {}
    for d in selected:
        m = json.loads((d / "manifest.json").read_text())
        assert m["selected"] is True and m["superseded"] is False
        by_seed[m["seed"]] = m
    assert by_seed[0]["attempt"] == 2  # the verified one
    assert by_seed[1]["attempt"] == 3  # none verified: the last attempt stays visible
    for d in every:
        m = json.loads((d / "manifest.json").read_text())
        assert "attempt" in m and m["superseded"] == (d not in selected)

    meta = json.loads((run / "run.json").read_text())
    assert meta["retry_failed"] == 2 and meta["n_attempts"] == 5
    assert meta["attempts"]["reach_target:0"]["selected"] == by_seed[0]["episode_id"]
    assert [a["attempt"] for a in meta["attempts"]["reach_target:1"]["attempts"]] == [1, 2, 3]

    summary = {r["seed"]: r for r in json.loads((run / "collect_summary.json").read_text())}
    assert summary[0]["reward"] == 1.0 and summary[0]["reason"] == "OK" and summary[0]["n_attempts"] == 2
    assert summary[1]["reward"] == 0.0 and summary[1]["n_attempts"] == 3
    assert [a["attempt"] for a in summary[1]["attempts"]] == [1, 2, 3]
    assert summary[0]["episode_id"] == by_seed[0]["episode_id"]

    # exporters and metrics see one attempt per seed; cost counts all five
    s = summarize_run(run)
    assert s["n_episodes"] == 2 and s["n_attempts"] == 5 and s["n_superseded"] == 3
    assert s["success_rate"]["k"] == 1 and s["success_rate"]["n"] == 2
    assert export_jsonl(run, tmp_path / "eps.jsonl") == 2
    stats = export_sft_pairs(run, tmp_path / "sft.jsonl")
    assert stats["episodes"] == 1
    assert all(json.loads(l)["seed"] == 0 for l in (tmp_path / "sft.jsonl").read_text().splitlines())


def test_retry_failed_zero_is_single_pass(tmp_path, monkeypatch):
    factory = _attempt_policy({0: 1, 1: 0})
    run = _collect(tmp_path, monkeypatch, factory)
    assert sorted(factory.calls) == [(0, 1), (1, 1)]
    assert len(iter_episode_dirs(run, include_superseded=True)) == 2
    summary = json.loads((run / "collect_summary.json").read_text())
    assert [(r["seed"], r["reward"], r["n_attempts"]) for r in summary] == [(0, 1.0, 1), (1, 0.0, 1)]
    meta = json.loads((run / "run.json").read_text())
    assert meta["retry_failed"] == 0 and meta["n_attempts"] == 2
    assert summarize_run(run)["n_superseded"] == 0


def test_retry_stops_early_when_everything_verified(tmp_path, monkeypatch):
    factory = _attempt_policy({0: 1, 1: 2})
    run = _collect(tmp_path, monkeypatch, factory, "--retry-failed", "3")
    assert sorted(factory.calls) == [(0, 1), (1, 1), (1, 2)]
    s = summarize_run(run)
    assert s["success_rate"]["k"] == 2 and s["n_attempts"] == 3 and s["n_superseded"] == 1


def _fake_episode(run: Path, task_id: str, *, attempt: int, seed: int, reward: float, n_steps: int, eid: str) -> Path:
    d = run / "episodes" / eid
    (d / "shots").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"task_id": task_id, "family": "f", "seed": seed, "split": "train",
                                                 "world": "w", "instruction": "x", "episode_id": eid,
                                                 "generated_at": f"2026-09-03T00:0{attempt}:00+00:00", "attempt": attempt}))
    (d / "steps.jsonl").write_text("".join(json.dumps({"i": i, "action": None, "raw_action": "", "valid": True,
                                                       "shot_before": "", "shot_after": ""}) + "\n" for i in range(n_steps)))
    (d / "verdict.json").write_text(json.dumps({"reward": reward, "reason_code": "OK" if reward >= 1 else "NOT_DONE"}))
    return d


def test_select_attempts_prefers_shortest_verified(tmp_path):
    run = tmp_path / "runs" / "sel"
    (run / "episodes").mkdir(parents=True)
    _fake_episode(run, "t0", attempt=1, seed=0, reward=1.0, n_steps=9, eid="t0-a1")
    _fake_episode(run, "t0", attempt=2, seed=0, reward=1.0, n_steps=5, eid="t0-a2")
    _fake_episode(run, "t0", attempt=3, seed=0, reward=0.0, n_steps=2, eid="t0-a3")
    _fake_episode(run, "t1", attempt=1, seed=1, reward=0.0, n_steps=4, eid="t1-a1")
    _fake_episode(run, "t1", attempt=2, seed=1, reward=0.0, n_steps=7, eid="t1-a2")
    sel = select_attempts(run)
    assert sel["f:0"]["selected"] == "t0-a2"  # shortest verified, not the shortest overall
    assert sel["f:1"]["selected"] == "t1-a2"  # nothing verified: last attempt
    assert [d.name for d in iter_episode_dirs(run)] == ["t0-a2", "t1-a2"]
    assert len(iter_episode_dirs(run, include_superseded=True)) == 5
    # idempotent and stable
    assert select_attempts(run) == sel


def test_select_attempts_ties_go_to_earliest(tmp_path):
    run = tmp_path / "runs" / "tie"
    (run / "episodes").mkdir(parents=True)
    _fake_episode(run, "t0", attempt=1, seed=0, reward=1.0, n_steps=5, eid="t0-a1")
    _fake_episode(run, "t0", attempt=2, seed=0, reward=1.0, n_steps=5, eid="t0-a2")
    assert select_attempts(run)["f:0"]["selected"] == "t0-a1"


def test_recorder_update_meta_merges(tmp_path):
    rec = Recorder(tmp_path / "runs", run_id="m", meta={"policy": "scripted"})
    rec.update_meta(n_attempts=3)
    meta = json.loads((rec.dir / "run.json").read_text())
    assert meta["policy"] == "scripted" and meta["n_attempts"] == 3 and meta["run_id"] == "m"
