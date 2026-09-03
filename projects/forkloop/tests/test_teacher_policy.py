"""Teacher policy against a stub Anthropic client — no network, no key."""

from __future__ import annotations

import copy
from types import SimpleNamespace

from forkloop.policies.teacher import TeacherPolicy
from forkloop.types import Observation

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def _obs(instruction: str = "do the thing") -> Observation:
    return Observation(screenshot=PNG, width=1280, height=720, instruction=instruction, step=0, history=[])


def _tool(id_: str, name: str, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp, toolset_name="computer",
                           model_dump=lambda exclude_none=True: {"type": "tool_use", "id": id_, "name": name, "input": inp})


def _text(t: str):
    return SimpleNamespace(type="text", text=t, model_dump=lambda exclude_none=True: {"type": "text", "text": t})


class StubClient:
    """Returns scripted responses; records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})  # the policy mutates its list later
        content = self.responses.pop(0)
        stop = "tool_use" if any(getattr(b, "type", "") == "tool_use" for b in content) else "end_turn"
        return SimpleNamespace(content=content, stop_reason=stop, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


async def test_screenshot_behind_actions_is_answered_locally(monkeypatch):
    monkeypatch.setattr("forkloop.policies.teacher.resize_png", lambda png, side: (png, 1.0))
    client = StubClient([
        [_text("confidence: 0.9"), _tool("a", "left_click", coordinate=[10, 20]), _tool("b", "screenshot"), _tool("c", "type", text="hi")],
        [_text("DONE")],
    ])
    pol = TeacherPolicy(client=client)
    obs = _obs()
    a1, m1 = await pol.act(obs)
    assert a1 is not None and a1.to_compact() == "click(10, 20)" and m1.get("error") is None
    a2, m2 = await pol.act(obs)  # the screenshot must not surface as an invalid env action
    assert a2 is not None and a2.to_compact() == 'type("hi")' and m2.get("error") is None
    a3, _ = await pol.act(obs)
    assert a3.is_terminal
    # the second request carried a tool_result for every block, the screenshot one as an image
    results = {r["tool_use_id"]: r for r in client.requests[1]["messages"][-1]["content"] if r.get("type") == "tool_result"}
    assert set(results) == {"a", "b", "c"}
    assert results["b"]["content"][0]["type"] == "image"
    assert not results["b"].get("is_error")


class _Overloaded(Exception):
    status_code = 529


async def test_transient_api_errors_are_retried_not_charged(monkeypatch):
    """A 529/429/5xx from the API must not surface as an invalid (budgeted) step."""
    monkeypatch.setattr("forkloop.policies.teacher.resize_png", lambda png, side: (png, 1.0))
    client = StubClient([[_text("confidence: 0.9"), _tool("a", "left_click", coordinate=[10, 20])], [_text("DONE")]])
    real = client._create
    calls = {"n": 0}

    async def flaky(**kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _Overloaded("Error code: 529 overloaded")
        return await real(**kw)

    client.beta.messages.create = flaky
    client.messages.create = flaky
    pol = TeacherPolicy(client=client)
    pol.retry_delays_s = (0.0, 0.0, 0.0)
    a, m = await pol.act(_obs())
    assert a is not None and a.to_compact() == "click(10, 20)" and m.get("error") is None
    assert calls["n"] == 3 and m["tokens"]["retries"] == 2


async def test_non_transient_api_errors_still_surface(monkeypatch):
    monkeypatch.setattr("forkloop.policies.teacher.resize_png", lambda png, side: (png, 1.0))
    client = StubClient([])

    async def bad(**kw):
        raise ValueError("bad request")

    client.beta.messages.create = bad
    pol = TeacherPolicy(client=client)
    a, m = await pol.act(_obs())
    assert a is None and "ValueError" in m["error"]


async def test_cache_breakpoint_moves_to_the_newest_user_block(monkeypatch):
    monkeypatch.setattr("forkloop.policies.teacher.resize_png", lambda png, side: (png, 1.0))
    client = StubClient([[_tool("a", "left_click", coordinate=[1, 2])], [_tool("b", "left_click", coordinate=[3, 4])], [_text("DONE")]])
    pol = TeacherPolicy(client=client)
    obs = _obs()
    await pol.act(obs); await pol.act(obs); await pol.act(obs)
    for req in client.requests:
        assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
        users = [m for m in req["messages"] if m["role"] == "user"]
        marked = [(i, j) for i, m in enumerate(users) for j, b in enumerate(m["content"]) if "cache_control" in b]
        assert marked == [(len(users) - 1, len(users[-1]["content"]) - 1)], marked


async def test_image_pruning_has_hysteresis(monkeypatch):
    monkeypatch.setattr("forkloop.policies.teacher.resize_png", lambda png, side: (png, 1.0))
    n = 20
    client = StubClient([[_tool(f"t{i}", "left_click", coordinate=[1, 2])] for i in range(n)] + [[_text("DONE")]])
    pol = TeacherPolicy(client=client, keep_images=4)
    pol.prune_hysteresis = 3
    obs = _obs()
    counts = []
    for _ in range(n):
        await pol.act(obs)
        imgs = sum(1 for m in pol.messages if m["role"] == "user" for b in m["content"] if b.get("type") == "image")
        counts.append(imgs)
    # never more than keep + hysteresis, and it drops back to keep in one go rather than every turn
    assert max(counts) <= 4 + 3 and 4 in counts and any(counts[i] > counts[i + 1] + 1 for i in range(len(counts) - 1))
