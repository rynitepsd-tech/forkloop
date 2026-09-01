"""Deterministic policies for tests and for the toy world."""

from __future__ import annotations

import random
from typing import Any, Callable, Optional

from ..actions import Action, InvalidAction
from ..types import Observation
from .base import PolicyResult


class ScriptedPolicy:
    """Replays a fixed list of actions (compact strings, dicts or Actions), then ``done()``."""

    name = "scripted"

    def __init__(self, actions: list[Any], *, done_success: bool = True, note: str = "") -> None:
        self.actions = list(actions)
        self.done_success = done_success
        self.note = note
        self.i = 0

    async def act(self, obs: Observation) -> PolicyResult:
        if self.i < len(self.actions):
            raw = self.actions[self.i]
            self.i += 1
            try:
                a = Action.parse(raw, width=obs.width, height=obs.height)
                return a, {"raw_action": a.to_compact(), "model_latency_s": 0.0, "note": self.note}
            except InvalidAction as e:
                return None, {"raw_action": str(raw), "model_latency_s": 0.0, "error": str(e)}
        a = Action.done(self.done_success)
        return a, {"raw_action": a.to_compact(), "model_latency_s": 0.0}

    def reset(self) -> None:
        self.i = 0


class CallbackPolicy:
    """Wraps a plain function ``(obs) -> Action | str | dict | None``."""

    name = "callback"

    def __init__(self, fn: Callable[[Observation], Any], name: str = "callback") -> None:
        self.fn = fn
        self.name = name

    async def act(self, obs: Observation) -> PolicyResult:
        out = self.fn(obs)
        if out is None:
            return None, {"raw_action": "", "error": "callback returned None"}
        try:
            a = Action.parse(out, width=obs.width, height=obs.height)
        except InvalidAction as e:
            return None, {"raw_action": str(out), "error": str(e)}
        return a, {"raw_action": a.to_compact(), "model_latency_s": 0.0}


class RandomPolicy:
    """Uniform random clicks; a floor for any learning curve."""

    name = "random"

    def __init__(self, seed: int = 0, p_done: float = 0.05, p_type: float = 0.1,
                 vocabulary: Optional[list[str]] = None) -> None:
        self.rng = random.Random(seed)
        self.p_done, self.p_type = p_done, p_type
        self.vocabulary = vocabulary or ["hello", "123", "test"]

    async def act(self, obs: Observation) -> PolicyResult:
        r = self.rng.random()
        if r < self.p_done:
            a = Action.done(True)
        elif r < self.p_done + self.p_type:
            a = Action.type_text(self.rng.choice(self.vocabulary))
        else:
            a = Action.click(self.rng.randrange(obs.width), self.rng.randrange(obs.height))
        return a, {"raw_action": a.to_compact(), "model_latency_s": 0.0}

    async def propose(self, obs: Observation, n: int) -> list[PolicyResult]:
        return [await self.act(obs) for _ in range(n)]


__all__ = ["ScriptedPolicy", "CallbackPolicy", "RandomPolicy"]
