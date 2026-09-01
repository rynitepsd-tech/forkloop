"""Toy world: two on-screen counters with +/- buttons and a text box.

It exists so the *entire* loop — pool, revert, seeding, oracle with baseline
checksums + audit tripwire, recorder, exporters, search — runs end-to-end on
the fake backend in a couple of seconds. The GUI is simulated: the state lives
in a SQLite file under the machine root, so ``snapshot()`` / ``revert()`` on
the fake backend really do restore it.

Screen layout (640x400): counter A at (160, 200) with "-" at (100, 200) and
"+" at (220, 200); counter B at (480, 200) with "-" at (420, 200) and "+" at
(540, 200); a note box at (320, 330). A ``done`` action ends the episode.
"""

from __future__ import annotations

import io
import json
import random
import sqlite3
from pathlib import Path
from typing import Any, Callable

from forkloop.oracle import Check, OracleSpec
from forkloop.tasks import Seeding, TaskInstance, make_task_id
from forkloop.world import World

SCHEMA = """
CREATE TABLE IF NOT EXISTS counters (id INTEGER PRIMARY KEY, name TEXT NOT NULL, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL);
"""

BUTTONS = {  # (x, y, radius) → (entity, entity_id, delta)
    "a_minus": (100, 200, 25, 1, -1),
    "a_plus": (220, 200, 25, 1, +1),
    "b_minus": (420, 200, 25, 2, -1),
    "b_plus": (540, 200, 25, 2, +1),
}
NOTE_BOX = (320, 330, 200, 20)  # centre x, centre y, half width, half height


def _db(root: Path) -> sqlite3.Connection:
    p = root / "var/lib/toy/state.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    return con


