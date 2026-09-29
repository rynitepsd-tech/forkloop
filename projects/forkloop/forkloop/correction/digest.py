"""World-state digests: what a checkpoint claims the world looked like, and how to check a restore.

A digest has two parts:

* **tables** — for every checksummed table, the sha256 of its ``{primary key: row hash}`` map,
  computed through the controller channel with the world's ignored and volatile (wall-clock)
  columns left out. Equal digests mean the persisted application state is the same row for row.
* **screen** — a 64×36 grayscale thumbnail of the screenshot with the desktop's top panel (the
  clock) masked. Screens are compared by the fraction of thumbnail cells that differ by more
  than a tolerance; this catches a different page, dialog or tab, not a blinking caret.

A digest is evidence about persisted state and the visible screen. It is not proof that every
byte of a running process (browser memory, unsaved form text scrolled out of view) is identical.
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from PIL import Image

THUMB = (64, 36)
#: Rows of the 1280x720 screen covered by the desktop panel (clock) are masked before hashing.
PANEL_PX = 28
#: A thumbnail cell "differs" beyond this absolute grayscale difference (0-255).
CELL_TOLERANCE = 24


@dataclass
class WorldDigest:
    tables: dict[str, str] = field(default_factory=dict)       # "db.table" -> sha256
    rows: dict[str, int] = field(default_factory=dict)         # "db.table" -> row count
    screen_thumb: str = ""                                     # hex of THUMB grayscale bytes
    screen_sha256: str = ""                                    # of the exact PNG, for provenance

    def to_dict(self) -> dict[str, Any]:
        return {"tables": dict(self.tables), "rows": dict(self.rows), "screen_thumb": self.screen_thumb,
                "screen_sha256": self.screen_sha256}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "WorldDigest":
        return WorldDigest(tables=dict(d.get("tables", {})), rows=dict(d.get("rows", {})),
                           screen_thumb=d.get("screen_thumb", ""), screen_sha256=d.get("screen_sha256", ""))


def screen_thumb(png: bytes, *, panel_px: int = PANEL_PX) -> str:
    if not png:
        return ""
    im = Image.open(io.BytesIO(png)).convert("L")
    if panel_px:
        cut = int(round(panel_px * im.height / 720))
        im = im.crop((0, cut, im.width, im.height))
    return im.resize(THUMB, Image.BILINEAR).tobytes().hex()


def screen_distance(a: str, b: str) -> float:
    """Fraction of thumbnail cells differing by more than CELL_TOLERANCE (1.0 if either is missing)."""
    if not a or not b or len(a) != len(b):
        return 1.0
    x, y = bytes.fromhex(a), bytes.fromhex(b)
    diff = sum(1 for p, q in zip(x, y) if abs(p - q) > CELL_TOLERANCE)
    return diff / len(x)


async def world_digest(world: Any, dbs: dict[str, Any], screenshot: bytes) -> WorldDigest:
    d = WorldDigest(screen_thumb=screen_thumb(screenshot),
                    screen_sha256=hashlib.sha256(screenshot).hexdigest() if screenshot else "")
    volatile = world.volatile_columns() if hasattr(world, "volatile_columns") else world.ignore_columns()
    pks = world.primary_keys()
    for db_name, tables in world.checksum_tables().items():
        hashes = await dbs[db_name].row_hashes(tables, pks, volatile.get(db_name))
        for t in tables:
            rows = hashes.get(t, {})
            d.tables[f"{db_name}.{t}"] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            d.rows[f"{db_name}.{t}"] = len(rows)
    return d


@dataclass
class Fidelity:
    ok: bool
    tables_equal: bool
    differing_tables: list[str]
    screen_distance: float
    screen_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "tables_equal": self.tables_equal, "differing_tables": self.differing_tables,
                "screen_distance": round(self.screen_distance, 4), "screen_ok": self.screen_ok}


def compare(expected: WorldDigest, actual: WorldDigest, *, max_screen_distance: float = 0.05) -> Fidelity:
    differing = sorted(k for k in set(expected.tables) | set(actual.tables)
                       if expected.tables.get(k) != actual.tables.get(k))
    dist = screen_distance(expected.screen_thumb, actual.screen_thumb) if expected.screen_thumb else 0.0
    screen_ok = dist <= max_screen_distance
    return Fidelity(ok=not differing and screen_ok, tables_equal=not differing, differing_tables=differing,
                    screen_distance=dist, screen_ok=screen_ok)


__all__ = ["WorldDigest", "world_digest", "compare", "Fidelity", "screen_thumb", "screen_distance"]
