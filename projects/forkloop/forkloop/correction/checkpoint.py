"""Bound checkpoints: one record ties the world state to the policy state at a trajectory position.

A checkpoint row (``Store.put_checkpoint``) holds:

* **world** — ``strategy`` and ``world_ref``: ``snapshot`` (a provider VM snapshot id, e.g. a
  Solari desktop snapshot that preserves memory and disk), ``replay`` (restore = reset the task
  and re-execute the recorded action prefix, then check fidelity), or ``reset`` (step 0: the
  seeded initial state). Plus a :class:`~forkloop.correction.digest.WorldDigest` of persisted
  tables and the screen, used to check any restore;
* **policy** — the full ``snapshot_state()`` (content-addressed blobs for screenshots), the
  declared ``agent_state()`` another policy may adopt (explicit memory), the policy identity
  (model, prompt hash, options), and trajectory counters (step, history, budget, elapsed time);
* **verifier status at that point** — ``clean`` / ``damaged`` / ``unknown``, computed by the
  controller with the task's oracle. ``damaged`` means a safety violation already happened
  (collateral edit, duplicate, wrong record, direct DB write, forbidden screen) or a record that
  did not exist at reset now exists with a wrong checked value; restart points are chosen among
  clean checkpoints only.

Nothing here is visible to the policy. Accounting for checkpoints (snapshot seconds and count,
digest seconds) is written to the store's append-only ``charges``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..oracle import SAFETY_REASONS, Oracle, Verdict
from .digest import world_digest

STRATEGIES = ("replay", "snapshot")
#: Reason codes of checks whose value, once changed from its reset value to a wrong one, is damage.
VALUE_REASONS = frozenset({"WRONG_VALUE", "WRONG_SLOT", "PROVIDER_CHANGED", "WRONG_ATTACHMENT", "WRONG_RECORD"})


@dataclass
class CheckpointPolicy:
    """When to take checkpoints during a recorded attempt.

    ``every``: every N policy steps (0 = off). ``before_types``: before every ``type`` action
    (data entry is where values go wrong). ``before_keys``: before these key chords (e.g.
    ``Return`` submits a form). Step 0 is always a checkpoint (the seeded reset state costs
    nothing to restore). ``max_snapshots`` caps provider snapshots per attempt for the
    ``snapshot`` strategy; further boundaries fall back to replay checkpoints.
    """

    strategy: str = "replay"
    every: int = 5
    before_types: bool = True
    before_keys: tuple[str, ...] = ("Return",)
    max_snapshots: int = 6
    oracle_status: bool = True

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"checkpoint strategy must be one of {STRATEGIES}")
        self.before_keys = tuple(self.before_keys)

    def boundary(self, step: int, action: Any, last_ckpt_step: Optional[int]) -> Optional[str]:
        if step == 0:
            return "start"
        if last_ckpt_step == step:
            return None
        t = getattr(action, "type", None)
        if self.before_types and t == "type":
            return "before_type"
        if self.before_keys and t == "key" and "+".join(getattr(action, "keys", ()) or ()) in self.before_keys:
            return "before_key"
        if self.every and step % self.every == 0:
            return f"every_{self.every}"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "every": self.every, "before_types": self.before_types,
                "before_keys": list(self.before_keys), "max_snapshots": self.max_snapshots,
                "oracle_status": self.oracle_status}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CheckpointPolicy":
        return CheckpointPolicy(**{k: v for k, v in d.items() if k in CheckpointPolicy.__dataclass_fields__})


# ----------------------------------------------------------------------------- blobs


class Blobs:
    """Content-addressed store for policy state (screenshots stay out of SQLite)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        p = self.root / sha[:2] / sha
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return sha

    def get(self, sha: str) -> bytes:
        data = (self.root / sha[:2] / sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"blob {sha} is corrupt")
        return data

    def encode(self, obj: Any) -> Any:
        if isinstance(obj, (bytes, bytearray)):
            return {"__blob__": self.put(bytes(obj))} if obj else {"__bytes__": ""}
        if isinstance(obj, dict):
            if any(not isinstance(k, str) for k in obj):
                return {"__intdict__": [[k, self.encode(v)] for k, v in obj.items()]}
            return {k: self.encode(v) for k, v in obj.items()}
        if isinstance(obj, tuple):
            return {"__tuple__": [self.encode(v) for v in obj]}
        if isinstance(obj, list):
            return [self.encode(v) for v in obj]
        return obj

    def decode(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            if "__blob__" in obj:
                return self.get(obj["__blob__"])
            if "__bytes__" in obj:
                return base64.b64decode(obj["__bytes__"])
            if "__tuple__" in obj:
                return tuple(self.decode(v) for v in obj["__tuple__"])
            if "__intdict__" in obj:
                return {k: self.decode(v) for k, v in obj["__intdict__"]}
            return {k: self.decode(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.decode(v) for v in obj]
        return obj

    def put_json(self, obj: Any) -> str:
        return self.put(json.dumps(self.encode(obj), sort_keys=True).encode())

    def get_json(self, sha: str) -> Any:
        return self.decode(json.loads(self.get(sha)))


# ----------------------------------------------------------------------------- policy identity / state


def policy_identity(policy: Any) -> dict[str, Any]:
    ident: dict[str, Any] = {"class": f"{type(policy).__module__}.{type(policy).__qualname__}",
                             "name": getattr(policy, "name", type(policy).__name__)}
    describe = getattr(policy, "describe", None)
    if callable(describe):
        try:
            ident["describe"] = describe()
        except Exception as e:  # noqa: BLE001
            ident["describe_error"] = f"{type(e).__name__}: {e}"
    prompt = getattr(policy, "system_prompt_override", None)
    if prompt:
        ident["system_prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    for k in ("seed", "temperature", "model", "base_url"):
        if hasattr(policy, k):
            ident[k] = getattr(policy, k)
    return ident


def snapshot_policy(policy: Any) -> tuple[dict, dict]:
    """(full branchable state, declared agent state). Policies without explicit state get ({}, {})."""
    full = policy.snapshot_state() if callable(getattr(policy, "snapshot_state", None)) else {}
    agent = policy.agent_state() if callable(getattr(policy, "agent_state", None)) else {}
    return full, agent


# ----------------------------------------------------------------------------- verifier status


def classify(verdict: Verdict, reference: Optional[dict[str, Any]] = None) -> tuple[str, list[str]]:
    """``clean`` / ``damaged`` / ``unknown`` for a mid-episode verdict.

    ``reference`` is the verdict's ``details`` at step 0: a value check counts as damage only when
    it observed nothing at reset and now observes a wrong value (a newly created wrong record)."""
    damage, errors = [], []
    ref = reference or {}
    for cid in verdict.failed:
        d = verdict.details.get(cid) or {}
        if "error" in d:
            errors.append(cid)
            continue
        rc = d.get("reason_code")
        if rc in SAFETY_REASONS:
            damage.append(cid)
        elif rc in VALUE_REASONS and "actual" in d:
            # A record that did not exist at reset and now exists with a wrong value (an appeal
            # filed with the wrong authorization) cannot be undone through the UI. A field that
            # existed at reset and now holds another value (a counter, a policy number, an
            # appointment date) can still be edited, so it is progress, not damage.
            before = (ref.get(cid) or {}).get("actual")
            if before in (None, "") and d.get("actual") not in (None, ""):
                damage.append(cid)
    return ("damaged" if damage else "unknown" if errors else "clean"), damage + errors


async def oracle_now(env: Any) -> Verdict:
    ep = env.ep
    ctx = env.world.oracle_context(ep.dbs, ep.baseline)
    try:
        return await Oracle(ctx).evaluate(ep.task.oracle)
    except Exception as e:  # noqa: BLE001
        return Verdict.error(f"{type(e).__name__}: {e}")


def _slim(details: dict[str, Any]) -> dict[str, Any]:
    """Verifier details without row dumps (they stay controller-side in the store)."""
    out = {}
    for cid, d in details.items():
        if isinstance(d, dict):
            out[cid] = {k: d[k] for k in ("passed", "actual", "expected", "reason_code", "error", "n_unexpected") if k in d}
    return out


async def capture(env: Any, *, store: Any, blobs: Blobs, attempt_id: str, step: int, boundary: str,
                  policy_state: dict, agent_state: dict, cfg: CheckpointPolicy, snapshots_taken: int,
                  reference: Optional[dict[str, Any]], experiment_id: Optional[str] = None) -> dict[str, Any]:
    """Take one bound checkpoint before the policy's action at ``step`` is applied."""
    from .store import stable_id

    ep = env.ep
    t0 = time.monotonic()
    digest = await world_digest(env.world, ep.dbs, ep.last_shot)
    t_digest = time.monotonic() - t0
    verdict = await oracle_now(env) if cfg.oracle_status else None
    status, why = classify(verdict, reference) if verdict is not None else ("unknown", [])
    strategy, world_ref, t_snap = "replay", f"replay:{step}", 0.0
    if step == 0:
        strategy, world_ref = "reset", "reset"
    elif cfg.strategy == "snapshot" and snapshots_taken < cfg.max_snapshots:
        t1 = time.monotonic()
        world_ref = await ep.machine.snapshot(f"fl-{attempt_id[-16:]}-s{step:03d}")
        t_snap = time.monotonic() - t1
        strategy = "snapshot"
        store.charge("snapshot", 1, "count", ref=world_ref, experiment_id=experiment_id,
                     evidence={"attempt_id": attempt_id, "step": step, "seconds": round(t_snap, 3),
                               "backend": getattr(env.backend, "name", "?")})
        store.charge("snapshot_seconds", t_snap, "s", ref=world_ref, experiment_id=experiment_id)
    ckpt_id = stable_id("ck", attempt_id, step)
    row = {
        "ckpt_id": ckpt_id, "attempt_id": attempt_id, "step": step, "strategy": strategy, "world_ref": world_ref,
        "status": status,
        "digest_json": json.dumps(digest.to_dict(), sort_keys=True),
        "oracle_json": json.dumps({"boundary": boundary, "why": why, "reason_code": verdict.reason_code if verdict else None,
                                   "milestones": verdict.milestones if verdict else None,
                                   "details": _slim(verdict.details) if verdict else {}}, sort_keys=True, default=str),
        "policy_state_ref": blobs.put_json(policy_state),
        "agent_state_json": json.dumps(agent_state, sort_keys=True),
        "elapsed_s": time.monotonic() - ep.started_at, "budget_steps": ep.budget_steps, "invalid": ep.invalid,
        "history_json": json.dumps(list(ep.history)),
        "cost_json": json.dumps({"digest_s": round(t_digest, 3), "snapshot_s": round(t_snap, 3),
                                 "total_s": round(time.monotonic() - t0, 3)}),
        "created_at": time.time(),
    }
    store.put_checkpoint(row)
    store.charge("checkpoint_seconds", time.monotonic() - t0, "s", ref=ckpt_id, experiment_id=experiment_id,
                 evidence={"strategy": strategy})
    return {"ckpt_id": ckpt_id, "strategy": strategy, "status": status, "details": verdict.details if verdict else {}}


__all__ = ["CheckpointPolicy", "Blobs", "policy_identity", "snapshot_policy", "classify", "capture", "oracle_now"]
