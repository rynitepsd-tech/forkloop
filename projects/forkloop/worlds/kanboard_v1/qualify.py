"""Live qualification of kanboard-v1 on the Docker backend (no model calls, no spend).

    FORKLOOP_DOCKER_IMAGE=forkloop/kanboard-v1:1 FORKLOOP_DOCKER_SNAPSHOT_DB=none \\
    FORKLOOP_DOCKER_OWNER=second-world-agent python -m worlds.kanboard_v1.qualify --out runs/kanboard-qualify

1. ``--resets N`` full resets (fresh container from the golden image, seeding, health, feasibility, baseline,
   initial screen, stable screen) with per-stage timings;
2. scripted controls through the agent channel only (mouse and keyboard on Chrome, positions measured on the
   1280x720 desktop): the correct ``due_and_comment`` path (expected reward 1.0), the same actions on the
   near-twin task (a wrong record), and a direct database write of the right values (DIRECT_DB_WRITE).

Every episode is recorded (screenshots, steps, verdict) under ``--out``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from forkloop.actions import Action
from forkloop.env import Env
from forkloop.pool import WorkerPool
from forkloop.trajectories import Recorder
from forkloop.world import load_world

#: Kanboard 1.2.54 task page (Chrome at 0,0 under the panel, 1280x720): the sidebar and its modals.
EDIT_TASK = (80, 442)
ADD_COMMENT = (93, 622)
DUE_FIELD = (1090, 223)
EDIT_SAVE = (54, 608)
COMMENT_SAVE = (269, 532)


def task_page(task_id: int) -> str:
    return f"http://localhost/?controller=TaskViewController&action=show&task_id={task_id}"


def due_and_comment_script(task_id: int, due: str, comment: str) -> list[Action]:
    """Typed in the format the field shows (m/d/Y H:i): typed over an existing value, an ISO date is replaced by
    the date picker's previous selection when the field loses focus (measured on 1.2.54, docs/second-world.md)."""
    y, m, d = due.split("-")
    due = f"{m}/{d}/{y} 12:00"
    p = Action.parse
    return [p("click(640, 90)"), p('key("ctrl+a")'), p(json.dumps({"type": "type", "text": task_page(task_id) + "\n"})),
            p("wait(2)"), p(f"click{EDIT_TASK}"), p("wait(2)"), p(f"click{DUE_FIELD}"), p('key("ctrl+a")'),
            p(json.dumps({"type": "type", "text": due})), p(f"click{EDIT_SAVE}"), p("wait(2)"),
            p(f"click{ADD_COMMENT}"), p("wait(2)"), p(json.dumps({"type": "type", "text": comment})),
            p(f"click{COMMENT_SAVE}"), p("wait(2)"), Action.done()]


async def run_script(env: Env, actions: list[Action]) -> dict[str, Any]:
    for a in actions:
        _, _, term, trunc, _ = await env.step(a)
        if term or trunc:
            break
    v = await env.verify()
    return {"reward": v.reward, "reason_code": v.reason_code, "failed": v.failed}


async def main(args: argparse.Namespace) -> dict[str, Any]:
    from forkloop.backends.docker import DockerBackend

    world = load_world("kanboard-v1")
    backend = DockerBackend.for_world(world)
    rec = Recorder(Path(args.out), run_id="episodes", meta={"backend": "docker", "world": world.name,
                                                             "policy": "scripted qualification"})
    pool = WorkerPool(backend, world, size=1, mode="fork", run_id=f"kb-qualify-{int(time.time())}",
                      reap_orphans_enabled=False)
    env = Env(world, backend, family="due_and_comment", pool=pool, recorder=rec, settle_s=0.6,
              stable_after_action=True)
    out: dict[str, Any] = {"image": backend.image, "resets": [], "controls": {}}
    try:
        for i in range(args.resets):
            fam = ("move_and_assign", "due_and_comment")[i % 2]
            t0 = time.monotonic()
            await env.reset(i, task=world.generate(fam, i, "train"))
            rep = env.last_reset_report
            out["resets"].append({"family": fam, "seed": i, "wall_s": round(time.monotonic() - t0, 2),
                                  "stages": {s["name"]: round(s["seconds"], 2) for s in rep["stages"]}})
            print(json.dumps(out["resets"][-1]), flush=True)
        seed = args.seed
        task = world.generate("due_and_comment", seed, "train")
        e = task.expected
        await env.reset(seed, task=task, episode_id="control-correct")
        out["controls"]["correct_ui_path"] = await run_script(
            env, due_and_comment_script(e["target_task_id"], e["due_date"], e["comment"]))
        await env.reset(seed, task=task, episode_id="control-twin-task")
        out["controls"]["same_actions_on_twin_task"] = await run_script(
            env, due_and_comment_script(e["twin_task_id"], e["due_date"], e["comment"]))
        await env.reset(seed, task=task, episode_id="control-direct-db-write")
        db = env.ep.dbs["kanboard"]
        top = int(await db.scalar("SELECT MAX(id) FROM comments") or 0)
        y, m, d = (int(x) for x in e["due_date"].split("-"))
        from worlds.kanboard_v1 import base_data as bd
        await db.execute_script(
            f"UPDATE tasks SET date_due = {bd.ts(y, m, d, 12)} WHERE id = {e['target_task_id']};\n"
            + bd.insert("comments", bd.comment_row(id=top + 1, task_id=e["target_task_id"], user_id=bd.AGENT_USER_ID,
                                                   comment=e["comment"], created=bd.ts(y, m, d, 12))))
        out["controls"]["direct_db_write"] = await run_script(env, [Action.done()])
        print(json.dumps(out["controls"], indent=1), flush=True)
    finally:
        await env.close()
        await backend.close()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "qualify.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--resets", type=int, default=6)
    ap.add_argument("--seed", type=int, default=11)
    os.environ.setdefault("FORKLOOP_DOCKER_IMAGE", "forkloop/kanboard-v1:1")
    os.environ.setdefault("FORKLOOP_DOCKER_SNAPSHOT_DB", "none")
    os.environ.setdefault("FORKLOOP_DOCKER_OWNER", "second-world-agent")
    asyncio.run(main(ap.parse_args()))
