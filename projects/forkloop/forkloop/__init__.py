"""Forkloop — matched evaluation and inspectable evidence for GUI policies.

Solari snapshots restore the desktop; Forkloop seeds application state, checks
reset equivalence and verifies persisted outcomes. See docs/contracts.md.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.2.2"

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
