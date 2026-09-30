"""Family ``due_and_comment``: set the due date of task "<title>" to <date> and add the comment "<text>".

Distractors: a near-twin title in the same project (sometimes carrying its own due date), the same title in
another project, and an existing comment on the target (the new one must be added, not typed over it).
``difficulty.has_due_date``: the target already has a due date that must be replaced.
"""

from __future__ import annotations

import datetime as dt
import random

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id

from .. import base_data as bd
from .common import (BOOKKEEPING, BUDGET, COLORS, EPISODE_CREATED, NOISE_TITLES, TITLE_PAIRS, WORLD, board_url,
                     common_invariants, episode_ids, pick_projects, preserve_task, seeded_comment, seeded_task)

FAMILY = "due_and_comment"

#: comment = "<status>; <next step>." — short enough to type, specific enough to check exactly
STATUSES = ("Blocked on vendor quote", "Waiting on legal sign-off", "QA found two regressions",
            "Design review moved to Thursday", "Customer asked for a demo first", "Budget approved by finance",
            "Staging deploy is green", "Needs a second reviewer")
NEXT_STEPS = ("follow up on Friday", "revisit after the sprint review", "ping the owner tomorrow",
              "keep the current scope", "ship behind a feature flag", "update the ticket once merged")
OLD_COMMENTS = ("Picked this up from the backlog.", "Draft is linked in the wiki.", "Scope agreed in standup.")
#: due date windows per split (UTC calendar days)
DUE_RANGES = {"train": (dt.date(2026, 10, 5), dt.date(2026, 12, 18)),
              "heldout_seeds": (dt.date(2027, 1, 11), dt.date(2027, 3, 26))}


def _day(rng: random.Random, split: str) -> dt.date:
    lo, hi = DUE_RANGES[split]
    return lo + dt.timedelta(days=rng.randint(0, (hi - lo).days))


def _stamp(d: dt.date) -> int:
    return bd.ts(d.year, d.month, d.day, 17, 0)


