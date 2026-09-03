"""SolariMachine re-dials a dropped control channel and retries the operation once (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from forkloop.backends.solari import SolariMachine


class ConnectionError(Exception):  # noqa: A001 - mirrors solari_core.errors.ConnectionError by name
    pass


class StubDesktop:
    def __init__(self, fail_first: int = 1):
        self.id = "d1"
        self.calls = {"connect": 0, "close": 0, "click": 0, "health": 0}
        self.fail_first = fail_first
        self.mouse = SimpleNamespace(click=self._click)

    async def connect(self):
        self.calls["connect"] += 1

    async def close(self):
        self.calls["close"] += 1

    async def health(self):
        self.calls["health"] += 1
        return SimpleNamespace(ready=True)

    async def _click(self, x, y, button="left"):
        self.calls["click"] += 1
        if self.calls["click"] <= self.fail_first:
            raise ConnectionError("Not connected — call connect() first")


def _machine(d):
    backend = SimpleNamespace(ready_timeout_s=5.0, name="solari")
    return SolariMachine(d, backend, (1280, 720), {}, kind="desktop")


async def test_dropped_channel_is_redialled_and_the_action_retried():
    d = StubDesktop(fail_first=1)
    m = _machine(d)
    await m.click(10, 20)
    assert d.calls["click"] == 2 and d.calls["connect"] == 1 and d.calls["close"] == 1
    assert m.reconnects == 1


async def test_persistent_drop_raises_after_one_retry():
    d = StubDesktop(fail_first=99)
    m = _machine(d)
    try:
        await m.click(10, 20)
    except ConnectionError:
        pass
    else:
        raise AssertionError("expected the second failure to surface")
    assert d.calls["click"] == 2 and m.reconnects == 1


async def test_other_errors_are_not_retried():
    d = StubDesktop(fail_first=0)

    async def boom(x, y, button="left"):
        d.calls["click"] += 1
        raise ValueError("bad coordinates")

    d.mouse.click = boom
    m = _machine(d)
    try:
        await m.click(1, 1)
    except ValueError:
        pass
    assert d.calls["click"] == 1 and d.calls["connect"] == 0
