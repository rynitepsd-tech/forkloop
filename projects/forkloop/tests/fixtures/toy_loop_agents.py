"""Toy agents for a CLI smoke test of the loop on the toy world (fake backend)."""
from forkloop.actions import Action
from forkloop.policies.base import BranchablePolicy
import re

A_PLUS, A_MINUS, B_PLUS = "click(220, 200)", "click(100, 200)", "click(540, 200)"

class ToyAgent(BranchablePolicy):
    branch_state_fields = ("_memory",)
    def __init__(self, mistake_at=None, name="toy"):
        self.mistake_at, self.name, self._memory, self.usage = mistake_at, name, [], {"in": 0, "out": 0}
    def reset(self): self._memory = []
    def agent_state(self): return {"memory": list(self._memory)}
    def load_agent_state(self, s): self._memory = list(s.get("memory", []))
    def describe(self): return {"policy": self.name, "mistake_at": self.mistake_at}
    async def act(self, obs):
        target = int(re.search(r"Set counter A to (-?\d+)", obs.instruction).group(1))
        facts = [m for m in self._memory if m.startswith("A=")]
        # the toy agent cannot read the screen; it starts from its memory or asks via a first "look" fact
        a = int(facts[-1][2:]) if facts else None
        if a is None:
            self._memory.append("A=unknown")
        if a is None:
            # without knowing A it guesses 0..3 range start by pressing nothing: done() fails -> use memory seed
            a = 0
        if self.mistake_at is not None and obs.step == self.mistake_at:
            fact = f"A={a}"; self._memory.append(fact)
            return Action.parse(B_PLUS), {"raw_action": f"oops\nMemory: {fact}\n{B_PLUS}", "memory_written": [fact]}
        if a == target:
            return Action.parse("done()"), {"raw_action": "done()", "memory_written": []}
        act, new = (A_PLUS, a + 1) if target > a else (A_MINUS, a - 1)
        fact = f"A={new}"; self._memory.append(fact)
        return Action.parse(act), {"raw_action": f"move\nMemory: {fact}\n{act}", "memory_written": [fact]}

def student(): return ToyAgent(mistake_at=1, name="toy-student")
def teacher(): return ToyAgent(name="toy-teacher")
