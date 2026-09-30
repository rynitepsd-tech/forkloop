"""A Forkloop project: one YAML file naming the world, backend, store, agents and loop settings.

```yaml
version: 1
world: claims-ops-v1
backend: docker                 # docker | solari | fake
store: runs/loop/forkloop.sqlite
budget: {max_steps: 80, max_seconds: 1500}
history_k: 8
checkpoints: {strategy: replay, every: 5, before_types: true, before_keys: [Return]}
student:                        # the agent being improved (same schema as a compare variant)
  name: student
  policy: student
  system_prompt_file: ../forkloop/policies/prompts/agent_memory_v1.md
  options: {base_url: http://127.0.0.1:8000/v1, model: holo, memory: true}
teacher:                        # proposes corrections under the same observation contract
  name: teacher
  policy: student
  system_prompt_file: ../forkloop/policies/prompts/agent_memory_v1.md
  options: {base_url: https://api.openai.com/v1, model: gpt-5.6-luna, memory: true}
repair: {k: 3, max_restart_points: 2, concurrency: 4}
```
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from ..policy_config import ConfiguredPolicy, _mapping, configure_policy
from .checkpoint import CheckpointPolicy
from .repair import RepairConfig
from .store import Store

KEYS = {"version", "world", "backend", "store", "budget", "history_k", "checkpoints", "student", "teacher", "repair",
        "concurrency", "infra_retries", "notes", "settle"}


@dataclass
class Project:
    path: Path
    world_name: str
    backend_name: str
    store_path: Path
    budget: dict[str, Any]
    history_k: int
    checkpoints: CheckpointPolicy
    repair: RepairConfig
    student: Optional[ConfiguredPolicy]
    teacher: Optional[ConfiguredPolicy]
    concurrency: int = 2
    infra_retries: int = 2
    #: "stable": after each action wait until two consecutive screenshots match (≤ 8 s), so the
    #: policy never sees a half-loaded page and replays reproduce the recorded screen; "fixed": 0.6 s.
    settle: str = "stable"
    raw: dict[str, Any] = field(default_factory=dict)

    def store(self) -> Store:
        return Store(self.store_path)

    def world(self):
        from ..world import load_world
        return load_world(self.world_name)

    def backend(self, world: Any):
        from ..cli import _backend
        return _backend(self.backend_name, world)


def load_project(path: str | Path, *, require_env: bool = True, overrides: Optional[dict[str, Any]] = None) -> Project:
    path = Path(path).resolve()
    raw = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "project", KEYS)
    if overrides:
        import copy
        raw = copy.deepcopy(raw)
        raw["student"] = {**raw["student"], "options": {**raw["student"].get("options", {}), **overrides}}
    if raw.get("version") != 1:
        raise ValueError("project configuration requires version: 1")
    backend = raw.get("backend", "docker")
    if backend not in ("docker", "solari", "fake"):
        raise ValueError("backend must be docker, solari or fake")
    budget = _mapping(raw.get("budget", {}), "budget", {"max_steps", "max_seconds"})
    for k, v in budget.items():
        if type(v) not in (int, float) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"budget.{k} must be positive")
    store = Path(raw.get("store", "runs/loop/forkloop.sqlite"))
    if not store.is_absolute():
        store = (path.parent / store).resolve()
    ck = CheckpointPolicy.from_dict(_mapping(raw.get("checkpoints", {}), "checkpoints"))
    history_k = int(raw.get("history_k", 8))
    rep_raw = dict(_mapping(raw.get("repair", {}), "repair"))
    rep_raw.setdefault("history_k", history_k)
    rep_raw.setdefault("budget_override", dict(budget))
    repair = RepairConfig(**rep_raw)
    student = configure_policy(raw["student"], path.parent, require_env=require_env) if raw.get("student") else None
    teacher = configure_policy(raw["teacher"], path.parent, require_env=require_env) if raw.get("teacher") else None
    return Project(path=path, world_name=raw.get("world", "claims-ops-v1"), backend_name=backend, store_path=store,
                   budget=dict(budget), history_k=history_k, checkpoints=ck, repair=repair, student=student,
                   teacher=teacher, concurrency=int(raw.get("concurrency", 2)),
                   infra_retries=int(raw.get("infra_retries", 2)), settle=str(raw.get("settle", "stable")), raw=raw)


__all__ = ["Project", "load_project"]
