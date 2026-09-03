"""Teacher policy: a frontier computer-use model through the Anthropic API.

Uses the GA ``computer_toolset_20260801`` (no beta header). The model may emit
several actions per turn; the policy queues them and hands the env one action
per ``act()`` call, answering ``screenshot``/``zoom``/``cursor_position``
members locally from the latest observation so they never consume an env
step. Text the model writes before acting is scanned for ``confidence: 0.NN``
which the search module uses to decide where to branch.

Only screenshots and the instruction cross the agent channel; the teacher
never sees the manifest.
"""

from __future__ import annotations

import base64
import io
import os
import re
import time
from typing import Any, Optional

from ..actions import Action, InvalidAction
from ..observe import resize_png
from ..types import Observation
from .base import PolicyResult

DEFAULT_MODEL = "claude-opus-5"
NOT_EXECUTED = "Not executed: an earlier computer action in this turn failed."
LOCAL_MEMBERS = ("screenshot", "zoom", "cursor_position")  # answered from the latest observation, never an env step

SYSTEM_PROMPT = """You are operating a Linux desktop (Chrome browser, screen {w}x{h}) to complete an administrative task in two web applications: a payer portal and OpenEMR. You see the screen only through screenshots.

Rules:
- Work only through the GUI. Do not open a terminal or any developer tools.
- Change exactly what the task asks for. Never edit other patients, claims, or appointments. Never submit the same form twice.
- Before each batch of actions, write one short line `confidence: <0.0-1.0>` stating how sure you are that the next action is right, then optionally one sentence of reasoning.
- When the task is complete, reply with the single word DONE and nothing else. If the task cannot be completed, reply FAILED: <reason>.
- Prefer clicking visible controls and typing into focused fields. After navigating, take a screenshot before acting on the new page.
"""

CONF_RE = re.compile(r"confidence\s*[:=]\s*([01](?:\.\d+)?|\.\d+)", re.I)


def anthropic_default_headers() -> dict[str, str]:
    """Extra headers for the Anthropic client.

    Identity-linked API keys (the kind issued to a person rather than a
    workspace) are refused with 400 ``anthropic-workspace-id is required``
    unless every request names the workspace it acts in. Set
    ``ANTHROPIC_WORKSPACE_ID`` (``wrkspc_...``, from the Console's Workspaces
    page) and the header is sent; ordinary keys need nothing.
    """
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    return {"anthropic-workspace-id": ws} if ws else {}


def _b64png(png: bytes) -> str:
    return base64.standard_b64encode(png).decode("ascii")


