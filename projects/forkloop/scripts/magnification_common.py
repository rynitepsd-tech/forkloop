"""Shared, label-free helpers for the document-magnification diagnostic.

Everything here operates on screen geometry that is fixed by the application UI
(Chrome's built-in PDF viewer toolbar inside OpenEMR's document panel) and on the
fixed letter template (``worlds/claims_ops_v1/tasks/common.py::authorization_letter``).
No function receives or infers the expected authorization value.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from forkloop.actions import Action

TOOLBAR_RGB = (60, 60, 60)          # Chrome PDF viewer toolbar band
READOUT_RGB = (30, 30, 30)          # zoom readout / page-number input boxes
TOOLBAR_X = (340, 1230)             # viewer toolbar horizontal extent on a 1280x720 desktop
PAGE_FIELD_DX = 595                 # page-number input x
ZOOM_FIELD_DX = 720                 # zoom readout input x
PANE_X = (645, 1210)                # PDF page canvas x range (right of the thumbnail rail, left of the scrollbar)
NEUTRAL_CLICK = (85, 353)           # 'Documents' heading of the Documents page at top-of-scroll (moves focus out of the plugin)
OUTER_SCROLL_AT = (1195, 680)       # where the teacher issued its outer Page_Down scroll
PANE_CLICK = (930, 600)             # inside the PDF canvas for every observed toolbar position (toolbar y <= 523)
TARGET_ZOOM = "150"


def load_rgb(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)


def detect_pdf_toolbar(png: bytes | np.ndarray, *, min_rows: int = 40, tol: int = 8) -> int | None:
    """Return the vertical centre of the PDF viewer toolbar band, or None when no viewer is visible.

    A band is >= ``min_rows`` consecutive rows in which >= 60% of the pixels across TOOLBAR_X
    match TOOLBAR_RGB (the icon rows dilute the band), below the desktop panel (y > 60) and with
    the dark readout box present at the band centre.
    """
    a = png if isinstance(png, np.ndarray) else load_rgb(png)
    band = a[:, TOOLBAR_X[0]:TOOLBAR_X[1], :]
    dark = (np.abs(band - np.array(TOOLBAR_RGB)).max(axis=2) <= tol).mean(axis=1)
    start = None
    for y in range(60, a.shape[0] + 1):
        on = y < a.shape[0] and dark[y] >= 0.6
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= min_rows:
                centre = (start + y - 1) // 2
                box = a[centre - 6:centre + 7, ZOOM_FIELD_DX - 18:ZOOM_FIELD_DX + 19]
                if (np.abs(box - np.array(READOUT_RGB)).max(axis=2) <= tol).mean() >= 0.5:
                    return centre
            start = None
    return None


def pane_region(toolbar_y: int) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the PDF page canvas below a toolbar centred at ``toolbar_y``."""
    return PANE_X[0], toolbar_y + 32, PANE_X[1], 662


@dataclass
class TextMetrics:
    rows: int
    median_pitch_px: float | None
    median_row_height_px: float | None
    ink_fraction: float


def measure_text(png: bytes | np.ndarray, toolbar_y: int) -> TextMetrics:
    """Line pitch / row height of dark text inside the PDF canvas (label-free size evidence)."""
    a = png if isinstance(png, np.ndarray) else load_rgb(png)
    x0, y0, x1, y1 = pane_region(toolbar_y)
    reg = a[y0:y1, x0:x1, :]
    ink = (reg.max(axis=2) < 110)
    rows_ink = ink.mean(axis=1)
    on = rows_ink > 0.004
    runs, start = [], None
    for i, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    runs = [r for r in runs if r[1] - r[0] >= 3]
    centres = [(r[0] + r[1]) / 2 for r in runs]
    pitches = [b - a_ for a_, b in zip(centres, centres[1:])]
    heights = [r[1] - r[0] + 1 for r in runs]
    return TextMetrics(rows=len(runs),
                       median_pitch_px=float(np.median([p for p in pitches if p <= 60])) if pitches else None,
                       median_row_height_px=float(np.median(heights)) if heights else None,
                       ink_fraction=float(ink.mean()))


def zoom_actions(toolbar_y: int, zoom: str = TARGET_ZOOM) -> list[Action]:
    """Set the viewer zoom through its visible readout input: click, select all, type, Enter."""
    return [Action.click(ZOOM_FIELD_DX, toolbar_y), Action.key("ctrl+a"), Action.type_text(zoom), Action.key("Return")]


def page_actions(toolbar_y: int, page: int) -> list[Action]:
    """Go to ``page`` through the visible page-number input."""
    return [Action.click(PAGE_FIELD_DX, toolbar_y), Action.key("ctrl+a"), Action.type_text(str(page)), Action.key("Return")]


def outer_scroll_actions(direction: str = "down") -> list[Action]:
    """Move focus out of the plugin, then page the outer Documents page (teacher's own recipe: amount 5 -> 2 keypresses)."""
    return [Action.click(*NEUTRAL_CLICK), Action.scroll(*OUTER_SCROLL_AT, direction, 5)]


def inner_position_actions(left: int, down: int, right: int = 0) -> list[Action]:
    """Focus the PDF canvas, then Left x N (to the left edge), Right x R (fixed offset), Down x K."""
    acts = [Action.click(*PANE_CLICK)]
    acts += [Action.key("Left")] * left
    acts += [Action.key("Right")] * right
    acts += [Action.key("Down")] * down
    return acts


__all__ = [n for n in dir() if not n.startswith("_")]
