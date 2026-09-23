"""A minimal custom agent for ``forkloop compare`` (see configs/custom-agent.yaml).

Replace ``decide`` with a call to your own model. The contract is small:

* ``make_agent(**options)`` returns a fresh agent; ``compare`` calls it once per cell,
  so no state leaks between seeds or arms.
* ``await agent.act(observation)`` returns ``(action, metadata)``:
  - ``observation`` has ``screenshot`` / ``previous_screenshot`` (PNG bytes), ``instruction``,
    ``step``, ``history`` (compact strings of the actions already taken), ``width``, ``height``.
    Nothing else crosses the agent channel: no expected values, SQL, seeding or oracle.
  - ``action`` is a ``forkloop.actions.Action`` (``Action.parse('click(640, 90)')``,
    ``Action.done()``, ...), or ``None`` for an invalid reply (it consumes the invalid-action
    budget and stays scored).
  - ``metadata`` may carry ``raw_action`` (your model's text), ``note`` and ``tokens``
    (``{"in": .., "out": ..}``, cumulative). Set ``error`` only for provider/runtime failures
    that prevent measurement; those cells are left unscored instead of counted as failures.
* Optional ``reset()`` and ``aclose()`` are awaited around each cell.
"""
from __future__ import annotations

from typing import Any, Optional

from forkloop.actions import Action
from forkloop.types import Observation


class ExampleAgent:
    name = "example-agent"

    def __init__(self, *, plan: list[str], give_up_after: int = 20) -> None:
        self.plan = list(plan)
        self.give_up_after = give_up_after
        self.usage = {"in": 0, "out": 0}

    def decide(self, obs: Observation) -> str:
        """Your model goes here: look at obs.screenshot and obs.instruction, return one action."""
        if obs.step < len(self.plan):
            return self.plan[obs.step]
        return "done(success=false, note=\"plan exhausted\")"

    async def act(self, obs: Observation) -> tuple[Optional[Action], dict[str, Any]]:
        if obs.step >= self.give_up_after:
            return Action.done(success=False, note="step limit"), {"raw_action": "done(success=false)"}
        text = self.decide(obs)
        try:
            action = Action.parse(text, width=obs.width, height=obs.height)
        except ValueError as exc:
            return None, {"raw_action": text, "note": f"invalid action: {exc}"}
        return action, {"raw_action": text, "tokens": dict(self.usage)}

    async def aclose(self) -> None:
        pass


def make_agent(**options: Any) -> ExampleAgent:
    return ExampleAgent(**options)
