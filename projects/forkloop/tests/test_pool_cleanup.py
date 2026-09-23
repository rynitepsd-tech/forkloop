"""Pool cleanup regressions found in the 2026-09-22 review."""
from __future__ import annotations

import asyncio

import pytest

from forkloop.backends.fake import FakeBackend
from forkloop.pool import WorkerPool, _is_revert_refusal
from forkloop.world import load_world


@pytest.fixture
def world():
    return load_world("toy-counter")


@pytest.fixture
def backend(tmp_path, world):
    b = FakeBackend(base_dir=tmp_path / "fake", concurrency_cap=2, gui_factory=world.gui_factory())
    yield b
    b.cleanup()


async def test_failed_kill_on_release_keeps_the_worker(world, backend):
    pool = WorkerPool(backend, world, size=1, mode="fork")
    w = await pool.acquire()
    await w.restore()

    async def boom():
        raise RuntimeError("404 machine already gone")

    w.machine.kill = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await pool.release(w, healthy=False)
    # The worker must still be available; before the fix acquire() waited forever.
    again = await asyncio.wait_for(pool.acquire(), timeout=2)
    assert again is w
    w.machine = None
    await pool.close()


async def test_revert_mode_kills_an_unhealthy_machine_before_replacing_it(world, backend):
    pool = WorkerPool(backend, world, size=1, mode="revert")
    w = await pool.acquire()
    first = await w.restore()
    killed = []
    orig_kill = first.kill

    async def unhealthy():
        return False

    async def kill():
        killed.append(first.id)
        await orig_kill()

    first.healthy = unhealthy  # type: ignore[method-assign]
    first.kill = kill  # type: ignore[method-assign]
    second = await w.restore()
    assert second.id != first.id
    assert killed == [first.id]
    await pool.release(w)
    await pool.close()


def test_sdk_409_is_recognised_as_a_revert_refusal():
    from solari_core import errors as se

    from forkloop.backends.solari import _wrap_error

    exc = se.GatewayError(409, "Not revertable")
    wrapped = _wrap_error(exc)
    assert _is_revert_refusal(wrapped)
    # other statuses are not refusals
    assert not _is_revert_refusal(_wrap_error(se.GatewayError(500, "boom")))
