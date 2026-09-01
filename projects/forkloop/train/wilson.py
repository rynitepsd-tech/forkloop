"""Wilson score interval for binomial proportions (used by eval and plot).

The Wilson interval is preferred over the normal approximation because it
behaves sensibly for small n and for rates near 0 or 1 (it never leaves [0, 1]).
"""

from __future__ import annotations

import math

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Return ``(low, high)`` of the Wilson score interval for ``successes / n``.

    ``n == 0`` returns ``(0.0, 1.0)`` (no information).
    """
    if n <= 0:
        return 0.0, 1.0
    k = max(0, min(int(successes), int(n)))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    low = max(0.0, centre - half)
    high = min(1.0, centre + half)
    return low, high


def wilson_summary(successes: int, n: int, z: float = Z_95) -> dict:
    """``{"k", "n", "rate", "low", "high"}`` — the shape written into eval_summary.json."""
    low, high = wilson_interval(successes, n, z)
    rate = (successes / n) if n > 0 else 0.0
    return {"k": int(successes), "n": int(n), "rate": rate, "low": low, "high": high}


def format_rate(successes: int, n: int, z: float = Z_95) -> str:
    """Human-readable ``"62.0% [45.1, 76.5] (k=31/50)"``."""
    s = wilson_summary(successes, n, z)
    return f"{100 * s['rate']:.1f}% [{100 * s['low']:.1f}, {100 * s['high']:.1f}] (k={s['k']}/{s['n']})"


__all__ = ["Z_95", "wilson_interval", "wilson_summary", "format_rate"]
