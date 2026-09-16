"""Matched-denominator, interruption and reset-integrity regression boundaries."""
from __future__ import annotations

import asyncio
import json

import pytest

from forkloop.backends.fake import FakeBackend
from forkloop.comparison import PolicyVariant, run_comparison, summarize_comparison, write_comparison_report
from forkloop.policies.scripted import ScriptedPolicy
from forkloop.world import load_world


@pytest.fixture
def environment(tmp_path):
    world = load_world("toy-counter")
    backend = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=1, gui_factory=world.gui_factory())
    yield world, backend
    backend.cleanup()


def variant(label, factory=None, **identity):
    return PolicyVariant(label, {"policy": "scripted", "version": "test", "options": {}, **identity},
                         factory or (lambda: ScriptedPolicy([])))


async def test_setup_errors_are_not_policy_failures(environment, tmp_path):
    world, backend = environment

    def unavailable():
        raise ConnectionError("policy endpoint unavailable")

    result = await run_comparison(world, backend, [variant("unavailable", unavailable), variant("empty")],
                                  [11, 12], output=tmp_path / "comparison", settle_s=0)
    assert result["arms"]["A"]["scored"] == 0
    assert result["arms"]["A"]["failures"] == 0
    assert result["arms"]["A"]["unscored"] == 2
    assert result["arms"]["B"]["scored"] == 2
    assert result["arms"]["B"]["failures"] == 2
    assert result["matched_pairs"] == 0
    assert not result["recommendation"]["eligible"]
    assert [c["status"] for c in result["cells"]] == ["setup_error", "completed", "completed", "setup_error"]
    assert all(c.get("episode") is None for c in result["cells"] if c["arm"] == "A")


async def test_cancellation_retains_attempt_and_missing_partner(environment, tmp_path):
    world, backend = environment

    class Interrupted:
        async def act(self, observation):
            raise asyncio.CancelledError()

    output = tmp_path / "comparison"
    with pytest.raises(asyncio.CancelledError):
        await run_comparison(world, backend, [variant("interrupted", Interrupted), variant("empty")],
                             [11], output=output, settle_s=0)
    result = summarize_comparison(output)
    assert result["cells"][0]["status"] == "interrupted"
    assert result["cells"][0]["episode"]
    assert result["missing_cells"] == ["B-000000"]
    assert result["arms"]["A"]["scored"] == result["arms"]["B"]["scored"] == 0
    assert result["paired_outcomes"] == {"both_pass": 0, "A_only": 0, "B_only": 0, "neither": 0}
    assert not result["recommendation"]["eligible"]
    with pytest.raises(FileExistsError):
        await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output)
    assert summarize_comparison(output)["cells"][0]["status"] == "interrupted"


