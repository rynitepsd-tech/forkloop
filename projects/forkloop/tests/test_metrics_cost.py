"""Token accounting and pricing in forkloop.metrics (docs/contracts.md §12)."""

from __future__ import annotations

import json

from forkloop.metrics import (MODEL_PRICES_PER_M, episode_tokens, summarize_episodes, summarize_run,
                              token_cost_usd)


def _ep(steps_tokens, reward=1.0, wall=3600.0, family="f"):
    # `tokens` on a step is the policy's running total, as TeacherPolicy reports it.
    steps = [{"i": i, "valid": True, "tokens": t} for i, t in enumerate(steps_tokens)]
    return {"manifest": {"family": family, "split": "train"}, "steps": steps,
            "verdict": {"reward": reward, "reason_code": "OK" if reward >= 1 else "NOT_DONE",
                        "wall_seconds": wall, "milestones": reward}, "reset": None}


def test_episode_tokens_is_max_not_sum():
    ep = _ep([{"in": 100, "out": 10}, {"in": 100, "out": 10}, {"in": 250, "out": 30}])
    assert episode_tokens(ep) == {"in": 250, "out": 30, "cache_read": 0, "cache_write": 0}


def test_token_cost_prices_cache_tiers():
    cost = token_cost_usd({"in": 1_000_000, "out": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000},
                          (5.0, 25.0))
    assert abs(cost - (5.0 + 25.0 + 0.5 + 6.25)) < 1e-9


def test_summary_prices_by_model_and_splits_vm_and_tokens():
    eps = [_ep([{"in": 500_000, "out": 5_000}], wall=3600.0),
           _ep([{"in": 100_000, "out": 1_000}], reward=0.0, wall=3600.0)]
    s = summarize_episodes(eps, model="claude-opus-5", vm_hour_usd=0.134)
    p_in, p_out = MODEL_PRICES_PER_M["claude-opus-5"]
    assert s["tokens"] == {"in": 600_000, "out": 6_000, "cache_read": 0, "cache_write": 0}
    assert abs(s["cost_vm_usd"] - 0.268) < 1e-6
    assert abs(s["cost_tokens_usd"] - (0.6 * p_in + 0.006 * p_out)) < 1e-6
    assert abs(s["cost_total_usd"] - (s["cost_vm_usd"] + s["cost_tokens_usd"])) < 1e-3
    assert abs(s["cost_per_success_usd"] - s["cost_total_usd"]) < 1e-3  # one success
    assert s["model"] == "claude-opus-5"


def test_unknown_model_prices_tokens_at_zero():
    s = summarize_episodes([_ep([{"in": 10**6, "out": 10**6}])], model="not-a-model")
    assert s["cost_tokens_usd"] == 0.0 and s["token_prices_per_m"] == [0.0, 0.0]


def test_summarize_run_reads_model_from_run_json(tmp_path):
    run = tmp_path / "run"
    ep = run / "episodes" / "f-train-000000-abc"
    ep.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": "run", "model": "claude-sonnet-5"}))
    (ep / "manifest.json").write_text(json.dumps({"family": "f", "split": "train", "episode_id": "f-train-000000-abc"}))
    (ep / "steps.jsonl").write_text(json.dumps({"i": 0, "valid": True, "tokens": {"in": 1_000_000, "out": 0}}) + "\n")
    (ep / "verdict.json").write_text(json.dumps({"reward": 1.0, "reason_code": "OK", "wall_seconds": 0, "milestones": 1}))
    s = summarize_run(run)
    assert s["model"] == "claude-sonnet-5"
    assert abs(s["cost_tokens_usd"] - MODEL_PRICES_PER_M["claude-sonnet-5"][0]) < 1e-9
    assert summarize_run(run, model="claude-opus-5")["model"] == "claude-opus-5"
