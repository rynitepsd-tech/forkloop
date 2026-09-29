"""kanboard-v1 (the second world) offline: pure generators, the base population, and the oracle on a SQLite
stand-in built from Kanboard 1.2.54's real schema (worlds/kanboard_v1/kanboard_schema.sql, dumped from the
image). The "UI" here is the SQL Kanboard itself issues for each action, as measured on the live app
(docs/second-world.md): the row update, the renumbering of card positions, and the activity-stream event.
No network, no Docker."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from forkloop.actions import Action
from forkloop.backends.fake import FakeBackend
from forkloop.correction.checkpoint import classify
from forkloop.env import Env
from forkloop.oracle import Oracle
from forkloop.reset import ResetError
from forkloop.world import load_world
from worlds.kanboard_v1 import base_data as bd
from worlds.kanboard_v1.tasks import FAMILIES, generate
from worlds.kanboard_v1.tasks.common import TITLE_PAIRS

ROOT = Path(__file__).resolve().parents[1]
NOW = bd.ts(2026, 9, 29, 12, 0)   # the stand-in's "wall clock" for simulated UI writes


@pytest.fixture(scope="module")
def world():
    return load_world("kanboard-v1")


@pytest.fixture
def backend(tmp_path):
    b = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=2)
    yield b
    b.cleanup()


# ----------------------------------------------------------------------------- generators


def test_world_registers_and_declares_two_families(world):
    assert world.name == "kanboard-v1" and world.size == (1280, 720)
    assert world.config.families == ["move_and_assign", "due_and_comment"] == list(FAMILIES)
    assert world.checksum_tables()["kanboard"][0] == "tasks"
    assert {"position", "last_modified"} <= set(world.ignore_columns()["kanboard"])
    assert "date_due" in world.volatile_columns()["kanboard"]


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_generators_are_pure_and_split_aware(world, family):
    for split, seeds in (("train", (0, 1, 2, 77, 99999)), ("heldout_seeds", (100000, 100001, 150123))):
        for seed in seeds:
            a, b = generate(family, seed, split), world.generate(family, seed, split)
            assert a.to_json() == b.to_json()
            assert a.task_id == f"{family}-{split}-{seed:06d}"
            assert "expected" not in a.public_info and "seeding" not in a.public_info
            assert a.expected["title"] in [p[0] for p in TITLE_PAIRS[split]]
            ids = [a.expected[k] for k in ("target_task_id", "twin_task_id", "same_title_task_id")]
            assert len(set(ids)) == 3 and all(i >= 500000 for i in ids)
    with pytest.raises(ValueError):
        generate(family, 5, "final_test")
    with pytest.raises(ValueError):
        generate(family, 100005, "train")     # seed outside the split's range


def test_generation_is_byte_identical_in_a_fresh_interpreter():
    """No hash-order or clock dependence: another process with another hash seed produces the same bytes."""
    code = ("import hashlib; from worlds.kanboard_v1.tasks import generate; "
            "print(hashlib.sha256(''.join(generate(f, s, 'train').to_json() for f in ('move_and_assign', "
            "'due_and_comment') for s in range(40)).encode()).hexdigest())")
    here = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
                          env={**os.environ, "PYTHONHASHSEED": "12345"}).stdout.strip()
    import hashlib
    local = hashlib.sha256("".join(generate(f, s, "train").to_json() for f in ("move_and_assign", "due_and_comment")
                                   for s in range(40)).encode()).hexdigest()
    assert here == local


def test_splits_use_disjoint_titles_and_dates():
    train = {p for pair in TITLE_PAIRS["train"] for p in pair}
    held = {p for pair in TITLE_PAIRS["heldout_seeds"] for p in pair}
    base = {t[0] for tasks in bd.BASE_TASKS.values() for t in tasks}
    assert not train & held and not (train | held) & base
    d_train = {generate("due_and_comment", s, "train").expected["due_date"] for s in range(60)}
    d_held = {generate("due_and_comment", s, "heldout_seeds").expected["due_date"] for s in range(100000, 100060)}
    assert max(d_train) < min(d_held)


def test_distractors_and_roles_vary_across_seeds():
    tasks = [generate("move_and_assign", s, "train") for s in range(80)]
    assert any(t.difficulty["start_on_other_project"] for t in tasks)
    assert not all(t.difficulty["start_on_other_project"] for t in tasks)
    # the target's #id does not give its role away
    offsets = {t.expected["target_task_id"] % 20 for t in tasks}
    assert len(offsets) >= 6
    for t in tasks:
        e = t.expected
        assert e["owner_id"] != e["start_owner_id"] and (e["owner_id"], e["twin_user_id"]) in bd.USER_TWINS
        assert e["column_id"] != e["source_column_id"]
        assert t.initial_screen["project_id"] in (e["project_id"], e["same_title_project_id"])
    dues = [generate("due_and_comment", s, "train") for s in range(60)]
    assert {t.difficulty["has_due_date"] for t in dues} == {True, False}
    assert all(t.expected["due_date"] != t.expected["old_due_date"] for t in dues)


def test_seed_sql_is_portable():
    for fam in FAMILIES:
        for seed in range(20):
            sql = generate(fam, seed, "train").seeding.extra_sql["kanboard"]
            assert "`" not in sql and "NOW()" not in sql.upper() and "ON DUPLICATE" not in sql.upper()
            assert "\\" not in sql
            assert all(ln.startswith("INSERT INTO ") for ln in sql.strip().splitlines())


def test_base_data_is_deterministic_and_consistent():
    assert bd.render_base_sql() == bd.render_base_sql() and len(bd.base_sha256()) == 64
    assert len({u.id for u in bd.USERS}) == len(bd.USERS) and all(u.id >= 100000 for u in bd.USERS)
    assert {bd.column_id(p.id, c) for p in bd.PROJECTS for c in bd.COLUMN_TITLES} == set(range(100011, 100016)) | \
        set(range(100021, 100026)) | set(range(100031, 100036)) | set(range(100041, 100046))
    assert max(r["id"] for r in bd.base_comment_rows()) == bd.MAX_BASE_COMMENT_ID
    names = [u.name for u in bd.USERS]
    for a, b in bd.USER_TWINS:   # twins are near-identical names
        na, nb = bd.user_by_id(a).name, bd.user_by_id(b).name
        assert na != nb and (na.split()[0] == nb.split()[0] or na.split()[1] == nb.split()[1])
    assert len(set(names)) == len(names)


# ----------------------------------------------------------------------------- the stand-in "UI"


async def ui(env: Env, sql: str) -> None:
    await env.ep.dbs["kanboard"].execute_script(sql)


async def activity(env: Env, event: str, task_id: int, aid: int) -> str:
    pid = (await env.ep.dbs["kanboard"].query("SELECT project_id FROM tasks WHERE id = ?", [task_id]))[0]["project_id"]
    return (f"INSERT INTO project_activities (id, date_creation, event_name, creator_id, project_id, task_id, data) "
            f"VALUES ({aid}, {NOW}, '{event}', {bd.AGENT_USER_ID}, {pid}, {task_id}, '{{}}');")


async def ui_move(env: Env, task_id: int, column_id: int, aid: int) -> None:
    """TaskPositionModel::movePosition: the card goes to the column, every open card of the source and the
    destination column is renumbered, the moved card's timestamps change, one task.move.column event."""
    db = env.ep.dbs["kanboard"]
    row = (await db.query("SELECT project_id, column_id FROM tasks WHERE id = ?", [task_id]))[0]
    stmts = [f"UPDATE tasks SET column_id = {column_id}, position = 1, date_moved = {NOW}, "
             f"date_modification = {NOW} WHERE id = {task_id};"]
    for col in (row["column_id"], column_id):
        others = await db.query("SELECT id FROM tasks WHERE project_id = ? AND column_id = ? AND is_active = 1 "
                                "AND id <> ? ORDER BY position, id", [row["project_id"], col, task_id])
        for n, o in enumerate(others, start=2):
            stmts.append(f"UPDATE tasks SET position = {n} WHERE id = {o['id']};")
    stmts.append(f"UPDATE projects SET last_modified = {NOW} WHERE id = {row['project_id']};")
    stmts.append(await activity(env, "task.move.column", task_id, aid))
    await ui(env, "\n".join(stmts))