class TeacherPolicy:
    name = "teacher"

    def __init__(self, *, model: str = DEFAULT_MODEL, max_tokens: int = 4096, effort: str = "high",
                 image_max_side: int = 1280, keep_images: int = 8, fallbacks: bool = True,
                 max_turns: int = 80, client: Any = None, thinking_display: Optional[str] = None,
                 extra_system: str = "") -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.image_max_side = image_max_side
        self.keep_images = keep_images
        self.fallbacks = fallbacks
        self.max_turns = max_turns
        self.thinking_display = thinking_display
        self.extra_system = extra_system
        self._client = client
        self.reset()

    # ------------------------------------------------------------ state
    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []      # pending tool_use blocks
        self.executed: list[dict[str, Any]] = []   # blocks executed since the last model call
        self.turns = 0
        self.instruction: Optional[str] = None
        self.last_confidence: Optional[float] = None
        self.last_text = ""
        self.scale = 1.0
        self.usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # type: ignore

            self._client = anthropic.AsyncAnthropic(default_headers=anthropic_default_headers())
        return self._client

    # -------------------------------------------------------------- api
    def _tools(self) -> list[dict[str, Any]]:
        return [{"type": "computer_toolset_20260801"}]

    def _system(self, obs: Observation) -> list[dict[str, Any]]:
        text = SYSTEM_PROMPT.format(w=obs.width, h=obs.height)
        if self.extra_system:
            text += "\n" + self.extra_system
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def _image_block(self, png: bytes) -> dict[str, Any]:
        small, scale = resize_png(png, self.image_max_side)
        self.scale = scale
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64png(small)}}

    def _prune_images(self) -> None:
        """Keep only the last ``keep_images`` screenshots in history (docs recommend ≤ 20)."""
        seen = 0
        for msg in reversed(self.messages):
            if msg["role"] != "user" or not isinstance(msg["content"], list):
                continue
            for block in msg["content"]:
                if block.get("type") == "image":
                    seen += 1
                    if seen > self.keep_images:
                        block.clear()
                        block.update({"type": "text", "text": "(earlier screenshot omitted)"})
                    continue
                if block.get("type") != "tool_result" or not isinstance(block.get("content"), list):
                    continue
                for j, item in enumerate(block["content"]):
                    if item.get("type") == "image":
                        seen += 1
                        if seen > self.keep_images:
                            block["content"][j] = {"type": "text", "text": "(earlier screenshot omitted)"}

    async def _call_model(self, obs: Observation) -> Any:
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=self.max_tokens, system=self._system(obs), messages=self.messages,
            tools=self._tools(), output_config={"effort": self.effort},
        )
        if self.thinking_display:
            kwargs["thinking"] = {"type": "adaptive", "display": self.thinking_display}
        if self.fallbacks:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
            resp = await self.client.beta.messages.create(**kwargs)
        else:
            resp = await self.client.messages.create(**kwargs)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.usage["in"] += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage["out"] += int(getattr(usage, "output_tokens", 0) or 0)
            self.usage["cache_read"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            self.usage["cache_write"] += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return resp

    # ------------------------------------------------------------- loop
    async def act(self, obs: Observation) -> PolicyResult:
        t0 = time.monotonic()
        if self.instruction is None:
            self.instruction = obs.instruction
            self.messages.append({"role": "user", "content": [
                {"type": "text", "text": f"Task: {obs.instruction}\n\nHere is the current screen."},
                self._image_block(obs.screenshot),
            ]})
        while not self.queue:
            if self.turns >= self.max_turns:
                a = Action.done(False, "teacher turn limit")
                return a, self._meta(a, t0, error="max_turns")
            if self.executed:
                self.messages.append({"role": "user", "content": self._results_for_executed(obs)})
                self.executed = []
            self._prune_images()
            try:
                resp = await self._call_model(obs)
            except Exception as e:  # noqa: BLE001
                return None, {"raw_action": "", "error": f"{type(e).__name__}: {e}", "model_latency_s": time.monotonic() - t0}
            self.turns += 1
            content = list(getattr(resp, "content", []) or [])
            self.messages.append({"role": "assistant", "content": [self._block_to_param(b) for b in content]})
            text = " ".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text").strip()
            self.last_text = text
            m = CONF_RE.search(text)
            self.last_confidence = float(m.group(1)) if m else None
            stop = getattr(resp, "stop_reason", None)
            tool_blocks = [b for b in content if getattr(b, "type", "") == "tool_use"]
            if stop == "refusal" or (not tool_blocks and stop != "tool_use"):
                ok = text.strip().upper().startswith("DONE")
                a = Action.done(ok, text[:200] if not ok else None)
                return a, self._meta(a, t0, note=text[:300])
            for b in tool_blocks:
                self.queue.append({"id": b.id, "name": b.name, "input": dict(b.input or {}),
                                   "toolset_name": getattr(b, "toolset_name", "computer")})
            self._drain_local(obs)
        blk = self.queue.pop(0)
        if blk["name"] in LOCAL_MEMBERS:
            # A screenshot/zoom/cursor_position queued *behind* real actions: answer it from
            # the observation that follows the preceding action instead of failing the step.
            self.executed.append({**blk, "_local": True})
            return await self.act(obs)
        try:
            action = self._to_action(blk, obs)
        except InvalidAction as e:
            self.executed.append({**blk, "error": str(e)})
            for rest in self.queue:
                self.executed.append({**rest, "error": NOT_EXECUTED})
            self.queue = []
            return None, {"raw_action": f"{blk['name']}({blk['input']})", "error": str(e), "model_latency_s": time.monotonic() - t0}
        self.executed.append(blk)
        return action, self._meta(action, t0)

    def _meta(self, action: Action, t0: float, **extra: Any) -> dict[str, Any]:
        d = {"raw_action": action.to_compact(), "model_latency_s": time.monotonic() - t0,
             "tokens": dict(self.usage), "confidence": self.last_confidence, "note": self.last_text[:200]}
        d.update(extra)
        return d

    def _drain_local(self, obs: Observation) -> None:
        """Answer screenshot/zoom/cursor_position members without touching the env."""
        while self.queue and self.queue[0]["name"] in LOCAL_MEMBERS:
            blk = self.queue.pop(0)
            self.executed.append({**blk, "_local": True})

    def _results_for_executed(self, obs: Observation) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for blk in self.executed:
            res: dict[str, Any] = {"type": "tool_result", "tool_use_id": blk["id"], "toolset_name": blk.get("toolset_name", "computer")}
            if blk.get("error"):
                res["content"] = blk["error"]
                res["is_error"] = True
            elif blk["name"] == "screenshot":
                res["content"] = [self._image_block(obs.screenshot)]
            elif blk["name"] == "zoom":
                res["content"] = [self._zoom_block(obs, blk["input"].get("region"))]
            elif blk["name"] == "cursor_position":
                res["content"] = [{"type": "text", "text": "unknown"}]
            else:
                res["content"] = [{"type": "text", "text": "OK"}]
            out.append(res)
        if any(not b.get("_local") and not b.get("error") for b in self.executed):
            out.append({"type": "text", "text": "Screen after your actions:"})
            out.append(self._image_block(obs.screenshot))
        return out

    def _zoom_block(self, obs: Observation, region: Any) -> dict[str, Any]:
        from PIL import Image

        try:
            x0, y0, x1, y1 = [int(v / self.scale) for v in region]
            with Image.open(io.BytesIO(obs.screenshot)) as im:
                crop = im.crop((max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)))
                buf = io.BytesIO()
                crop.save(buf, format="PNG")
            return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64png(buf.getvalue())}}
        except Exception:  # noqa: BLE001
            return {"type": "text", "text": "zoom failed: bad region"}

    def _to_action(self, blk: dict[str, Any], obs: Observation) -> Action:
        name, inp = blk["name"], blk["input"]

        def coord(key: str = "coordinate") -> tuple[int, int]:
            c = inp.get(key)
            if not (isinstance(c, (list, tuple)) and len(c) == 2):
                raise InvalidAction(f"{name} needs {key} [x, y]")
            return int(round(c[0] / self.scale)), int(round(c[1] / self.scale))

        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            if "coordinate" not in inp:
                raise InvalidAction(f"{name} without coordinate is not supported")
            x, y = coord()
            if name == "left_click":
                a = Action.click(x, y)
            elif name == "right_click":
                a = Action.right_click(x, y)
            elif name == "middle_click":
                a = Action.click(x, y, "middle")
            else:
                a = Action.double_click(x, y)  # triple_click approximated by double_click
        elif name == "mouse_move":
            a = Action.move(*coord())
        elif name == "left_click_drag":
            x1, y1 = coord("start_coordinate")
            x2, y2 = coord("coordinate")
            a = Action.drag(x1, y1, x2, y2)
        elif name == "scroll":
            x, y = coord() if "coordinate" in inp else (obs.width // 2, obs.height // 2)
            a = Action.scroll(x, y, str(inp.get("scroll_direction", "down")), int(inp.get("scroll_amount", 3) or 3))
        elif name == "type":
            a = Action.type_text(str(inp.get("text", "")))
        elif name in ("key", "hold_key"):
            a = Action.key(str(inp.get("text", "")))
        elif name == "wait":
            a = Action.wait(min(float(inp.get("duration", 1) or 1), 30.0))
        elif name in ("left_mouse_down", "left_mouse_up"):
            raise InvalidAction(f"{name} is not supported by the action schema; use left_click_drag")
        else:
            raise InvalidAction(f"unknown computer member {name!r}")
        a.validate(width=obs.width, height=obs.height)
        return a

    @staticmethod
    def _block_to_param(b: Any) -> dict[str, Any]:
        if hasattr(b, "model_dump"):
            return b.model_dump(exclude_none=True)
        if isinstance(b, dict):
            return dict(b)
        return {"type": getattr(b, "type", "text"), "text": getattr(b, "text", "")}


__all__ = ["TeacherPolicy", "DEFAULT_MODEL", "SYSTEM_PROMPT", "anthropic_default_headers"]
