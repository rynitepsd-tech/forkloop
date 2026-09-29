"""``generate(family, seed, split) -> TaskInstance`` — kanboard-v1's pure task generator (``world.yaml``
``seed_module``). Families: ``move_and_assign``, ``due_and_comment``; splits ``train`` (seeds 0-99999) and
``heldout_seeds`` (100000-199999), with disjoint title pools and due-date windows."""

from __future__ import annotations

from forkloop.tasks import TaskInstance

from . import due_and_comment, move_and_assign
from .common import SEED_RANGES, check_split, rng_for

FAMILIES = {
    "move_and_assign": move_and_assign.generate,
    "due_and_comment": due_and_comment.generate,
}


def generate(family: str, seed: int, split: str = "train") -> TaskInstance:
    if family not in FAMILIES:
        raise ValueError(f"unknown kanboard-v1 family {family!r}; have {sorted(FAMILIES)}")
    check_split(split, int(seed))
    task = FAMILIES[family](rng_for(family, int(seed), split), int(seed), split)
    task.oracle.validate()
    return task


__all__ = ["generate", "FAMILIES", "SEED_RANGES"]
