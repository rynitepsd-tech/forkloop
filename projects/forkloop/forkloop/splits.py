"""Split policy for the claims-ops-v1 learning program (policy v1, 2026-09-29).

Four named pools, each a set of ``(generator split, seed range, families)`` blocks with provenance:

- ``dev`` — everything used before 2026-09-29 (and the flagship demonstrations of that day):
  legacy generator splits ``train``/``heldout_seeds``/``heldout_compositions``. Development only.
- ``train`` — generator split ``train_v2``, seeds 10000-99999, minus every held-out structure.
- ``val`` — generator split ``val_v2``, seeds 300000-304999, minus every held-out structure. The
  frozen list in the manifest is what model selection uses.
- ``final_test`` — generator split ``final_test`` (a fresh random stream and a fresh surname pool),
  seeds 350000-359999, frozen with quotas of held-out structures; plus the ``heldout_seeds``
  block 100500-100529 of ``resolve_denial`` that has been sealed since 2026-09-06.

The split string is part of every generator's rng seed, so the three v2 split names give three
disjoint random streams; seed ranges are also numerically disjoint across pools, so a bare seed
names its pool. *Held-out structures* (``HELDOUT_STRUCTURES``) are difficulty combinations and
composition pairings/orderings that may appear only in ``final_test``: ``train``/``val`` skip
them, and :func:`assert_not_final` rejects any task that carries one, whatever its split.

``python -m forkloop.splits write`` regenerates the manifest (``MANIFEST_PATH``); ``check``
verifies it (every frozen task regenerates to its recorded sha256). docs/tasks-and-splits.md
documents the policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Union

WORLD = "claims-ops-v1"
POLICY_VERSION = 1
POLICY_DATE = "2026-09-29"
POOL_NAMES = ("dev", "train", "val", "final_test")
#: families the learning program samples from (resolve_denial_easy is a diagnostic variant: dev only)
LEARNING_FAMILIES = ("reschedule_constrained", "update_insurance_reconcile", "resolve_denial", "compose_claims")
FINAL_SPLITS = ("final_test",)
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "worlds" / "claims_ops_v1" / "tasks" / "splits_manifest.json"


class FinalTestLeak(AssertionError):
    """A final-test task (by pool, split name or held-out structure) reached training/export code."""


@dataclass(frozen=True)
class Block:
    split: str                                   # generator split name (part of the rng seed)
    lo: int
    hi: Optional[int]                            # inclusive; None = unbounded
    families: Optional[tuple[str, ...]] = None   # None = every family of the world
    note: str = ""

    def contains(self, family: str, split: str, seed: int) -> bool:
        return (split == self.split and seed >= self.lo and (self.hi is None or seed <= self.hi)
                and (self.families is None or family in self.families))

    def to_dict(self) -> dict[str, Any]:
        return {"split": self.split, "seeds": [self.lo, self.hi], "families": list(self.families) if self.families else "*",
                "note": self.note}


POOLS: dict[str, tuple[Block, ...]] = {
    "dev": (
        Block("train", 0, 9999, None,
              "legacy train seeds: resolve_denial 0-141, 200-229 and 1234; reschedule_constrained and "
              "update_insurance_reconcile 0-34; resolve_denial_easy 200-229 (runs/, data/, configs/); test fixtures and doc "
              "examples below 10000"),
        Block("train", 900000, None, None,
              "reset benchmark seeds (forkloop/bench, 900000+) and the flagship/dev demonstrations of 2026-09-29 "
              "(resolve_denial 900001-900099)"),
        Block("heldout_seeds", 100000, 100499, None,
              "live comparisons resolve_denial 100300-100351 and the 100400 smoke check; test fixtures 100000-100003, 100314"),
        Block("heldout_compositions", 200000, 200999, None, "test fixtures 200000-200003 (no recorded episodes)"),
    ),
    "train": (Block("train_v2", 10000, 99999, LEARNING_FAMILIES, "fresh stream; held-out structures skipped"),),
    "val": (Block("val_v2", 300000, 304999, LEARNING_FAMILIES, "fresh stream, training surnames; held-out structures skipped"),),
    "final_test": (
        Block("final_test", 350000, 359999, LEARNING_FAMILIES, "fresh stream and surname pool; frozen list with structure quotas"),
        Block("heldout_seeds", 100500, 100529, ("resolve_denial",),
              "reserved for one final evaluation since 2026-09-06 (docs/contracts.md section 13)"),
    ),
}

#: Frozen list sizes and held-out-structure quotas (family -> (n, {structure: minimum}))
VAL_SIZES = {"reschedule_constrained": 20, "update_insurance_reconcile": 20, "resolve_denial": 20, "compose_claims": 40}
FINAL_SIZES = {
    "reschedule_constrained": (30, {"H5": 10}),
    "update_insurance_reconcile": (30, {"H2": 10}),
    "resolve_denial": (30, {"H1": 8, "H6": 8}),
    "compose_claims": (60, {"H3": 20, "H4": 12}),
}

#: Task ids found in the repository before this policy (runs/, data/, docs/, configs/, tests/), as ranges.
HISTORICAL_TASKS = {
    "resolve_denial|train": [[0, 141], [200, 229], [1234, 1234]],
    "resolve_denial_easy|train": [[200, 229]],
    "reschedule_constrained|train": [[0, 34]],
    "update_insurance_reconcile|train": [[0, 34]],
    "resolve_denial|heldout_seeds": [[100000, 100000], [100300, 100351], [100400, 100400]],
}


# ----------------------------------------------------------------------------- structures

@dataclass(frozen=True)
class Holdout:
    id: str
    families: tuple[str, ...]        # base family names; compose parts are checked under their base family
    description: str
    rule: Callable[[dict[str, Any]], bool]


HELDOUT_STRUCTURES: tuple[Holdout, ...] = (
    Holdout("H1", ("resolve_denial", "resolve_denial_easy"),
            "attachment required AND two distractor patients with denied claims",
            lambda f: bool(f.get("require_attachment")) and f.get("distractors") == 2),
    Holdout("H2", ("update_insurance_reconcile",),
            "OpenEMR already updated (portal-only) AND a same-surname distractor with a near-miss member ID",
            lambda f: bool(f.get("partially_updated")) and bool(f.get("same_surname_distractor"))),
    Holdout("H3", ("compose_claims",), "pairing: insurance update + reschedule (INS+RSC)",
            lambda f: f.get("pairing") == "INS+RSC"),
    Holdout("H4", ("compose_claims",), "ordering: the reschedule stated before the appeal (APL+RSC, RSC first)",
            lambda f: f.get("pairing") == "APL+RSC" and list(f.get("order") or []) == ["RSC", "APL"]),
    Holdout("H5", ("reschedule_constrained",),
            "occupied natural slot AND the patient's other-provider appointment AND a same-surname distractor (v2 only)",
            lambda f: bool(f.get("occupied_slot")) and bool(f.get("has_other_provider_appt"))
            and bool(f.get("same_surname_distractor"))),
    Holdout("H6", ("resolve_denial", "resolve_denial_easy"),
            "a previously rejected appeal citing a decoy AND attachment required (v2 only)",
            lambda f: bool(f.get("prior_rejected_appeal")) and bool(f.get("require_attachment"))),
)
_PART_FAMILY = {"INS": "update_insurance_reconcile", "APL": "resolve_denial", "RSC": "reschedule_constrained"}
_PATIENT_ROW = re.compile(r"INSERT INTO patients \([^)]*\) VALUES \((\d+), '((?:[^']|'')*)', '((?:[^']|'')*)', '((?:[^']|'')*)'")

TaskLike = Union[Any, dict, tuple]


def _triple(task: TaskLike) -> tuple[str, str, int]:
    if isinstance(task, tuple):
        return str(task[0]), str(task[1]), int(task[2])
    if isinstance(task, dict):
        return str(task["family"]), str(task["split"]), int(task["seed"])
    return str(task.family), str(task.split), int(task.seed)


def _generate(family: str, split: str, seed: int) -> Any:
    from worlds.claims_ops_v1.seed_world import generate

    task = generate(family, seed, split)
    task.oracle.validate()
    return task


def _fields(task: TaskLike) -> tuple[dict[str, Any], str]:
    """(difficulty, portal seeding SQL) of a TaskInstance, a manifest dict, or a (family, split, seed) triple."""
    if isinstance(task, tuple):
        task = _generate(*_triple(task))
    if isinstance(task, dict):
        seeding = task.get("seeding") or {}
        return dict(task.get("difficulty") or {}), str(seeding.get("portal_sql", "") if isinstance(seeding, dict) else "")
    return dict(task.difficulty or {}), str(task.seeding.portal_sql)


def _same_surname_from_sql(portal_sql: str) -> bool:
    """Legacy tasks do not record ``same_surname_distractor`` for families 2-3: the first seeded
    portal patient is the task's patient, the later ones are distractors."""
    lasts = [m.group(4) for m in _PATIENT_ROW.finditer(portal_sql)]
    return bool(lasts) and lasts[0] in lasts[1:]


