"""``forkloop ops``: inventory, leases and the out-of-process reaper.

    forkloop ops inventory [--provider]        registry state (+ live provider listings)
    forkloop ops renew RID --hours H           extend a lease while the owning job is healthy
    forkloop ops retain RID --reason TEXT      keep a resource past its lease, with a reason
    forkloop ops reap [--grace-min M] [--dry-run]   terminate/delete resources whose lease expired

Reaping acts only on resources in the registry, re-checks the provider's name/id before acting,
and never touches anything the registry does not own.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any

from .registry import Registry


def _lambda():
    from .lambda_cloud import LambdaCloud
    return LambdaCloud()


def _provider_listing() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if os.environ.get("LAMBDA_API_KEY"):
        try:
            lc = _lambda()
            out["lambda_instances"] = [{k: i.get(k) for k in ("id", "name", "status", "ip")} |
                                       {"type": i["instance_type"]["name"]} for i in lc.instances()]
            out["lambda_filesystems"] = [{k: f.get(k) for k in ("id", "name", "bytes_used")} for f in lc.filesystems()]
        except Exception as e:  # noqa: BLE001
            out["lambda_error"] = f"{type(e).__name__}: {e}"
    if os.environ.get("SOLARI_API_KEY"):
        async def solari() -> dict:
            from solari_sandbox import SandboxClient
            c = SandboxClient(api_key=os.environ["SOLARI_API_KEY"], base_url=os.environ.get("SOLARI_BASE_URL", "https://api.getsolari.com"))
            try:
                snaps = [{"id": s.id, "name": s.name, "gb": round((s.sizeBytes or 0) / 1e9, 2), "created": s.createdAt}
                         for s in await c.list_snapshots()]
                machines = []
                for kind in ("desktop", "sandbox"):
                    async for v in c.list_all(kind=kind):
                        machines.append({"id": v.sandboxId, "state": v.state, "kind": kind, "metadata": dict(v.metadata or {})})
                return {"solari_snapshots": snaps, "solari_machines": machines}
            finally:
                await c.aclose()
        try:
            out.update(asyncio.run(solari()))
        except Exception as e:  # noqa: BLE001
            out["solari_error"] = f"{type(e).__name__}: {e}"
    return out


def cmd_inventory(args: argparse.Namespace) -> int:
    reg = Registry()
    rows = [r.to_dict() for r in (reg.current().values() if args.all else reg.live())]
    now = time.time()
    for r in rows:
        r["lease_left_h"] = round((r["lease_until"] - now) / 3600, 2) if r.get("lease_until") else None
    out: dict[str, Any] = {"registry": str(reg.path), "resources": rows}
    if args.provider:
        out["provider"] = _provider_listing()
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_renew(args: argparse.Namespace) -> int:
    r = Registry().renew(args.rid, args.hours * 3600)
    print(f"{r.rid} lease until {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(r.lease_until))}")
    return 0


def cmd_retain(args: argparse.Namespace) -> int:
    Registry().retain(args.rid, args.reason)
    print(f"{args.rid} retained: {args.reason}")
    return 0


async def _reap_solari(res: Any, dry: bool) -> str:
    from ..backends.solari import SolariBackend
    b = SolariBackend(session_ledger=os.environ.get("FORKLOOP_SESSION_LEDGER") or "/dev/null")
    try:
        if res.kind == "snapshot":
            if not dry:
                await b.delete_snapshot(res.provider_id)
            return "deleted snapshot"
        if res.kind == "machine":
            if not dry:
                await b.kill_machine(res.provider_id)
            return "killed machine"
    finally:
        await b.close()
    return "unsupported kind"


def cmd_reap(args: argparse.Namespace) -> int:
    reg = Registry()
    expired = reg.expired(grace_s=args.grace_min * 60)
    report = []
    for res in expired:
        action = "skip"
        try:
            if res.state == "requested" and not res.provider_id:
                action = "no provider id (request never confirmed); inspect provider inventory manually"
            elif res.provider == "lambda" and res.kind == "instance":
                if not args.dry_run:
                    lc = _lambda()
                    lc.terminate(res.rid)
                action = "terminate requested"
            elif res.provider == "solari":
                action = asyncio.run(_reap_solari(res, args.dry_run))
                if not args.dry_run and action.startswith(("deleted", "killed")):
                    reg.update(res.rid, "reaped", state="deleted" if res.kind == "snapshot" else "terminated")
            else:
                action = f"no reaper for {res.provider}/{res.kind}"
        except Exception as e:  # noqa: BLE001 - keep going; the next pass retries
            action = f"error: {type(e).__name__}: {str(e)[:200]}"
        report.append({"rid": res.rid, "name": res.name, "kind": res.kind, "provider_id": res.provider_id, "action": action})
    # confirm pending Lambda terminations
    for res in reg.live():
        if res.provider == "lambda" and res.kind == "instance" and res.state == "terminating" and not args.dry_run:
            try:
                if _lambda().confirm_terminated(res.rid):
                    report.append({"rid": res.rid, "action": "termination confirmed"})
            except Exception as e:  # noqa: BLE001
                report.append({"rid": res.rid, "action": f"confirm error: {e}"})
    print(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dry_run": args.dry_run,
                      "expired": len(expired), "actions": report}, indent=2))
    return 0


def add_commands(sub: Any) -> None:
    p = sub.add_parser("ops", help="resource inventory, leases and the out-of-process reaper")
    s = p.add_subparsers(dest="ops_cmd", required=True)
    q = s.add_parser("inventory")
    q.add_argument("--provider", action="store_true", help="also list live provider resources")
    q.add_argument("--all", action="store_true", help="include terminated/deleted rows")
    q.set_defaults(fn=cmd_inventory)
    q = s.add_parser("renew")
    q.add_argument("rid")
    q.add_argument("--hours", type=float, required=True)
    q.set_defaults(fn=cmd_renew)
    q = s.add_parser("retain")
    q.add_argument("rid")
    q.add_argument("--reason", required=True)
    q.set_defaults(fn=cmd_retain)
    q = s.add_parser("reap")
    q.add_argument("--grace-min", type=float, default=10.0)
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(fn=cmd_reap)


__all__ = ["add_commands"]
