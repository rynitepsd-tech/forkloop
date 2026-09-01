"""Per-step recorder (docs/contracts.md §10). Ours, not Solari's.

Layout::

    runs/<run_id>/run.json
    runs/<run_id>/episodes/<episode_id>/{manifest.json, steps.jsonl, verdict.json, reset.json, shots/*.png, episode.mp4}

Branch rollouts from search live under ``episodes/<id>/branches/<label>/`` with
the same layout; ``adopt()`` copies a winning branch's steps into the parent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .actions import Action
from .oracle import Verdict
from .tasks import TaskInstance


def _now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class StepRecord:
    i: int
    t_wall: float
    action: Optional[dict[str, Any]]
    raw_action: str
    valid: bool
    shot_before: str
    shot_after: str
    model_latency_s: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)
    milestones: Optional[float] = None
    policy_note: str = ""
    search: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {"i": self.i, "t_wall": round(self.t_wall, 4), "action": self.action, "raw_action": self.raw_action,
             "valid": self.valid, "shot_before": self.shot_before, "shot_after": self.shot_after,
             "model_latency_s": round(self.model_latency_s, 4), "tokens": self.tokens}
        if self.milestones is not None:
            d["milestones"] = round(self.milestones, 4)
        if self.policy_note:
            d["policy_note"] = self.policy_note
        if self.search:
            d["search"] = self.search
        if self.error:
            d["error"] = self.error
        return d


class EpisodeRecorder:
    def __init__(self, dir: Path, task: TaskInstance, *, episode_id: str, extra: Optional[dict[str, Any]] = None) -> None:
        self.dir = dir
        self.task = task
        self.episode_id = episode_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "shots").mkdir(exist_ok=True)
        self.t0 = time.monotonic()
        self.steps: list[StepRecord] = []
        self._steps_fh = open(self.dir / "steps.jsonl", "a", encoding="utf-8")
        manifest = task.to_dict()
        manifest.update({"episode_id": episode_id, "generated_at": _now(), **(extra or {})})
        (self.dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
        self.verdict: Optional[Verdict] = None

    # ------------------------------------------------------------- steps
    def _shot(self, name: str, png: bytes) -> str:
        rel = f"shots/{name}"
        if png:
            (self.dir / rel).write_bytes(png)
        return rel if png else ""

    def record_step(self, i: int, *, shot_before: bytes, shot_after: bytes, action: Optional[Action],
                    raw_action: str, valid: bool, model_latency_s: float = 0.0,
                    tokens: Optional[dict[str, int]] = None, milestones: Optional[float] = None,
                    policy_note: str = "", search: Optional[dict[str, Any]] = None,
                    error: Optional[str] = None) -> StepRecord:
        rec = StepRecord(
            i=i, t_wall=time.monotonic() - self.t0, action=action.to_dict() if action else None,
            raw_action=raw_action, valid=valid,
            shot_before=self._shot(f"{i:03d}_before.png", shot_before),
            shot_after=self._shot(f"{i:03d}_after.png", shot_after),
            model_latency_s=model_latency_s, tokens=dict(tokens or {}), milestones=milestones,
            policy_note=policy_note, search=search, error=error)
        self.steps.append(rec)
        self._steps_fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        self._steps_fh.flush()
        return rec

    def record_reset(self, report: dict[str, Any]) -> None:
        (self.dir / "reset.json").write_text(json.dumps(report, indent=2))

    def finish(self, verdict: Verdict, *, extra: Optional[dict[str, Any]] = None) -> None:
        self.verdict = verdict
        d = verdict.to_dict()
        d["wall_seconds"] = round(time.monotonic() - self.t0, 3)
        d["n_steps"] = len(self.steps)
        d["n_invalid"] = sum(1 for s in self.steps if not s.valid)
        d["finished_at"] = _now()
        if extra:
            d.update(extra)
        (self.dir / "verdict.json").write_text(json.dumps(d, indent=2, ensure_ascii=False))
        self._steps_fh.close()

    # ---------------------------------------------------------- branching
    def fork(self, label: str) -> "EpisodeRecorder":
        child = EpisodeRecorder(self.dir / "branches" / label, self.task, episode_id=f"{self.episode_id}/{label}")
        return child

    def adopt(self, child: "EpisodeRecorder", *, from_step: int) -> None:
        """Replace this episode's steps from ``from_step`` on with the child's steps."""
        keep = [s for s in self.steps if s.i < from_step]
        self._steps_fh.close()
        (self.dir / "steps.jsonl").write_text("")
        self._steps_fh = open(self.dir / "steps.jsonl", "a", encoding="utf-8")
        self.steps = []
        for s in keep:
            self.steps.append(s)
            self._steps_fh.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
        for s in child.steps:
            new = StepRecord(**{**s.__dict__})
            for attr in ("shot_before", "shot_after"):
                rel = getattr(s, attr)
                if rel:
                    src = child.dir / rel
                    dst_name = f"{new.i:03d}_{'before' if attr == 'shot_before' else 'after'}.png"
                    shutil.copyfile(src, self.dir / "shots" / dst_name)
                    setattr(new, attr, f"shots/{dst_name}")
            new.search = {**(new.search or {}), "adopted_from": child.episode_id}
            self.steps.append(new)
            self._steps_fh.write(json.dumps(new.to_dict(), ensure_ascii=False) + "\n")
        self._steps_fh.flush()

    # -------------------------------------------------------------- video
    def render_video(self, *, fps: int = 2, out: Optional[Path] = None) -> Optional[Path]:
        """Render shots/*_after.png into an mp4 with ffmpeg if available."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        frames = sorted((self.dir / "shots").glob("*_after.png"))
        if not frames:
            return None
        lst = self.dir / "frames.txt"
        lst.write_text("".join(f"file '{f.name}'\nduration {1 / fps}\n" for f in frames) + f"file '{frames[-1].name}'\n")
        out = out or (self.dir / "episode.mp4")
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p", "-r", str(fps), str(out)]
        subprocess.run(cmd, cwd=self.dir / "shots", check=False)
        lst.unlink(missing_ok=True)
        return out if out.exists() else None


class Recorder:
    def __init__(self, root: str | Path = "runs", run_id: Optional[str] = None, meta: Optional[dict[str, Any]] = None) -> None:
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.root = Path(root)
        self.dir = self.root / self.run_id
        (self.dir / "episodes").mkdir(parents=True, exist_ok=True)
        run_meta = {"run_id": self.run_id, "started_at": _now(), "git_sha": _git_sha(), **(meta or {})}
        (self.dir / "run.json").write_text(json.dumps(run_meta, indent=2, default=str))

    def episode(self, task: TaskInstance, *, episode_id: Optional[str] = None, extra: Optional[dict[str, Any]] = None) -> EpisodeRecorder:
        eid = episode_id or f"{task.task_id}-{uuid.uuid4().hex[:6]}"
        return EpisodeRecorder(self.dir / "episodes" / eid, task, episode_id=eid, extra=extra)

    def episodes(self) -> list[Path]:
        return sorted(p for p in (self.dir / "episodes").iterdir() if (p / "manifest.json").exists())


def _git_sha() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False,
                              cwd=os.path.dirname(__file__)).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def iter_episode_dirs(run_dir: str | Path) -> list[Path]:
    run_dir = Path(run_dir)
    ep = run_dir / "episodes"
    if not ep.exists():
        return []
    return sorted(p for p in ep.iterdir() if (p / "manifest.json").exists())


def load_episode(ep_dir: Path) -> dict[str, Any]:
    manifest = json.loads((ep_dir / "manifest.json").read_text())
    steps = [json.loads(l) for l in (ep_dir / "steps.jsonl").read_text().splitlines() if l.strip()] if (ep_dir / "steps.jsonl").exists() else []
    verdict = json.loads((ep_dir / "verdict.json").read_text()) if (ep_dir / "verdict.json").exists() else None
    reset = json.loads((ep_dir / "reset.json").read_text()) if (ep_dir / "reset.json").exists() else None
    return {"dir": ep_dir, "manifest": manifest, "steps": steps, "verdict": verdict, "reset": reset}


__all__ = ["Recorder", "EpisodeRecorder", "StepRecord", "iter_episode_dirs", "load_episode"]