def structure(task: TaskLike) -> dict[str, Any]:
    """Structure features used by the partition: the task's difficulty knobs (compose parts under
    ``parts``), with ``same_surname_distractor`` derived from the seeding SQL where a legacy task
    does not record it."""
    family, split, seed = _triple(task)
    diff, portal_sql = _fields(task)
    feats = {k: v for k, v in diff.items() if k != "parts"}
    if family in ("resolve_denial", "resolve_denial_easy", "update_insurance_reconcile") and "same_surname_distractor" not in feats:
        if not portal_sql and not isinstance(task, tuple):
            _, portal_sql = _fields((family, split, seed))
        feats["same_surname_distractor"] = _same_surname_from_sql(portal_sql)
    if family == "compose_claims":
        feats["parts"] = {k: dict(v) for k, v in (diff.get("parts") or {}).items()}
    return feats


def heldout_structures(task: TaskLike) -> list[str]:
    """Ids of the held-out structures a task carries (compose parts are checked under their base family)."""
    family, _, _ = _triple(task)
    feats = structure(task)
    hits = [h.id for h in HELDOUT_STRUCTURES if family in h.families and h.rule(feats)]
    for kind, part in (feats.get("parts") or {}).items():
        base = _PART_FAMILY.get(kind)
        hits += [f"{h.id}@{kind}" for h in HELDOUT_STRUCTURES if base in h.families and h.rule(part)]
    return hits


