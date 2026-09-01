"""Forkloop — snapshot-native training worlds for vision-only GUI agents.

reset() is one ``revert()`` call on a Solari desktop VM; fork() is one
``create(from_snapshot=...)``. See docs/contracts.md for every interface.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.1.0"

_LAZY = {
    "Action": "forkloop.actions",
    "InvalidAction": "forkloop.actions",
    "Env": "forkloop.env",
    "make": "forkloop.env",
    "Observation": "forkloop.types",
    "Check": "forkloop.oracle",
    "OracleSpec": "forkloop.oracle",
    "Verdict": "forkloop.oracle",
    "Seeding": "forkloop.tasks",
    "SeedFile": "forkloop.tasks",
    "TaskInstance": "forkloop.tasks",
    "FakeBackend": "forkloop.backends.fake",
    "SolariBackend": "forkloop.backends.solari",
    "WorkerPool": "forkloop.pool",
    "Recorder": "forkloop.trajectories",
    "load_world": "forkloop.world",
}


def __getattr__(name: str) -> Any:
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module 'forkloop' has no attribute {name!r}")
    return getattr(importlib.import_module(mod), name)


__all__ = ["__version__", *sorted(_LAZY)]