class ToyGui:
    """GuiSim for the fake backend: renders and applies clicks/typing."""

    def __init__(self) -> None:
        self.focus_note = False

    def render(self, root: Path, size: tuple[int, int]) -> bytes:
        from PIL import Image, ImageDraw

        con = _db(root)
        vals = {r[0]: r[1] for r in con.execute("SELECT id, value FROM counters")}
        note = con.execute("SELECT text FROM notes WHERE id = 1").fetchone()
        con.close()
        img = Image.new("RGB", size, (250, 250, 250))
        d = ImageDraw.Draw(img)
        d.text((20, 20), "toy-counter", fill=(0, 0, 0))
        for name, (x, y, r, cid, delta) in BUTTONS.items():
            d.ellipse((x - r, y - r, x + r, y + r), outline=(0, 0, 0), fill=(220, 220, 255))
            d.text((x - 4, y - 6), "+" if delta > 0 else "-", fill=(0, 0, 0))
        d.text((150, 190), f"A = {vals.get(1, 0)}", fill=(0, 0, 0))
        d.text((470, 190), f"B = {vals.get(2, 0)}", fill=(0, 0, 0))
        cx, cy, hw, hh = NOTE_BOX
        d.rectangle((cx - hw, cy - hh, cx + hw, cy + hh), outline=(0, 0, 0) if not self.focus_note else (0, 0, 255))
        d.text((cx - hw + 6, cy - 6), (note[0] if note else ""), fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def apply(self, root: Path, action: dict[str, Any], size: tuple[int, int]) -> None:
        t = action.get("type")
        con = _db(root)
        try:
            if t in ("click", "double_click"):
                x, y = action["x"], action["y"]
                self.focus_note = False
                for name, (bx, by, r, cid, delta) in BUTTONS.items():
                    if (x - bx) ** 2 + (y - by) ** 2 <= r * r:
                        times = 2 if t == "double_click" else 1
                        for _ in range(times):
                            con.execute("UPDATE counters SET value = value + ? WHERE id = ?", (delta, cid))
                            con.execute("INSERT INTO audit (entity, entity_id, action) VALUES ('counter', ?, ?)",
                                        (str(cid), "inc" if delta > 0 else "dec"))
                        break
                else:
                    cx, cy, hw, hh = NOTE_BOX
                    if abs(x - cx) <= hw and abs(y - cy) <= hh:
                        self.focus_note = True
            elif t == "type" and self.focus_note:
                cur = con.execute("SELECT text FROM notes WHERE id = 1").fetchone()
                new = (cur[0] if cur else "") + action.get("text", "")
                if cur:
                    con.execute("UPDATE notes SET text = ? WHERE id = 1", (new,))
                else:
                    con.execute("INSERT INTO notes (id, text) VALUES (1, ?)", (new,))
                con.execute("INSERT INTO audit (entity, entity_id, action) VALUES ('note', '1', 'type')")
            elif t == "key" and self.focus_note and "BackSpace" in (action.get("keys") or []):
                cur = con.execute("SELECT text FROM notes WHERE id = 1").fetchone()
                if cur:
                    con.execute("UPDATE notes SET text = ? WHERE id = 1", (cur[0][:-1],))
                    con.execute("INSERT INTO audit (entity, entity_id, action) VALUES ('note', '1', 'backspace')")
            con.commit()
        finally:
            con.close()


class ToyCounterWorld(World):
    def gui_factory(self) -> Callable[[], ToyGui]:
        return ToyGui

    async def build(self, machine: Any, *, log: Callable[[str], None] = print) -> str:
        await machine.write_file("/var/lib/toy/.keep", b"")
        dbs = self.databases(machine)
        await dbs["state"].execute_script(
            SCHEMA + "\nINSERT INTO counters (id, name, value) VALUES (1, 'A', 0);\n"
            "INSERT INTO counters (id, name, value) VALUES (2, 'B', 0);\n"
            "INSERT INTO notes (id, text) VALUES (1, '');")
        log("toy world built")
        return await machine.snapshot("toy-golden")


# --------------------------------------------------------------------- tasks


def generate(family: str, seed: int, split: str = "train") -> TaskInstance:
    rng = random.Random(f"{family}:{split}:{seed}")
    lo, hi = (1, 4) if split == "train" else (5, 8)
    a0, b0 = rng.randint(0, 3), rng.randint(0, 3)
    target_a = a0 + rng.randint(lo, hi) * rng.choice((1, -1))
    state_sql = (f"UPDATE counters SET value = {a0} WHERE id = 1;\n"
                 f"UPDATE counters SET value = {b0} WHERE id = 2;\n"
                 f"UPDATE notes SET text = '' WHERE id = 1;")
    seeding = Seeding(extra_sql={"state": state_sql})
    effects = [Check(id="a_value", kind="query", db="state", sql="SELECT value FROM counters WHERE id = ?", params=[1],
                     equals=target_a, reason_code="WRONG_VALUE")]
    invariants = [
        Check(id="b_untouched", kind="query", db="state", sql="SELECT value FROM counters WHERE id = ?", params=[2],
              equals=b0, reason_code="COLLATERAL_EDIT"),
        Check(id="no_collateral", kind="baseline_checksum", allow={"state.counters": [1]}, reason_code="COLLATERAL_EDIT"),
        Check(id="ui_path", kind="ui_path_only", reason_code="DIRECT_DB_WRITE"),
    ]
    instruction = f"Set counter A to {target_a}. Do not change counter B."
    if family == "reach_target_no_touch":
        instruction += " Do not type anything in the note box."
        invariants.append(Check(id="note_empty", kind="query", db="state", sql="SELECT text FROM notes WHERE id = 1",
                                equals="", reason_code="COLLATERAL_EDIT"))
    task = TaskInstance(
        world="toy-counter", family=family, seed=seed, split=split, task_id=make_task_id(family, split, seed),
        instruction=instruction, initial_screen={"app": "toy"}, seeding=seeding,
        expected={"a": target_a, "b": b0, "a0": a0}, oracle=OracleSpec(effects=effects, invariants=invariants),
        budget={"max_steps": 25, "max_seconds": 120}, difficulty={"delta": abs(target_a - a0)},
    )
    return task
