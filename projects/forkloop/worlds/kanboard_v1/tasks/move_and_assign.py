"""Family ``move_and_assign``: move task "<title>" in project "<name>" to column "<col>" and assign it to
"<user>".

Distractors (seeded per episode): a near-twin title in the same project and column ("Fix logout redirect"
next to "Fix login redirect"), a task with the *same* title in another project whose columns have the same
names, and a near-twin user name in the assignee list (every project has "Priya Raman" and "Priya Rao").
``difficulty.start_on_other_project``: the episode opens on the other project's board, where the
same-titled task is the first match the agent sees.
"""

from __future__ import annotations

import random

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from .. import base_data as bd
from .common import (BOOKKEEPING, BUDGET, COLORS, EPISODE_CREATED, NOISE_TITLES, TITLE_PAIRS, WORLD, board_url,
                     common_invariants, episode_ids, pick_projects, preserve_task, seeded_task)

FAMILY = "move_and_assign"


def generate(rng: random.Random, seed: int, split: str) -> TaskInstance:
    project, other = pick_projects(rng)
    title, twin_title = rng.choice(TITLE_PAIRS[split])
    cols = list(bd.COLUMN_TITLES)
    src_col = rng.choice(cols[:3])                      # Backlog / Ready / In progress
    dst_col = rng.choice([c for c in cols if c != src_col and c != "Backlog"])
    assignee_id, twin_user_id = rng.choice(bd.USER_TWINS)
    start_owner = rng.choice([0, 0, *[u.id for u in bd.USERS[1:] if u.id not in (assignee_id, twin_user_id)]])
    start_on_other = rng.random() < 0.4
    n_noise = rng.randint(1, 3)
    tids, _ = episode_ids(seed, rng, 3 + n_noise)
    target_id, twin_id, same_title_id, *noise_ids = tids
    created = EPISODE_CREATED + rng.randint(0, 5) * 3600

    positions = rng.sample(range(11, 60, 2), 3 + n_noise)
    sql = [
        seeded_task(id=target_id, title=title, project_id=project.id, column=src_col, owner_id=start_owner,
                    position=positions[0], color_id=rng.choice(COLORS), creator_id=bd.AGENT_USER_ID, created=created),
        # the near-twin title sits in the same column of the same project
        seeded_task(id=twin_id, title=twin_title, project_id=project.id, column=src_col,
                    owner_id=rng.choice([0, twin_user_id]), position=positions[1], color_id=rng.choice(COLORS),
                    creator_id=bd.AGENT_USER_ID, created=created + 60),
        # the same title in another project, in the same-named column
        seeded_task(id=same_title_id, title=title, project_id=other.id, column=src_col, owner_id=0,
                    position=positions[2], color_id=rng.choice(COLORS), creator_id=bd.AGENT_USER_ID,
                    created=created + 120),
    ]
    for i, (nid, ntitle) in enumerate(zip(noise_ids, rng.sample(NOISE_TITLES, n_noise))):
        sql.append(seeded_task(id=nid, title=ntitle, project_id=project.id,
                               column=rng.choice(cols), owner_id=rng.choice([0, *[u.id for u in bd.USERS[1:]]]),
                               position=positions[3 + i], color_id=rng.choice(COLORS), creator_id=bd.AGENT_USER_ID,
                               created=created + 180 + i))
    dst_id = bd.column_id(project.id, dst_col)
    user = bd.user_by_id(assignee_id)
    instruction = (f'In the Kanboard project "{project.name}", move the task "{title}" to the "{dst_col}" column '
                   f"and assign it to {user.name}.")
    effects = [
        Check(id="moved", kind="query", db="kanboard", sql="SELECT column_id FROM tasks WHERE id = ?",
              params=[target_id], equals=bd.column_id(project.id, src_col), op="ne", reason_code="NOT_DONE"),
        Check(id="assigned", kind="query", db="kanboard", sql="SELECT owner_id FROM tasks WHERE id = ?",
              params=[target_id], equals=start_owner, op="ne", reason_code="NOT_DONE"),
        Check(id="column", kind="query", db="kanboard", sql="SELECT column_id FROM tasks WHERE id = ?",
              params=[target_id], equals=dst_id, reason_code="WRONG_VALUE"),
        Check(id="assignee", kind="query", db="kanboard", sql="SELECT owner_id FROM tasks WHERE id = ?",
              params=[target_id], equals=assignee_id, reason_code="WRONG_VALUE"),
    ]
    invariants = [
        # the target stays in its project and swimlane, open, with its title, description, dates and colour
        preserve_task("target_other_fields", target_id, mutable=["column_id", "owner_id", *BOOKKEEPING],
                      reason="COLLATERAL_EDIT"),
        *common_invariants(target=target_id, twins=[twin_id, same_title_id], comment_floor=bd.MAX_BASE_COMMENT_ID,
                           comments_exempt=False),
    ]
    return TaskInstance(
        world=WORLD, family=FAMILY, seed=seed, split=split, task_id=make_task_id(FAMILY, split, seed),
        instruction=instruction,
        initial_screen={"app": "kanboard", "url": board_url(other.id if start_on_other else project.id),
                        "project_id": other.id if start_on_other else project.id},
        seeding=Seeding(extra_sql={"kanboard": "\n".join(sql) + "\n"}),
        expected={"target_task_id": target_id, "project_id": project.id, "column_id": dst_id, "column": dst_col,
                  "source_column_id": bd.column_id(project.id, src_col), "owner_id": assignee_id,
                  "start_owner_id": start_owner, "twin_task_id": twin_id, "same_title_task_id": same_title_id,
                  "same_title_project_id": other.id, "twin_user_id": twin_user_id, "noise_task_ids": noise_ids,
                  "title": title, "twin_title": twin_title},
        oracle=OracleSpec(effects=effects, invariants=invariants), budget=dict(BUDGET),
        difficulty={"start_on_other_project": start_on_other, "noise_tasks": n_noise, "source_column": src_col,
                    "twin_user": True, "twin_title": True, "same_title_other_project": True},
    )