async def test_reset_mismatch_excludes_pair_but_retains_scored_outcomes(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    assert result["paired_outcomes"]["neither"] == 1
    cell_path = output / "cells" / "B-000000.json"
    cell = json.loads(cell_path.read_text())
    table = next(iter(cell["baseline_digest"]["tables"]))
    cell["baseline_digest"]["tables"][table] = "different baseline"
    cell_path.write_text(json.dumps(cell))
    (output / cell["episode"] / "baseline-digest.json").write_text(json.dumps(cell["baseline_digest"]))
    result = summarize_comparison(output)
    assert result["matched_pairs"] == 0
    assert result["arms"]["A"]["failures"] == result["arms"]["B"]["failures"] == 1
    assert result["pairs"][0]["reset_equivalence"]["baseline_tables_differing"] == [table]
    assert not result["recommendation"]["eligible"]
    assert result["recommendation"]["observed_leader"] is None


async def test_episode_identity_change_is_unscored(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    manifest_path = output / result["cells"][0]["episode"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["policy_identity"]["version"] = "other-model"
    manifest_path.write_text(json.dumps(manifest))
    result = summarize_comparison(output)
    assert result["arms"]["A"]["scored"] == 0
    assert result["arms"]["A"]["unscored"] == 1
    assert result["matched_pairs"] == 0
    assert result["cells"][0]["issues"]


@pytest.mark.parametrize("options", [
    {"endpoint": "https://user:password@example.test/v1"},
    {"endpoint": "https://example.test/v1?token=credential"},
    {"api_key": "credential"},
])
async def test_credentials_rejected_before_output_or_factory(environment, tmp_path, options):
    world, backend = environment

    def forbidden_factory():
        pytest.fail("credential validation must precede factory invocation")

    output = tmp_path / "comparison"
    with pytest.raises(ValueError):
        await run_comparison(world, backend, [variant("a", forbidden_factory, options=options), variant("b")],
                             [11], output=output)
    assert not output.exists()


async def test_failed_reset_stops_without_reusing_uncertain_pool(environment, tmp_path, monkeypatch):
    world, backend = environment

    async def failed_initial_screen(machine, screen):
        raise ConnectionError("desktop is unavailable")

    monkeypatch.setattr(world, "open_initial_screen", failed_initial_screen)
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11, 12],
                                  output=tmp_path / "comparison", settle_s=0)
    assert result["execution"]["status"] == "setup_error"
    assert result["cells"][0]["status"] == "setup_error"
    assert result["cells"][0]["reset_report"]["ok"] is False
    assert result["missing_cells"] == ["B-000000", "B-000001", "A-000001"]
    assert result["arms"]["A"]["scored"] == result["arms"]["B"]["scored"] == 0
    assert not result["recommendation"]["eligible"]


async def test_teacher_provider_failure_is_not_scored(environment, tmp_path, monkeypatch):
    from forkloop.policies.teacher import TeacherPolicy
    world, backend = environment

    async def rejected(self, observation):
        raise PermissionError("provider rejected credentials")

    monkeypatch.setattr(TeacherPolicy, "_call_model", rejected)
    result = await run_comparison(world, backend, [variant("teacher", TeacherPolicy), variant("empty")],
                                  [11], output=tmp_path / "comparison", settle_s=0, max_invalid=1)
    assert result["arms"]["A"]["scored"] == 0
    assert result["arms"]["A"]["failures"] == 0
    assert result["cells"][0]["status"] == "execution_error"
    assert result["matched_pairs"] == 0


async def test_builtin_invalid_actions_remain_in_paired_denominators(environment, tmp_path):
    from forkloop.policies.scripted import CallbackPolicy

    world, backend = environment
    invalid = "click(99999, 99999)"
    result = await run_comparison(world, backend, [
        variant("scripted", lambda: ScriptedPolicy([invalid, invalid])),
        variant("callback", lambda: CallbackPolicy(lambda obs: None if obs.step == 0 else invalid)),
    ], [11], output=tmp_path / "comparison", settle_s=0, max_invalid=2)
    assert result["arms"]["A"]["scored"] == result["arms"]["A"]["failures"] == 1
    assert result["arms"]["B"]["scored"] == result["arms"]["B"]["failures"] == 1
    assert result["matched_pairs"] == 1
    assert [cell["status"] for cell in result["cells"]] == ["completed", "completed"]


async def test_checker_exception_cannot_count_as_policy_failure(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    path = output / result["cells"][0]["episode"] / "verdict.json"
    verdict = json.loads(path.read_text())
    check = verdict["failed"][0]
    verdict["details"][check] = {"passed": False, "error": "database transport disconnected"}
    path.write_text(json.dumps(verdict))
    result = summarize_comparison(output)
    assert result["arms"]["A"]["scored"] == 0
    assert result["arms"]["A"]["failures"] == 0
    assert result["matched_pairs"] == 0


async def test_backend_action_error_is_not_scored(environment, tmp_path, monkeypatch):
    import forkloop.env as env_module
    from forkloop.backends.base import BackendError
    world, backend = environment
    apply = env_module.apply_action

    async def disconnected(machine, action):
        if action.type == "click":
            raise BackendError("desktop control channel disconnected")
        await apply(machine, action)

    monkeypatch.setattr(env_module, "apply_action", disconnected)
    result = await run_comparison(world, backend,
                                  [variant("click", lambda: ScriptedPolicy(["click(10,10)"])), variant("empty")],
                                  [11], output=tmp_path / "comparison", settle_s=0)
    assert result["arms"]["A"]["scored"] == 0
    assert result["arms"]["B"]["scored"] == 1
    assert result["matched_pairs"] == 0


async def test_crash_left_artifacts_remain_unscored_and_unchanged(environment, tmp_path, monkeypatch):
    world, backend = environment
    # Exercise recommendation withholding for the live evidence category offline.
    builder = await backend.create()
    snapshot = await world.build(builder, log=lambda _: None)
    await builder.kill()
    monkeypatch.setenv(world.config.golden_snapshot_env, snapshot)
    monkeypatch.setattr(backend, "name", "offline-live-category-control")
    output = tmp_path / "comparison"
    await run_comparison(world, backend, [variant("a"), variant("b")], [11, 12],
                         output=output, settle_s=0)
    cell_path = output / "cells" / "B-000001.json"
    cell = json.loads(cell_path.read_text())
    cell["status"] = "running"
    cell.pop("finished_at")
    cell_path.write_text(json.dumps(cell))
    (output / cell["episode"] / "verdict.json").unlink()
    (output / "cells" / "A-000001.json").unlink()
    execution_path = output / "execution.json"
    execution = json.loads(execution_path.read_text())
    execution["status"] = "running"
    execution.pop("finished_at")
    execution_path.write_text(json.dumps(execution))
    primary = [output / "protocol.json", execution_path, *output.glob("cells/*.json"),
               *output.glob("runs/**/manifest.json"), *output.glob("runs/**/steps.jsonl"),
               *output.glob("runs/**/verdict.json")]
    before = {path: path.read_bytes() for path in primary}

    result = write_comparison_report(output)
    assert result["execution"]["status"] == "incomplete"
    assert result["execution"]["recorded_status"] == "running"
    assert result["planned_pairs"] == 2
    assert result["matched_pairs"] == 1
    assert [cell["status"] for cell in result["cells"]] == ["completed", "completed", "unfinished", "missing"]
    assert result["arms"]["A"]["scored"] == result["arms"]["B"]["scored"] == 1
    assert result["arms"]["A"]["unscored"] == result["arms"]["B"]["unscored"] == 1
    assert result["cells"][2]["evidence"]["verdict"] is None
    assert result["cells"][2]["issues"] and result["cells"][3]["issues"]
    assert not result["recommendation"]["eligible"]
    assert result["recommendation"]["observed_leader"] is None
    assert {path: path.read_bytes() for path in primary} == before


async def test_policy_error_string_is_not_a_task_failure(environment, tmp_path):
    world, backend = environment

    class Unavailable:
        async def act(self, observation):
            return None, {"error": "provider connection was refused"}

    result = await run_comparison(world, backend, [variant("unavailable", Unavailable), variant("empty")],
                                  [11], output=tmp_path / "comparison", settle_s=0)
    assert result["cells"][0]["status"] == "execution_error"
    assert result["arms"]["A"]["failures"] == result["arms"]["A"]["scored"] == 0
    assert result["arms"]["B"]["scored"] == 1
    assert result["matched_pairs"] == 0


async def test_cleanup_failure_excludes_verdict_and_stops_future_cells(environment, tmp_path):
    world, backend = environment

    class FailingCleanup(ScriptedPolicy):
        def __init__(self):
            super().__init__([])

        async def aclose(self):
            raise ConnectionError("could not release policy resources")

    result = await run_comparison(world, backend, [variant("a", FailingCleanup), variant("b")],
                                  [11], output=tmp_path / "comparison", settle_s=0)
    assert result["execution"]["status"] == "cleanup_error"
    assert result["cells"][0]["status"] == "cleanup_error"
    assert result["cells"][0]["evidence"]["verdict"]["reward"] == 0
    assert result["arms"]["A"]["failures"] == result["arms"]["A"]["scored"] == 0
    assert result["missing_cells"] == ["B-000000"]


async def test_historical_cleanup_error_does_not_score_completed_cell(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    path = output / "cells" / "A-000000.json"
    cell = json.loads(path.read_text())
    cell["cleanup_error"] = "worker release failed after verification"
    path.write_text(json.dumps(cell))
    result = write_comparison_report(output)
    assert result["cells"][0]["status"] == "completed"
    assert result["cells"][0]["evidence"]["verdict"] is not None
    assert result["arms"]["A"]["scored"] == result["arms"]["A"]["failures"] == 0
    assert result["matched_pairs"] == 0


@pytest.mark.parametrize(("artifact", "replacement"), [
    ("manifest.json", "[]"),
    ("verdict.json", "[]"),
    ("reset.json", "[]"),
    ("baseline-digest.json", "[]"),
    ("steps.jsonl", "[]\n"),
    ("verdict.json", '{"truncated":'),
    ("verdict.json", None),
    ("steps.jsonl", None),
])
async def test_malformed_or_missing_episode_evidence_is_reportable(environment, tmp_path, artifact, replacement):
    world, backend = environment
    output = tmp_path / "comparison"
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    path = output / result["cells"][0]["episode"] / artifact
    if replacement is None:
        path.unlink()
    else:
        path.write_text(replacement)
    result = write_comparison_report(output)
    assert result["arms"]["A"]["planned"] == result["arms"]["B"]["planned"] == 1
    assert result["arms"]["A"]["scored"] == result["arms"]["A"]["failures"] == 0
    assert result["arms"]["B"]["scored"] == 1
    assert result["cells"][0]["issues"]
    assert result["matched_pairs"] == 0


async def test_corrupt_attempt_and_execution_keep_planned_denominators(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    (output / "execution.json").write_text("[]")
    (output / "cells" / "A-000000.json").write_text('{"truncated":')
    result = write_comparison_report(output)
    assert result["execution"]["status"] == "unknown"
    assert result["cells"][0]["status"] == "unreadable"
    assert result["arms"]["A"]["planned"] == result["arms"]["A"]["unscored"] == 1
    assert result["arms"]["B"]["scored"] == 1
    assert result["matched_pairs"] == 0
    assert result["issues"]


async def test_malformed_attempt_fields_do_not_crash_aggregation(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    path = output / "cells" / "A-000000.json"
    cell = json.loads(path.read_text())
    cell.update(baseline_digest=["not a baseline"], reset_report=["not a reset"],
                recorded_policy_usage={"in": "unknown"}, setup_and_episode_seconds="unknown")
    path.write_text(json.dumps(cell))
    result = write_comparison_report(output)
    assert result["arms"]["A"]["scored"] == result["arms"]["A"]["usage_cells"] == result["arms"]["A"]["duration_cells"] == 0
    assert result["arms"]["A"]["unscored"] == 1
    assert result["pairs"][0]["reset_equivalence"]["equivalent"] is False
    assert result["pairs"][0]["reset_equivalence"]["reasons"]


async def test_missing_screenshot_excludes_retained_verdict(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    episode = output / result["cells"][0]["episode"]
    step = json.loads((episode / "steps.jsonl").read_text().splitlines()[0])
    (episode / step["shot_before"]).unlink()
    result = write_comparison_report(output)
    assert result["cells"][0]["evidence"]["verdict"] is not None
    assert not result["cells"][0]["scored"]
    assert result["matched_pairs"] == 0


async def test_truncated_step_log_cannot_score_from_intact_verdict(environment, tmp_path):
    world, backend = environment
    output = tmp_path / "comparison"
    result = await run_comparison(world, backend, [variant("a"), variant("b")], [11], output=output, settle_s=0)
    episode = output / result["cells"][0]["episode"]
    (episode / "steps.jsonl").write_text("")
    result = write_comparison_report(output)
    assert result["cells"][0]["evidence"]["verdict"] is not None
    assert result["cells"][0]["issues"]
    assert result["arms"]["A"]["scored"] == result["arms"]["A"]["failures"] == 0


async def test_provider_timeout_is_not_confused_with_trajectory_deadline(environment, tmp_path, monkeypatch):
    world, backend = environment
    monkeypatch.setattr("forkloop.env.Env.remaining_seconds", lambda self: 0.01)

    class ProviderTimeout:
        async def act(self, observation):
            raise TimeoutError("provider connection timed out before task deadline")

    class SlowPolicy:
        async def act(self, observation):
            await asyncio.Event().wait()

    result = await run_comparison(world, backend, [variant("provider", ProviderTimeout), variant("slow", SlowPolicy)],
                                  [11], output=tmp_path / "comparison", settle_s=0)
    assert result["cells"][0]["status"] == "execution_error"
    assert result["arms"]["A"]["scored"] == result["arms"]["A"]["failures"] == 0
    assert result["cells"][1]["status"] == "completed"
    assert result["arms"]["B"]["scored"] == result["arms"]["B"]["failures"] == 1
    assert result["matched_pairs"] == 0