# ----------------------------------------------------------------------------- pools and guards

def is_final_split(split: str) -> bool:
    """True for a generator split that exists only for the final test (``final_test``)."""
    return split in FINAL_SPLITS


def pool_of(family: str, split: str, seed: int) -> Optional[str]:
    """The pool whose blocks contain this triple, or None (unassigned: do not use without a policy revision)."""
    for name in POOL_NAMES:
        if any(b.contains(family, split, int(seed)) for b in POOLS[name]):
            return name
    return None


def final_reasons(task: TaskLike) -> list[str]:
    """Why a task must stay out of training/export (empty: it may be used)."""
    family, split, seed = _triple(task)
    reasons = []
    if is_final_split(split):
        reasons.append(f"generator split {split!r} is final-test only")
    if pool_of(family, split, seed) == "final_test":
        reasons.append(f"{family}/{split}/{seed} is in a final_test block")
    held = heldout_structures(task)
    if held:
        reasons.append(f"held-out structure(s) {held} may appear only in final_test")
    return reasons


def is_final(task: TaskLike) -> bool:
    return bool(final_reasons(task))


def assert_not_final(task: TaskLike) -> None:
    """Guard for training, SFT/dataset export and model selection: raises :class:`FinalTestLeak` for a
    final-test task (split name, final block such as heldout_seeds 100500-100529) or any task that
    carries a held-out structure. Accepts a TaskInstance, a manifest dict or a (family, split, seed) triple."""
    reasons = final_reasons(task)
    if reasons:
        family, split, seed = _triple(task)
        raise FinalTestLeak(f"{family}-{split}-{seed:06d}: " + "; ".join(reasons))


def assert_trainable(task: TaskLike, *, allow_dev: bool = False) -> None:
    """Stricter guard for building a training set: the task must be in the ``train`` pool (or ``dev``
    with ``allow_dev``) and carry no held-out structure. Validation and final tasks are refused."""
    assert_not_final(task)
    family, split, seed = _triple(task)
    pool = pool_of(family, split, seed)
    if pool != "train" and not (allow_dev and pool == "dev"):
        raise FinalTestLeak(f"{family}-{split}-{seed:06d} is in pool {pool!r}, not 'train'")


def _block_seeds(block: Block) -> Iterator[int]:
    hi = block.hi if block.hi is not None else block.lo + 10**6
    return iter(range(block.lo, hi + 1))


def iter_pool(pool: str, family: str) -> Iterator[Any]:
    """Tasks of one family in a pool, in seed order (``train``/``val`` skip held-out structures;
    ``final_test`` yields every task of its blocks — the frozen list is :func:`pool_tasks`)."""
    if pool not in POOLS:
        raise ValueError(f"unknown pool {pool!r}; have {POOL_NAMES}")
    for block in POOLS[pool]:
        if block.families is not None and family not in block.families:
            continue
        for seed in _block_seeds(block):
            task = _generate(family, block.split, seed)
            if pool in ("train", "val") and heldout_structures(task):
                continue
            yield task


