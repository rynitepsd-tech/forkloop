"""StudentPolicy: a small vision-language model behind any OpenAI-compatible chat endpoint.

Works with vLLM serving ``microsoft/Fara1.5-4B``, ``Qwen/Qwen3.5-VL-4B`` (or any
Qwen-VL), UI-TARS, or a LoRA checkpoint produced by ``train/train_lora.py``.
Only ``httpx`` and ``Pillow`` are required; the ``openai`` package is not used.

Prompt styles (``prompt_style``):

* ``"compact"`` — the contract's compact grammar (``click(640, 360)``). Coordinates
  are pixels of the (possibly resized) image the model sees.
* ``"json"`` — the contract's JSON dict. Same coordinate space as compact.
* ``"fara"`` — Fara 1.5's trained system prompt (identity + critical points +
  ``computer_use`` tool schema, as in ``src/fara/agents/fara/_prompts.py`` of
  https://github.com/microsoft/fara). Fara emits coordinates in a fixed
  1000x1000 display space, so ``coord_space`` defaults to ``"norm1000"``.

The policy resizes the screenshot so its longest side is ``image_max_side``
(default 1280, so a 1280x720 world screenshot is sent untouched), remembers the
scale, parses the reply with :mod:`forkloop.policies.action_parse`, rescales the
coordinates into screen space (contracts.md §3) and returns
``(Action | None, meta)``. ``meta`` always carries ``raw_action``,
``model_latency_s``, ``tokens`` and ``note`` (the recorder's ``policy_note``).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
from typing import TYPE_CHECKING, Any

import httpx
from PIL import Image

from . import action_parse as ap

if TYPE_CHECKING:  # pragma: no cover - typing only; forkloop.policies.base is written elsewhere
    from .base import Observation

PROMPT_STYLES: tuple[str, ...] = ("compact", "json", "fara")
COORD_SPACES: tuple[str, ...] = ("auto", "image", "screen", "norm1000", "norm999")

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

COMPACT_SYSTEM_TEMPLATE = """\
You are a GUI agent operating a desktop computer through screenshots. Each turn you receive the task, the actions you already took, and a screenshot that is {width}x{height} pixels. Coordinates are pixel positions in that screenshot: x from 0 (left edge) to {xmax}, y from 0 (top edge) to {ymax}. Aim for the centre of the element you want to hit.

Reply with exactly ONE action as the last line of your answer, using this grammar and nothing else:
  click(x, y)                 left click
  double_click(x, y)          double click
  right_click(x, y)           right click
  move(x, y)                  move the mouse without clicking
  drag(x1, y1, x2, y2)        press at (x1, y1), release at (x2, y2)
  scroll(x, y, "down", 3)     scroll at (x, y); direction up|down|left|right; amount = wheel notches
  type("text")                type text into the focused field (JSON string escaping)
  key("ctrl+l")               press a key or chord; names: Return, Tab, Escape, BackSpace, Delete, Page_Down, Page_Up, Home, End, Up, Down, Left, Right, ctrl, alt, shift, super, letters and digits
  wait(1.0)                   wait for the screen to settle
  done()                      the task is complete
  done(success=false, note="reason")   the task cannot be completed

You may write one short line of reasoning before the action. Never output more than one action, and never output anything after it."""

JSON_SYSTEM_TEMPLATE = """\
You are a GUI agent operating a desktop computer through screenshots. Each turn you receive the task, the actions you already took, and a screenshot that is {width}x{height} pixels. Coordinates are pixel positions in that screenshot: x from 0 (left edge) to {xmax}, y from 0 (top edge) to {ymax}. Aim for the centre of the element you want to hit.

