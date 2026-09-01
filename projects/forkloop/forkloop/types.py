"""Small shared records used across modules (see docs/contracts.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class SnapshotInfo:
    id: str
    name: Optional[str] = None
    parent: Optional[str] = None
    size_bytes: int = 0
    created_at: str = ""
    kind: str = "desktop"
    template: str = ""


@dataclass
class MachineInfo:
    id: str
    state: str
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: str = ""


@dataclass
class Observation:
    """What the agent sees. Nothing else crosses the agent channel."""

    screenshot: bytes
    instruction: str
    step: int
    history: list[str]
    width: int
    height: int

    def to_dict(self, *, include_image: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "instruction": self.instruction,
            "step": self.step,
            "history": list(self.history),
            "width": self.width,
            "height": self.height,
        }
        if include_image:
            import base64

            d["screenshot_b64"] = base64.b64encode(self.screenshot).decode()
        return d


@dataclass
class StageTiming:
    name: str
    seconds: float
    ok: bool = True
    note: str = ""


@dataclass
class ResetReport:
    """Timings for one reset(), stage by stage (docs/contracts.md §11)."""

    method: str                       # revert | fork | cold
    stages: list[StageTiming] = field(default_factory=list)
    total_seconds: float = 0.0
    ok: bool = True
    error: Optional[str] = None

    def stage(self, name: str) -> Optional[StageTiming]:
        for s in self.stages:
            if s.name == name:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "ok": self.ok,
            "error": self.error,
            "total_seconds": round(self.total_seconds, 4),
            "stages": [
                {"name": s.name, "seconds": round(s.seconds, 4), "ok": s.ok, "note": s.note}
                for s in self.stages
            ],
        }
