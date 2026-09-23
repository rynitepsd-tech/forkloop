"""Infrastructure and oracle failures are unscored, never counted as policy failures (2026-09-22 review)."""
from __future__ import annotations

import pytest

from forkloop.actions import Action
from forkloop.backends.base import BackendError
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env
from forkloop.metrics import summarize_episodes
from forkloop.oracle import Check, Oracle, OracleContext, OracleSpec
from forkloop.pool import WorkerPool
from forkloop.world import load_world


class _DeadDb:
    async def query(self, sql, params=None):
        raise ConnectionError("mysql: lost connection")


async def test_a_check_that_raises_is_an_oracle_error_not_the_configured_reason():
    ctx = OracleContext(dbs={"portal": _DeadDb()}, baseline=None, primary_keys={}, exempt_tables=[],
                        audit={}, page_views=None, forbidden_paths=[])
    spec = OracleSpec(effects=[Check(id="appeal", kind="query", db="portal", sql="SELECT 1",
                                     equals=1, reason_code="NOT_DONE")])
    v = await Oracle(ctx).evaluate(spec)
    assert v.reward == 0.0
    assert v.reason_code == "ORACLE_ERROR"
    assert v.details["appeal"]["reason_code"] == "ORACLE_ERROR"


def _episode(reason, reward=0.0):
    return {"verdict": None if reason is None else {"reward": reward, "reason_code": reason, "failed": []},
            "steps": [], "manifest": {"family": "f", "split": "train"}, "dir": "/nonexistent"}


def test_metrics_exclude_unscored_episodes_from_the_success_denominator():
    eps = [_episode("OK", 1.0), _episode("WRONG_VALUE"), _episode(None), _episode("ORACLE_ERROR"),
           _episode("INFRA_ERROR")]
    s = summarize_episodes(eps)
    assert s["n_episodes"] == 5
    assert s["n_unscored"] == 3
    assert s["success_rate"]["k"] == 1 and s["success_rate"]["n"] == 2
    assert s["by_family"]["f"]["n"] == 2


def test_metrics_with_nothing_scored_report_an_unavailable_rate():
    s = summarize_episodes([_episode(None)])
    assert s["success_rate"] is None


@pytest.fixture
def world():
    return load_world("toy-counter")


@pytest.fixture
def backend(tmp_path, world):
    b = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=2, gui_factory=world.gui_factory())
    yield b
    b.cleanup()


async def test_backend_failures_end_the_episode_as_infra_error_not_invalid_actions(world, backend):
    pool = WorkerPool(backend, world, size=1, mode="fork")
    env = Env(world, backend, family="reach_target", pool=pool, settle_s=0)
    await env.reset(1)

    async def dead(*a, **k):
        raise BackendError("machine x: control channel closed")

    env.ep.machine.click = dead  # type: ignore[method-assign]
    info = {}
    for _ in range(3):
        _, _, term, trunc, info = await env.step(Action.parse("click(10, 10)", width=env.width, height=env.height))
        if term or trunc:
            break
    assert env.ep.end_reason == "infrastructure_error"
    assert env.ep.invalid == 0
    assert env.ep.verdict.reason_code == "INFRA_ERROR"
    await env.close()
    await pool.close()


async def test_a_rejected_action_value_is_still_the_policys_invalid_action(world, backend):
    pool = WorkerPool(backend, world, size=1, mode="fork")
    env = Env(world, backend, family="reach_target", pool=pool, settle_s=0)
    await env.reset(1)

    async def reject(*a, **k):
        raise ValueError("type() needs non-empty text")

    env.ep.machine.click = reject  # type: ignore[method-assign]
    await env.step(Action.parse("click(10, 10)", width=env.width, height=env.height))
    assert env.ep.invalid == 1 and env.ep.infra_errors == 0
    await env.close()
    await pool.close()


async def test_backend_failure_steps_carry_the_backend_prefix(world, backend):
    from forkloop.env import BACKEND_FAILURE_PREFIX

    pool = WorkerPool(backend, world, size=1, mode="fork")
    env = Env(world, backend, family="reach_target", pool=pool, settle_s=0)
    await env.reset(1)

    async def dead(*a, **k):
        raise ConnectionError("control channel closed")

    env.ep.machine.click = dead  # type: ignore[method-assign]
    _, _, _, _, info = await env.step(Action.parse("click(10, 10)", width=env.width, height=env.height))
    assert str(info["error"]).startswith(BACKEND_FAILURE_PREFIX)
    await env.close()
    await pool.close()


def test_identifiers_with_leading_zeros_are_not_numbers():
    from forkloop.oracle import _compare

    assert _compare("eq", "0123", "0123")
    assert not _compare("eq", "123", "0123")
    assert _compare("eq", "7", 7) and _compare("eq", "0", 0) and _compare("eq", "-5", -5)


class _OkDb:
    async def query(self, sql, params=None):
        return [{"v": "CHANGED"}]


async def test_an_errored_check_does_not_hide_a_violation_another_check_observed():
    ctx = OracleContext(dbs={"portal": _OkDb(), "dead": _DeadDb()}, baseline=None, primary_keys={},
                        exempt_tables=[], audit={}, page_views=None, forbidden_paths=[])
    spec = OracleSpec(effects=[Check(id="appeal", kind="query", db="dead", sql="SELECT 1", equals=1, reason_code="NOT_DONE")],
                      invariants=[Check(id="distractor", kind="query", db="portal", sql="SELECT 1", equals="DENIED",
                                        reason_code="WRONG_RECORD")])
    v = await Oracle(ctx).evaluate(spec)
    assert v.reason_code == "WRONG_RECORD"


def test_backend_failure_steps_are_not_invalid_actions():
    ep = _episode("INFRA_ERROR")
    ep["steps"] = [{"valid": False, "error": "backend failed: ConnectionError: x"},
                   {"valid": False, "error": "apply failed: ActionError: empty key"}, {"valid": True}]
    s = summarize_episodes([ep])
    assert s["invalid_action_rate"]["k"] == 1


def test_setup_errors_exit_4_not_the_regression_code(tmp_path, monkeypatch):
    from forkloop.cli import main

    cfg = tmp_path / "c.yaml"
    cfg.write_text("version: 1\nbackend: solari\nseeds: [1]\nvariants:\n"
                   "  - {name: a, policy: scripted, options: {actions: []}}\n"
                   "  - {name: b, policy: scripted, options: {actions: []}}\n")
    monkeypatch.delenv("SOLARI_API_KEY", raising=False)
    assert main(["compare", "--config", str(cfg), "--out", str(tmp_path / "out"), "--fail-on-regression"]) == 4
