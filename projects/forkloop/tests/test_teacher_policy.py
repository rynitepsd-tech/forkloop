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
