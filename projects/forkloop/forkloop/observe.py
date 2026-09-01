"""Screenshot capture helpers: hashing, stability wait, resizing."""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .backends.base import Machine


class ScreenNotStable(RuntimeError):
    pass


def png_hash(png: bytes) -> str:
    return hashlib.sha1(png).hexdigest()


def image_size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        return im.size


def resize_png(png: bytes, max_side: int) -> tuple[bytes, float]:
    """Downscale so the longer side is ``max_side``; returns (png, scale)."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        w, h = im.size
        longest = max(w, h)
        if longest <= max_side:
            return png, 1.0
        scale = max_side / longest
        im2 = im.convert("RGB").resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im2.save(buf, format="PNG")
        return buf.getvalue(), scale


async def wait_stable(machine: "Machine", *, timeout_s: float = 15.0, interval_s: float = 0.4,
                      required: int = 2) -> tuple[bytes, int]:
    """Return the first screenshot that repeats ``required`` times consecutively.

    Also returns the number of screenshots taken. Raises :class:`ScreenNotStable`.
    """
    t0 = time.monotonic()
    last_hash: Optional[str] = None
    streak = 0
    n = 0
    shot = b""
    while time.monotonic() - t0 < timeout_s:
        shot = await machine.screenshot()
        n += 1
        h = png_hash(shot)
        if h == last_hash:
            streak += 1
            if streak >= required - 1:
                return shot, n
        else:
            streak = 0
            last_hash = h
        await asyncio.sleep(interval_s)
    raise ScreenNotStable(f"screen did not settle within {timeout_s}s ({n} shots)")


__all__ = ["png_hash", "image_size", "resize_png", "wait_stable", "ScreenNotStable"]
