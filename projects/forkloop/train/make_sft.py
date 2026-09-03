"""Export verified trajectories to an SFT dataset (rung 1 -> rung 2).

Walks ``runs/<run_id>/episodes/*/`` exactly as laid out in contracts.md §10,
keeps episodes whose ``verdict.json`` has ``reward == 1.0`` and writes one JSONL
record per valid step::

    {"images": ["/abs/.../shots/003_before.png"], "instruction": "...",
     "history": ["click(640, 360)", ...], "target": "type(\\"C-1042\\")",
     "task_id": "resolve_denial-train-000123", "family": "resolve_denial",
     "seed": 123, "split": "train", "step": 3, "episode_id": "...", "run_id": "..."}

``target`` is the step's action in the contract's compact text form (converted
from the canonical ``action`` dict; ``raw_action`` is the fallback), so a
student trained on it speaks the ``compact`` prompt style of
``forkloop.policies.student``.

Usage::

    python -m train.make_sft --run-dir runs/teacher_v1 --out data/sft_all.jsonl
    python -m train.make_sft --run-dir runs/teacher_v1 --limit 50 --out data/sft_50.jsonl
    python -m train.make_sft --run-dir runs/a --run-dir runs/b --families resolve_denial \\
        --exclude-split 'heldout_*' --out data/sft.jsonl

``--limit N`` keeps the first N qualifying episodes in ``task_id`` order (stable
across runs, so the 25/50/100/200 checkpoints are nested subsets).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# Allow running from the project root without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from forkloop.policies.action_parse import to_compact  # noqa: E402


@dataclass
class Episode:
    run_dir: Path
    episode_dir: Path
    manifest: dict
    verdict: dict
    steps: list[dict]
    bad_lines: int = 0

    @property
    def task_id(self) -> str:
        return str(self.manifest.get("task_id") or self.episode_dir.name)

    @property
    def reward(self) -> float:
        try:
            return float(self.verdict.get("reward", 0.0))
        except (TypeError, ValueError):
            return 0.0


@dataclass
class Stats:
    runs: list[str] = field(default_factory=list)
    episodes_seen: int = 0
    episodes_missing_files: int = 0
    episodes_unverified: int = 0
    episodes_superseded: int = 0  # attempts collect --retry-failed replaced with a better one
    episodes_filtered_family: int = 0
    episodes_filtered_split: int = 0
    episodes_failed: int = 0
    episodes_kept: int = 0
    episodes_dropped_by_limit: int = 0
    steps_seen: int = 0
    steps_invalid_skipped: int = 0
    steps_no_image: int = 0
    steps_bad_lines: int = 0
    records: int = 0
    per_family: dict = field(default_factory=dict)
    per_split: dict = field(default_factory=dict)
    per_action_type: dict = field(default_factory=dict)
    mean_steps_per_kept_episode: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _read_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_steps(path: Path) -> tuple[list[dict], int]:
    steps: list[dict] = []
    bad = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    steps.append(obj)
                else:
                    bad += 1
    except OSError:
        return [], bad
    steps.sort(key=lambda s: int(s.get("i", 0)))
    return steps, bad


def iter_episodes(run_dir: Path, stats: Stats | None = None) -> Iterator[Episode]:
    """Yield every episode directory under ``run_dir/episodes`` that has a manifest and steps."""
    run_dir = Path(run_dir)
    ep_root = run_dir / "episodes"
    if not ep_root.is_dir():
        return
    for ep_dir in sorted(p for p in ep_root.iterdir() if p.is_dir()):
        if stats is not None:
            stats.episodes_seen += 1
        manifest = _read_json(ep_dir / "manifest.json")
        if manifest is None or not (ep_dir / "steps.jsonl").exists():
            if stats is not None:
                stats.episodes_missing_files += 1
            continue
        if manifest.get("superseded"):  # contracts.md §10: only the selected attempt per seed is data
            if stats is not None:
                stats.episodes_superseded += 1
            continue
        verdict = _read_json(ep_dir / "verdict.json")
        if verdict is None:
            if stats is not None:
                stats.episodes_unverified += 1
            continue
        steps, bad = _read_steps(ep_dir / "steps.jsonl")
        yield Episode(run_dir=run_dir, episode_dir=ep_dir, manifest=manifest, verdict=verdict, steps=steps, bad_lines=bad)


def _target_for(step: dict) -> str | None:
    action = step.get("action")
    if isinstance(action, dict):
        try:
            return to_compact(action)
        except Exception:
            pass
    raw = step.get("raw_action")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def episode_records(ep: Episode, *, history_k: int, keep_invalid: bool, stats: Stats | None = None) -> list[dict]:
    """One record per (valid) step of an episode."""
    m = ep.manifest
    base = {
        "instruction": str(m.get("instruction", "")),
        "task_id": ep.task_id,
        "family": m.get("family"),
        "seed": m.get("seed"),
        "split": m.get("split"),
        "episode_id": ep.episode_dir.name,
        "run_id": ep.run_dir.name,
        "reward": ep.reward,
    }
    out: list[dict] = []
    history: list[str] = []
    for step in ep.steps:
        if stats is not None:
            stats.steps_seen += 1
        target = _target_for(step)
        valid = bool(step.get("valid", True))
        if not valid and not keep_invalid:
            if stats is not None:
                stats.steps_invalid_skipped += 1
            if target:
                history.append(target)  # the model saw its own invalid attempt in history
            continue
        if target is None:
            if stats is not None:
                stats.steps_invalid_skipped += 1
            continue
        shot = step.get("shot_before")
        if not isinstance(shot, str) or not shot:
            if stats is not None:
                stats.steps_no_image += 1
            history.append(target)
            continue
        image_path = (ep.episode_dir / shot).resolve()
        rec = dict(base)
        rec.update({
            "images": [str(image_path)],
            "history": list(history[-history_k:]) if history_k > 0 else [],
            "target": target,
            "step": int(step.get("i", len(out))),
        })
        out.append(rec)
        if stats is not None:
            action = step.get("action")
            t = action.get("type") if isinstance(action, dict) else "raw"
            stats.per_action_type[t] = stats.per_action_type.get(t, 0) + 1
        history.append(target)
    return out


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, p) for p in patterns)


def build_sft_records(
    run_dirs: Iterable[Path | str],
    *,
    limit: int | None = None,
    families: Iterable[str] | None = None,
    exclude_splits: Iterable[str] = (),
    history_k: int = 8,
    min_reward: float = 1.0,
    keep_invalid: bool = False,
) -> tuple[list[dict], Stats]:
    """Collect SFT records from one or more run directories.

    Episodes are sorted by ``(task_id, episode_dir)`` before ``limit`` is applied,
    so ``--limit 25/50/100/200`` yield nested subsets.
    """
    stats = Stats()
    fam_set = {f.strip() for f in families if f.strip()} if families else None
    excl = [p for p in exclude_splits if p]
    kept: list[Episode] = []
    for rd in run_dirs:
        rd = Path(rd)
        stats.runs.append(str(rd))
        for ep in iter_episodes(rd, stats):
            stats.steps_bad_lines += ep.bad_lines
            fam = str(ep.manifest.get("family", ""))
            split = str(ep.manifest.get("split", ""))
            if fam_set is not None and fam not in fam_set:
                stats.episodes_filtered_family += 1
                continue
            if excl and _matches_any(split, excl):
                stats.episodes_filtered_split += 1
                continue
            if ep.reward < min_reward:
                stats.episodes_failed += 1
                continue
            kept.append(ep)
    kept.sort(key=lambda e: (e.task_id, e.run_dir.name, e.episode_dir.name))
    if limit is not None and limit >= 0 and len(kept) > limit:
        stats.episodes_dropped_by_limit = len(kept) - limit
        kept = kept[:limit]
    records: list[dict] = []
    fam_counter: Counter = Counter()
    split_counter: Counter = Counter()
    for ep in kept:
        recs = episode_records(ep, history_k=history_k, keep_invalid=keep_invalid, stats=stats)
        records.extend(recs)
        fam_counter[str(ep.manifest.get("family"))] += 1
        split_counter[str(ep.manifest.get("split"))] += 1
    stats.episodes_kept = len(kept)
    stats.records = len(records)
    stats.per_family = dict(fam_counter)
    stats.per_split = dict(split_counter)
    stats.mean_steps_per_kept_episode = (len(records) / len(kept)) if kept else 0.0
    return records, stats


def write_jsonl(records: Iterable[dict], path: Path | str) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-dir", action="append", default=[], help="runs/<run_id> directory (repeatable)")
    p.add_argument("--runs-root", default="runs", help="root holding run directories (used with --run-id)")
    p.add_argument("--run-id", action="append", default=[], help="run id under --runs-root (repeatable)")
    p.add_argument("--out", required=True, help="output sft.jsonl path")
    p.add_argument("--stats", default=None, help="stats JSON path (default: <out>.stats.json)")
    p.add_argument("--limit", type=int, default=None, help="keep the first N qualifying episodes (task_id order)")
    p.add_argument("--families", default=None, help="comma-separated family whitelist")
    p.add_argument("--exclude-split", action="append", default=[], help="fnmatch pattern of splits to drop (repeatable), e.g. 'heldout_*'")
    p.add_argument("--history-k", type=int, default=8, help="previous actions kept in each record's history")
    p.add_argument("--min-reward", type=float, default=1.0, help="minimum verdict reward to keep an episode")
    p.add_argument("--keep-invalid", action="store_true", help="also emit steps recorded with valid=false")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dirs = [Path(d) for d in args.run_dir] + [Path(args.runs_root) / rid for rid in args.run_id]
    if not run_dirs:
        print("error: give at least one --run-dir or --run-id", file=sys.stderr)
        return 2
    families = args.families.split(",") if args.families else None
    records, stats = build_sft_records(
        run_dirs, limit=args.limit, families=families, exclude_splits=args.exclude_split,
        history_k=args.history_k, min_reward=args.min_reward, keep_invalid=args.keep_invalid,
    )
    n = write_jsonl(records, args.out)
    stats_path = Path(args.stats) if args.stats else Path(str(args.out) + ".stats.json")
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats.to_dict(), indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "records": n, "episodes_kept": stats.episodes_kept,
                      "episodes_seen": stats.episodes_seen, "stats": str(stats_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
