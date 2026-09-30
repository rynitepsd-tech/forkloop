"""Append-only resource registry with renewable leases.

Every billable resource Forkloop creates (a Lambda instance or filesystem, a Solari machine or
snapshot) gets a registry row *before* the create request is sent, then rows for each state
change. A lease says how long the owner intends to keep it; the owner renews it while healthy.
An out-of-process reaper (``forkloop ops reap``) terminates resources whose lease expired plus a
grace period. Retained resources carry an explicit purpose and retention reason instead of an
expiry. Nothing here stores credentials.

The registry is JSONL so that a crash mid-write loses at most one line, and so that it can be
copied between the controller and a persistent filesystem with plain rsync.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

#: Default location (cwd-independent): ``$FORKLOOP_REGISTRY`` or ``~/.forkloop/resources.jsonl``.
DEFAULT_REGISTRY = str(Path.home() / ".forkloop" / "resources.jsonl")


def default_registry_path() -> Path:
    return Path(os.environ.get("FORKLOOP_REGISTRY") or DEFAULT_REGISTRY)

#: Terminal states: the resource no longer exists at the provider (confirmed by inventory).
GONE = {"terminated", "deleted"}


def _now() -> float:
    return time.time()


@dataclass
class Resource:
    rid: str                       # stable Forkloop id, assigned before the create request
    provider: str                  # lambda | solari
    kind: str                      # instance | filesystem | machine | snapshot
    name: str                      # provider-visible name/tag we can reconcile by
    purpose: str
    owner: str                     # job or session that owns it
    provider_id: Optional[str] = None
    state: str = "requested"       # requested | running | retained | uncertain | terminated | deleted
    lease_until: Optional[float] = None
    hourly_usd: Optional[float] = None
    monthly_usd_per_gb: Optional[float] = None
    size_gb: Optional[float] = None
    retain_reason: Optional[str] = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Registry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_registry_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- writes
    def _append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, sort_keys=True, default=str)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def request(self, *, provider: str, kind: str, name: str, purpose: str, owner: str,
                lease_s: Optional[float] = None, hourly_usd: Optional[float] = None, **extra: Any) -> Resource:
        r = Resource(rid=f"{provider}-{kind}-{uuid.uuid4().hex[:10]}", provider=provider, kind=kind, name=name,
                     purpose=purpose, owner=owner, hourly_usd=hourly_usd,
                     lease_until=(_now() + lease_s) if lease_s else None, extra=dict(extra))
        self._append({"event": "requested", **r.to_dict()})
        return r

    def update(self, rid: str, event: str, **fields: Any) -> Resource:
        cur = self.get(rid)
        if cur is None:
            raise KeyError(rid)
        for k, v in fields.items():
            if hasattr(cur, k):
                setattr(cur, k, v)
            else:
                cur.extra[k] = v
        cur.updated_at = _now()
        self._append({"event": event, **cur.to_dict()})
        return cur

    def renew(self, rid: str, lease_s: float) -> Resource:
        return self.update(rid, "lease_renewed", lease_until=_now() + lease_s)

    def retain(self, rid: str, reason: str) -> Resource:
        return self.update(rid, "retained", state="retained", retain_reason=reason, lease_until=None)

    # ----------------------------------------------------------------- reads
    def rows(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn final line from a crash
        return out

    def current(self) -> dict[str, Resource]:
        state: dict[str, Resource] = {}
        fields = set(Resource.__dataclass_fields__)
        for row in self.rows():
            data = {k: v for k, v in row.items() if k in fields}
            state[row["rid"]] = Resource(**data)
        return state

    def get(self, rid: str) -> Optional[Resource]:
        return self.current().get(rid)

    def live(self) -> list[Resource]:
        return [r for r in self.current().values() if r.state not in GONE]

    def expired(self, *, grace_s: float = 600.0, now: Optional[float] = None) -> list[Resource]:
        now = _now() if now is None else now
        return [r for r in self.live()
                if r.state != "retained" and r.lease_until is not None and now > r.lease_until + grace_s]


__all__ = ["Registry", "Resource", "DEFAULT_REGISTRY", "GONE"]
