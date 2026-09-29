"""Lambda Cloud: inventory, launch with reconcile-before-retry, terminate, filesystems.

A launch is registered before the request is sent and carries a unique instance name. If the
response is ambiguous (timeout, 5xx, dropped connection), the instance list is reconciled by that
name before any retry, so a timeout cannot create a duplicate GPU. The API key is read from
``LAMBDA_API_KEY`` and never logged.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

from .registry import Registry, Resource

API = "https://cloud.lambda.ai/api/v1/"


class LambdaError(RuntimeError):
    pass


class LambdaCloud:
    def __init__(self, api_key: Optional[str] = None, *, registry: Optional[Registry] = None, timeout_s: float = 60) -> None:
        key = api_key or os.environ.get("LAMBDA_API_KEY")
        if not key:
            raise LambdaError("LAMBDA_API_KEY is not set")
        self._c = httpx.Client(base_url=API, auth=(key, ""), timeout=timeout_s,
                               headers={"User-Agent": "forkloop-ops"})
        self.registry = registry or Registry()

    # ------------------------------------------------------------ inventory
    def _get(self, path: str) -> Any:
        r = self._c.get(path)
        if r.status_code >= 400:
            raise LambdaError(f"GET {path}: HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["data"]

    def instances(self) -> list[dict[str, Any]]:
        return self._get("instances")

    def instance(self, instance_id: str) -> dict[str, Any]:
        return self._get(f"instances/{instance_id}")

    def instance_types(self) -> dict[str, Any]:
        return self._get("instance-types")

    def filesystems(self) -> list[dict[str, Any]]:
        return self._get("file-systems")

    def available(self, type_name: str) -> list[str]:
        t = self.instance_types().get(type_name)
        return [r["name"] for r in t["regions_with_capacity_available"]] if t else []

    def price_usd_h(self, type_name: str) -> float:
        return self.instance_types()[type_name]["instance_type"]["price_cents_per_hour"] / 100

    # --------------------------------------------------------------- launch
    def launch(self, *, type_name: str, region: str, name: str, purpose: str, owner: str, lease_s: float,
               ssh_key: str = "Solari Macbook GPU", filesystems: Optional[list[str]] = None,
               max_attempts: int = 3) -> Resource:
        if any(i.get("name") == name for i in self.instances()):
            raise LambdaError(f"an instance named {name!r} already exists; refuse to launch a duplicate")
        res = self.registry.request(provider="lambda", kind="instance", name=name, purpose=purpose, owner=owner,
                                    lease_s=lease_s, hourly_usd=self.price_usd_h(type_name), type_name=type_name,
                                    region=region)
        body = {"region_name": region, "instance_type_name": type_name, "ssh_key_names": [ssh_key], "name": name,
                "quantity": 1}
        if filesystems:
            body["file_system_names"] = filesystems
        last = ""
        for attempt in range(1, max_attempts + 1):
            try:
                r = self._c.post("instance-operations/launch", json=body)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                r = None
            if r is not None and r.status_code < 400:
                ids = r.json()["data"]["instance_ids"]
                return self.registry.update(res.rid, "created", state="running", provider_id=ids[0])
            if r is not None and 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                self.registry.update(res.rid, "create_refused", state="terminated", error=r.text[:300])
                raise LambdaError(f"launch refused: HTTP {r.status_code}: {r.text[:300]}")
            last = last or (f"HTTP {r.status_code}: {r.text[:300]}" if r is not None else "no response")
            # Ambiguous: reconcile by name before retrying.
            for _ in range(6):
                time.sleep(10)
                match = [i for i in self.instances() if i.get("name") == name]
                if match:
                    return self.registry.update(res.rid, "created_after_reconcile", state="running",
                                                provider_id=match[0]["id"], note=last)
            self.registry.update(res.rid, "launch_retry", attempt=attempt, error=last)
        self.registry.update(res.rid, "create_uncertain", state="uncertain", error=last)
        raise LambdaError(f"launch failed after {max_attempts} attempts: {last}")

    def wait_active(self, instance_id: str, timeout_s: float = 1200) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            d = self.instance(instance_id)
            if d["status"] == "active" and d.get("ip"):
                return d
            if d["status"] in ("terminated", "unhealthy"):
                raise LambdaError(f"instance {instance_id} is {d['status']}")
            time.sleep(15)
        raise LambdaError(f"instance {instance_id} not active after {timeout_s}s")

    def terminate(self, rid: str) -> None:
        res = self.registry.get(rid)
        if res is None or res.provider != "lambda" or res.kind != "instance" or not res.provider_id:
            raise LambdaError(f"{rid} is not a registered Lambda instance")
        d = self.instance(res.provider_id)
        if d.get("name") != res.name:
            raise LambdaError("provider name does not match the registry; refusing to terminate")
        r = self._c.post("instance-operations/terminate", json={"instance_ids": [res.provider_id]})
        if r.status_code >= 400:
            raise LambdaError(f"terminate: HTTP {r.status_code}: {r.text[:300]}")
        self.registry.update(rid, "terminate_requested", state="terminating")

    def confirm_terminated(self, rid: str) -> bool:
        res = self.registry.get(rid)
        assert res is not None and res.provider_id
        alive = [i for i in self.instances() if i["id"] == res.provider_id and i["status"] != "terminated"]
        if not alive:
            self.registry.update(rid, "terminated", state="terminated")
            return True
        return False

    # ---------------------------------------------------------- filesystems
    def create_filesystem(self, *, name: str, region: str, purpose: str, owner: str) -> Resource:
        existing = [f for f in self.filesystems() if f.get("name") == name]
        if existing:
            raise LambdaError(f"filesystem {name!r} already exists")
        res = self.registry.request(provider="lambda", kind="filesystem", name=name, purpose=purpose, owner=owner,
                                    region=region, monthly_usd_per_gb=0.20)
        r = self._c.post("filesystems", json={"name": name, "region": region})  # POST route differs from GET file-systems
        if r.status_code >= 400:
            match = [f for f in self.filesystems() if f.get("name") == name]
            if not match:
                self.registry.update(res.rid, "create_refused", state="deleted", error=r.text[:300])
                raise LambdaError(f"filesystem create: HTTP {r.status_code}: {r.text[:300]}")
            return self.registry.update(res.rid, "created_after_reconcile", state="running", provider_id=match[0]["id"])
        return self.registry.update(res.rid, "created", state="running", provider_id=r.json()["data"]["id"])


__all__ = ["LambdaCloud", "LambdaError"]
