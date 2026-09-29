"""Explicit memory: facts come only from the policy's own replies, are rendered identically for
serving and training, and are part of the branchable policy state."""
from __future__ import annotations

import asyncio
import io
import json

import httpx
from PIL import Image

from forkloop.policies.observation import observation_messages
from forkloop.policies.student import (MEMORY_MAX_FACTS, StudentPolicy, extend_memory, memory_from_reply)
from forkloop.types import Observation


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_memory_from_reply_parses_only_memory_lines():
    reply = "The letter shows the code.\nMemory: auth AUTH-36G14538 for claim C-60350\nMemory: none\nclick(10, 20)"
    assert memory_from_reply(reply) == ["auth AUTH-36G14538 for claim C-60350"]
    assert memory_from_reply("click(1, 2)") == []
    assert memory_from_reply("<think>Memory: hidden</think>\nMemory: kept\nclick(1,2)") == ["hidden", "kept"]


def test_extend_memory_dedupes_and_caps():
    mem = extend_memory([], ["a", "a", "b"])
    assert mem == ["a", "b"]
    mem = extend_memory(mem, [f"f{i}" for i in range(40)])
    assert len(mem) == MEMORY_MAX_FACTS and mem[-1] == "f39"


def test_memory_rendered_in_canonical_input():
    msgs = observation_messages(instruction="do it", history=["click(1, 2)"], step=1, screen=(1280, 720),
                                coords=(1280, 720), style="compact", history_k=8, image_count=1,
                                memory=["auth AUTH-1"])
    text = msgs[1]["content"][0]["text"]
    assert "Memory (facts you wrote down on earlier steps, oldest first):\n- auth AUTH-1" in text
    off = observation_messages(instruction="do it", history=[], step=0, screen=(1280, 720), coords=(1280, 720),
                               style="compact", history_k=8, image_count=1)
    assert "Memory" not in off[1]["content"][0]["text"]


def test_policy_carries_memory_across_steps_and_checkpoints():
    replies = iter(["I see the code.\nMemory: auth AUTH-9Z\nclick(10, 10)", "Typing it.\ntype(\"AUTH-9Z\")"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["messages"][1]["content"][0]["text"])
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    pol = StudentPolicy("http://localhost:9/v1", "m", transport=httpx.MockTransport(handler), memory=True)
    shot = _png()

    async def run():
        a0, m0 = await pol.act(Observation(shot, "task", 0, [], 1280, 720))
        state = pol.snapshot_state()
        a1, m1 = await pol.act(Observation(shot, "task", 1, ["click(10, 10)"], 1280, 720, previous_screenshot=shot))
        return m0, state, m1

    m0, state, m1 = asyncio.run(run())
    assert m0["memory_written"] == ["auth AUTH-9Z"] and state["_memory"] == ["auth AUTH-9Z"]
    assert "Memory: empty" in seen[0]
    assert "- auth AUTH-9Z" in seen[1]
    clone = pol.clone_for_branch(state)
    assert clone._memory == ["auth AUTH-9Z"] and clone._memory is not pol._memory


def test_memory_line_does_not_swallow_the_next_line_and_bullets_are_facts():
    assert memory_from_reply("ok\nMemory:\n- auth A-1\n- member W2\nclick(1, 2)") == ["auth A-1", "member W2"]
    assert memory_from_reply("Memory: x\nclick(1, 2)") == ["x"]
    assert memory_from_reply("Memory:\nclick(1, 2)") == []


def test_image_scale_is_shared_by_serving_and_training():
    from PIL import Image
    from forkloop.policies.observation import resize_for_model
    from forkloop.policies.student import prepare_image
    png = _png()
    url, model_size, orig = prepare_image(png, 1920, 1.5)
    assert orig == (1280, 720) and model_size == (1920, 1080)
    im = resize_for_model(Image.open(io.BytesIO(png)), image_max_side=1920, image_scale=1.5)
    assert im.size == (1920, 1080)
    assert prepare_image(png, 1280)[1] == (1280, 720)


def test_self_hosted_transport_errors_are_retried_hosted_are_not():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "click(10, 10)"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    pol = StudentPolicy("http://127.0.0.1:9/v1", "m", transport=httpx.MockTransport(handler))
    a, m = asyncio.run(pol.act(Observation(_png(), "t", 0, [], 1280, 720)))
    assert a is not None and calls["n"] == 3 and not m.get("error")