Reply with exactly ONE action as a JSON object inside a ```json fence, using one of these schemas:
  {{"type": "click", "x": 640, "y": 360, "button": "left"}}
  {{"type": "double_click", "x": 640, "y": 360}}
  {{"type": "right_click", "x": 640, "y": 360}}
  {{"type": "move", "x": 640, "y": 360}}
  {{"type": "scroll", "x": 640, "y": 360, "direction": "down", "amount": 3}}
  {{"type": "drag", "x": 10, "y": 10, "x2": 200, "y2": 200}}
  {{"type": "type", "text": "hello"}}
  {{"type": "key", "keys": ["ctrl", "l"]}}
  {{"type": "wait", "seconds": 1.0}}
  {{"type": "done", "success": true, "note": "optional"}}
Key names: Return, Tab, Escape, BackSpace, Delete, Page_Down, Page_Up, Home, End, Up, Down, Left, Right, ctrl, alt, shift, super, letters and digits.

You may write one short line of reasoning before the fence. Never output more than one action."""

FARA_IDENTITY = """\
You are Fara, a computer use agent (CUA) specialized for web browsers. You are developed by Microsoft AI Frontiers. You assist users with completing and automating tasks that require the use of a web browser.

The model was trained in the timeframe of January - April 2026. You can effectively perform tasks even beyond this range by accessing the web browser and using the latest information on the live web. But your knowledge cutoff is limited to early 2026, so you may not be aware of events or developments that occurred after that time, without explicitly browsing and searching for latest information on the web.

This edition of the model was trained using SFT on top of Qwen3.5-4B, using a synthetic data mixture generated and developed by Microsoft AI Frontiers."""

FARA_CRITICAL_POINTS = """\
A critical point is a situation where we must pause and request information or confirmation from the user before proceeding. There are three types:

Case 1: Missing User Information — The task requires personal information that the user has not provided (e.g., email, phone number, address, payment details). Never fabricate or assume personal information. Fill in only what the user has explicitly provided, then pause and ask for any missing required fields. (e.g., form requires phone number but user only gave name and email -> fill name and email, then ask for phone number.) If the user has provided all required information, proceed without stopping.

Case 2: Underspecified Task — The task description is ambiguous or missing details needed to make a decision at the current step. Pause and ask for clarification. (e.g., user asks to book a flight but doesn't specify destination -> ask for destination.) If the user's instructions contain all information needed for the current decision, proceed without stopping.

Case 3: Irreversible Action — We are about to perform an action that cannot be undone (e.g., submitting a form, completing a purchase, sending a message, deleting data). If the user explicitly authorized the action (e.g., "submit the form", "complete the purchase", "you have my permission to submit") -> proceed without stopping. If the user did NOT explicitly authorize the action -> stop and ask for confirmation. (e.g., "fill out a form" with no mention of submitting -> fill the form, then ask before submitting; "fill out and submit a form" -> fill and submit without stopping.)

Only stop at a critical point if (1) required information is missing, (2) the task is ambiguous, OR (3) an irreversible action lacks explicit user authorization. If the user has provided all necessary information AND explicitly authorized the action, proceed without interruption."""

FARA_FN_CALL_FORMAT = (
    "You are provided with function signatures within <tools></tools> XML tags:\n"
    "<tools>\n"
    "{tool_descs}\n"
    "</tools>\n"
    "\n"
    "For each function call, return a json object with function name and arguments "
    "within <tool_call></tool_call> XML tags:\n"
    "<tool_call>\n"
    '{"name": <function-name>, "arguments": <args-json-object>}\n'
    "</tool_call>"
)

#: Fara actions the contract can execute. Fara's harness restricts the enum in the
#: prompt the same way (it re-reads the allowed set from the schema text).
FARA_DEFAULT_ALLOWED: tuple[str, ...] = (
    "key", "type", "mouse_move", "left_click", "left_click_drag", "right_click",
    "double_click", "triple_click", "scroll", "hscroll", "wait", "terminate",
)

_FARA_ACTION_DOCS: dict[str, str] = {
    "key": '`key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order. Includes "Enter", "Alt", "Shift", "Tab", "Control", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Shift", etc.',
    "type": "`type`: Type a string of text on the keyboard.",
    "mouse_move": "`mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.",
    "left_click": "`left_click`: Click the left mouse button.",
    "double_click": "`double_click`: Double-click the left mouse button.",
    "right_click": "`right_click`: Click the right mouse button.",
    "triple_click": "`triple_click`: Triple-click the left mouse button (e.g. to select a line of text).",
    "left_click_drag": "`left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.",
    "scroll": "`scroll`: Performs a scroll of the mouse scroll wheel.",
    "hscroll": "`hscroll`: Performs a horizontal scroll (mapped to regular scroll).",
    "visit_url": "`visit_url`: Visit a specified URL.",
    "history_back": "`history_back`: Go back to the previous page in the browser history.",
    "web_search": "`web_search`: Perform a web search with a specified query.",
    "read_page_answer_question": "`read_page_answer_question`: Read the current page content and answer a question about it.",
    "pause_and_memorize_fact": "`pause_and_memorize_fact`: Pause and memorize a fact for future reference.",
    "ask_user_question": "`ask_user_question`: Ask the user a clarifying question and wait for a response.",
    "wait": "`wait`: Wait specified seconds for the change to happen.",
    "terminate": "`terminate`: Terminate the current task and provide the final answer.",
}