async def ui_assign(env: Env, task_id: int, user_id: int, aid: int) -> None:
    await ui(env, f"UPDATE tasks SET owner_id = {user_id}, date_modification = {NOW} WHERE id = {task_id};\n"
                  + await activity(env, "task.assignee_change", task_id, aid))


async def ui_due(env: Env, task_id: int, iso: str, aid: int) -> None:
    y, m, d = (int(x) for x in iso.split("-"))
    stamp = bd.ts(y, m, d, 12, 34)   # the form keeps the time of day of the save
    await ui(env, f"UPDATE tasks SET date_due = {stamp}, date_modification = {NOW} WHERE id = {task_id};\n"
                  + await activity(env, "task.update", task_id, aid))


async def ui_comment(env: Env, task_id: int, text: str, aid: int) -> None:
    top = int(await env.ep.dbs["kanboard"].scalar("SELECT MAX(id) FROM comments") or 0)
    await ui(env, bd.insert("comments", bd.comment_row(id=top + 1, task_id=task_id, user_id=bd.AGENT_USER_ID,
                                                        comment=text, created=NOW)) + "\n"
                  + await activity(env, "comment.create", task_id, aid))


async def finish(env: Env):
    await env.step(Action.done())
    return await env.verify()


async def fresh(world, backend, family: str, seed: int) -> Env:
    env = Env(world, backend, family=family, settle_s=0)
    await env.reset(seed)
    return env


