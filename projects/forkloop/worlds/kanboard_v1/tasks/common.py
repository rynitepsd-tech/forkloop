"""Shared pieces of the kanboard-v1 task families: seeded randomness, episode ids, title pools, SQL and
the checks both families use.

Purity: every ``random.Random`` is seeded from ``"kanboard-v1:{family}:{split}:{seed}"``; nothing reads
the clock, the environment or any file (the base population is Python constants in ``base_data``).
"""

from __future__ import annotations

import random
from typing import Any

from forkloop.oracle import Check

from .. import base_data as bd

WORLD = "kanboard-v1"
SPLITS = ("train", "heldout_seeds")
#: seed ranges, as in claims-ops-v1 (a bare seed names its split)
SEED_RANGES = {"train": (0, 99999), "heldout_seeds": (100000, 199999)}
BUDGET = {"max_steps": 40, "max_seconds": 600}

#: (title, near-twin title) pairs; train and held-out pools are disjoint and never reuse a base title.
TITLE_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "train": (
        ("Fix login redirect", "Fix logout redirect"),
        ("Update onboarding checklist", "Update offboarding checklist"),
        ("Draft release notes", "Draft release plan"),
        ("Review vendor contract", "Review vendor contacts"),
        ("Prepare Q4 roadmap", "Prepare Q3 roadmap"),
        ("Refactor payment form", "Refactor payment flow"),
        ("Test push notifications", "Test email notifications"),
        ("Clean up analytics tags", "Clean up analytics tables"),
        ("Renew SSL certificate", "Renew SSH certificate"),
        ("Design empty states", "Design error states"),
        ("Translate help center", "Translate help widget"),
        ("Schedule user interviews", "Schedule user surveys"),
        ("Archive old invoices", "Archive old invoice PDFs"),
        ("Benchmark search latency", "Benchmark sync latency"),
        ("Write API changelog", "Write API migration guide"),
        ("Audit access permissions", "Audit access policies"),
    ),
    "heldout_seeds": (
        ("Upgrade build pipeline", "Upgrade build agents"),
        ("Plan beta rollout", "Plan beta recruiting"),
        ("Rename billing fields", "Rename billing tables"),
        ("Verify backup restore", "Verify backup schedule"),
        ("Shorten signup form", "Shorten signin form"),
        ("Map support macros", "Map support tags"),
        ("Tune alert thresholds", "Tune alert routing"),
        ("Publish status page", "Publish status report"),
    ),
}
#: extra tasks that fill the board (never the target or a twin)
NOISE_TITLES: tuple[str, ...] = (
    "Collect design feedback", "Sync with finance", "Order test devices", "Update team wiki", "Groom backlog",
    "Check accessibility report", "Book venue for offsite", "Review error budget", "Consolidate dashboards",
    "Retire legacy endpoint",
)
COLORS: tuple[str, ...] = ("yellow", "blue", "green", "purple", "orange", "grey", "teal", "pink")
#: Episode rows are created a few weeks before the base "today" of the board (fixed, no clock reads).
EPISODE_CREATED = bd.ts(2026, 8, 24, 10, 30)


def check_split(split: str, seed: int) -> None:
    if split not in SPLITS:
        raise ValueError(f"kanboard-v1 has splits {SPLITS}, not {split!r}")
    lo, hi = SEED_RANGES[split]
    if not lo <= seed <= hi:
        raise ValueError(f"seed {seed} is outside the {split} range {lo}-{hi}")


def rng_for(family: str, seed: int, split: str) -> random.Random:
    return random.Random(f"{WORLD}:{family}:{split}:{seed}")


def episode_ids(seed: int, rng: random.Random, n_tasks: int) -> tuple[list[int], int]:
    """Episode task ids (shuffled, so the target's #id says nothing about its role) and the first comment id.
    Block of 20 ids per seed from 500000; episodes never share a machine, the block keeps them readable."""
    base = 500000 + (seed % 5000) * 20
    tids = [base + i for i in range(1, 11)]
    rng.shuffle(tids)
    return tids[:n_tasks], base + 11


def pick_projects(rng: random.Random) -> tuple[bd.Project, bd.Project]:
    target, other = rng.sample(list(bd.PROJECTS), 2)
    return target, other


def board_url(project_id: int) -> str:
    return f"http://localhost/?controller=BoardViewController&action=show&project_id={project_id}"


def seeded_task(**kw: Any) -> str:
    return bd.insert("tasks", bd.task_row(**kw))


def seeded_comment(**kw: Any) -> str:
    return bd.insert("comments", bd.comment_row(**kw))


# ----------------------------------------------------------------------------- checks

#: Columns the edit form or a move rewrites on the changed task by itself (measured on 1.2.54, docs/second-world.md)
BOOKKEEPING = ["date_modification", "date_moved"]


def task_fields_sql() -> str:
    return "SELECT * FROM tasks WHERE id = ?"


def preserve_task(check_id: str, task_id: int, *, mutable: list[str], reason: str) -> Check:
    """Every column of one task row equals its reset value, except ``mutable`` and the world's ignored
    columns (``position``: Kanboard renumbers every card of a column when one card moves)."""
    return Check(id=check_id, kind="preserve_fields", db="kanboard", sql=task_fields_sql(), params=[task_id],
                 mutable_fields=list(mutable), reason_code=reason)


def common_invariants(*, target: int, twins: list[int], comment_floor: int, comments_exempt: bool) -> list[Check]:
    """Invariants both families share, in reporting order (the first failure names the verdict)."""
    out = [preserve_task(f"untouched_{i}", t, mutable=[], reason="WRONG_RECORD") for i, t in enumerate(twins, 1)]
    out.append(Check(id="existing_comments_unchanged", kind="preserve_fields", db="kanboard",
                     sql="SELECT id, task_id, user_id, comment, visibility FROM comments WHERE id <= ? ORDER BY id",
                     params=[comment_floor], reason_code="COLLATERAL_EDIT"))
    out.append(Check(id="no_collateral", kind="baseline_checksum", allow={"kanboard.tasks": [target]},
                     exempt_tables=["kanboard.comments"] if comments_exempt else None, reason_code="COLLATERAL_EDIT"))
    out.append(Check(id="ui_path", kind="ui_path_only", reason_code="DIRECT_DB_WRITE"))
    return out


__all__ = ["WORLD", "SPLITS", "SEED_RANGES", "BUDGET", "TITLE_PAIRS", "NOISE_TITLES", "COLORS", "check_split",
           "rng_for", "episode_ids", "pick_projects", "board_url", "seeded_task", "seeded_comment",
           "preserve_task", "common_invariants", "EPISODE_CREATED", "BOOKKEEPING"]
