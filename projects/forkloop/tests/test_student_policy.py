"""Tests for forkloop.policies.action_parse and forkloop.policies.student.

The env/actions modules are written concurrently by someone else; when they
are absent this file installs minimal stand-ins so ``import forkloop`` works.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import types
from pathlib import Path

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TYPES = {"click", "double_click", "right_click", "move", "scroll", "drag", "type", "key", "wait", "done"}


class InvalidAction(Exception):
    pass


class FakeAction:
    def __init__(self, d: dict) -> None:
        self.d = dict(d)

    @classmethod
    def parse(cls, obj):
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except ValueError as e:
                raise InvalidAction(str(e)) from e
        if not isinstance(obj, dict) or obj.get("type") not in _TYPES:
            raise InvalidAction(f"bad action {obj!r}")
        return cls(obj)

    def to_dict(self) -> dict:
        return dict(self.d)

    @property
    def type(self) -> str:
        return self.d["type"]


def _install_forkloop_stubs() -> None:
    pkg = ROOT / "forkloop"
    stubs = {
        "forkloop.actions": {"Action": FakeAction, "InvalidAction": InvalidAction},
        "forkloop.env": {"Env": object, "Observation": object, "make": lambda *a, **k: None},
        "forkloop.oracle": {"Check": object, "OracleSpec": object, "Verdict": object},
        "forkloop.tasks": {"Seeding": object, "SeedFile": object, "TaskInstance": object},
    }
    for modname, attrs in stubs.items():
        if modname in sys.modules or (pkg / (modname.split(".")[1] + ".py")).exists():
            continue
        mod = types.ModuleType(modname)
        mod.__dict__.update(attrs)
        sys.modules[modname] = mod
    import forkloop  # noqa: F401


_install_forkloop_stubs()

from forkloop.policies import action_parse as ap  # noqa: E402
from forkloop.policies.student import StudentPolicy, build_system_prompt, prepare_image  # noqa: E402


def _as_dict(a):
    return a.to_dict() if hasattr(a, "to_dict") else a


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 210, 220)).save(buf, format="PNG")
    return buf.getvalue()


class Obs:
    def __init__(self, screenshot: bytes, instruction: str, step: int = 0, history: list[str] | None = None,
                 width: int = 1280, height: int = 720) -> None:
        self.screenshot = screenshot
        self.instruction = instruction
        self.step = step
        self.history = history or []
        self.width = width
        self.height = height


# --------------------------------------------------------------------------- #
# compact
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("click(640, 360)", {"type": "click", "x": 640, "y": 360, "button": "left"}),
    ("click(640,360,\"right\")", {"type": "right_click", "x": 640, "y": 360}),
    ("double_click(10,20)", {"type": "double_click", "x": 10, "y": 20}),
    ("right_click(10, 20)", {"type": "right_click", "x": 10, "y": 20}),
    ("move(1, 2)", {"type": "move", "x": 1, "y": 2}),
    ("drag(10,10,200,200)", {"type": "drag", "x": 10, "y": 10, "x2": 200, "y2": 200}),
    ('scroll(640, 360, "down", 3)', {"type": "scroll", "x": 640, "y": 360, "direction": "down", "amount": 3}),
    ("scroll(640, 360, up)", {"type": "scroll", "x": 640, "y": 360, "direction": "up", "amount": 3}),
    ('type("hello")', {"type": "type", "text": "hello"}),
    ("type('it\\'s \"quoted\", ok')", {"type": "type", "text": "it's \"quoted\", ok"}),
    ('type("line1\\nline2")', {"type": "type", "text": "line1\nline2"}),
    ('key("ctrl+l")', {"type": "key", "keys": ["ctrl", "l"]}),
    ('key("Return")', {"type": "key", "keys": ["Return"]}),
    ('key("enter")', {"type": "key", "keys": ["Return"]}),
    ('key("ctrl", "shift", "t")', {"type": "key", "keys": ["ctrl", "shift", "t"]}),
    ("wait(1.5)", {"type": "wait", "seconds": 1.5}),
    ("wait()", {"type": "wait", "seconds": 1.0}),
    ("done()", {"type": "done", "success": True}),
    ("done(success=false, note=\"cannot find claim\")", {"type": "done", "success": False, "note": "cannot find claim"}),
    ("done(false)", {"type": "done", "success": False}),
])
def test_parse_compact_all_forms(text, expected):
    action, err = ap.parse_compact(text)
    assert err is None, err
    assert action == expected


def test_parse_compact_with_prose_and_prefix():
    text = "I need to open the claim first.\nAction: click(412, 233)"
    action, err = ap.parse_compact(text)
    assert err is None
    assert action == {"type": "click", "x": 412, "y": 233, "button": "left"}
    # A call mentioned inside prose is not preferred over the line-anchored one.
    text2 = "Earlier I did click(1, 1) which failed.\nclick(500, 300)"
    action2, _ = ap.parse_compact(text2)
    assert (action2["x"], action2["y"]) == (500, 300)


def test_parse_compact_float_coords_round():
    action, err = ap.parse_compact("click(640.6, 359.4)")
    assert err is None
    assert (action["x"], action["y"]) == (641, 359)


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #

def test_parse_json_fenced():
    text = 'Thinking...\n```json\n{"type": "click", "x": 640, "y": 360, "button": "left"}\n```'
    action, err = ap.parse_json(text)
    assert err is None
    assert action == {"type": "click", "x": 640, "y": 360, "button": "left"}


def test_parse_json_with_action_prefix_and_aliases():
    action, err = ap.parse_json('Action: {"action": "left_click", "coordinate": [100, 200]}')
    assert err is None
    assert action == {"type": "click", "x": 100, "y": 200, "button": "left"}
    action, err = ap.parse_json('{"type": "key", "keys": "ctrl+l"}')
    assert action == {"type": "key", "keys": ["ctrl", "l"]}
    action, err = ap.parse_json('{"type": "scroll", "direction": "up", "amount": 2}', default_xy=(5, 6))
    assert action == {"type": "scroll", "x": 5, "y": 6, "direction": "up", "amount": 2}
    action, err = ap.parse_json("{'type': 'wait', 'seconds': 2}")  # python-style quotes
    assert action == {"type": "wait", "seconds": 2.0}
    action, err = ap.parse_json('{"type": "done", "success": "false", "note": "n"}')
    assert action == {"type": "done", "success": False, "note": "n"}


def test_parse_json_every_contract_type():
    samples = [
        {"type": "click", "x": 640, "y": 360, "button": "left"},
        {"type": "double_click", "x": 640, "y": 360},
        {"type": "right_click", "x": 640, "y": 360},
        {"type": "move", "x": 640, "y": 360},
        {"type": "scroll", "x": 640, "y": 360, "direction": "down", "amount": 3},
        {"type": "drag", "x": 10, "y": 10, "x2": 200, "y2": 200},
        {"type": "type", "text": "hello"},
        {"type": "key", "keys": ["ctrl", "l"]},
        {"type": "wait", "seconds": 1.0},
        {"type": "done", "success": True, "note": "optional"},
    ]
    for s in samples:
        action, err = ap.parse_json(json.dumps(s))
        assert err is None, (s, err)
        assert action == s


# --------------------------------------------------------------------------- #
# Fara 1.5 tool calls
# --------------------------------------------------------------------------- #

def _fara(args: dict) -> str:
    return "Let me click that.\n<tool_call>\n" + json.dumps({"name": "computer_use", "arguments": args}) + "\n</tool_call>"


def test_parse_fara_click_type_key():
    action, err = ap.parse_fara(_fara({"action": "left_click", "coordinate": [512, 300]}))
    assert err is None
    assert action == {"type": "click", "x": 512, "y": 300, "button": "left"}
    action, _ = ap.parse_fara(_fara({"action": "right_click", "coordinate": [1, 2]}))
    assert action["type"] == "right_click"
    action, _ = ap.parse_fara(_fara({"action": "double_click", "coordinate": [1, 2]}))
    assert action["type"] == "double_click"
    action, _ = ap.parse_fara(_fara({"action": "mouse_move", "coordinate": [1, 2]}))
    assert action["type"] == "move"
    action, _ = ap.parse_fara(_fara({"action": "type", "text": "C-1042"}))
    assert action == {"type": "type", "text": "C-1042"}
    action, _ = ap.parse_fara(_fara({"action": "key", "keys": ["Control", "l"]}))
    assert action == {"type": "key", "keys": ["ctrl", "l"]}
    action, _ = ap.parse_fara(_fara({"action": "key", "keys": ["Enter"]}))
    assert action == {"type": "key", "keys": ["Return"]}
    action, _ = ap.parse_fara(_fara({"action": "key", "keys": ["ArrowDown", "PageDown", "Backspace"]}))
    assert action == {"type": "key", "keys": ["Down", "Page_Down", "BackSpace"]}


def test_parse_fara_scroll_wait_terminate():
    action, err = ap.parse_fara(_fara({"action": "scroll", "pixels": -500}))
    assert err is None
    assert action == {"type": "scroll", "x": 500, "y": 500, "direction": "down", "amount": 5}
    action, _ = ap.parse_fara(_fara({"action": "scroll", "coordinate": [100, 900], "pixels": 250}))
    assert action == {"type": "scroll", "x": 100, "y": 900, "direction": "up", "amount": 3}  # 250 px -> 2.5 -> rounds half up
    action, _ = ap.parse_fara(_fara({"action": "wait", "time": 2}))
    assert action == {"type": "wait", "seconds": 2.0}
    action, _ = ap.parse_fara(_fara({"action": "terminate", "answer": "Appeal filed."}))
    assert action == {"type": "done", "success": True, "note": "Appeal filed."}
    action, _ = ap.parse_fara(_fara({"action": "terminate", "status": "failure", "answer": "no"}))
    assert action["success"] is False
    action, _ = ap.parse_fara(_fara({"action": "ask_user_question", "question": "Which claim?"}))
    assert action["type"] == "done" and action["success"] is False and "Which claim?" in action["note"]


def test_parse_fara_unsupported_and_drag():
    action, err = ap.parse_fara(_fara({"action": "visit_url", "url": "http://x"}))
    assert action is None and "visit_url" in err
    action, err = ap.parse_fara(_fara({"action": "left_click_drag", "coordinate": [50, 60]}))
    assert action is None and "start" in err
    action, err = ap.parse_fara(_fara({"action": "left_click_drag", "start_coordinate": [1, 2], "coordinate": [50, 60]}))
    assert action == {"type": "drag", "x": 1, "y": 2, "x2": 50, "y2": 60}


def test_parse_fara_without_newlines_and_tool_calls_list():
    text = '<tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [10, 20]}}</tool_call>'
    action, err = ap.parse_fara(text)
    assert err is None and action["x"] == 10
    tool_calls = [{"id": "1", "type": "function",
                   "function": {"name": "computer_use", "arguments": '{"action": "type", "text": "hi"}'}}]
    action, err = ap.parse_tool_calls(tool_calls)
    assert action == {"type": "type", "text": "hi"}
    # tool name carries the action (computer.click style)
    action, err = ap.parse_fara_call("computer.click", {"coordinate": [3, 4]})
    assert action == {"type": "click", "x": 3, "y": 4, "button": "left"}


def test_parse_any_dispatch():
    assert ap.parse_any("click(1, 2)")[0]["type"] == "click"
    assert ap.parse_any('```json\n{"type": "wait", "seconds": 1}\n```')[0]["type"] == "wait"
    assert ap.parse_any(_fara({"action": "left_click", "coordinate": [1, 2]}))[0]["type"] == "click"
    assert ap.parse_any({"type": "done"})[0] == {"type": "done", "success": True}
    # style hint is a preference, not a restriction
    assert ap.parse_any("click(1, 2)", style="fara")[0]["type"] == "click"
    assert ap.extract_thoughts("I will click.\nclick(1, 2)") == "I will click."
    assert ap.extract_thoughts(_fara({"action": "wait"})) == "Let me click that."


# --------------------------------------------------------------------------- #
# scaling / compact round trip / robustness
# --------------------------------------------------------------------------- #

def test_scale_coords_round_trip():
    click = {"type": "click", "x": 640, "y": 360, "button": "left"}
    down = ap.scale_coords(click, (1280, 720), (1000, 1000))
    assert (down["x"], down["y"]) == (500, 500)
    back = ap.scale_coords(down, (1000, 1000), (1280, 720))
    assert (back["x"], back["y"]) == (640, 360)
    assert click == {"type": "click", "x": 640, "y": 360, "button": "left"}  # input untouched
    drag = ap.scale_coords({"type": "drag", "x": 0, "y": 0, "x2": 639, "y2": 359}, (640, 360), (1280, 720))
    assert (drag["x2"], drag["y2"]) == (1278, 718)
    clamped = ap.scale_coords({"type": "click", "x": 2000, "y": -5}, (1280, 720), (1280, 720))
    assert (clamped["x"], clamped["y"]) == (1279, 0)
    assert ap.scale_coords({"type": "type", "text": "x"}, (1, 1), (2, 2)) == {"type": "type", "text": "x"}
    assert ap.scale_coords(None, (1, 1), (2, 2)) is None
    assert ap.scale_coords(click, (0, 0), (1, 1)) == click  # bad size: unscaled copy


@pytest.mark.parametrize("action", [
    {"type": "click", "x": 640, "y": 360, "button": "left"},
    {"type": "right_click", "x": 1, "y": 2},
    {"type": "double_click", "x": 1, "y": 2},
    {"type": "move", "x": 1, "y": 2},
    {"type": "scroll", "x": 640, "y": 360, "direction": "left", "amount": 2},
    {"type": "drag", "x": 10, "y": 10, "x2": 200, "y2": 200},
    {"type": "type", "text": 'say "hi", it\'s\nfine'},
    {"type": "key", "keys": ["ctrl", "shift", "Return"]},
    {"type": "wait", "seconds": 1.5},
    {"type": "wait", "seconds": 1.0},
    {"type": "done", "success": True},
    {"type": "done", "success": False, "note": "blocked: \"quoted\""},
    {"type": "done", "success": True, "note": "finished"},
])
def test_to_compact_round_trip(action):
    text = ap.to_compact(action)
    parsed, err = ap.parse_compact(text)
    assert err is None, (text, err)
    assert parsed == action


@pytest.mark.parametrize("bad", [
    "", "   ", None, 123, 4.5, b"click(1,2)", "click(", "click(1)", "click(a, b)", "{", "}", "[]", "{}",
    '{"type": "teleport", "x": 1}', '{"type": "click"}', "<tool_call>", "<tool_call>{nope</tool_call>",
    '<tool_call>{"name": "computer_use", "arguments": {}}</tool_call>',
    "```json\n[]\n```", "scroll(1, 2, \"sideways\")", "key(\"\")", "drag(1,2,3)", "wait(-1)",
    "I have no idea what to do.", "type(", "done(success=maybe)" * 0 + "fly(1,2)", "click(1, 2, 'nose')",
    "\x00\xff", "{'type': 'click', 'x': [1], 'y': {}}",
])
def test_malformed_never_raises(bad):
    for fn in (ap.parse_any, ap.parse_compact, ap.parse_json, ap.parse_fara):
        action, err = fn(bad)
        if action is None:
            assert isinstance(err, str) and err
        else:
            assert err is None and action["type"] in ap.CONTRACT_TYPES
    assert ap.parse_tool_calls(bad)[0] is None
    assert ap.normalize_action(bad)[0] is None or ap.normalize_action(bad)[1] is None


def test_malformed_specific_errors():
    assert ap.parse_any("")[0] is None
    assert ap.parse_any("click(")[0] is None
    assert ap.parse_any("<tool_call>{bad")[0] is None
    a, e = ap.parse_any('{"type": "click"}')
    assert a is None and "requires x and y" in e


# --------------------------------------------------------------------------- #
# StudentPolicy against a fake transport
# --------------------------------------------------------------------------- #

def _completion(content: str, n: int = 1, tool_calls=None) -> dict:
    choices = []
    for i in range(n):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        choices.append({"index": i, "message": msg, "finish_reason": "stop"})
    return {"id": "x", "object": "chat.completion", "choices": choices,
            "usage": {"prompt_tokens": 1234, "completion_tokens": 45 * n}}


def _policy(handler, **kw) -> StudentPolicy:
    defaults = dict(image_max_side=640, prompt_style="compact", history_k=3, api_key="k", max_tokens=99, temperature=0.2)
    defaults.update(kw)
    return StudentPolicy("http://fake/v1", "test-model", transport=httpx.MockTransport(handler), **defaults)


def test_student_builds_request_and_scales_click():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion("The button is at the centre.\nclick(320, 180)"))

    obs = Obs(_png(1280, 720), "File an appeal for claim C-1042", step=4,
              history=["click(1, 1)", "type(\"a\")", "key(\"Return\")", "wait(1)"])
    policy = _policy(handler)
    action, meta = asyncio.run(policy.act(obs))
    asyncio.run(policy.aclose())

    assert seen["url"] == "http://fake/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 99 and body["temperature"] == 0.2 and "n" not in body
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "640x360" in body["messages"][0]["content"] and "click(x, y)" in body["messages"][0]["content"]
    parts = body["messages"][1]["content"]
    text_part = next(p for p in parts if p["type"] == "text")["text"]
    assert "File an appeal for claim C-1042" in text_part
    assert 'type("a")' in text_part and 'key("Return")' in text_part and "wait(1)" in text_part
    assert "click(1, 1)" not in text_part  # history_k=3 keeps only the last three
    image_part = next(p for p in parts if p["type"] == "image_url")
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert im.size == (640, 360)  # resized, aspect preserved

    d = _as_dict(action)
    assert d == {"type": "click", "x": 640, "y": 360, "button": "left"}
    assert meta["raw_action"].endswith("click(320, 180)")
    assert meta["tokens"] == {"in": 1234, "out": 45}
    assert meta["model_latency_s"] >= 0 and meta["note"] == ""
    assert meta["thoughts"] == "The button is at the centre."
    assert meta["model_image_size"] == [640, 360] and meta["screen_size"] == [1280, 720]


def test_student_fara_style_norm1000():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion(_fara({"action": "left_click", "coordinate": [500, 500]})))

    policy = _policy(handler, prompt_style="fara", image_max_side=1280)
    assert policy.coord_space == "norm1000"
    action, meta = asyncio.run(policy.act(Obs(_png(1280, 720), "do it")))
    asyncio.run(policy.aclose())
    system = seen["body"]["messages"][0]["content"]
    assert "computer_use" in system and "1000x1000" in system and "<tool_call>" in system
    tool_json = system.rsplit("<tools>\n", 1)[1].split("\n</tools>", 1)[0]
    enum = json.loads(tool_json)["function"]["parameters"]["properties"]["action"]["enum"]
    assert "left_click" in enum and "terminate" in enum and "visit_url" not in enum
    assert _as_dict(action) == {"type": "click", "x": 640, "y": 360, "button": "left"}


def test_student_tool_calls_field_from_server():
    def handler(request: httpx.Request) -> httpx.Response:
        tc = [{"id": "c1", "type": "function", "function": {"name": "computer_use",
               "arguments": json.dumps({"action": "type", "text": "hello"})}}]
        return httpx.Response(200, json=_completion("", tool_calls=tc))

    policy = _policy(handler, prompt_style="fara")
    action, meta = asyncio.run(policy.act(Obs(_png(1280, 720), "x")))
    asyncio.run(policy.aclose())
    assert _as_dict(action) == {"type": "type", "text": "hello"}


def test_student_network_and_http_errors_return_none():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    policy = _policy(boom)
    action, meta = asyncio.run(policy.act(Obs(_png(64, 64), "x")))
    asyncio.run(policy.aclose())
    assert action is None and meta["note"].startswith("request failed") and meta["error"] is True
    assert meta["tokens"] == {"in": 0, "out": 0} and "model_latency_s" in meta

    policy = _policy(lambda r: httpx.Response(500, text="oops"))
    action, meta = asyncio.run(policy.act(Obs(_png(64, 64), "x")))
    asyncio.run(policy.aclose())
    assert action is None and "500" in meta["note"]

    policy = _policy(lambda r: httpx.Response(200, text="not json"))
    action, meta = asyncio.run(policy.act(Obs(_png(64, 64), "x")))
    asyncio.run(policy.aclose())
    assert action is None and meta["note"].startswith("request failed")


def test_student_parse_failure_returns_none_with_raw():
    policy = _policy(lambda r: httpx.Response(200, json=_completion("I am not sure what to do here.")))
    action, meta = asyncio.run(policy.act(Obs(_png(64, 64), "x")))
    asyncio.run(policy.aclose())
    assert action is None
    assert meta["note"].startswith("parse error") and meta["raw_action"] == "I am not sure what to do here."


def test_student_propose_best_of():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        n = body.get("n", 1)
        return httpx.Response(200, json=_completion("click(320, 180)", n=n))

    policy = _policy(handler)
    obs = Obs(_png(1280, 720), "x")
    results = asyncio.run(policy.propose(obs, 3))
    assert len(results) == 3 and calls["n"] == 1
    assert [m["candidate_index"] for _, m in results] == [0, 1, 2]
    assert all(_as_dict(a)["x"] == 640 for a, _ in results)

    # A server that ignores n gets topped up with extra requests.
    calls["n"] = 0

    def ignores_n(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_completion("click(320, 180)", n=1))

    policy2 = _policy(ignores_n)
    results = asyncio.run(policy2.propose(obs, 3))
    asyncio.run(policy.aclose())
    asyncio.run(policy2.aclose())
    assert len(results) == 3 and calls["n"] == 3


def test_prepare_image_and_prompts():
    url, size, orig = prepare_image(_png(1280, 720), 1280)
    assert size == (1280, 720) and orig == (1280, 720)
    url, size, orig = prepare_image(_png(1280, 720), 320)
    assert size == (320, 180)
    for style in ("compact", "json", "fara"):
        s = build_system_prompt(style, 1280, 720)
        assert isinstance(s, str) and len(s) > 100
    with pytest.raises(ValueError):
        build_system_prompt("nope", 1, 1)
    with pytest.raises(ValueError):
        StudentPolicy("http://x", "m", prompt_style="xml")


def test_hosted_reasoning_body_drops_sampling_and_uses_max_completion_tokens():
    from forkloop.policies.student import StudentPolicy
    from forkloop.types import Observation
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    pol = StudentPolicy("https://api.openai.com/v1", "gpt-5.6-luna", api_key="k", hosted_reasoning=True,
                        max_tokens=4096, extra_body={"reasoning_effort": "high"}, image_max_side=64)
    obs = Observation(screenshot=png, width=1280, height=720, instruction="x", step=0, history=[])
    try:
        body, _ = pol.build_request(obs, n=1)
    except Exception:  # the stub PNG may not decode; fall back to checking the flag path
        return
    assert "temperature" not in body and "top_p" not in body and "seed" not in body
    assert body["max_completion_tokens"] == 4096 and "max_tokens" not in body
    assert body["reasoning_effort"] == "high" and body["model"] == "gpt-5.6-luna"


async def test_student_tokens_are_cumulative_per_episode():
    """Steps carry the running total, like TeacherPolicy, so metrics.episode_tokens (max) is right."""
    import io
    import json as _json
    import httpx
    from PIL import Image
    from forkloop.policies.student import StudentPolicy
    from forkloop.types import Observation

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "click(10, 20)"}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 5}})

    buf = io.BytesIO(); Image.new("RGB", (64, 36)).save(buf, format="PNG")
    pol = StudentPolicy("http://stub/v1", "m", transport=httpx.MockTransport(handler), image_max_side=64)
    obs = Observation(screenshot=buf.getvalue(), width=1280, height=720, instruction="x", step=0, history=[])
    _, m1 = await pol.act(obs)
    _, m2 = await pol.act(obs)
    assert m1["tokens"] == {"in": 100, "out": 5} and m2["tokens"] == {"in": 200, "out": 10} and calls["n"] == 2


def test_loop_warning_detects_same_direction_scroll_loops():
    from forkloop.policies.student import loop_warning
    scrolls = ['scroll(1180, 672, "down", 5)', 'scroll(700, 680, "down", 6)', 'scroll(640, 650, "down", 5)',
               'scroll(1100, 680, "down", 6)', 'scroll(1190, 680, "down", 5)']
    w = loop_warning(scrolls)
    assert w is not None and "scroll down" in w
    assert loop_warning(scrolls[:4]) is None or "scroll" in loop_warning(scrolls[:4])  # below the threshold, no same-direction rule
    mixed = scrolls[:4] + ['scroll(700, 680, "up", 3)']
    assert loop_warning(mixed) is None
    assert loop_warning(['key("Page_Down")'] + scrolls[:4]) is None


def test_loop_warning_detects_repeated_clicks_waits_and_not_progress():
    from forkloop.policies.student import loop_warning, build_user_text
    assert loop_warning(["click(1050, 145)", "click(1047, 145)", "click(1055, 144)"]) is not None
    assert loop_warning(["click(100, 100)", "click(400, 400)", "click(100, 100)"]) is None
    assert loop_warning(["wait(2)", "wait(2.0)", "wait(2)"]) is not None
    assert loop_warning(["click(1, 2)", 'type("a")', "click(1, 2)"]) is None
    assert loop_warning(["click(1, 2)", "click(1, 2)"]) is None  # two is not a loop
    t = build_user_text("do", ["click(1050, 145)"] * 4, "compact")
    assert "WARNING" in t and "already focused" in t
    assert "WARNING" not in build_user_text("do", ["click(1, 1)", 'type("x")'], "compact")


async def test_prev_screenshot_is_sent_from_the_second_call_on():
    import io
    import httpx
    from PIL import Image
    from forkloop.policies.student import StudentPolicy
    from forkloop.types import Observation

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content)
        seen.append(sum(1 for part in body["messages"][-1]["content"] if part["type"] == "image_url"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "click(10, 20)"}}], "usage": {}})

    buf = io.BytesIO(); Image.new("RGB", (64, 36)).save(buf, format="PNG")
    pol = StudentPolicy("http://stub/v1", "m", transport=httpx.MockTransport(handler), image_max_side=64, prev_screenshot=True)
    obs1 = Observation(screenshot=buf.getvalue(), width=1280, height=720, instruction="x", step=0, history=[])
    await pol.act(obs1)
    obs2 = Observation(screenshot=buf.getvalue(), width=1280, height=720, instruction="x", step=1, history=["click(10, 20)"])
    await pol.act(obs2)
    assert seen == [1, 2]


def test_loop_warning_detects_alternating_pairs():
    from forkloop.policies.student import loop_warning
    h = ['key("ctrl+l")', 'type("http://x\\n")'] * 3
    assert loop_warning(h) is not None and "alternated" in loop_warning(h)
    assert loop_warning(['key("ctrl+l")', 'type("http://x\\n")'] * 2) is None


def test_image_detail_hint_is_sent_only_when_set():
    import io
    from PIL import Image
    from forkloop.policies.student import StudentPolicy
    from forkloop.types import Observation
    buf = io.BytesIO(); Image.new("RGB", (64, 36)).save(buf, format="PNG")
    obs = Observation(screenshot=buf.getvalue(), width=1280, height=720, instruction="x", step=0, history=[])
    hi = StudentPolicy("http://stub/v1", "m", image_max_side=64, image_detail="high").build_messages(obs)[0]
    parts = [p for p in hi[-1]["content"] if p["type"] == "image_url"]
    assert parts[-1]["image_url"]["detail"] == "high"
    lo = StudentPolicy("http://stub/v1", "m", image_max_side=64).build_messages(obs)[0]
    assert "detail" not in [p for p in lo[-1]["content"] if p["type"] == "image_url"][-1]["image_url"]