# ----------------------------------------------------------------------------- reset pipeline


@pytest.mark.parametrize("family", sorted(FAMILIES))
async def test_reset_runs_health_and_feasibility(world, backend, family):
    env = await fresh(world, backend, family, 3)
    try:
        stages = {s["name"]: s for s in env.last_reset_report["stages"]}
        assert all(s["ok"] for s in stages.values()), stages
        assert "True" in stages["feasibility"]["note"] and "failed" not in stages["feasibility"]["note"]
        assert "kanboard.tasks" in env.ep.baseline.tables and "kanboard.forkloop_task_audit" in env.ep.baseline.watermarks
        # nothing done yet: a scored failure, not an oracle error
        v = await finish(env)
        assert v.reward == 0.0 and v.reason_code == "NOT_DONE", v.to_dict()
    finally:
        await env.close()


async def test_health_refuses_an_image_built_from_other_base_data(world, backend):
    env = await fresh(world, backend, "move_and_assign", 1)
    try:
        await ui(env, "UPDATE forkloop_meta SET value = 'stale' WHERE key = 'base_sha256';")
        rep = await world.health(env.ep.machine, env.ep.dbs)
        assert not rep.ok and rep.checks["base_sha256"] == "stale"
    finally:
        await env.close()


async def test_feasibility_fails_when_a_seeded_record_is_missing(world, backend):
    env = await fresh(world, backend, "due_and_comment", 2)
    try:
        await ui(env, f"DELETE FROM tasks WHERE id = {env.ep.task.expected['twin_task_id']};")
        rep = await world.feasibility(env.ep.machine, env.ep.dbs, env.ep.task)
        assert not rep.ok and str(rep.checks["twin"]).startswith("failed")
    finally:
        await env.close()


# ----------------------------------------------------------------------------- move_and_assign oracle


async def test_move_and_assign_through_the_ui_verifies(world, backend):
    env = await fresh(world, backend, "move_and_assign", 1)
    try:
        e = env.ep.task.expected
        await ui_move(env, e["target_task_id"], e["column_id"], 1)
        await ui_assign(env, e["target_task_id"], e["owner_id"], 2)
        v = await finish(env)
        # the sibling cards' renumbered positions and the project's last_modified are not edits
        assert v.reward == 1.0 and v.reason_code == "OK", v.to_dict()
        m = v.details["ui_milestones"]
        assert m["rungs"]["target_moved"] and m["rungs"]["target_updated"] and not m["rungs"]["distractor_touched"]
    finally:
        await env.close()


