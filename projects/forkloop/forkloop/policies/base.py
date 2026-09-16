"""Policy protocol. A policy sees an :class:`Observation` and returns an
:class:`Action` (or ``None`` when it could not produce a valid one) plus a
metadata dict: ``raw_action``, ``model_latency_s``, ``tokens``, ``note``,
``confidence`` (0..1, optional; used by search to decide where to branch).

A truthy ``error`` is reserved for provider/runtime failures that prevent a valid
measurement. Invalid model actions return ``None`` with a diagnostic ``note``;
policy give-ups return a terminal action. Both remain scored policy behavior.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable
import copy

from ..actions import Action
from ..types import Observation

PolicyResult = tuple[Optional[Action], dict[str, Any]]


class BranchablePolicy:
    """Explicit local state cloning; clients and monotonic usage are shared.

    Subclasses list only policy-local decision state. Restoring a checkpoint
    never refunds calls. Branch clones do not own or close the network client.
    """
    branch_state_fields: tuple[str, ...] = ()

    def snapshot_state(self) -> dict:
        return copy.deepcopy({k: getattr(self, k) for k in self.branch_state_fields})

    def restore_state(self, state: dict) -> None:
        if set(state) != set(self.branch_state_fields):
            raise ValueError("policy state does not match its declared fields")
        for k, v in copy.deepcopy(state).items():
            setattr(self, k, v)

    def clone_for_branch(self, state: dict | None = None):
        clone = copy.copy(self)  # config/client references intentionally shared
        clone.restore_state(self.snapshot_state() if state is None else state)
        clone._owns_client = False
        return clone


def require_branchable(policy: Policy) -> None:
    if not all(callable(getattr(policy, name, None)) for name in ("snapshot_state", "restore_state", "clone_for_branch")):
        raise TypeError("search requires explicit snapshot_state, restore_state and clone_for_branch; use single rollout")


@runtime_checkable
class Policy(Protocol):
    name: str

    async def act(self, obs: Observation) -> PolicyResult: ...


class ProposingPolicy(Policy, Protocol):
    async def propose(self, obs: Observation, n: int) -> list[PolicyResult]: ...


async def propose_or_repeat(policy: Policy, obs: Observation, n: int) -> list[PolicyResult]:
    """Use ``propose`` when the policy has it, else call ``act`` n times."""
    fn = getattr(policy, "propose", None)
    if fn is not None:
        return await fn(obs, n)
    out = []
    for _ in range(n):
        out.append(await policy.act(obs))
    return out


__all__ = ["Policy", "ProposingPolicy", "PolicyResult", "Observation", "propose_or_repeat"]