def fara_computer_use_tool(display_w: int, display_h: int, allowed: tuple[str, ...] = FARA_DEFAULT_ALLOWED) -> dict:
    """The ``computer_use`` function schema Fara 1.5 was trained with (browser action space)."""
    description = (
        "Use a mouse and keyboard to interact with a computer, and take screenshots.\n"
        "* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu. "
        "You must click on desktop icons to start applications.\n"
        "* Some applications may take time to start or process actions, so you may need to wait and take "
        "successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window "
        "doesn't open, try wait and taking another screenshot.\n"
        f"* The screen's resolution is {display_w}x{display_h}.\n"
        "* Whenever you intend to move the cursor to click on an element like an icon, you should consult a "
        "screenshot to determine the coordinates of the element before moving the cursor.\n"
        "* If you tried clicking on a program or link but it failed to load, even after waiting, try adjusting "
        "your cursor position so that the tip of the cursor visually falls on the element that you want to click.\n"
        "* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. "
        "Don't click boxes on their edges."
    )
    action_desc = "The action to perform. The available actions are:\n" + "\n".join(
        "* " + _FARA_ACTION_DOCS[a] for a in allowed if a in _FARA_ACTION_DOCS
    )
    return {
        "type": "function",
        "function": {
            "name": ap.FARA_TOOL_NAME,
            "description": description,
            "parameters": {
                "properties": {
                    "action": {"description": action_desc, "enum": list(allowed), "type": "string"},
                    "keys": {"description": "Required only by `action=key`.", "type": "array"},
                    "text": {"description": "Required only by `action=type`.", "type": "string"},
                    "coordinate": {
                        "description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) "
                                       "coordinates to move the mouse to. Required by `action=left_click`, "
                                       "`action=double_click`, `action=right_click`, `action=triple_click`, "
                                       "`action=left_click_drag`, and `action=mouse_move`.",
                        "type": "array",
                    },
                    "pixels": {
                        "description": "The amount of scrolling to perform. Positive values scroll up, negative "
                                       "values scroll down. Required only by `action=scroll` and `action=hscroll`.",
                        "type": "number",
                    },
                    "url": {"description": "The URL to visit. Required only by `action=visit_url`.", "type": "string"},
                    "query": {"description": "The query to search for. Required only by `action=web_search`.", "type": "string"},
                    "fact": {"description": "The fact to remember for the future. Required only by `action=pause_and_memorize_fact`.", "type": "string"},
                    "question": {"description": "The question to ask. Required by `action=read_page_answer_question` and `action=ask_user_question`.", "type": "string"},
                    "time": {"description": "The seconds to wait. Required only by `action=wait`.", "type": "number"},
                    "answer": {"description": "The final answer for the task. Required only by `action=terminate`.", "type": "string"},
                },
                "required": ["action"],
                "type": "object",
            },
        },
    }


def build_system_prompt(style: str, width: int, height: int, *, fara_allowed: tuple[str, ...] = FARA_DEFAULT_ALLOWED) -> str:
    """System prompt for a prompt style. ``width``/``height`` is the coordinate space the model uses."""
    if style == "compact":
        return COMPACT_SYSTEM_TEMPLATE.format(width=width, height=height, xmax=width - 1, ymax=height - 1)
    if style == "json":
        return JSON_SYSTEM_TEMPLATE.format(width=width, height=height, xmax=width - 1, ymax=height - 1)
    if style == "fara":
        tool = fara_computer_use_tool(width, height, fara_allowed)
        return (FARA_IDENTITY + "\n\n" + FARA_CRITICAL_POINTS + "\n\n"
                + FARA_FN_CALL_FORMAT.replace("{tool_descs}", json.dumps(tool, ensure_ascii=False)))
    raise ValueError(f"unknown prompt_style {style!r}; expected one of {PROMPT_STYLES}")


_ACT_RE = re.compile(r"^\s*(\w+)\s*\((.*)\)\s*$", re.S)