async def test_move_to_the_wrong_column_is_wrong_value(world, backend):
    env = await fresh(world, backend, "move_and_assign", 4)
    try:
        e = env.ep.task.expected
        wrong = next(bd.column_id(e["project_id"], c) for c in bd.COLUMN_TITLES
                     if bd.column_id(e["project_id"], c) not in (e["column_id"], e["source_column_id"]))
        await ui_move(env, e["target_task_id"], wrong, 1)
        await ui_assign(env, e["target_task_id"], e["owner_id"], 2)
        v = await finish(env)
        assert v.reason_code == "WRONG_VALUE" and v.failed == ["column"], v.to_dict()
    finally:
        await env.close()


async def test_assigning_the_near_twin_user_is_wrong_value(world, backend):
    env = await fresh(world, backend, "move_and_assign", 5)
    try:
        e = env.ep.task.expected
        await ui_move(env, e["target_task_id"], e["column_id"], 1)
        await ui_assign(env, e["target_task_id"], e["twin_user_id"], 2)
        v = await finish(env)
        assert v.reason_code == "WRONG_VALUE" and v.failed == ["assignee"], v.to_dict()
    finally:
        await env.close()


async def test_editing_the_same_title_in_the_other_project_is_a_wrong_record(world, backend):
    env = await fresh(world, backend, "move_and_assign", 6)
    try:
        e = env.ep.task.expected
        other_col = bd.column_id(e["same_title_project_id"], e["column"])
        await ui_move(env, e["same_title_task_id"], other_col, 1)
        await ui_assign(env, e["same_title_task_id"], e["owner_id"], 2)
        v = await finish(env)
        assert v.reward == 0.0 and v.reason_code == "NOT_DONE"
        assert {"untouched_2", "no_collateral"} <= set(v.failed)
        assert v.details["untouched_2"]["reason_code"] == "WRONG_RECORD"
        # the checkpoint classifier sees damage (a safety violation), so restart points avoid it
        status, why = classify(v)
        assert status == "damaged" and "untouched_2" in why
    finally:
        await env.close()


async def test_a_direct_database_write_is_caught(world, backend):
    env = await fresh(world, backend, "move_and_assign", 7)
    try:
        e = env.ep.task.expected
        await ui(env, f"UPDATE tasks SET column_id = {e['column_id']}, owner_id = {e['owner_id']} "
                      f"WHERE id = {e['target_task_id']};")
        v = await finish(env)
        assert v.reward == 0.0 and v.reason_code == "DIRECT_DB_WRITE" and v.failed == ["ui_path"], v.to_dict()
    finally:
        await env.close()


async def test_a_new_comment_is_collateral_in_move_and_assign(world, backend):
    env = await fresh(world, backend, "move_and_assign", 8)
    try:
        e = env.ep.task.expected
        await ui_move(env, e["target_task_id"], e["column_id"], 1)
        await ui_assign(env, e["target_task_id"], e["owner_id"], 2)
        await ui_comment(env, e["target_task_id"], "done", 3)
        v = await finish(env)
        assert v.reason_code == "COLLATERAL_EDIT" and v.failed == ["no_collateral"], v.to_dict()
    finally:
        await env.close()


# ----------------------------------------------------------------------------- due_and_comment oracle


async def test_due_and_comment_through_the_ui_verifies(world, backend):
    env = await fresh(world, backend, "due_and_comment", 1)
    try:
        e = env.ep.task.expected
        reference = (await Oracle(world.oracle_context(env.ep.dbs, env.ep.baseline)).evaluate(env.ep.task.oracle)).details
        await ui_due(env, e["target_task_id"], e["due_date"], 1)
        await ui_comment(env, e["target_task_id"], e["comment"], 2)
        v = await finish(env)
        assert v.reward == 1.0 and v.reason_code == "OK", v.to_dict()
        assert classify(v, reference) == ("clean", [])
    finally:
        await env.close()


