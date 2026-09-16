"""CLI limits must constrain the executed episode, not just its metadata."""
import json

from forkloop.cli import main


def test_run_honors_action_budget(tmp_path):
    result = main([
        "run", "--backend", "fake", "--family", "resolve_denial",
        "--policy", "scripted", "--script", '["click(10,10)","click(20,20)"]',
        "--max-steps", "1", "--runs", str(tmp_path), "--run-id", "bounded",
    ])
    assert result == 1
    verdict = json.loads(next((tmp_path / "bounded" / "episodes").glob("*/verdict.json")).read_text())
    assert verdict["n_steps"] == 1
    assert verdict["end_reason"] == "max_steps"
