"""Turn model text into the Forkloop action dict (docs/contracts.md §3).

Three input styles are accepted and all of them normalise to the contract's
canonical dict form, e.g. ``{"type": "click", "x": 640, "y": 360, "button": "left"}``:

(a) **compact text** — the contract's own grammar::

        click(x, y)            double_click(x, y)      right_click(x, y)     move(x, y)
        drag(x1, y1, x2, y2)   scroll(x, y, "down", 3) type("hello")         key("ctrl+l")
        wait(1.5)              done()                  done(success=false, note="why")

(b) **JSON** — a contract dict, optionally wrapped in ```json fences and/or
    preceded by prose such as ``Action:``. Common aliases are tolerated
    (``"action"`` instead of ``"type"``, ``"coordinate": [x, y]`` instead of
    ``x``/``y``, ``left_click`` instead of ``click`` ...).

(c) **Fara 1.5 tool calls** — verified against the model card
    (https://huggingface.co/microsoft/Fara1.5-4B) and the reference harness
    (https://github.com/microsoft/fara, ``src/fara/agents/fara/_prompts.py`` and
    ``fara15_agent.py``). The model emits chain-of-thought text followed by::

        <tool_call>
        {"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [512, 300]}}
        </tool_call>

    ``computer_use`` argument fields: ``action`` (enum: key, type, mouse_move,
    left_click, left_click_drag, right_click, double_click, triple_click, scroll,
    hscroll, visit_url, history_back, web_search, read_page_answer_question,
    pause_and_memorize_fact, ask_user_question, wait, terminate), ``keys``
    (list, Playwright key names such as "Enter"/"Control"/"ArrowDown"),
    ``text``, ``coordinate`` ([x, y]), ``pixels`` (scroll amount, positive = up,
    negative = down), ``url``, ``query``, ``fact``, ``question``, ``time``
    (seconds for wait), ``answer`` (final answer for terminate).
    Coordinates are emitted in a fixed **1000x1000 display space**
    (``FARA_DISPLAY_SIZE = 1000`` in ``src/fara/agents/coord_spaces.py``); the
    harness rescales them with ``viewport / 1000``. Use
    :func:`scale_coords` with ``from_size=(1000, 1000)`` for Fara output.

Every public parser returns ``(action_dict | None, error_str | None)`` and never
raises. :func:`scale_coords` maps coordinates from the size the model saw to
the screen size; :func:`to_compact` is the inverse of the compact parser and is
what the SFT exporter writes as the training target.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Iterable

ParseResult = tuple[dict | None, str | None]

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

CONTRACT_TYPES: frozenset[str] = frozenset(
    {"click", "double_click", "right_click", "move", "scroll", "drag", "type", "key", "wait", "done"}
)
DIRECTIONS: tuple[str, ...] = ("up", "down", "left", "right")
BUTTONS: tuple[str, ...] = ("left", "right", "middle")

FARA_DISPLAY_SIZE: int = 1000
FARA_TOOL_NAME: str = "computer_use"
FARA_ACTIONS: tuple[str, ...] = (
    "key", "type", "mouse_move", "left_click", "left_click_drag", "right_click",
    "double_click", "triple_click", "scroll", "hscroll", "visit_url", "history_back",
    "web_search", "read_page_answer_question", "pause_and_memorize_fact",
    "ask_user_question", "wait", "terminate",
)
#: Fara actions that have no counterpart in the contract action space.
FARA_UNSUPPORTED: frozenset[str] = frozenset(
    {"visit_url", "history_back", "web_search", "read_page_answer_question"}
)
#: Browser-navigation actions the student policy can expand into contract actions
#: (``StudentPolicy(nav_macro=True)``: visit_url -> omnibox click, ctrl+a, type, Return;
#: history_back -> alt+Left). Measured need (2026-09-04, runs/fara15-4b-base-f3-s200-229):
#: base Fara 1.5 emits ``visit_url`` on 10 of its first 27 steps whatever the tool enum says,
#: and the env's invalid-action limit ends the episode before anything is done.
FARA_NAV_ACTIONS: frozenset[str] = frozenset({"visit_url", "history_back"})

_TYPE_ALIASES: dict[str, str] = {
    "click": "click", "left_click": "click", "leftclick": "click", "single_click": "click", "tap": "click",
    "double_click": "double_click", "doubleclick": "double_click", "dblclick": "double_click",
    "triple_click": "double_click",  # contract has no triple click; nearest supported action
    "right_click": "right_click", "rightclick": "right_click", "context_click": "right_click",
    "move": "move", "mouse_move": "move", "hover": "move", "move_to": "move", "mousemove": "move",
    "scroll": "scroll", "wheel": "scroll", "hscroll": "scroll", "scroll_down": "scroll", "scroll_up": "scroll",
    "drag": "drag", "left_click_drag": "drag", "drag_and_drop": "drag", "dragto": "drag",
    "type": "type", "type_text": "type", "input": "type", "input_text": "type", "write": "type",
    "key": "key", "press": "key", "hotkey": "key", "keypress": "key", "press_key": "key", "key_press": "key",
    "wait": "wait", "sleep": "wait", "pause": "wait",
    "done": "done", "finish": "done", "finished": "done", "terminate": "done", "stop": "done",
    "complete": "done", "completed": "done", "end": "done", "answer": "done", "fail": "done",
}

# xdotool key names per contracts.md §3. Lower-cased alias -> canonical name.
KEY_ALIASES: dict[str, str] = {
    "enter": "Return", "return": "Return", "kp_enter": "KP_Enter",
    "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "back_space": "BackSpace",
    "delete": "Delete", "del": "Delete",
    "tab": "Tab", "space": "space", "spacebar": "space",
    "home": "Home", "end": "End", "insert": "Insert",
    "pagedown": "Page_Down", "page_down": "Page_Down", "pgdn": "Page_Down",
    "pageup": "Page_Up", "page_up": "Page_Up", "pgup": "Page_Up",
    "up": "Up", "arrowup": "Up", "arrow_up": "Up",
    "down": "Down", "arrowdown": "Down", "arrow_down": "Down",
    "left": "Left", "arrowleft": "Left", "arrow_left": "Left",
    "right": "Right", "arrowright": "Right", "arrow_right": "Right",
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "super": "super", "meta": "super", "cmd": "super", "command": "super", "win": "super", "windows": "super",
    "capslock": "Caps_Lock", "caps_lock": "Caps_Lock",
    "plus": "plus", "minus": "minus", "divide": "slash", "slash": "slash", "backslash": "backslash",
    "period": "period", "comma": "comma",
}

_FUNC_KEY_RE = re.compile(r"^f([1-9]|1[0-9]|2[0-4])$")


def normalize_key(name: Any) -> str | None:
    """Map one key name (any style) to its xdotool name. Returns None for empty input."""
    if name is None:
        return None
    s = str(name).strip()
    if not s:
        return None
    low = s.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    if _FUNC_KEY_RE.match(low):
        return low.upper()
    if len(s) == 1:
        return s.lower() if s.isalpha() else s
    return s  # already an xdotool name (Return, Page_Down, ...) or something we pass through


def split_key_combo(combo: Any) -> list[str]:
    """``"ctrl+shift+l"`` -> ``["ctrl", "shift", "l"]``. Handles a literal ``+`` and whitespace."""
    if combo is None:
        return []
    if isinstance(combo, (list, tuple)):
        out: list[str] = []
        for item in combo:
            out.extend(split_key_combo(item))
        return out
    s = str(combo).strip()
    if not s:
        return []
    if s == "+":
        return ["plus"]
    parts: list[str] = []
    for chunk in re.split(r"\s+", s):
        if not chunk:
            continue
        if chunk == "+":
            parts.append("plus")
            continue
        pieces = chunk.split("+")
        for i, piece in enumerate(pieces):
            if piece == "":
                # "ctrl++" -> ctrl, plus ; a leading empty piece means "+x"
                if i > 0 and (i == len(pieces) - 1 or pieces[i + 1] == ""):
                    parts.append("plus")
                continue
            parts.append(piece)
    keys = [k for k in (normalize_key(p) for p in parts) if k]
    return keys


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #

def _num(v: Any) -> float:
    if isinstance(v, bool):
        raise ValueError("boolean is not a number")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return float(v.strip())
    raise ValueError(f"not a number: {v!r}")


def _to_int(v: Any) -> int:
    return int(round(_num(v)))


_PAIR_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)")


def _pair(v: Any) -> tuple[int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return _to_int(v[0]), _to_int(v[1])
    if isinstance(v, dict) and "x" in v and "y" in v:
        return _to_int(v["x"]), _to_int(v["y"])
    if isinstance(v, str):
        m = _PAIR_RE.search(v)
        if m:
            return _to_int(m.group(1)), _to_int(m.group(2))
    return None


def _xy_from(obj: dict, xy_keys: tuple[str, str] | tuple[()], alt_keys: Iterable[str]) -> tuple[int, int] | None:
    if xy_keys:
        kx, ky = xy_keys
        if obj.get(kx) is not None and obj.get(ky) is not None:
            return _to_int(obj[kx]), _to_int(obj[ky])
    for k in alt_keys:
        if k in obj and obj[k] is not None:
            p = _pair(obj[k])
            if p is not None:
                return p
    return None


def _first(obj: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _to_bool(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("true", "yes", "1", "success", "succeeded", "ok", "done", "completed", "complete"):
        return True
    if s in ("false", "no", "0", "fail", "failed", "failure", "error", "abort", "aborted", "infeasible"):
        return False
    return default


def _fmt_num(v: float) -> str:
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _short(text: Any, n: int = 120) -> str:
    s = str(text).replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 3] + "..."


# --------------------------------------------------------------------------- #
# Normalisation: any dict -> canonical contract dict
# --------------------------------------------------------------------------- #

def normalize_action(obj: Any, *, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Validate/coerce a loosely-shaped action dict into the contract's canonical dict.

    ``default_xy`` fills in the position of a ``scroll`` that has none (JSON and
    Fara tool calls commonly scroll "the page" without a coordinate).
    """
    try:
        if not isinstance(obj, dict):
            return None, f"action must be an object, got {type(obj).__name__}"
        raw_type = _first(obj, ("type", "action", "name", "action_type"))
        if not isinstance(raw_type, str) or not raw_type.strip():
            return None, "action has no 'type'"
        raw_l = raw_type.strip().lower()
        if "." in raw_l:  # e.g. "computer.click"
            raw_l = raw_l.rsplit(".", 1)[-1]
        t = _TYPE_ALIASES.get(raw_l)
        if t is None:
            return None, f"unknown action type {raw_type!r}"

        if t in ("click", "double_click", "right_click", "move"):
            xy = _xy_from(obj, ("x", "y"), ("coordinate", "coordinates", "position", "point", "xy", "target"))
            if xy is None:
                return None, f"{t} requires x and y"
            out: dict[str, Any] = {"type": t, "x": xy[0], "y": xy[1]}
            if t == "click":
                button = str(_first(obj, ("button",)) or "left").strip().lower()
                if button not in BUTTONS:
                    return None, f"unknown mouse button {button!r}"
                if button == "right":
                    return {"type": "right_click", "x": xy[0], "y": xy[1]}, None
                out["button"] = button
            return out, None

        if t == "scroll":
            xy = _xy_from(obj, ("x", "y"), ("coordinate", "coordinates", "position", "point", "xy"))
            if xy is None:
                xy = default_xy
            if xy is None:
                return None, "scroll requires x and y"
            direction = _first(obj, ("direction", "dir"))
            pixels = _first(obj, ("pixels", "delta", "dy", "delta_y"))
            if direction is None:
                if raw_l == "scroll_down":
                    direction = "down"
                elif raw_l == "scroll_up":
                    direction = "up"
                elif pixels is not None:
                    p = _num(pixels)
                    if raw_l == "hscroll":
                        direction = "left" if p > 0 else "right"
                    else:
                        direction = "up" if p > 0 else "down"  # Fara: positive = up
                else:
                    direction = "down"
            direction = str(direction).strip().lower()
            if direction not in DIRECTIONS:
                return None, f"scroll direction must be one of {DIRECTIONS}, got {direction!r}"
            amount_v = _first(obj, ("amount", "clicks", "repeat", "n", "count"))
            if amount_v is not None:
                amount = max(1, abs(_to_int(amount_v)))
            elif pixels is not None:
                amount = max(1, min(20, int(abs(_num(pixels)) / 100.0 + 0.5)))  # half-up, not banker's
            else:
                amount = 3
            return {"type": "scroll", "x": xy[0], "y": xy[1], "direction": direction, "amount": amount}, None

        if t == "drag":
            start = _xy_from(obj, ("x1", "y1"), ("start", "from", "start_coordinate", "source", "origin"))
            end = _xy_from(obj, ("x2", "y2"), ("end", "to", "end_coordinate", "target", "destination", "coordinate2"))
            if start is None:
                start = _xy_from(obj, ("x", "y"), ("coordinate", "coordinates", "position", "point"))
            if start is None or end is None:
                return None, "drag requires a start (x, y) and an end (x2, y2)"
            return {"type": "drag", "x": start[0], "y": start[1], "x2": end[0], "y2": end[1]}, None

        if t == "type":
            text = _first(obj, ("text", "content", "value", "input", "string"))
            if text is None:
                return None, "type requires 'text'"
            if isinstance(text, (list, tuple)):
                text = "".join(str(x) for x in text)
            return {"type": "type", "text": str(text)}, None

        if t == "key":
            keys_v = _first(obj, ("keys", "key", "combo", "hotkey", "text", "value"))
            keys = split_key_combo(keys_v)
            if not keys:
                return None, "key requires a non-empty 'keys' list"
            return {"type": "key", "keys": keys}, None

        if t == "wait":
            secs_v = _first(obj, ("seconds", "time", "duration", "secs", "s"))
            if secs_v is None and obj.get("ms") is not None:
                seconds = _num(obj["ms"]) / 1000.0
            else:
                seconds = 1.0 if secs_v is None else _num(secs_v)
            if seconds < 0:
                return None, "wait seconds must be >= 0"
            return {"type": "wait", "seconds": float(seconds)}, None

        if t == "done":
            success_v = _first(obj, ("success", "status", "result", "ok"))
            success = _to_bool(success_v, default=raw_l not in ("fail",))
            note = _first(obj, ("note", "answer", "message", "reason", "text", "summary"))
            out = {"type": "done", "success": bool(success)}
            if note is not None and str(note) != "":
                out["note"] = str(note)
            return out, None

        return None, f"unhandled action type {t!r}"  # pragma: no cover - alias table covers all
    except (ValueError, TypeError) as e:
        return None, f"bad field in {_short(obj)}: {e}"
    except Exception as e:  # never raise
        return None, f"normalize failed: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# (a) compact text form