async def test_wrong_due_date_is_progress_not_damage(world, backend):
    env = await fresh(world, backend, "due_and_comment", 7)   # a target without a due date
    try:
        e = env.ep.task.expected
        assert e["old_due_date"] is None
        reference = (await Oracle(world.oracle_context(env.ep.dbs, env.ep.baseline)).evaluate(env.ep.task.oracle)).details
        assert reference["due_date"]["actual"] == "none"
        await ui_due(env, e["target_task_id"], "2026-01-02", 1)
        v = await finish(env)
        assert v.reason_code == "WRONG_VALUE" and "due_date" in v.failed
        assert classify(v, reference)[0] == "clean"     # the date can still be corrected in the form
    finally:
        await env.close()


async def test_duplicate_and_mistyped_comments(world, backend):
    env = await fresh(world, backend, "due_and_comment", 2)
    try:
        e = env.ep.task.expected
        await ui_due(env, e["target_task_id"], e["due_date"], 1)
        await ui_comment(env, e["target_task_id"], e["comment"], 2)
        await ui_comment(env, e["target_task_id"], e["comment"], 3)
        v = await finish(env)
        assert v.reason_code == "DUPLICATE_SIDE_EFFECT" and v.failed == ["one_comment"], v.to_dict()
    finally:
        await env.close()
    env = await fresh(world, backend, "due_and_comment", 3)
    try:
        e = env.ep.task.expected
        await ui_due(env, e["target_task_id"], e["due_date"], 1)
        await ui_comment(env, e["target_task_id"], e["comment"].replace(";", ","), 2)
        v = await finish(env)
        assert v.reason_code == "WRONG_VALUE" and v.failed == ["comment_text"], v.to_dict()
    finally:
        await env.close()


async def test_comment_on_the_twin_task_is_a_wrong_record(world, backend):
    env = await fresh(world, backend, "due_and_comment", 4)
    try:
        e = env.ep.task.expected
        await ui_due(env, e["target_task_id"], e["due_date"], 1)
        await ui_comment(env, e["twin_task_id"], e["comment"], 2)
        v = await finish(env)
        assert v.reason_code == "NOT_DONE" and {"one_comment", "no_comment_elsewhere"} <= set(v.failed)
        assert v.details["no_comment_elsewhere"]["reason_code"] == "WRONG_RECORD"
    finally:
        await env.close()


async def test_editing_an_existing_comment_is_collateral(world, backend):
    env = await fresh(world, backend, "due_and_comment", 5)
    try:
        e = env.ep.task.expected
        await ui_due(env, e["target_task_id"], e["due_date"], 1)
        await ui_comment(env, e["target_task_id"], e["comment"], 2)
        old = int(await env.ep.dbs["kanboard"].scalar("SELECT MIN(id) FROM comments WHERE task_id = ?",
                                                      [e["target_task_id"]]))
        await ui(env, f"UPDATE comments SET comment = 'overwritten' WHERE id = {old};\n"
                      + await activity(env, "comment.update", e["target_task_id"], 3))
        v = await finish(env)
        assert v.reason_code == "COLLATERAL_EDIT" and v.failed == ["existing_comments_unchanged"], v.to_dict()
    finally:
        await env.close()


async def test_changing_another_field_of_the_target_is_collateral(world, backend):
    env = await fresh(world, backend, "due_and_comment", 6)
    try:
        e = env.ep.task.expected
        await ui_due(env, e["target_task_id"], e["due_date"], 1)
        await ui_comment(env, e["target_task_id"], e["comment"], 2)
        await ui(env, f"UPDATE tasks SET title = 'renamed' WHERE id = {e['target_task_id']};\n"
                      + await activity(env, "task.update", e["target_task_id"], 3))
        v = await finish(env)
        assert v.reason_code == "COLLATERAL_EDIT" and v.failed == ["target_other_fields"], v.to_dict()
    finally:
        await env.close()


def test_world_yaml_audit_matches_the_view_and_schema():
    schema = (ROOT / "worlds/kanboard_v1/kanboard_schema.sql").read_text()
    for table in ("tasks", "comments", "project_activities", "project_has_users", "columns", "users", "settings"):
        assert re.search(rf'CREATE TABLE "?{table}"?\s', schema), table
    assert "forkloop" not in schema          # the view and meta table come from base_data, not the dump
    assert "project_activities" in bd.AUDIT_VIEW_SQL and json.dumps(bd.SETTINGS)