def pool_tasks(pool: str, families: Optional[Iterable[str]] = None, *, limit_per_family: Optional[int] = None,
               include_legacy_sealed: bool = True, manifest: Optional[dict[str, Any]] = None) -> list[Any]:
    """The task list of a pool. ``val`` and ``final_test`` come from the frozen manifest lists (each task is
    regenerated and must match its recorded sha256); ``train`` and ``dev`` are generated in seed order and
    need ``limit_per_family``."""
    fams = list(families) if families is not None else list(LEARNING_FAMILIES)
    if pool in ("val", "final_test"):
        m = manifest or load_manifest()
        entries = list(m["pools"][pool]["frozen"])
        if pool == "final_test" and include_legacy_sealed:
            entries += m["pools"][pool]["legacy_sealed"]
        out = []
        per: dict[str, int] = {}
        for e in entries:
            if e["family"] not in fams:
                continue
            if limit_per_family is not None and per.get(e["family"], 0) >= limit_per_family:
                continue
            task = _generate(e["family"], e["split"], e["seed"])
            digest = task_sha256(task)
            if digest != e["sha256"]:
                raise RuntimeError(f"{task.task_id}: regenerated sha256 {digest} != manifest {e['sha256']} "
                                   "(the generator changed; bump POLICY_VERSION and rewrite the manifest)")
            per[e["family"]] = per.get(e["family"], 0) + 1
            out.append(task)
        return out
    if limit_per_family is None:
        raise ValueError(f"pool {pool!r} is large: pass limit_per_family")
    out = []
    for fam in fams:
        it = iter_pool(pool, fam)
        for _ in range(limit_per_family):
            task = next(it, None)
            if task is None:
                break
            out.append(task)
    return out


# ----------------------------------------------------------------------------- manifest

def task_sha256(task: Any) -> str:
    return hashlib.sha256(task.to_json().encode("utf-8")).hexdigest()


def _entry(task: Any) -> dict[str, Any]:
    return {"task_id": task.task_id, "family": task.family, "split": task.split, "seed": task.seed,
            "sha256": task_sha256(task), "heldout": heldout_structures(task)}


def _frozen_val() -> list[dict[str, Any]]:
    out = []
    for fam in LEARNING_FAMILIES:
        it = iter_pool("val", fam)
        out += [_entry(next(it)) for _ in range(VAL_SIZES[fam])]
    return out


def _frozen_final() -> list[dict[str, Any]]:
    block = POOLS["final_test"][0]
    out = []
    for fam in LEARNING_FAMILIES:
        n, quotas = FINAL_SIZES[fam]
        taken: dict[int, dict[str, Any]] = {}
        need = dict(quotas)
        n_regular = n - sum(quotas.values())
        regular = 0
        for seed in _block_seeds(block):
            if not any(need.values()) and regular >= n_regular:
                break
            e = _entry(_generate(fam, block.split, seed))
            base_ids = {h.split("@")[0] for h in e["heldout"]}
            hit = next((h for h, left in need.items() if left and h in base_ids), None)
            if hit is not None:
                need[hit] -= 1
                taken[seed] = e
            elif not e["heldout"] and regular < n_regular:
                regular += 1
                taken[seed] = e
        if any(need.values()) or regular < n_regular:
            raise RuntimeError(f"final_test block too small for {fam}: missing {need}, regular {regular}/{n_regular}")
        out += [taken[s] for s in sorted(taken)]
    return out


def _legacy_sealed() -> list[dict[str, Any]]:
    block = POOLS["final_test"][1]
    return [_entry(_generate(fam, block.split, seed)) for fam in block.families or () for seed in _block_seeds(block)]


def _historical_heldout() -> dict[str, list[str]]:
    """Historical (dev) task ids that carry a held-out structure: anything trained on them has seen it."""
    out: dict[str, list[str]] = {}
    for key, ranges in sorted(HISTORICAL_TASKS.items()):
        fam, split = key.split("|")
        for lo, hi in ranges:
            for seed in range(lo, hi + 1):
                held = heldout_structures(_generate(fam, split, seed))
                if held:
                    out.setdefault(",".join(sorted(held)), []).append(f"{fam}-{split}-{seed:06d}")
    return out


