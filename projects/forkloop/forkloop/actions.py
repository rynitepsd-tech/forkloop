"""Normalized action schema (docs/contracts.md §3).

Coordinates are in the world's fixed screenshot pixel space. Policies that
look at a resized image must map back before emitting an action; see
``forkloop.policies.action_parse.scale_coords``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

ACTION_TYPES = frozenset(
    {"click", "double_click", "right_click", "move", "scroll", "drag", "type", "key", "wait", "done"}
)
SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})
MOUSE_BUTTONS = frozenset({"left", "middle", "right"})

#: Human aliases → xdotool key names. Anything not listed passes through as-is.
KEY_ALIASES: dict[str, str] = {
    "enter": "Return", "return": "Return",
    "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "bksp": "BackSpace",
    "tab": "Tab", "space": "space",
    "delete": "Delete", "del": "Delete",
    "pagedown": "Page_Down", "page_down": "Page_Down", "pgdn": "Page_Down",
    "pageup": "Page_Up", "page_up": "Page_Up", "pgup": "Page_Up",
    "home": "Home", "end": "End",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "arrowup": "Up", "arrowdown": "Down", "arrowleft": "Left", "arrowright": "Right",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "super": "super", "cmd": "super", "meta": "super", "win": "super",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5", "f6": "F6",
    "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}

MAX_TYPE_LEN = 2000
MAX_WAIT_S = 30.0
MAX_SCROLL_AMOUNT = 50


class InvalidAction(ValueError):
    """Raised by :meth:`Action.parse` when the input is not a valid action."""


def normalize_key(name: str) -> str:
    n = name.strip()
    if not n:
        raise InvalidAction("empty key name")
    return KEY_ALIASES.get(n.lower(), n if len(n) > 1 else n)


@dataclass(frozen=True)
class Action:
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    button: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    text: Optional[str] = None
    keys: Optional[tuple[str, ...]] = None
    seconds: Optional[float] = None
    success: Optional[bool] = None
    note: Optional[str] = None

    # ------------------------------------------------------------------ build
    @staticmethod
    def click(x: int, y: int, button: str = "left") -> "Action":
        return Action(type="click", x=int(x), y=int(y), button=button)

    @staticmethod
    def double_click(x: int, y: int) -> "Action":
        return Action(type="double_click", x=int(x), y=int(y))

    @staticmethod
    def right_click(x: int, y: int) -> "Action":
        return Action(type="right_click", x=int(x), y=int(y))

    @staticmethod
    def move(x: int, y: int) -> "Action":
        return Action(type="move", x=int(x), y=int(y))

    @staticmethod
    def scroll(x: int, y: int, direction: str = "down", amount: int = 3) -> "Action":
        return Action(type="scroll", x=int(x), y=int(y), direction=direction, amount=int(amount))

    @staticmethod
    def drag(x: int, y: int, x2: int, y2: int) -> "Action":
        return Action(type="drag", x=int(x), y=int(y), x2=int(x2), y2=int(y2))

    @staticmethod
    def type_text(text: str) -> "Action":
        return Action(type="type", text=text)

    @staticmethod
    def key(*keys: str) -> "Action":
        flat: list[str] = []
        for k in keys:
            flat.extend(p for p in re.split(r"[+\s]+", k) if p)
        return Action(type="key", keys=tuple(normalize_key(k) for k in flat))

    @staticmethod
    def wait(seconds: float = 1.0) -> "Action":
        return Action(type="wait", seconds=float(seconds))

    @staticmethod
    def done(success: bool = True, note: Optional[str] = None) -> "Action":
        return Action(type="done", success=bool(success), note=note)

    # -------------------------------------------------------------- serialize
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        for f in ("x", "y", "x2", "y2", "button", "direction", "amount", "text", "seconds", "success", "note"):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        if self.keys is not None:
            d["keys"] = list(self.keys)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    def to_compact(self) -> str:
        t = self.type
        if t in ("click", "double_click", "right_click", "move"):
            if t == "click" and self.button and self.button != "left":
                return f'click({self.x}, {self.y}, "{self.button}")'
            return f"{t}({self.x}, {self.y})"
        if t == "scroll":
            return f'scroll({self.x}, {self.y}, "{self.direction}", {self.amount})'
        if t == "drag":
            return f"drag({self.x}, {self.y}, {self.x2}, {self.y2})"
        if t == "type":
            return f"type({json.dumps(self.text, ensure_ascii=False)})"
        if t == "key":
            return f'key("{"+".join(self.keys or ())}")'
        if t == "wait":
            return f"wait({self.seconds:g})"
        if t == "done":
            if self.note:
                return f"done({str(self.success).lower()}, {json.dumps(self.note, ensure_ascii=False)})"
            return "done()" if self.success else "done(false)"
        raise InvalidAction(f"unknown action type {t!r}")

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.to_compact()

    @property
    def is_terminal(self) -> bool:
        return self.type == "done"

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, obj: Any, *, width: Optional[int] = None, height: Optional[int] = None) -> "Action":
        """Parse a dict, a JSON string, or the compact text form.

        Raises :class:`InvalidAction` with a human-readable reason.
        """
        if isinstance(obj, Action):
            a = obj
        elif isinstance(obj, dict):
            a = cls.from_dict(obj)
        elif isinstance(obj, str):
            s = obj.strip()
            if not s:
                raise InvalidAction("empty action")
            if s.startswith("{"):
                try:
                    a = cls.from_dict(json.loads(s))
                except json.JSONDecodeError as e:
                    raise InvalidAction(f"bad JSON: {e}") from e
            else:
                a = cls.from_text(s)
        else:
            raise InvalidAction(f"unsupported action input type {type(obj).__name__}")
        a.validate(width=width, height=height)
        return a

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Action":
        if not isinstance(d, dict):
            raise InvalidAction("action must be an object")
        t = d.get("type") or d.get("action")
        if not isinstance(t, str):
            raise InvalidAction("missing 'type'")
        t = t.strip().lower()
        alias = {"left_click": "click", "doubleclick": "double_click", "rightclick": "right_click",
                 "mouse_move": "move", "hover": "move", "type_text": "type", "keypress": "key",
                 "hotkey": "key", "press": "key", "finish": "done", "stop": "done", "terminate": "done",
                 "sleep": "wait"}
        t = alias.get(t, t)
        if t not in ACTION_TYPES:
            raise InvalidAction(f"unknown action type {t!r}")

        def _int(k: str, required: bool = False) -> Optional[int]:
            v = d.get(k)
            if v is None:
                if required:
                    raise InvalidAction(f"{t} requires '{k}'")
                return None
            try:
                return int(round(float(v)))
            except (TypeError, ValueError) as e:
                raise InvalidAction(f"'{k}' must be a number, got {v!r}") from e

        # tolerate {"coordinate": [x, y]} and {"start"/"end": [..]} shapes
        if "coordinate" in d and "x" not in d:
            c = d["coordinate"]
            if isinstance(c, (list, tuple)) and len(c) == 2:
                d = {**d, "x": c[0], "y": c[1]}
        if "start" in d and "end" in d and "x" not in d:
            s, e = d["start"], d["end"]
            d = {**d, "x": s[0], "y": s[1], "x2": e[0], "y2": e[1]}

        if t in ("click", "double_click", "right_click", "move"):
            btn = d.get("button", "left" if t == "click" else None)
            if t == "click":
                btn = str(btn or "left").lower()
                if btn not in MOUSE_BUTTONS:
                    raise InvalidAction(f"bad button {btn!r}")
                if btn == "right":
                    return Action(type="right_click", x=_int("x", True), y=_int("y", True))
                return Action(type="click", x=_int("x", True), y=_int("y", True), button=btn)
            return Action(type=t, x=_int("x", True), y=_int("y", True))
        if t == "scroll":
            direction = str(d.get("direction", "down")).lower()
            if direction not in SCROLL_DIRECTIONS:
                raise InvalidAction(f"bad scroll direction {direction!r}")
            amount = _int("amount")
            if amount is None:
                amount = 3
            return Action(type="scroll", x=_int("x", True), y=_int("y", True), direction=direction, amount=amount)
        if t == "drag":
            return Action(type="drag", x=_int("x", True), y=_int("y", True), x2=_int("x2", True), y2=_int("y2", True))
        if t == "type":
            text = d.get("text")
            if not isinstance(text, str):
                raise InvalidAction("type requires string 'text'")
            return Action(type="type", text=text)
        if t == "key":
            keys = d.get("keys", d.get("key"))
            if isinstance(keys, str):
                return Action.key(keys)
            if isinstance(keys, (list, tuple)) and keys and all(isinstance(k, str) for k in keys):
                return Action.key(*keys)
            raise InvalidAction("key requires 'keys' (string or list of strings)")
        if t == "wait":
            v = d.get("seconds", d.get("duration", 1.0))
            try:
                return Action(type="wait", seconds=float(v))
            except (TypeError, ValueError) as e:
                raise InvalidAction("wait requires numeric 'seconds'") from e
        if t == "done":
            succ = d.get("success", True)
            if isinstance(succ, str):
                succ = succ.strip().lower() in ("true", "1", "yes", "success")
            note = d.get("note") or d.get("message") or d.get("answer")
            return Action(type="done", success=bool(succ), note=str(note) if note is not None else None)
        raise InvalidAction(f"unhandled type {t!r}")  # pragma: no cover

    @classmethod
    def from_text(cls, s: str) -> "Action":
        """Compact form: ``click(640, 360)``, ``type("hi")``, ``key("ctrl+l")`` ..."""
        m = re.match(r"^\s*([A-Za-z_]+)\s*\((.*)\)\s*;?\s*$", s, re.S)
        if not m:
            raise InvalidAction(f"not a compact action: {s[:80]!r}")
        name = m.group(1).lower()
        args = _split_args(m.group(2))
        d: dict[str, Any] = {"type": name}
        try:
            if name in ("click", "double_click", "right_click", "move", "left_click", "hover"):
                if len(args) < 2:
                    raise InvalidAction(f"{name} needs x, y")
                d.update(x=args[0], y=args[1])
                if name == "click" and len(args) >= 3:
                    d["button"] = args[2]
            elif name == "scroll":
                if len(args) < 2:
                    raise InvalidAction("scroll needs x, y")
                d.update(x=args[0], y=args[1])
                if len(args) >= 3:
                    d["direction"] = args[2]
                if len(args) >= 4:
                    d["amount"] = args[3]
            elif name == "drag":
                if len(args) < 4:
                    raise InvalidAction("drag needs x, y, x2, y2")
                d.update(x=args[0], y=args[1], x2=args[2], y2=args[3])
            elif name in ("type", "type_text"):
                if len(args) != 1 or not isinstance(args[0], str):
                    raise InvalidAction("type needs one quoted string")
                d.update(type="type", text=args[0])
            elif name in ("key", "press", "hotkey"):
                if not args or not all(isinstance(a, str) for a in args):
                    raise InvalidAction("key needs quoted key names")
                d.update(type="key", keys=list(args))
            elif name in ("wait", "sleep"):
                d.update(type="wait", seconds=args[0] if args else 1.0)
            elif name in ("done", "finish", "stop"):
                d["type"] = "done"
                if args:
                    d["success"] = args[0] if not isinstance(args[0], str) else args[0]
                    if len(args) >= 2:
                        d["note"] = args[1]
            else:
                raise InvalidAction(f"unknown action {name!r}")
        except IndexError as e:  # pragma: no cover - defensive
            raise InvalidAction(f"bad arguments for {name}") from e
        return cls.from_dict(d)

    # --------------------------------------------------------------- validate
    def validate(self, *, width: Optional[int] = None, height: Optional[int] = None) -> None:
        t = self.type
        if t not in ACTION_TYPES:
            raise InvalidAction(f"unknown action type {t!r}")
        for f in ("x", "y", "x2", "y2"):
            v = getattr(self, f)
            if v is not None:
                if v < 0:
                    raise InvalidAction(f"{f}={v} is negative")
                if width is not None and f in ("x", "x2") and v >= width:
                    raise InvalidAction(f"{f}={v} outside screen width {width}")
                if height is not None and f in ("y", "y2") and v >= height:
                    raise InvalidAction(f"{f}={v} outside screen height {height}")
        if t == "type":
            if self.text is None:
                raise InvalidAction("type requires text")
            if len(self.text) > MAX_TYPE_LEN:
                raise InvalidAction(f"text longer than {MAX_TYPE_LEN} chars")
        if t == "key" and not self.keys:
            raise InvalidAction("key requires at least one key")
        if t == "wait":
            if self.seconds is None or self.seconds < 0:
                raise InvalidAction("wait requires non-negative seconds")
            if self.seconds > MAX_WAIT_S:
                raise InvalidAction(f"wait longer than {MAX_WAIT_S}s")
        if t == "scroll":
            if self.amount is None or self.amount <= 0 or self.amount > MAX_SCROLL_AMOUNT:
                raise InvalidAction(f"scroll amount must be in 1..{MAX_SCROLL_AMOUNT}")


def _split_args(s: str) -> list[Any]:
    """Split ``a, "b, c", 3.5`` into python values (ints, floats, strs, bools)."""
    out: list[Any] = []
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i] in " \t\n,":
            i += 1
        if i >= n:
            break
        c = s[i]
        if c in "\"'":
            q = c
            j = i + 1
            buf = []
            while j < n and s[j] != q:
                if s[j] == "\\" and j + 1 < n:
                    nxt = s[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}.get(nxt, nxt))
                    j += 2
                    continue
                buf.append(s[j])
                j += 1
            if j >= n:
                raise InvalidAction("unterminated string literal")
            out.append("".join(buf))
            i = j + 1
            continue
        j = i
        while j < n and s[j] != ",":
            j += 1
        tok = s[i:j].strip()
        i = j
        if not tok:
            continue
        low = tok.lower()
        if low in ("true", "false"):
            out.append(low == "true")
            continue
        try:
            out.append(int(tok))
            continue
        except ValueError:
            pass
        try:
            out.append(float(tok))
            continue
        except ValueError:
            pass
        out.append(tok)  # bare word, e.g. down
    return out


def parse_many(items: Iterable[Any], **kw: Any) -> list[Action]:
    return [Action.parse(i, **kw) for i in items]


__all__ = ["Action", "InvalidAction", "ACTION_TYPES", "KEY_ALIASES", "normalize_key", "parse_many"]