# --------------------------------------------------------------------------- #

_CALL_RE = re.compile(r"(?<![\w.])(click|double_click|right_click|move|scroll|drag|type|key|wait|done)\s*\(")
_LEAD_RE = re.compile(r"^(?:[-*>]\s*)?(?:action|output|final action|next action)\s*:\s*", re.I)


def _scan_paren(text: str, open_idx: int) -> int | None:
    """Return index one past the ')' matching text[open_idx] == '(' honouring quotes."""
    depth = 0
    quote: str | None = None
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        else:
            if c in "\"'":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return None


def _split_args(s: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c in "([{":
            depth += 1
            buf.append(c)
        elif c in ")]}":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail or out:
        out.append(tail)
    return [a for a in out if a != ""]


_KW_RE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(.*)$", re.S)


def _parse_value(tok: str) -> Any:
    tok = tok.strip()
    if not tok:
        return ""
    if tok[0] in "\"'":
        try:
            v = ast.literal_eval(tok)
            if isinstance(v, str):
                return v
        except Exception:
            pass
        if len(tok) >= 2 and tok[-1] == tok[0]:
            return tok[1:-1]
        return tok[1:]
    low = tok.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("none", "null"):
        return None
    try:
        if re.fullmatch(r"-?\d+", tok):
            return int(tok)
        return float(tok)
    except ValueError:
        return tok


def _parse_args(argstr: str) -> tuple[list[Any], dict[str, Any]]:
    pos: list[Any] = []
    kw: dict[str, Any] = {}
    for tok in _split_args(argstr):
        m = _KW_RE.match(tok)
        if m and not tok.lstrip()[0] in "\"'":
            kw[m.group(1).lower()] = _parse_value(m.group(2))
        else:
            pos.append(_parse_value(tok))
    return pos, kw


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        try:
            v = ast.literal_eval(s)
            if isinstance(v, str):
                return v
        except Exception:
            return s[1:-1]
    return s


def _build_compact(name: str, argstr: str, default_xy: tuple[int, int] | None) -> ParseResult:
    try:
        if name == "type":
            pos, kw = _parse_args(argstr)
            if "text" in kw:
                text = kw["text"]
            elif len(pos) == 1 and isinstance(pos[0], str):
                text = pos[0]
            else:
                text = _strip_quotes(argstr)
            return normalize_action({"type": "type", "text": str(text)})
        pos, kw = _parse_args(argstr)
        if name in ("click", "double_click", "right_click", "move"):
            x = kw.get("x", pos[0] if len(pos) > 0 else None)
            y = kw.get("y", pos[1] if len(pos) > 1 else None)
            d: dict[str, Any] = {"type": name, "x": x, "y": y}
            if name == "click":
                button = kw.get("button", pos[2] if len(pos) > 2 else "left")
                d["button"] = button
            return normalize_action(d)
        if name == "scroll":
            nums = [p for p in pos if isinstance(p, (int, float)) and not isinstance(p, bool)]
            words = [p for p in pos if isinstance(p, str)]
            d = {"type": "scroll"}
            if "x" in kw and "y" in kw:
                d["x"], d["y"] = kw["x"], kw["y"]
                rest = nums
            elif len(nums) >= 2:
                d["x"], d["y"] = nums[0], nums[1]
                rest = nums[2:]
            else:
                rest = nums
            d["direction"] = kw.get("direction", words[0] if words else "down")
            if "amount" in kw:
                d["amount"] = kw["amount"]
            elif rest:
                d["amount"] = rest[0]
            return normalize_action(d, default_xy=default_xy)
        if name == "drag":
            vals = [kw.get(k) for k in ("x1", "y1", "x2", "y2")]
            if any(v is None for v in vals):
                if len(pos) < 4:
                    return None, "drag requires four coordinates"
                vals = pos[:4]
            return normalize_action({"type": "drag", "x1": vals[0], "y1": vals[1], "x2": vals[2], "y2": vals[3]})
        if name == "key":
            keys_v: Any
            if "keys" in kw or "key" in kw:
                keys_v = kw.get("keys", kw.get("key"))
            elif pos:
                keys_v = pos
            else:
                keys_v = _strip_quotes(argstr)
            return normalize_action({"type": "key", "keys": keys_v})
        if name == "wait":
            secs = kw.get("seconds", pos[0] if pos else 1.0)
            return normalize_action({"type": "wait", "seconds": secs})
        if name == "done":
            d = {"type": "done"}
            if "success" in kw:
                d["success"] = kw["success"]
            if "note" in kw:
                d["note"] = kw["note"]
            for p in pos:
                if isinstance(p, bool):
                    d["success"] = p
                elif isinstance(p, str):
                    if p.lower() in ("true", "false", "success", "failure", "fail"):
                        d["success"] = p
                    else:
                        d["note"] = p
            return normalize_action(d)
        return None, f"unknown compact action {name!r}"
    except Exception as e:  # never raise
        return None, f"could not parse {name}({_short(argstr)}): {type(e).__name__}: {e}"


def parse_compact(text: Any, *, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Parse the contract's compact text form. Prose before/after the call is tolerated."""
    if not isinstance(text, str):
        return None, f"expected str, got {type(text).__name__}"
    if not text.strip():
        return None, "empty model output"
    try:
        successes: list[tuple[bool, dict]] = []
        last_err: str | None = None
        for m in _CALL_RE.finditer(text):
            open_idx = m.end() - 1
            close = _scan_paren(text, open_idx)
            if close is None:
                last_err = f"unbalanced parentheses after {m.group(1)}("
                continue
            argstr = text[open_idx + 1: close - 1]
            action, err = _build_compact(m.group(1), argstr, default_xy)
            if action is None:
                last_err = err
                continue
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", close)
            line_end = len(text) if line_end == -1 else line_end
            line = _LEAD_RE.sub("", text[line_start:line_end].strip()).rstrip(".;")
            anchored = line == text[m.start():close].strip()
            successes.append((anchored, action))
        if not successes:
            return None, last_err or f"no compact action found in: {_short(text)}"
        anchored = [a for ok, a in successes if ok]
        if anchored:
            return anchored[-1], None
        return successes[-1][1], None
    except Exception as e:  # never raise
        return None, f"compact parse failed: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# (b) JSON
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json|JSON|python)?\s*(.*?)```", re.S)
_ACTION_KEYS = ("type", "action", "name", "action_type")


def _loads_lenient(s: str) -> Any:
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        return ast.literal_eval(s)
    except Exception:
        pass
    # Trailing commas / single quotes are the usual culprits.
    fixed = re.sub(r",\s*([}\]])", r"\1", s)
    try:
        return json.loads(fixed)
    except Exception:
        return None


def _iter_objects(text: str) -> Iterable[Any]:
    """Yield every top-level {...} object found in text, lenient about quoting."""
    decoder = json.JSONDecoder()
    i = 0
    n = len(text)
    while i < n:
        j = text.find("{", i)
        if j == -1:
            return
        try:
            obj, end = decoder.raw_decode(text, j)
            yield obj
            i = j + max(1, end - j)
            continue
        except Exception:
            pass
        # Fallback: find matching brace honouring quotes, then ast.literal_eval.
        depth = 0
        quote: str | None = None
        k = j
        end_idx: int | None = None
        while k < n:
            c = text[k]
            if quote:
                if c == "\\":
                    k += 2
                    continue
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end_idx = k + 1
                    break
            k += 1
        if end_idx is not None:
            obj = _loads_lenient(text[j:end_idx])
            if obj is not None:
                yield obj
                i = end_idx
                continue
        i = j + 1


def _from_obj(obj: Any, default_xy: tuple[int, int] | None) -> ParseResult:
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                return _from_obj(item, default_xy)
        return None, "JSON list contains no action object"
    if not isinstance(obj, dict):
        return None, f"JSON value is a {type(obj).__name__}, not an object"
    if "arguments" in obj and ("name" in obj or "function" in obj):
        name = obj.get("name") or (obj.get("function") or {}).get("name") or FARA_TOOL_NAME
        return parse_fara_call(name, obj["arguments"], default_xy=default_xy)
    if "function" in obj and isinstance(obj["function"], dict):
        fn = obj["function"]
        return parse_fara_call(fn.get("name") or FARA_TOOL_NAME, fn.get("arguments"), default_xy=default_xy)
    if "action" in obj and isinstance(obj["action"], dict):
        return _from_obj(obj["action"], default_xy)
    if "action" in obj and isinstance(obj["action"], str) and "type" not in obj:
        low = obj["action"].strip().lower().rsplit(".", 1)[-1]
        if low in FARA_ACTIONS:
            return parse_fara_call(FARA_TOOL_NAME, obj, default_xy=default_xy)
    if any(k in obj for k in _ACTION_KEYS):
        return normalize_action(obj, default_xy=default_xy)
    return None, f"JSON object has no action key: {_short(obj)}"


def parse_json(text: Any, *, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Parse a JSON action, tolerating ```json fences, 'Action:' prefixes and prose."""
    if isinstance(text, (dict, list)):
        return _from_obj(text, default_xy)
    if not isinstance(text, str):
        return None, f"expected str, got {type(text).__name__}"
    if not text.strip():
        return None, "empty model output"
    try:
        candidates = _FENCE_RE.findall(text) or [text]
        last_err: str | None = None
        for cand in candidates:
            for obj in _iter_objects(cand):
                action, err = _from_obj(obj, default_xy)
                if action is not None:
                    return action, None
                last_err = err
        return None, last_err or f"no JSON object found in: {_short(text)}"
    except Exception as e:  # never raise
        return None, f"json parse failed: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# (c) Fara 1.5 tool calls
# --------------------------------------------------------------------------- #

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def parse_fara_call(name: Any, arguments: Any, *, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Map one ``computer_use`` call (tool name + arguments) to the contract dict.

    Coordinates are left in Fara's 1000x1000 display space; call
    :func:`scale_coords` with ``from_size=(1000, 1000)`` afterwards.
    """
    try:
        if isinstance(arguments, str):
            arguments = _loads_lenient(arguments)
        if not isinstance(arguments, dict):
            return None, f"tool call {name!r} has no arguments object"
        args = dict(arguments)
        action = args.get("action")
        if not isinstance(action, str) or not action.strip():
            # Some servers put the action in the tool name: "computer.click", "left_click".
            tail = str(name or "").strip().lower().rsplit(".", 1)[-1]
            if tail and tail not in (FARA_TOOL_NAME, "computer", "computer_use_tool"):
                action = tail
            else:
                return None, f"tool call {name!r} has no 'action'"
        action = action.strip().lower().rsplit(".", 1)[-1]
        if default_xy is None:
            default_xy = (FARA_DISPLAY_SIZE // 2, FARA_DISPLAY_SIZE // 2)

        if action in FARA_UNSUPPORTED:
            return None, f"unsupported Fara action {action!r}: browser navigation is not in the contract action space"
        if action == "left_click_drag":
            end = _pair(args.get("coordinate"))
            start = _xy_from(args, ("x1", "y1"), ("start_coordinate", "start", "from", "source", "origin"))
            if end is None:
                return None, "left_click_drag requires 'coordinate'"
            if start is None:
                return None, ("left_click_drag drags from the current cursor position, which the contract does "
                              "not track; provide 'start_coordinate'")
            return normalize_action({"type": "drag", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]})
        if action == "terminate":
            return normalize_action({"type": "done", "success": args.get("status", args.get("success")),
                                     "note": args.get("answer", args.get("note"))})
        if action == "ask_user_question":
            q = args.get("question") or args.get("text") or ""
            return normalize_action({"type": "done", "success": False, "note": f"ask_user_question: {q}"})
        if action == "pause_and_memorize_fact":
            # Observationally a no-op; a short wait keeps the trajectory moving.
            return {"type": "wait", "seconds": 0.1}, None
        if action == "wait":
            secs = _first(args, ("time", "seconds", "duration"))
            return normalize_action({"type": "wait", "seconds": 1.0 if secs is None else secs})
        if action in ("scroll", "hscroll"):
            d = {"type": action}
            if "coordinate" in args:
                d["coordinate"] = args["coordinate"]
            for k in ("pixels", "direction", "amount", "x", "y"):
                if k in args:
                    d[k] = args[k]
            return normalize_action(d, default_xy=default_xy)
        if action == "key":
            keys_v = _first(args, ("keys", "key", "text"))
            return normalize_action({"type": "key", "keys": keys_v})
        if action == "type":
            return normalize_action({"type": "type", "text": args.get("text", args.get("value"))})
        # left_click / right_click / double_click / triple_click / mouse_move
        d = dict(args)
        d["type"] = action
        d.pop("action", None)
        return normalize_action(d, default_xy=default_xy)
    except Exception as e:  # never raise
        return None, f"fara call parse failed: {type(e).__name__}: {e}"


def parse_fara(text: Any, *, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Parse Fara 1.5 output: thoughts + ``<tool_call>{...}</tool_call>``."""
    if isinstance(text, (dict, list)):
        return _from_obj(text, default_xy)
    if not isinstance(text, str):
        return None, f"expected str, got {type(text).__name__}"
    if not text.strip():
        return None, "empty model output"
    try:
        bodies = _TOOL_CALL_RE.findall(text)
        if not bodies and "<tool_call>" in text:
            bodies = [text.split("<tool_call>", 1)[1]]
        if not bodies:
            # No tags at all: accept a bare {"name": ..., "arguments": ...} object.
            for obj in _iter_objects(text):
                if isinstance(obj, dict) and ("arguments" in obj or "action" in obj):
                    return _from_obj(obj, default_xy)
            return None, f"no <tool_call> block in: {_short(text)}"
        last_err: str | None = None
        for body in bodies:
            obj = _loads_lenient(body)
            if obj is None:
                last_err = f"tool_call body is not JSON: {_short(body)}"
                continue
            action, err = _from_obj(obj, default_xy)
            if action is not None:
                return action, None
            last_err = err
        return None, last_err or "no usable tool call"
    except Exception as e:  # never raise
        return None, f"fara parse failed: {type(e).__name__}: {e}"


def fara_call_name_args(output: Any) -> tuple[str, dict] | None:
    """The ``(action, arguments)`` of the first usable ``computer_use`` call in Fara output,
    before any mapping to the contract: raw text with ``<tool_call>`` blocks, or an
    OpenAI-style ``tool_calls`` list. ``None`` when there is no call with an ``action``."""
    try:
        candidates: list[Any] = []
        if isinstance(output, dict):
            output = [output]
        if isinstance(output, list):
            for tc in output:
                if isinstance(tc, dict):
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                    candidates.append(fn.get("arguments") if "arguments" in fn else fn)
        elif isinstance(output, str):
            bodies = _TOOL_CALL_RE.findall(output)
            if not bodies and "<tool_call>" in output:
                bodies = [output.split("<tool_call>", 1)[1]]
            for body in bodies:
                obj = _loads_lenient(body)
                if isinstance(obj, dict):
                    candidates.append(obj.get("arguments") if "arguments" in obj else obj)
        for args in candidates:
            if isinstance(args, str):
                args = _loads_lenient(args)
            if isinstance(args, dict) and isinstance(args.get("action"), str) and args["action"].strip():
                return args["action"].strip().lower().rsplit(".", 1)[-1], dict(args)
        return None
    except Exception:  # never raise
        return None


def parse_tool_calls(tool_calls: Any, *, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Parse the OpenAI-style ``message.tool_calls`` list that vLLM returns when
    ``--enable-auto-tool-choice`` is on (instead of raw ``<tool_call>`` text)."""
    try:
        if isinstance(tool_calls, dict):
            tool_calls = [tool_calls]
        if not isinstance(tool_calls, list) or not tool_calls:
            return None, "no tool calls"
        last_err: str | None = None
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            action, err = parse_fara_call(fn.get("name"), fn.get("arguments"), default_xy=default_xy)
            if action is not None:
                return action, None
            last_err = err
        return None, last_err or "no usable tool call"
    except Exception as e:  # never raise
        return None, f"tool_calls parse failed: {type(e).__name__}: {e}"


def extract_thoughts(text: Any) -> str:
    """Best-effort free text preceding the action (for policy_note)."""
    if not isinstance(text, str):
        return ""
    if "<tool_call>" in text:
        return text.split("<tool_call>", 1)[0].strip()
    m = _FENCE_RE.search(text)
    if m:
        return text[: m.start()].strip()
    lines = text.strip().splitlines()
    if len(lines) > 1 and _CALL_RE.search(lines[-1]):
        return "\n".join(lines[:-1]).strip()
    return ""


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #

def parse_any(text: Any, *, style: str | None = None, default_xy: tuple[int, int] | None = None) -> ParseResult:
    """Parse model output in any supported style.

    ``style`` ("compact" | "json" | "fara" | None) only picks the parser tried
    first; the others are tried as fallbacks. Returns ``(action, None)`` or
    ``(None, error)`` and never raises.
    """
    try:
        if isinstance(text, (dict, list)):
            return _from_obj(text, default_xy)
        if text is None:
            return None, "model returned no content"
        if not isinstance(text, str):
            text = str(text)
        if not text.strip():
            return None, "empty model output"
        order: list[str] = []
        if style in ("compact", "json", "fara"):
            order.append(style)
        if "<tool_call>" in text and "fara" not in order:
            order.append("fara")
        for s in ("json", "compact", "fara"):
            if s not in order:
                order.append(s)
        parsers = {"compact": parse_compact, "json": parse_json, "fara": parse_fara}
        first_err: str | None = None
        for s in order:
            if s == "json" and "{" not in text:
                continue
            if s == "fara" and "<tool_call>" not in text and "arguments" not in text:
                continue
            action, err = parsers[s](text, default_xy=default_xy)
            if action is not None:
                return action, None
            if first_err is None:
                first_err = err
        return None, first_err or f"no action found in model output: {_short(text)}"
    except Exception as e:  # never raise
        return None, f"parse failed: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# Coordinate scaling and compact serialisation
# --------------------------------------------------------------------------- #

def scale_coords(action: dict | None, from_size: tuple[int, int], to_size: tuple[int, int]) -> dict | None:
    """Map x/y/x2/y2 from the image size the model saw to the screen size.

    Rounds to int and clamps into ``[0, to_w-1] x [0, to_h-1]``. Returns a new
    dict (input untouched). Never raises: bad sizes return an unscaled copy.
    """
    if action is None:
        return None
    out = dict(action)
    try:
        fw, fh = float(from_size[0]), float(from_size[1])
        tw, th = int(to_size[0]), int(to_size[1])
        if fw <= 0 or fh <= 0 or tw <= 0 or th <= 0:
            return out
        sx, sy = tw / fw, th / fh
        for k, s, lim in (("x", sx, tw), ("x2", sx, tw), ("y", sy, th), ("y2", sy, th)):
            if k in out and out[k] is not None:
                v = int(round(_num(out[k]) * s))
                out[k] = max(0, min(lim - 1, v))
        return out
    except Exception:
        return out


def to_compact(action: dict | Any) -> str:
    """Serialise a contract action dict to the compact text form (inverse of parse_compact)."""
    if hasattr(action, "to_dict") and not isinstance(action, dict):
        action = action.to_dict()
    if not isinstance(action, dict):
        raise TypeError(f"to_compact expects a dict, got {type(action).__name__}")
    t = action.get("type")
    if t == "click":
        button = action.get("button", "left")
        if button and button != "left":
            return f'click({int(action["x"])}, {int(action["y"])}, "{button}")'
        return f"click({int(action['x'])}, {int(action['y'])})"
    if t in ("double_click", "right_click", "move"):
        return f"{t}({int(action['x'])}, {int(action['y'])})"
    if t == "scroll":
        return (f'scroll({int(action["x"])}, {int(action["y"])}, "{action.get("direction", "down")}", '
                f'{int(action.get("amount", 3))})')
    if t == "drag":
        return f"drag({int(action['x'])}, {int(action['y'])}, {int(action['x2'])}, {int(action['y2'])})"
    if t == "type":
        return f"type({json.dumps(str(action.get('text', '')), ensure_ascii=False)})"
    if t == "key":
        keys = action.get("keys") or []
        if isinstance(keys, str):
            keys = split_key_combo(keys)
        return f'key({json.dumps("+".join(str(k) for k in keys), ensure_ascii=False)})'
    if t == "wait":
        return f"wait({_fmt_num(float(action.get('seconds', 1.0)))})"
    if t == "done":
        success = bool(action.get("success", True))
        note = action.get("note")
        if success and not note:
            return "done()"
        parts = [f"success={'true' if success else 'false'}"]
        if note:
            parts.append(f"note={json.dumps(str(note), ensure_ascii=False)}")
        return f"done({', '.join(parts)})"
    raise ValueError(f"cannot serialise action type {t!r}")


__all__ = [
    "ParseResult", "CONTRACT_TYPES", "DIRECTIONS", "BUTTONS", "FARA_DISPLAY_SIZE", "FARA_TOOL_NAME",
    "FARA_NAV_ACTIONS", "fara_call_name_args",
    "FARA_ACTIONS", "FARA_UNSUPPORTED", "KEY_ALIASES", "normalize_key", "split_key_combo",
    "normalize_action", "parse_compact", "parse_json", "parse_fara", "parse_fara_call",
    "parse_tool_calls", "parse_any", "extract_thoughts", "scale_coords", "to_compact",
]