def loop_warning(history: list[str] | None, *, min_repeats: int = 3, px: int = 20) -> str | None:
    """Detect a stuck policy from its own history: the last ``min_repeats`` actions are the
    same kind and (for pointer actions) land within ``px`` of each other, or are all waits.
    Returns a warning line for the prompt, or None. Model-agnostic; measured need: GPT-5.6 Luna
    clicked an already-focused search box 40 times in a row (2026-09-03)."""
    hist = [h.strip() for h in (history or []) if isinstance(h, str) and h.strip()]
    if len(hist) < min_repeats:
        return None
    # An alternating two-action cycle (key("ctrl+l"), type(url), key("ctrl+l"), type(url), ...)
    # is a loop too: measured 2026-09-03 when the address bar never took the typed URL.
    if len(hist) >= 2 * min_repeats:
        tail2 = hist[-2 * min_repeats:]
        a, b = tail2[-2], tail2[-1]
        if a != b and all(tail2[i] == (a if i % 2 == 0 else b) for i in range(len(tail2))):
            return (f"WARNING: you have alternated {a} and {b} {min_repeats} times and the screen is not changing. "
                    "That approach is not working. Look at the screenshot: check where keyboard focus is (a document "
                    "viewer or an iframe may be swallowing keys), click the element you need first, or use a different "
                    "route (a menu, a link, another tab, or key(\"F5\")).")
    # Scrolling the same direction over and over with varying coordinates is a loop too: measured
    # 2026-09-04 (runs/luna-v8-fam1-s0-9), 15-50 consecutive scroll(...,"down") calls on a dashboard
    # whose iframe does not take wheel events at the chosen spot.
    n_scroll = min_repeats + 2
    if len(hist) >= n_scroll:
        dirs = [re.search(r'scroll\([^)]*"(up|down|left|right)"', h) for h in hist[-n_scroll:]]
        if all(dirs) and len({d.group(1) for d in dirs}) == 1:
            d = dirs[-1].group(1)
            return (f"WARNING: your last {n_scroll} actions were all scroll {d} and the screen is not changing: the "
                    "element under the mouse does not scroll. Do NOT scroll again. Click inside the content you want to "
                    "scroll first and use key(\"Page_Down\") or key(\"End\"), drag its scrollbar, or use a different "
                    "route (a menu, a tab, a link, browser find with key(\"ctrl+f\")).")
    tail = hist[-min_repeats:]
    parsed = []
    for h in tail:
        m = _ACT_RE.match(h)
        if not m:
            return None
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m.group(2))[:2]]
        parsed.append((m.group(1), nums))
    kinds = {k for k, _ in parsed}
    if len(kinds) != 1:
        return None
    kind = parsed[0][0]
    if kind in ("click", "double_click", "right_click", "move", "scroll"):
        xs = [n for _, n in parsed if len(n) >= 2]
        if len(xs) < min_repeats or max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in xs for b in xs) > px:
            return None
        return (f"WARNING: your last {min_repeats} actions were the same {kind} at about "
                f"({int(xs[-1][0])}, {int(xs[-1][1])}) and the screen did not change. Do NOT repeat it. "
                "If that element is a text field it is already focused: type into it now. Otherwise choose a "
                "different element, press a key, or navigate by URL.")
    if kind == "wait":
        return (f"WARNING: your last {min_repeats} actions were waits. The page is not going to change on its own. "
                "Act now: click, type, press a key, or navigate by URL.")
    if kind in ("key", "type"):
        return (f"WARNING: your last {min_repeats} actions were the same {kind}. Repeating it will not help; "
                "look at the screen and try a different approach.")
    return None


