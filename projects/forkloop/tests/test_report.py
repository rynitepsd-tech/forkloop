"""`forkloop report` over recorded artifacts, the ledger subcommand, and the offline path
staying usable when a Solari golden id is in the environment."""

from __future__ import annotations

import json
import re

import pytest

from forkloop import cli
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env, run_episode
from forkloop.metrics import summarize_run
from forkloop.policies.scripted import ScriptedPolicy
from forkloop.report import episode_report, run_report, side_effect_failures
from forkloop.trajectories import Recorder
from forkloop.world import load_world

A_PLUS, A_MINUS, B_PLUS = "click(220, 200)", "click(100, 200)", "click(540, 200)"


async def _toy_run(tmp_path, *, extra_actions):
    world = load_world("toy-counter")
    backend = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=1, gui_factory=world.gui_factory())
    rec = Recorder(tmp_path / "runs", run_id="toy", meta={"policy": "scripted", "backend": "fake"})
    env = Env(world, backend, family="reach_target", recorder=rec, settle_s=0)
    task = world.generate("reach_target", 3, "train")
    delta = task.expected["a"] - task.expected["a0"]
    actions = [A_PLUS if delta > 0 else A_MINUS] * abs(delta) + extra_actions
    try:
        await run_episode(env, ScriptedPolicy(actions), 3, family="reach_target")
    finally:
        await env.close()
        backend.cleanup()
    return rec.dir, rec.episodes()[0]


async def test_report_names_the_collateral_row_and_labels_the_simulation(tmp_path):
    run_dir, ep_dir = await _toy_run(tmp_path, extra_actions=[B_PLUS])
    text = episode_report(ep_dir, turns=2)
    assert "backend   fake: OFFLINE SIMULATION" in text
    assert re.search(r"FAIL  b_untouched .* -> COLLATERAL_EDIT", text)
    assert re.search(r"FAIL  no_collateral .*1 row\(s\) changed outside the allow-list \[state.counters rows 1 may change\]", text)
    assert "changed  state.counters pk=2" in text
    assert "side effects  DETECTED: b_untouched, no_collateral" in text
    table = run_report(run_dir)
    assert "side-effect failures" in table
    assert re.search(r"reach_target\s+3\s+1\s+0\.0\s+COLLATERAL_EDIT.*b_untouched, no_collateral", table)
    assert "success   0/1" in table


async def test_report_on_a_clean_success_shows_every_check_passing(tmp_path):
    _, ep_dir = await _toy_run(tmp_path, extra_actions=[])
    text = episode_report(ep_dir, turns=0)
    assert "outcome   reward 1.0  reason OK" in text
    assert "side effects  none detected" in text
    assert "FAIL" not in text
    assert "expected (controller-only ground truth" in text and re.search(r"\n  a \s+\d+", text)


async def test_env_info_never_carries_what_the_report_prints(tmp_path):
    """The report prints `expected` and the oracle spec for the researcher; the policy-facing
    `info` dict and `Observation` must not carry them."""
    world = load_world("toy-counter")
    backend = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=1, gui_factory=world.gui_factory())
    env = Env(world, backend, family="reach_target", settle_s=0)
    try:
        obs, info = await env.reset(3)
        assert not {"expected", "oracle", "seeding", "difficulty"} & set(info)
        assert set(info) >= {"task_id", "family", "seed", "split", "world", "budget", "step"}
        assert set(obs.to_dict()) <= {"screenshot", "previous_screenshot", "instruction", "step", "history", "width", "height"}
    finally:
        await env.close()
        backend.cleanup()




def test_not_done_count_invariant_is_not_a_side_effect(tmp_path, monkeypatch):
    # A Solari golden id in the environment must not break the offline path (the pool ignores it).
    monkeypatch.setenv("FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1", "snap_from_someone_elses_account")
    rc = cli.main(["run", "--backend", "fake", "--world", "claims-ops-v1", "--family", "resolve_denial",
                   "--policy", "scripted", "--seed", "4", "--runs", str(tmp_path / "runs"), "--run-id", "offline"])
    assert rc == 1  # a do-nothing policy does not verify
    run_dir = tmp_path / "runs" / "offline"
    ep_dir = next((run_dir / "episodes").iterdir())
    text = episode_report(ep_dir, turns=0)
    assert "reason NOT_DONE" in text
    verdict = json.loads((ep_dir / "verdict.json").read_text())
    assert verdict["details"]["single_appeal"]["passed"] is False
    assert verdict["details"]["single_appeal"]["reason_code"] == "NOT_DONE"
    assert "side effects  none detected" in text
    manifest = json.loads((ep_dir / "manifest.json").read_text())
    verdict = json.loads((ep_dir / "verdict.json").read_text())
    assert side_effect_failures(verdict["details"], {c["id"]: c for c in manifest["oracle"]["invariants"]}) == []
    assert re.search(r"resolve_denial\s+4\s+1\s+0\.0\s+NOT_DONE\s+done .* -\s*$", run_report(run_dir), re.M)


def test_report_rejects_a_directory_that_is_neither_run_nor_episode(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["report", str(tmp_path)])


def test_ledger_cli_creates_once_and_shows(tmp_path, capsys):
    path = tmp_path / "session-ledger.sqlite"
    assert cli.main(["ledger", str(path), "--create", "--solari-usd", "5"]) == 0
    out = capsys.readouterr().out
    assert f"export FORKLOOP_SESSION_LEDGER={path.resolve()}" in out
    assert re.search(r"solari\s+.*ceiling_usd=5\.0\s+stop_usd=4\.0", out)
    with pytest.raises(SystemExit):
        cli.main(["ledger", str(path), "--create"])
    assert cli.main(["ledger", str(path)]) == 0
    assert "operations: 0" in capsys.readouterr().out


def test_metrics_tolerates_a_session_ledger_from_another_machine(tmp_path):
    run_dir = tmp_path / "copied"
    (run_dir / "episodes").mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"backend": "solari", "session_ledger": "/Users/someone/else/ledger.sqlite"}))
    s = summarize_run(run_dir)
    assert s["session_spend"] == {"unavailable": "/Users/someone/else/ledger.sqlite"}
