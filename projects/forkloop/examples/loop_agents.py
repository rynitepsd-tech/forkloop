"""Agents for the offline loop demo (``forkloop demo-loop``) and a template for your own.

The correction loop needs only the ``compare`` agent contract (``examples/custom_agent.py``):
``await act(observation) -> (Action | None, metadata)``. Two optional additions let checkpoints
bind your agent's state:

* ``snapshot_state() -> dict`` / ``restore_state(dict)`` — the agent's full decision state, so a
  checkpoint can put *this* agent back exactly where it was;
* ``agent_state() -> dict`` / ``load_agent_state(dict)`` — the declared, observation-contract state
  another policy may adopt at a checkpoint (Forkloop's built-in agents declare their explicit
  memory: ``{"memory": [...]}``). A teacher continuing your agent's checkpoint adopts only this.

Report the facts your agent writes to its memory in ``metadata["memory_written"]`` so datasets can
audit memory provenance. Nothing else crosses the agent channel.

The toy agents below play the toy-counter world (two counters, +/- buttons): the "student" makes
a collateral click on counter B at step 1; the "teacher" does not. The toy agents cannot see pixels,
so they track counter A in their explicit memory. OFFLINE SIMULATION ONLY.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from forkloop.actions import Action

A_PLUS, A_MINUS, B_PLUS = "click(220, 200)", "click(100, 200)", "click(540, 200)"


class ToyCounterAgent:
    """Explicit-memory toy agent. ``mistake_at``: the step at which it clicks counter B."""

    def __init__(self, *, mistake_at: Optional[int] = None, start_value: int = 0, name: str = "toy") -> None:
        self.mistake_at, self.start_value, self.name = mistake_at, start_value, name
        self._memory: list[str] = []
        self.usage = {"in": 0, "out": 0}

    # --- optional checkpoint hooks -------------------------------------------------------
    def reset(self) -> None:
        self._memory = []

    def snapshot_state(self) -> dict:
        return {"memory": list(self._memory)}

    def restore_state(self, state: dict) -> None:
        self._memory = list(state.get("memory", []))

    def agent_state(self) -> dict:
        return {"memory": list(self._memory)}

    def load_agent_state(self, state: dict) -> None:
        self._memory = list(state.get("memory", []))

    def describe(self) -> dict:
        return {"policy": self.name, "mistake_at": self.mistake_at}

    # --- the agent contract ----------------------------------------------------------------
    async def act(self, obs: Any) -> tuple[Optional[Action], dict]:
        target = int(re.search(r"Set counter A to (-?\d+)", obs.instruction).group(1))
        facts = [m for m in self._memory if m.startswith("A=")]
        a = int(facts[-1][2:]) if facts else self.start_value
        if self.mistake_at is not None and obs.step == self.mistake_at:
            fact = f"A={a}"
            self._memory.append(fact)
            return Action.parse(B_PLUS), {"raw_action": f"Pressing a button.\nMemory: {fact}\n{B_PLUS}",
                                          "memory_written": [fact]}
        if a == target:
            return Action.parse("done()"), {"raw_action": "Counter A is at the target.\ndone()", "memory_written": []}
        act, new = (A_PLUS, a + 1) if target > a else (A_MINUS, a - 1)
        fact = f"A={new}"
        self._memory.append(fact)
        return Action.parse(act), {"raw_action": f"Moving counter A.\nMemory: {fact}\n{act}", "memory_written": [fact]}


def student(**kw: Any) -> ToyCounterAgent:
    return ToyCounterAgent(mistake_at=1, name="toy-student", **kw)


def teacher(**kw: Any) -> ToyCounterAgent:
    return ToyCounterAgent(name="toy-teacher", **kw)