def note_from_reply(text: str, *, max_chars: int = 160) -> str:
    """The model's own reasoning line from one reply, without the action line: what it gets
    to see next to that action in later turns when ``history_notes`` is on. Measured need
    (2026-09-04, runs/luna-v9-fam1-s0-9): the history is compact actions only, so the
    "CURRENT -> TARGET" line the prompt asks for never reached the next turn and the policy
    re-derived the target from whatever the date field showed (two of five failures)."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if lines and _ACT_RE.match(lines[-1]):
        lines = lines[:-1]
    note = " ".join(" ".join(lines).split())
    if len(note) > max_chars:
        note = note[: max_chars - 1].rstrip() + "…"
    return note


def build_user_text(instruction: str, history: list[str] | None, style: str, step: int | None = None,
                    notes: list[str | None] | None = None) -> str:
    """The text part of the user turn: instruction + last-k history (+ a loop warning when stuck).
    ``notes`` (same length as ``history``) puts the policy's own earlier reasoning next to each action."""
    lines = [f"Task: {instruction.strip()}" if style != "fara" else instruction.strip()]
    pairs = [(h, (notes[i] if notes and i < len(notes) else None))
             for i, h in enumerate(history or []) if isinstance(h, str) and h.strip()]
    hist = [h for h, _ in pairs]
    if hist:
        lines.append("")
        lines.append("Previous actions (oldest first)" + (", each with the note you wrote when you took it:" if notes else ":"))
        for i, (h, n) in enumerate(pairs, 1):
            lines.append(f"{i}. {h.strip()}" + (f" — note: {n.strip()}" if n and n.strip() else ""))
        warn = loop_warning(hist)
        if warn:
            lines.append("")
            lines.append(warn)
    else:
        lines.append("")
        lines.append("Previous actions: none (this is the first step).")
    if step is not None:
        lines.append(f"Step {step}.")
    if style == "fara":
        lines.append("Decide the next action from the screenshot.")
    else:
        lines.append("Look at the screenshot and output the next action.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Image handling
# --------------------------------------------------------------------------- #

def prepare_image(png_bytes: bytes, image_max_side: int) -> tuple[str, tuple[int, int], tuple[int, int]]:
    """Resize preserving aspect so max(w, h) <= image_max_side.

    Returns ``(data_url, model_size, original_size)``.
    """
    im = Image.open(io.BytesIO(png_bytes))
    im.load()
    orig = (im.width, im.height)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    scale = 1.0
    if image_max_side and max(orig) > image_max_side:
        scale = image_max_side / float(max(orig))
    if scale < 1.0:
        new = (max(1, int(round(orig[0] * scale))), max(1, int(round(orig[1] * scale))))
        im = im.resize(new, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return data_url, (im.width, im.height), orig


def _content_text(content: Any) -> str:
    """OpenAI ``message.content`` may be a string or a list of parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") in (None, "text", "output_text") and isinstance(p.get("text"), str):
                    parts.append(p["text"])
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return str(content)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #

class StudentPolicy:
    """Vision-only GUI policy backed by an OpenAI-compatible ``/chat/completions`` endpoint.

    Parameters
    ----------
    base_url:
        e.g. ``http://localhost:8000/v1`` (vLLM). ``/chat/completions`` is appended.
    model:
        model name as registered on the server.
    api_key:
        optional bearer token.
    image_max_side:
        longest side of the image sent to the model (aspect preserved).
    prompt_style:
        ``"compact"`` | ``"json"`` | ``"fara"``.
    history_k:
        how many previous raw actions to include in the user turn.
    coord_space:
        ``"auto"`` (``norm1000`` for fara, ``image`` otherwise), ``"image"`` (pixels of the
        resized image), ``"screen"`` (already screen pixels), ``"norm1000"``, ``"norm999"``.
    seed:
        sampling seed forwarded to the server (``n_seeds`` repeats in eval use different seeds).
    transport:
        optional ``httpx`` transport (tests use ``httpx.MockTransport``).
    screen_size:
        fallback screen size if the observation carries no ``width``/``height``.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        image_max_side: int = 1280,
        temperature: float = 0.0,
        max_tokens: int = 512,
        prompt_style: str = "compact",
        history_k: int = 8,
        coord_space: str = "auto",
        timeout_s: float = 120.0,
        seed: int | None = None,
        top_p: float | None = None,
        extra_body: dict | None = None,
        system_prompt: str | None = None,
        fara_allowed: tuple[str, ...] = FARA_DEFAULT_ALLOWED,
        transport: Any = None,
        screen_size: tuple[int, int] | None = None,
        name: str | None = None,
        hosted_reasoning: bool = False,
        prev_screenshot: bool = False,
        image_detail: str | None = None,
        history_notes: bool = False,
        nav_macro: bool = False,
    ) -> None:
        if prompt_style not in PROMPT_STYLES:
            raise ValueError(f"prompt_style must be one of {PROMPT_STYLES}, got {prompt_style!r}")
        if coord_space not in COORD_SPACES:
            raise ValueError(f"coord_space must be one of {COORD_SPACES}, got {coord_space!r}")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.image_max_side = int(image_max_side)
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.prompt_style = prompt_style
        self.history_k = int(history_k)
        self.coord_space = coord_space if coord_space != "auto" else ("norm1000" if prompt_style == "fara" else "image")
        self.timeout_s = float(timeout_s)
        self.seed = seed
        self.top_p = top_p
        self.extra_body = dict(extra_body or {})
        self.system_prompt_override = system_prompt
        #: Expand Fara's browser-navigation calls into contract actions instead of rejecting
        #: them: ``visit_url`` becomes click(omnibox), key(ctrl+a), type(url), key(Return) over
        #: four env steps (the model is not consulted for the queued three; the next screenshot
        #: it sees is the loaded page), ``history_back`` becomes key(alt+Left). The tool schema
        #: then advertises both. Only meaningful with ``prompt_style="fara"``.
        self.nav_macro = bool(nav_macro)
        self._queue: list[tuple[dict, str]] = []
        allowed = tuple(fara_allowed)
        if self.nav_macro:
            allowed = allowed + tuple(a for a in ("visit_url", "history_back") if a not in allowed)
        self.fara_allowed = allowed
        self.screen_size = tuple(screen_size) if screen_size else None
        #: Hosted reasoning models (OpenAI GPT-5.x via /chat/completions) reject sampling
        #: parameters and count reasoning tokens against ``max_completion_tokens``.
        self.hosted_reasoning = bool(hosted_reasoning)
        #: Also send the screenshot from before the previous action, so the model can see
        #: whether that action changed anything (the loop failure mode of 2026-09-03).
        self.prev_screenshot = bool(prev_screenshot)
        #: Show the policy's own reasoning line next to each previous action: its only memory
        #: across turns (the env's history is compact actions). Keyed by observation step.
        self.history_notes = bool(history_notes)
        self._notes: dict[int, str] = {}
        #: OpenAI-style image fidelity hint ("high"/"low"/"auto"); None omits the field (vLLM).
        #: Hosted models default to "auto", which may downscale a 1280x720 screenshot enough to
        #: misread an authorization code (measured 2026-09-03: "G" read as "6", digits dropped).
        self.image_detail = image_detail
        self._prev_data_url: str | None = None
        self.name = name or f"student:{model}:{prompt_style}"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=httpx.Timeout(self.timeout_s), transport=transport
        )
        self.n_requests = 0
        #: Cumulative usage for the episode, like TeacherPolicy (steps carry the running total).
        self.usage = {"in": 0, "out": 0}
        self.n_errors = 0

    # -- lifecycle ---------------------------------------------------------- #

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "StudentPolicy":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def describe(self) -> dict:
        """Config summary for run.json / eval_summary.json."""
        return {
            "policy": "student", "name": self.name, "base_url": self.base_url, "model": self.model,
            "prompt_style": self.prompt_style, "coord_space": self.coord_space,
            "image_max_side": self.image_max_side, "temperature": self.temperature,
            "max_tokens": self.max_tokens, "history_k": self.history_k, "seed": self.seed,
            "nav_macro": self.nav_macro,
        }

    # -- request construction ---------------------------------------------- #

    def _coord_from_size(self, model_size: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
        if self.coord_space == "image":
            return model_size
        if self.coord_space == "screen":
            return screen
        if self.coord_space == "norm1000":
            return (1000, 1000)
        return (999, 999)

    def _screen_size(self, obs: Any, orig: tuple[int, int]) -> tuple[int, int]:
        w = getattr(obs, "width", None)
        h = getattr(obs, "height", None)
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            return (w, h)
        if self.screen_size:
            return self.screen_size
        return orig

    def build_messages(self, obs: Any) -> tuple[list[dict], dict]:
        """Build the chat messages for an observation.

        Returns ``(messages, ctx)`` where ctx has ``model_size``, ``screen_size``,
        ``coord_size`` and ``orig_size`` used for rescaling.
        """
        data_url, model_size, orig = prepare_image(obs.screenshot, self.image_max_side)
        screen = self._screen_size(obs, orig)
        coord_size = self._coord_from_size(model_size, screen)
        system = self._formatted_override(coord_size) if self.system_prompt_override else build_system_prompt(
            self.prompt_style, coord_size[0], coord_size[1], fara_allowed=self.fara_allowed
        )
        history = list(getattr(obs, "history", None) or [])
        if self.history_k >= 0:
            history = history[-self.history_k:] if self.history_k > 0 else []
        notes: list[str | None] | None = None
        if self.history_notes and history:
            # history[i] is the action taken at step (obs.step - len(history) + i)
            base = int(getattr(obs, "step", 0) or 0) - len(history)
            notes = [self._notes.get(base + i) for i in range(len(history))]
        text = build_user_text(getattr(obs, "instruction", ""), history, self.prompt_style,
                               step=getattr(obs, "step", None), notes=notes)
        def img(url: str) -> dict[str, Any]:
            part: dict[str, Any] = {"url": url}
            if self.image_detail:
                part["detail"] = self.image_detail
            return {"type": "image_url", "image_url": part}

        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if self.prev_screenshot and self._prev_data_url and history:
            content.append({"type": "text", "text": f"Screen BEFORE your last action ({history[-1]}):"})
            content.append(img(self._prev_data_url))
            content.append({"type": "text", "text": "Screen NOW (act on this one):"})
        content.append(img(data_url))
        self._prev_data_url = data_url
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        ctx = {"model_size": model_size, "screen_size": screen, "coord_size": coord_size, "orig_size": orig}
        return messages, ctx

    def build_request(self, obs: Any, n: int = 1) -> tuple[dict, dict]:
        messages, ctx = self.build_messages(obs)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if n > 1:
            body["n"] = int(n)
        if self.seed is not None:
            body["seed"] = int(self.seed)
        if self.top_p is not None:
            body["top_p"] = float(self.top_p)
        if self.hosted_reasoning:
            for k in ("temperature", "top_p", "seed"):
                body.pop(k, None)
            body["max_completion_tokens"] = body.pop("max_tokens")
        body.update(self.extra_body)
        return body, ctx

    # -- response handling -------------------------------------------------- #

    def _to_action(self, d: dict) -> tuple[Any, str | None]:
        try:
            from forkloop.actions import Action  # lazy: the module is written by the env author
        except ImportError:
            return d, None  # standalone use: hand back the validated dict
        try:
            return Action.parse(d), None
        except Exception as e:  # InvalidAction or anything else
            return None, f"Action.parse rejected {d}: {e}"

    #: Chrome's omnibox on the 1280x720 desktop is at (640, 90) (CLAUDE.md); scaled by screen size.
    NAV_OMNIBOX_FRAC: tuple[float, float] = (0.5, 0.125)

    def nav_macro_actions(self, name: str, args: dict, screen: tuple[int, int]) -> list[dict] | None:
        """Contract actions (screen space) for one Fara navigation call, or None if not expandable."""
        if name == "history_back":
            return [{"type": "key", "keys": ["alt", "Left"]}]
        if name == "visit_url":
            url = str(args.get("url") or args.get("text") or "").strip()
            if not url:
                return None
            x = int(round(self.NAV_OMNIBOX_FRAC[0] * screen[0]))
            y = int(round(self.NAV_OMNIBOX_FRAC[1] * screen[1]))
            return [{"type": "click", "x": x, "y": y, "button": "left"}, {"type": "key", "keys": ["ctrl", "a"]},
                    {"type": "type", "text": url}, {"type": "key", "keys": ["Return"]}]
        return None

    def _macro_step(self, d: dict, label: str, ctx: dict | None) -> tuple[Any, dict]:
        action, err = self._to_action(d)
        meta: dict[str, Any] = {
            "raw_action": label, "note": "" if action is not None else f"invalid action: {err}",
            "thoughts": "", "finish_reason": None, "parsed": d, "macro": label.split("]")[0].strip("["),
            "model_latency_s": 0.0, "tokens": dict(self.usage),
        }
        if ctx:
            meta.update({"model_image_size": list(ctx["model_size"]), "screen_size": list(ctx["screen_size"]),
                         "coord_space": self.coord_space})
        return action, meta

    def parse_choice(self, choice: dict, ctx: dict) -> tuple[Any, dict]:
        """Turn one ``choices[i]`` entry into ``(Action | None, meta)``."""
        message = choice.get("message") or {}
        content = _content_text(message.get("content"))
        tool_calls = message.get("tool_calls")
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        coord_size = ctx["coord_size"]
        default_xy = (coord_size[0] // 2, coord_size[1] // 2)
        if self.nav_macro and self.prompt_style == "fara":
            call = ap.fara_call_name_args(tool_calls or content)
            if call and call[0] in ap.FARA_NAV_ACTIONS:
                seq = self.nav_macro_actions(call[0], call[1], tuple(ctx["screen_size"]))
                if seq:
                    n = len(seq)
                    labels = [f"[nav macro {call[0]} {i}/{n}] {ap.to_compact(d)}" for i, d in enumerate(seq, 1)]
                    self._queue = list(zip(seq[1:], labels[1:]))
                    action, meta = self._macro_step(seq[0], labels[0], ctx)
                    meta["raw_action"] = content or json.dumps(tool_calls, ensure_ascii=False)
                    meta["thoughts"] = (ap.extract_thoughts(content) or "")[:2000]
                    meta["finish_reason"] = choice.get("finish_reason")
                    return action, meta
        if tool_calls:
            action_dict, err = ap.parse_tool_calls(tool_calls, default_xy=default_xy)
            raw = content or json.dumps(tool_calls, ensure_ascii=False)
        else:
            action_dict, err = ap.parse_any(content, style=self.prompt_style, default_xy=default_xy)
            raw = content
        thoughts = ap.extract_thoughts(content) or (reasoning if isinstance(reasoning, str) else "")
        meta: dict[str, Any] = {
            "raw_action": raw,
            "note": "",
            "thoughts": thoughts[:2000],
            "finish_reason": choice.get("finish_reason"),
            "model_image_size": list(ctx["model_size"]),
            "screen_size": list(ctx["screen_size"]),
            "coord_space": self.coord_space,
            "parsed": None,
        }
        if action_dict is None:
            meta["note"] = f"parse error: {err}"
            return None, meta
        scaled = ap.scale_coords(action_dict, coord_size, ctx["screen_size"])
        meta["parsed"] = scaled
        action, err2 = self._to_action(scaled)
        if action is None:
            meta["note"] = f"invalid action: {err2}"
            return None, meta
        return action, meta

    def _formatted_override(self, coord_size: tuple[int, int]) -> str:
        """Fill {w}/{h}/{w1}/{h1} in a user-supplied system prompt; other braces are left alone."""
        w, h = int(coord_size[0]), int(coord_size[1])
        out = self.system_prompt_override
        for k, v in (("{w1}", w - 1), ("{h1}", h - 1), ("{w}", w), ("{h}", h)):
            out = out.replace(k, str(v))
        return out

    async def _post(self, body: dict) -> dict:
        self.n_requests += 1
        resp = await self._client.post("/chat/completions", json=body)
        resp.raise_for_status()
        return resp.json()

    def _tokens(self, data: dict, n: int = 1) -> dict:
        usage = data.get("usage") or {}
        self.usage["in"] += int(usage.get("prompt_tokens") or 0)
        self.usage["out"] += int(usage.get("completion_tokens") or 0)
        return dict(self.usage)

    def _error_meta(self, exc: BaseException, latency: float, raw: str = "") -> dict:
        self.n_errors += 1
        return {
            "raw_action": raw, "model_latency_s": latency, "tokens": dict(self.usage),
            "note": f"request failed: {type(exc).__name__}: {exc}", "parsed": None, "error": True,
        }

    # -- Policy protocol ---------------------------------------------------- #

    async def act(self, obs: Any) -> tuple[Any, dict]:
        t0 = time.perf_counter()
        if int(getattr(obs, "step", 0) or 0) == 0:
            self._queue = []  # a new episode never inherits a half-finished macro
        if self._queue:
            d, label = self._queue.pop(0)
            return self._macro_step(d, label, None)
        try:
            body, ctx = self.build_request(obs, n=1)
        except Exception as e:
            return None, self._error_meta(e, time.perf_counter() - t0)
        try:
            data = await self._post(body)
        except (httpx.HTTPError, asyncio.TimeoutError, OSError, ValueError) as e:
            return None, self._error_meta(e, time.perf_counter() - t0)
        latency = time.perf_counter() - t0
        try:
            choices = data.get("choices") or []
            if not choices:
                meta = self._error_meta(RuntimeError("response has no choices"), latency)
                return None, meta
            action, meta = self.parse_choice(choices[0], ctx)
        except Exception as e:
            return None, self._error_meta(e, latency)
        meta["model_latency_s"] = latency
        meta["tokens"] = self._tokens(data)
        if self.history_notes:
            self._notes[int(getattr(obs, "step", 0) or 0)] = note_from_reply(meta.get("raw_action") or "")
        return action, meta

    async def propose(self, obs: Any, n: int) -> list[tuple[Any, dict]]:
        """Best-of-N support: ``n`` sampled candidates for the same observation.

        Uses the server's ``n`` parameter and tops up with extra requests if the
        server returns fewer choices (some servers ignore ``n``).
        """
        n = max(1, int(n))
        t0 = time.perf_counter()
        try:
            body, ctx = self.build_request(obs, n=n)
        except Exception as e:
            return [(None, self._error_meta(e, time.perf_counter() - t0)) for _ in range(n)]
        if n > 1 and self.temperature <= 0.0:
            body["temperature"] = 1.0  # sampling identical candidates is pointless
        results: list[tuple[Any, dict]] = []
        try:
            data = await self._post(body)
            latency = time.perf_counter() - t0
            choices = data.get("choices") or []
            tokens = self._tokens(data, max(1, len(choices)))
            for i, ch in enumerate(choices[:n]):
                action, meta = self.parse_choice(ch, ctx)
                meta.update({"model_latency_s": latency, "tokens": tokens, "candidate_index": len(results)})
                results.append((action, meta))
        except (httpx.HTTPError, asyncio.TimeoutError, OSError, ValueError) as e:
            results.append((None, self._error_meta(e, time.perf_counter() - t0)))
        missing = n - len(results)
        if missing > 0:
            single = dict(body)
            single.pop("n", None)

            async def one(idx: int) -> tuple[Any, dict]:
                t1 = time.perf_counter()
                b = dict(single)
                if self.seed is not None:
                    b["seed"] = int(self.seed) + idx
                try:
                    d = await self._post(b)
                except (httpx.HTTPError, asyncio.TimeoutError, OSError, ValueError) as e:
                    return None, self._error_meta(e, time.perf_counter() - t1)
                lat = time.perf_counter() - t1
                chs = d.get("choices") or []
                if not chs:
                    return None, self._error_meta(RuntimeError("response has no choices"), lat)
                a, m = self.parse_choice(chs[0], ctx)
                m.update({"model_latency_s": lat, "tokens": self._tokens(d), "candidate_index": idx})
                return a, m

            extra = await asyncio.gather(*(one(len(results) + i) for i in range(missing)))
            results.extend(extra)
        return results[:n]


__all__ = [
    "StudentPolicy", "PROMPT_STYLES", "COORD_SPACES", "build_system_prompt", "build_user_text",
    "fara_computer_use_tool", "prepare_image", "FARA_DEFAULT_ALLOWED",
]
