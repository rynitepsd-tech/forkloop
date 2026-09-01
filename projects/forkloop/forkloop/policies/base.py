"""Policy protocol. A policy sees an :class:`Observation` and returns an
:class:`Action` (or ``None`` when it could not produce a valid one) plus a
metadata dict: ``raw_action``, ``model_latency_s``, ``tokens``, ``note``,
``confidence`` (0..1, optional; used by search to decide where to branch)."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from ..actions import Action
from ..types import Observation

PolicyResult = tuple[Optional[Action], dict[str, Any]]


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
