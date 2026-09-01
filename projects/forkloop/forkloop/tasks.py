"""TaskInstance / Seeding records (docs/contracts.md §5)."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .oracle import OracleSpec


@dataclass
class SeedFile:
    path: str
    content_b64: str
    mode: Optional[int] = None

    @staticmethod
    def from_bytes(path: str, data: bytes, mode: Optional[int] = None) -> "SeedFile":
        return SeedFile(path=path, content_b64=base64.b64encode(data).decode("ascii"), mode=mode)

    @property
    def content(self) -> bytes:
        return base64.b64decode(self.content_b64)


@dataclass
class Seeding:
    portal_sql: str = ""
    openemr_sql: str = ""
    files: list[SeedFile] = field(default_factory=list)
    post_commands: list[list[str]] = field(default_factory=list)
    #: extra ``{db_name: sql_script}`` for worlds whose databases are not called portal/openemr
    extra_sql: dict[str, str] = field(default_factory=dict)


@dataclass
class TaskInstance:
    world: str
    family: str
    seed: int
    split: str
    task_id: str
    instruction: str
    initial_screen: dict[str, Any]
    seeding: Seeding
    expected: dict[str, Any]
    oracle: OracleSpec
    budget: dict[str, Any] = field(default_factory=lambda: {"max_steps": 60, "max_seconds": 600})
    difficulty: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------------- codec
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, **kw)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TaskInstance":
        seeding = d.get("seeding") or {}
        return TaskInstance(
            world=d["world"],
            family=d["family"],
            seed=int(d["seed"]),
            split=d["split"],
            task_id=d["task_id"],
            instruction=d["instruction"],
            initial_screen=dict(d.get("initial_screen") or {}),
            seeding=Seeding(
                portal_sql=seeding.get("portal_sql", ""),
                openemr_sql=seeding.get("openemr_sql", ""),
                files=[SeedFile(**f) for f in seeding.get("files", [])],
                post_commands=[list(c) for c in seeding.get("post_commands", [])],
                extra_sql=dict(seeding.get("extra_sql", {})),
            ),
            expected=dict(d.get("expected") or {}),
            oracle=OracleSpec.from_dict(d["oracle"]),
            budget=dict(d.get("budget") or {"max_steps": 60, "max_seconds": 600}),
            difficulty=dict(d.get("difficulty") or {}),
        )

    @property
    def public_info(self) -> dict[str, Any]:
        """The subset that may cross the agent channel."""
        return {"task_id": self.task_id, "family": self.family, "seed": self.seed, "split": self.split,
                "world": self.world, "budget": dict(self.budget)}


def make_task_id(family: str, split: str, seed: int) -> str:
    return f"{family}-{split}-{seed:06d}"


__all__ = ["SeedFile", "Seeding", "TaskInstance", "make_task_id"]
