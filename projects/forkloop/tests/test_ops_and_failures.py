"""Operations and failure handling: registry leases, ambiguous Lambda launches reconciled by name
(no duplicate instance), refused launches, and a dropped transport during a repair branch being
kept as an unscored infrastructure outcome."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from forkloop.ops.registry import Registry


def test_registry_leases_and_retention(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    a = reg.request(provider="lambda", kind="instance", name="x", purpose="p", owner="o", lease_s=10)
    b = reg.request(provider="solari", kind="snapshot", name="s", purpose="p", owner="o", lease_s=10)
    reg.update(a.rid, "created", state="running", provider_id="i-1")
    reg.update(b.rid, "created", state="running", provider_id="snap-1")
    reg.retain(b.rid, "evidence for the flagship replay")
    later = time.time() + 10 + 601
    assert [r.rid for r in reg.expired(now=later)] == [a.rid]          # retained resources never expire
    reg.renew(a.rid, 3600)
    assert reg.expired(now=later) == []
    with open(reg.path, "a") as fh:                                       # a torn final line is ignored
        fh.write('{"event": "requ')
    assert {r.rid for r in reg.live()} == {a.rid, b.rid}


def _lambda_transport(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/instance-types"):
            return httpx.Response(200, json={"data": {"gpu_1x_x": {"instance_type": {"price_cents_per_hour": 100},
                                                                   "regions_with_capacity_available": [{"name": "r1"}]}}})
        if path.endswith("/instances") and request.method == "GET":
            return httpx.Response(200, json={"data": state["instances"]})
        if path.endswith("/instance-operations/launch"):
            state["launch_posts"] += 1
            if state["mode"] == "timeout_then_exists":
                state["instances"].append({"id": "inst-1", "name": json.loads(request.content)["name"], "status": "booting"})
                raise httpx.ReadTimeout("simulated timeout after the provider accepted the request")
            if state["mode"] == "refused":
                return httpx.Response(400, json={"error": {"message": "quota"}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.fixture
def fast_sleep(monkeypatch):
    monkeypatch.setattr("forkloop.ops.lambda_cloud.time.sleep", lambda s: None)


def test_ambiguous_launch_is_reconciled_by_name_not_retried(tmp_path, fast_sleep):
    from forkloop.ops.lambda_cloud import LambdaCloud
    state = {"instances": [], "launch_posts": 0, "mode": "timeout_then_exists"}
    lc = LambdaCloud("k", registry=Registry(tmp_path / "r.jsonl"))
    lc._c = httpx.Client(base_url="https://cloud.lambda.ai/api/v1/", transport=_lambda_transport(state))
    res = lc.launch(type_name="gpu_1x_x", region="r1", name="forkloop-test-1", purpose="t", owner="t", lease_s=60)
    assert state["launch_posts"] == 1 and res.provider_id == "inst-1" and res.state == "running"
    events = [r["event"] for r in lc.registry.rows()]
    assert events == ["requested", "created_after_reconcile"]
    with pytest.raises(Exception):   # a second launch with the same name is refused before any POST
        lc.launch(type_name="gpu_1x_x", region="r1", name="forkloop-test-1", purpose="t", owner="t", lease_s=60)
    assert state["launch_posts"] == 1


def test_refused_launch_is_recorded(tmp_path, fast_sleep):
    from forkloop.ops.lambda_cloud import LambdaCloud, LambdaError
    state = {"instances": [], "launch_posts": 0, "mode": "refused"}
    lc = LambdaCloud("k", registry=Registry(tmp_path / "r.jsonl"))
    lc._c = httpx.Client(base_url="https://cloud.lambda.ai/api/v1/", transport=_lambda_transport(state))
    with pytest.raises(LambdaError):
        lc.launch(type_name="gpu_1x_x", region="r1", name="forkloop-test-2", purpose="t", owner="t", lease_s=60)
    assert lc.registry.live() == []


def test_dropped_transport_in_a_branch_is_unscored(tmp_path):
    """A connection error while applying a teacher action ends the branch as infrastructure, not failure."""
    from forkloop.backends.fake import FakeBackend
    from forkloop.correction import CheckpointPolicy, RepairConfig, Store, record_attempt, repair_attempt
    from forkloop.env import Env
    from forkloop.pool import WorkerPool
    from forkloop.world import load_world
    from tests.test_correction_engine import ToyAgent

    world = load_world("toy-counter")
    backend = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=4, gui_factory=world.gui_factory())
    store = Store(tmp_path / "s" / "f.sqlite")
    task = world.generate("reach_target", 3, "train")

    class Flaky(ToyAgent):
        async def act(self, obs):
            a, m = await super().act(obs)
            raise_now = obs.step >= 2
            if raise_now:
                machine = next(iter(backend.machines.values())) if hasattr(backend, "machines") else None
            return a, m

    async def run():
        env = Env(world, backend, pool=WorkerPool(backend, world, size=1, mode="revert"), history_k=100)
        try:
            res = await record_attempt(env, ToyAgent(task.expected["a"], task.expected["a0"], mistake_at=1), task,
                                       store=store, ckpt=CheckpointPolicy(strategy="replay", every=1), attempt_id="att-x")
        finally:
            await env.close()
        from forkloop.backends import fake as fake_mod
        orig = fake_mod.FakeMachine.click

        async def dropping_click(self, x, y, *, button="left"):
            raise ConnectionError("simulated dropped control channel")
        fake_mod.FakeMachine.click = dropping_click
        try:
            return await repair_attempt(store, world, backend, res.attempt_id,
                                        teacher_factory=lambda: ToyAgent(task.expected["a"], task.expected["a0"]),
                                        cfg=RepairConfig(k=1, max_restart_points=1, history_k=100, concurrency=1))
        finally:
            fake_mod.FakeMachine.click = orig

    rep = asyncio.run(run())
    b = store.branches(repair_id=rep.repair_id)[0]
    assert b["status"] == "infra_error" and b["reason_code"] == "INFRA_ERROR"
    assert rep.status == "unrepaired" and rep.verified_branches == []
    backend.cleanup()