def _train_exclusion(n: int = 500) -> dict[str, float]:
    """Share of the first ``n`` train-pool seeds per family that carry a held-out structure (skipped)."""
    block = POOLS["train"][0]
    return {fam: round(sum(bool(heldout_structures(_generate(fam, block.split, s)))
                           for s in range(block.lo, block.lo + n)) / n, 4) for fam in LEARNING_FAMILIES}


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def build_manifest() -> dict[str, Any]:
    from worlds.claims_ops_v1.tasks.common import LEGACY_SPLITS, SURNAMES, V2_SPLITS

    body = {
        "policy": "claims-ops-v1 split policy", "policy_version": POLICY_VERSION, "policy_date": POLICY_DATE, "world": WORLD,
        "learning_families": list(LEARNING_FAMILIES),
        "generator_splits": {"legacy": list(LEGACY_SPLITS), "v2": list(V2_SPLITS), "final_only": list(FINAL_SPLITS),
                             "surname_pools": {s: SURNAMES[s][0] + ".." + SURNAMES[s][-1] for s in V2_SPLITS}},
        "pools": {name: {"blocks": [b.to_dict() for b in POOLS[name]]} for name in POOL_NAMES},
        "heldout_structures": {h.id: {"families": list(h.families), "description": h.description} for h in HELDOUT_STRUCTURES},
        "historical_tasks": HISTORICAL_TASKS,
        "historical_tasks_with_heldout_structures": _historical_heldout(),
        "train_pool_exclusion_rate_first_500": _train_exclusion(),
    }
    body["pools"]["val"]["frozen"] = _frozen_val()
    body["pools"]["final_test"]["frozen"] = _frozen_final()
    body["pools"]["final_test"]["quotas"] = {f: {"n": n, "min": q} for f, (n, q) in FINAL_SIZES.items()}
    body["pools"]["final_test"]["legacy_sealed"] = _legacy_sealed()
    return {**body, "sha256": canonical_sha256(body)}


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    m = json.loads(Path(path).read_text())
    body = {k: v for k, v in m.items() if k != "sha256"}
    if canonical_sha256(body) != m.get("sha256"):
        raise RuntimeError(f"{path}: sha256 does not match its content")
    return m


def check_manifest(path: Path = MANIFEST_PATH) -> list[str]:
    """Problems with the committed manifest (empty = it is exactly what this code generates)."""
    problems = []
    try:
        committed = load_manifest(path)
    except (OSError, ValueError, RuntimeError) as e:
        return [f"cannot load {path}: {e}"]
    fresh = build_manifest()
    if fresh["sha256"] != committed["sha256"]:
        for key in sorted(set(fresh) | set(committed)):
            if fresh.get(key) != committed.get(key):
                problems.append(f"manifest field {key!r} differs from the generated one")
    return problems


def write_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    m = build_manifest()
    Path(path).write_text(json.dumps(m, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return m


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["write", "check", "show", "pool-of"])
    p.add_argument("args", nargs="*", help="pool-of: FAMILY SPLIT SEED")
    a = p.parse_args(argv)
    if a.command == "write":
        m = write_manifest()
        print(f"wrote {MANIFEST_PATH} sha256={m['sha256']}")
        return 0
    if a.command == "check":
        problems = check_manifest()
        print("\n".join(problems) if problems else f"ok {load_manifest()['sha256']}")
        return 1 if problems else 0
    if a.command == "pool-of":
        fam, split, seed = a.args
        task = (fam, split, int(seed))
        print(json.dumps({"pool": pool_of(fam, split, int(seed)), "final_reasons": final_reasons(task)}))
        return 0
    m = load_manifest()
    for name in POOL_NAMES:
        pool = m["pools"][name]
        print(name, json.dumps(pool["blocks"]), f"frozen={len(pool.get('frozen', []))}")
    return 0


__all__ = ["POOLS", "POOL_NAMES", "LEARNING_FAMILIES", "HELDOUT_STRUCTURES", "Block", "Holdout", "FinalTestLeak",
           "structure", "heldout_structures", "is_final_split", "pool_of", "final_reasons", "is_final", "assert_not_final",
           "assert_trainable", "iter_pool", "pool_tasks", "build_manifest", "load_manifest", "check_manifest",
           "write_manifest", "task_sha256", "MANIFEST_PATH"]

if __name__ == "__main__":
    sys.exit(main())