def generate(rng: random.Random, seed: int, split: str) -> TaskInstance:
    project, other = pick_projects(rng)
    title, twin_title = rng.choice(TITLE_PAIRS[split])
    cols = list(bd.COLUMN_TITLES)
    col = rng.choice(cols[:4])
    due = _day(rng, split)
    has_due = rng.random() < 0.5
    old_due = _day(rng, split) if has_due else None
    while old_due == due:
        old_due = _day(rng, split)
    comment = f"{rng.choice(STATUSES)}; {rng.choice(NEXT_STEPS)}."
    n_noise = rng.randint(1, 3)
    tids, first_comment = episode_ids(seed, rng, 3 + n_noise)
    target_id, twin_id, same_title_id, *noise_ids = tids
    created = EPISODE_CREATED + rng.randint(0, 5) * 3600
    owner = rng.choice([0, *[u.id for u in bd.USERS[1:]]])
    positions = rng.sample(range(11, 60, 2), 3 + n_noise)
    twin_due = _stamp(_day(rng, split)) if rng.random() < 0.5 else 0
    sql = [
        seeded_task(id=target_id, title=title, project_id=project.id, column=col, owner_id=owner,
                    position=positions[0], color_id=rng.choice(COLORS), creator_id=bd.AGENT_USER_ID, created=created,
                    date_due=_stamp(old_due) if old_due else 0),
        seeded_task(id=twin_id, title=twin_title, project_id=project.id, column=rng.choice(cols[:4]),
                    owner_id=rng.choice([0, owner]), position=positions[1], color_id=rng.choice(COLORS),
                    creator_id=bd.AGENT_USER_ID, created=created + 60, date_due=twin_due),
        seeded_task(id=same_title_id, title=title, project_id=other.id, column=col, owner_id=0,
                    position=positions[2], color_id=rng.choice(COLORS), creator_id=bd.AGENT_USER_ID,
                    created=created + 120),
    ]
    for i, (nid, ntitle) in enumerate(zip(noise_ids, rng.sample(NOISE_TITLES, n_noise))):
        sql.append(seeded_task(id=nid, title=ntitle, project_id=project.id, column=rng.choice(cols),
                               owner_id=rng.choice([0, *[u.id for u in bd.USERS[1:]]]), position=positions[3 + i],
                               color_id=rng.choice(COLORS), creator_id=bd.AGENT_USER_ID, created=created + 180 + i))
    # one existing comment on the target and one on the same-titled task elsewhere
    author = rng.choice([u.id for u in bd.USERS[1:]])
    sql.append(seeded_comment(id=first_comment, task_id=target_id, user_id=author, comment=rng.choice(OLD_COMMENTS),
                              created=created + 1800))
    sql.append(seeded_comment(id=first_comment + 1, task_id=same_title_id, user_id=author,
                              comment=rng.choice(OLD_COMMENTS), created=created + 1860))
    floor = first_comment + 1   # every comment id above this was written during the episode
    iso = due.isoformat()
    instruction = (f'In the Kanboard project "{project.name}", set the due date of the task "{title}" to {iso} '
                   f'and add the comment "{comment}" to it.')
    new_on_target = "SELECT {what} FROM comments WHERE task_id = ? AND id > ?"
    effects = [
        Check(id="due_changed", kind="query", db="kanboard", sql="SELECT date_due FROM tasks WHERE id = ?",
              params=[target_id], equals=_stamp(old_due) if old_due else 0, op="ne", reason_code="NOT_DONE"),
        Check(id="due_date", kind="query", db="kanboard",
              # 'none' (not NULL) when unset, so a wrong date on a task that had none reads as progress to
              # the checkpoint classifier, not as a new wrong record (the field stays editable)
              sql="SELECT CASE WHEN date_due > 0 THEN strftime('%Y-%m-%d', date_due, 'unixepoch') ELSE 'none' END "
                  "FROM tasks WHERE id = ?", params=[target_id], equals=iso, reason_code="WRONG_VALUE"),
        # exactly one new comment on the target (a shortfall is NOT_DONE, an excess a duplicate)
        Check(id="one_comment", kind="count", db="kanboard", sql=new_on_target.format(what="COUNT(*)"),
              params=[target_id, floor], equals=1, reason_code="DUPLICATE_SIDE_EFFECT"),
        Check(id="comment_text", kind="query", db="kanboard",
              sql=new_on_target.format(what="TRIM(comment)") + " ORDER BY id LIMIT 1",
              params=[target_id, floor], equals=comment, reason_code="WRONG_VALUE"),
    ]
    invariants = [
        preserve_task("target_other_fields", target_id, mutable=["date_due", *BOOKKEEPING],
                      reason="COLLATERAL_EDIT"),
        Check(id="no_comment_elsewhere", kind="count", db="kanboard",
              sql="SELECT COUNT(*) FROM comments WHERE id > ? AND task_id <> ?", params=[floor, target_id],
              equals=0, reason_code="WRONG_RECORD"),
        *common_invariants(target=target_id, twins=[twin_id, same_title_id], comment_floor=floor,
                           comments_exempt=True),
    ]
    return TaskInstance(
        world=WORLD, family=FAMILY, seed=seed, split=split, task_id=make_task_id(FAMILY, split, seed),
        instruction=instruction,
        initial_screen={"app": "kanboard", "url": board_url(project.id), "project_id": project.id},
        seeding=Seeding(extra_sql={"kanboard": "\n".join(sql) + "\n"}),
        expected={"target_task_id": target_id, "project_id": project.id, "due_date": iso,
                  "old_due_date": old_due.isoformat() if old_due else None, "comment": comment,
                  "comment_floor": floor, "twin_task_id": twin_id, "same_title_task_id": same_title_id,
                  "same_title_project_id": other.id, "noise_task_ids": noise_ids, "title": title,
                  "twin_title": twin_title},
        oracle=OracleSpec(effects=effects, invariants=invariants), budget=dict(BUDGET),
        difficulty={"has_due_date": has_due, "twin_has_due_date": bool(twin_due), "noise_tasks": n_noise,
                    "column": col, "twin_title": True, "same_title_other_project": True},
    )
