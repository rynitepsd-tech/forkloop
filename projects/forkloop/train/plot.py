"""Headless matplotlib charts for the Forkloop write-up.

* :func:`chart1` — learning curve: success rate on held-out seeds (y, %) against
  the number of verified teacher trajectories used for SFT (x: 0/25/50/100/200/all),
  one line per method (base, base+best-of-N, SFT, SFT+best-of-N) with Wilson 95%
  CI error bars.
* :func:`chart2` — reset benchmark: grouped bars of p50/p95/p99 reset latency per
  method (seconds) with the failure rate annotated above each group and a table
  underneath carrying n, the percentiles, failure rate and cost per 1k resets.

Input JSON shapes (either a path, a JSON string, or a dict)::

    curve = {"title": "...", "x": [0, 25, 50, 100, 200, "all"], "n_per_point": 150,
             "series": {"base":            {"rate": [0.18], "low": [0.12], "high": [0.25]},   # len 1 = flat line
                        "base+best-of-4":  {"rate": [...6 values...], "low": [...], "high": [...]},
                        "SFT":             {...}, "SFT+best-of-4": {...}},
             "synthetic": false}
    bench = {"title": "...", "methods": [
             {"name": "revert()", "n": 500, "p50_s": 1.4, "p95_s": 2.3, "p99_s": 3.9,
              "failure_rate": 0.004, "cost_per_1k_usd": 0.9}, ...], "synthetic": false}

``python -m train.plot --demo`` renders both charts from clearly labelled
SYNTHETIC placeholder data into ``train/examples/`` so the pipeline can be
checked before real results exist.

Style: Agg backend, dark ink on a light surface, 2px lines, >= 8px markers with a
surface ring, hairline gridlines, every axis labelled with units. Categorical
colours are the first four slots of a validated colour-blind-safe order; the
percentile bars use one blue ramp (light -> dark = p50 -> p99) because the
percentiles are ordered magnitudes, not identities.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import gridspec  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from train.wilson import wilson_interval  # noqa: E402

INK = {
    "surface": "#fcfcfb", "page": "#f9f9f7", "primary": "#0b0b0b", "secondary": "#52514e",
    "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
}
#: Categorical slots 1-4 (blue, orange, aqua, yellow) — validated adjacent order, light mode.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SERIES_ORDER = ["base", "base+best-of-n", "sft", "sft+best-of-n"]
#: One-hue ordinal ramp for p50 < p95 < p99 (blue steps 250 / 450 / 650).
PERCENTILE_COLORS = {"p50": "#86b6ef", "p95": "#2a78d6", "p99": "#104281"}
DEFAULT_EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
FONT = ["system-ui", "-apple-system", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans", "sans-serif"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _load(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, Path) or (isinstance(obj, str) and not obj.lstrip().startswith("{")):
        return json.loads(Path(obj).read_text(encoding="utf-8"))
    return json.loads(str(obj))


def _style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": FONT, "font.size": 10,
        "axes.facecolor": INK["surface"], "figure.facecolor": INK["surface"], "savefig.facecolor": INK["surface"],
        "axes.edgecolor": INK["axis"], "axes.labelcolor": INK["secondary"], "axes.titlecolor": INK["primary"],
        "xtick.color": INK["muted"], "ytick.color": INK["muted"], "text.color": INK["primary"],
        "axes.grid": False, "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 1.0, "xtick.major.size": 0, "ytick.major.size": 0,
        "legend.frameon": False, "legend.fontsize": 9,
    })


def _recessive_axes(ax) -> None:
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color=INK["grid"], linewidth=1.0, linestyle="-")
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK["axis"])
    ax.tick_params(axis="both", colors=INK["muted"], labelsize=9)


def _series_color(name: str, taken: set[int]) -> tuple[str, int]:
    """Colour follows the entity: known method names get fixed slots, others take the next free slot."""
    key = name.strip().lower().replace(" ", "")
    for i, pattern in enumerate(SERIES_ORDER):
        if key == pattern or key.replace("-", "") == pattern.replace("-", "") or \
                (pattern.endswith("best-of-n") and key.startswith(pattern[:-1]) and "best" in key):
            if i not in taken:
                return SERIES_COLORS[i], i
    for i in range(len(SERIES_COLORS)):
        if i not in taken:
            return SERIES_COLORS[i], i
    return SERIES_COLORS[-1], len(SERIES_COLORS) - 1


def _title(fig, title: str, subtitle: str | None) -> None:
    fig.text(0.06, 0.965, title, fontsize=13, fontweight="bold", color=INK["primary"], ha="left", va="top")
    if subtitle:
        fig.text(0.06, 0.925, subtitle, fontsize=9.5, color=INK["secondary"], ha="left", va="top")


# --------------------------------------------------------------------------- #
# chart 1 — learning curve
# --------------------------------------------------------------------------- #

def chart1(curve_json: Any, out_png: str | Path, *, dpi: int = 150) -> Path:
    """Learning curve with Wilson-CI error bars. Returns the written path."""
    data = _load(curve_json)
    _style()
    xs = list(data.get("x") or [0, 25, 50, 100, 200, "all"])
    n_pts = len(xs)
    pos = list(range(n_pts))
    series: dict[str, dict] = data.get("series") or {}
    title = str(data.get("title") or "Success rate vs verified trajectories")
    n_per_point = data.get("n_per_point")
    sub = [f"Held-out seeds; error bars are Wilson 95% CIs", ]
    if n_per_point:
        sub.append(f"n = {n_per_point} episodes per point")
    if data.get("synthetic"):
        sub.append("SYNTHETIC PLACEHOLDER DATA")
    subtitle = " · ".join(sub)

    fig = plt.figure(figsize=(9.2, 5.6), dpi=dpi)
    ax = fig.add_axes([0.085, 0.14, 0.72, 0.70])
    _recessive_axes(ax)
    taken: set[int] = set()
    ends: list[tuple[float, str, str]] = []
    for name, s in series.items():
        rate = list(s.get("rate") or [])
        low = list(s.get("low") or rate)
        high = list(s.get("high") or rate)
        if not rate:
            continue
        if len(rate) == 1:  # a method that does not depend on the data count: flat line
            rate, low, high = rate * n_pts, low * n_pts, high * n_pts
        color, slot = _series_color(name, taken)
        taken.add(slot)
        px, py, lo, hi = [], [], [], []
        for i in range(min(n_pts, len(rate))):
            if rate[i] is None:
                continue
            px.append(pos[i])
            py.append(100.0 * float(rate[i]))
            lo.append(100.0 * max(0.0, float(rate[i]) - float(low[i] if low[i] is not None else rate[i])))
            hi.append(100.0 * max(0.0, float(high[i] if high[i] is not None else rate[i]) - float(rate[i])))
        if not px:
            continue
        flat = len(set(py)) == 1
        ax.errorbar(px, py, yerr=[lo, hi], color=color, ecolor=color, elinewidth=1.2, capsize=3, capthick=1.2,
                    linewidth=2.0, linestyle=(0, (4, 3)) if flat else "-", marker="o", markersize=7,
                    markerfacecolor=color, markeredgecolor=INK["surface"], markeredgewidth=2, label=name,
                    solid_joinstyle="round", solid_capstyle="round", zorder=3)
        ends.append((py[-1], name, color))

    # Selective direct labels: the end value of every series, pushed apart if they collide.
    ends.sort(key=lambda t: t[0])
    placed: list[float] = []
    for y, name, color in ends:
        yy = y
        if placed and yy - placed[-1] < 4.5:
            yy = placed[-1] + 4.5
        placed.append(yy)
        ax.annotate(f"{y:.0f}%", xy=(pos[-1], y), xytext=(pos[-1] + 0.12, yy), textcoords="data",
                    fontsize=9, color=INK["secondary"], va="center", ha="left",
                    arrowprops=dict(arrowstyle="-", color=INK["axis"], linewidth=0.8, shrinkA=0, shrinkB=4)
                    if abs(yy - y) > 0.5 else None, zorder=4)

    ax.set_xticks(pos)
    ax.set_xticklabels([str(x) for x in xs])
    ax.set_xlim(-0.35, n_pts - 1 + 0.6)
    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 20))
    ax.set_yticklabels([f"{v}%" for v in range(0, 101, 20)])
    ax.set_xlabel("Verified teacher trajectories used for SFT (count of episodes; \"all\" = every verified episode)")
    ax.set_ylabel("Task success rate on held-out seeds (%)")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), title="Method", title_fontsize=9,
              handlelength=2.2, borderaxespad=0.0)
    _title(fig, title, subtitle)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    return out


# --------------------------------------------------------------------------- #
# chart 2 — reset benchmark
# --------------------------------------------------------------------------- #

def chart2(bench_json: Any, out_png: str | Path, *, dpi: int = 150) -> Path:
    """Grouped p50/p95/p99 reset-latency bars + failure-rate annotations + cost table."""
    data = _load(bench_json)
    _style()
    methods = list(data.get("methods") or [])
    if not methods:
        raise ValueError("bench JSON has no 'methods'")
    title = str(data.get("title") or "Reset latency by method")
    sub = ["Bars: latency percentiles per reset; text above each group: failure rate"]
    if data.get("synthetic"):
        sub.append("SYNTHETIC PLACEHOLDER DATA")
    subtitle = " · ".join(sub)

    n = len(methods)
    fig = plt.figure(figsize=(9.2, 7.0), dpi=dpi)
    gs = gridspec.GridSpec(2, 1, height_ratios=[3.2, 1.0 + 0.18 * n], left=0.09, right=0.97, top=0.86, bottom=0.04, hspace=0.35)
    ax = fig.add_subplot(gs[0])
    tax = fig.add_subplot(gs[1])
    _recessive_axes(ax)

    # Bar geometry: cap each bar at ~24px, 2px surface gaps between the three bars of a group.
    fig.canvas.draw()
    ax_px = ax.get_window_extent().width
    units_per_px = (n + 0.0) / max(1.0, ax_px)  # x-axis spans n units (one per method)
    bar_w = min(0.2, 24 * units_per_px)
    gap = 2 * units_per_px
    keys = [("p50", "p50_s"), ("p95", "p95_s"), ("p99", "p99_s")]
    offsets = [(-1, 0, 1)[i] * (bar_w + gap) for i in range(3)]
    centers = [i + 0.5 for i in range(n)]
    ymax = 0.0
    for (label, key), off in zip(keys, offsets):
        vals = [float(m.get(key) or 0.0) for m in methods]
        ymax = max(ymax, max(vals))
        ax.bar([c + off for c in centers], vals, width=bar_w, color=PERCENTILE_COLORS[label], label=label,
               linewidth=0, zorder=3)
    ax.set_ylim(0, ymax * 1.28 if ymax > 0 else 1.0)
    for c, m in zip(centers, methods):
        p50 = float(m.get("p50_s") or 0.0)
        p99 = float(m.get("p99_s") or 0.0)
        # Selective direct labels: p50 and p99 tips only; the table carries the rest.
        ax.annotate(f"{p50:.1f} s", xy=(c + offsets[0], p50), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8, color=INK["secondary"], zorder=4)
        ax.annotate(f"{p99:.1f} s", xy=(c + offsets[2], p99), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8, color=INK["secondary"], zorder=4)
        fr = m.get("failure_rate")
        fr_txt = "failure rate n/a" if fr is None else f"failure rate {100 * float(fr):.1f}%"
        ax.annotate(fr_txt, xy=(c, ymax * 1.13), xytext=(0, 0), textcoords="offset points", ha="center",
                    va="bottom", fontsize=9, color=INK["primary"], fontweight="bold", zorder=4)
    ax.set_xticks(centers)
    ax.set_xticklabels([str(m.get("name", f"method {i + 1}")) for i, m in enumerate(methods)])
    ax.set_xlim(0, n)
    ax.set_xlabel("Reset method")
    ax.set_ylabel("Reset latency (seconds, wall-clock)")
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.01), title=None, ncol=3, handlelength=1.4,
              columnspacing=1.2, borderaxespad=0.0)
    ax.text(0.0, 1.02, "Percentile of reset latency:", transform=ax.transAxes, fontsize=9, color=INK["secondary"],
            ha="left", va="bottom")

    # Table below: everything the bars did not label, plus cost.
    tax.axis("off")
    cols = ["Method", "n resets", "p50 (s)", "p95 (s)", "p99 (s)", "Failure rate (%)", "Cost per 1k resets (USD)"]
    col_widths = [0.28, 0.10, 0.10, 0.10, 0.10, 0.14, 0.18]
    rows = []
    for m in methods:
        fr = m.get("failure_rate")
        cost = m.get("cost_per_1k_usd")
        rows.append([
            str(m.get("name", "")), "n/a" if m.get("n") is None else f"{int(m['n']):,}",
            f"{float(m.get('p50_s') or 0):.2f}", f"{float(m.get('p95_s') or 0):.2f}", f"{float(m.get('p99_s') or 0):.2f}",
            "n/a" if fr is None else f"{100 * float(fr):.2f}", "n/a" if cost is None else f"{float(cost):,.2f}",
        ])
    table = tax.table(cellText=rows, colLabels=cols, colWidths=col_widths, loc="upper center", cellLoc="right",
                      colLoc="right", edges="horizontal")
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1.0, 1.35)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(INK["grid"])
        cell.set_linewidth(0.8)
        cell.set_facecolor(INK["surface"])
        if r == 0:
            cell.get_text().set_color(INK["secondary"])
            cell.get_text().set_fontweight("bold")
        else:
            cell.get_text().set_color(INK["primary"])
        if c == 0:
            cell.get_text().set_ha("left")
            cell._loc = "left"
    _title(fig, title, subtitle)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    return out


# --------------------------------------------------------------------------- #
# synthetic placeholders
# --------------------------------------------------------------------------- #

def synthetic_curve(n_per_point: int = 150) -> dict:
    xs = [0, 25, 50, 100, 200, "all"]

    def ci(rates: list[float]) -> dict:
        lows, highs = [], []
        for r in rates:
            lo, hi = wilson_interval(int(round(r * n_per_point)), n_per_point)
            lows.append(lo)
            highs.append(hi)
        return {"rate": rates, "low": lows, "high": highs}

    return {
        "title": "SYNTHETIC PLACEHOLDER — success rate vs verified trajectories",
        "x": xs, "n_per_point": n_per_point, "synthetic": True,
        "series": {
            "base": ci([0.18]),
            "base+best-of-4": ci([0.30]),
            "SFT": ci([0.18, 0.26, 0.35, 0.44, 0.52, 0.58]),
            "SFT+best-of-4": ci([0.30, 0.40, 0.49, 0.58, 0.66, 0.71]),
        },
        "note": "Every number in this file is made up to exercise train/plot.py. Replace with evals/*/eval_summary.json.",
    }


def synthetic_bench() -> dict:
    return {
        "title": "SYNTHETIC PLACEHOLDER — reset latency by method",
        "synthetic": True,
        "methods": [
            {"name": "revert() to golden snapshot", "n": 500, "p50_s": 1.4, "p95_s": 2.3, "p99_s": 3.9,
             "failure_rate": 0.004, "cost_per_1k_usd": 0.9},
            {"name": "create(from_snapshot) fork", "n": 500, "p50_s": 4.8, "p95_s": 7.1, "p99_s": 11.0,
             "failure_rate": 0.010, "cost_per_1k_usd": 2.4},
            {"name": "fresh VM + reseed", "n": 200, "p50_s": 41.0, "p95_s": 63.0, "p99_s": 95.0,
             "failure_rate": 0.060, "cost_per_1k_usd": 19.5},
        ],
        "note": "Every number in this file is made up to exercise train/plot.py. Replace with forkloop.bench output.",
    }


def demo(out_dir: str | Path = DEFAULT_EXAMPLES_DIR, *, dpi: int = 150) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    curve = synthetic_curve()
    bench = synthetic_bench()
    (out_dir / "curve_synthetic.json").write_text(json.dumps(curve, indent=2), encoding="utf-8")
    (out_dir / "bench_synthetic.json").write_text(json.dumps(bench, indent=2), encoding="utf-8")
    p1 = chart1(curve, out_dir / "chart1_synthetic.png", dpi=dpi)
    p2 = chart2(bench, out_dir / "chart2_synthetic.png", dpi=dpi)
    return p1, p2


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="train.plot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=["chart1", "chart2"], help="which chart to render")
    p.add_argument("inputs", nargs="*", help="<input.json> <out.png>")
    p.add_argument("--demo", action="store_true", help="render both charts from SYNTHETIC placeholder data")
    p.add_argument("--out-dir", default=str(DEFAULT_EXAMPLES_DIR), help="directory for --demo outputs")
    p.add_argument("--dpi", type=int, default=150)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.demo:
        p1, p2 = demo(args.out_dir, dpi=args.dpi)
        print(f"wrote {p1}\nwrote {p2}")
        return 0
    if not args.command or len(args.inputs) != 2:
        build_parser().print_help()
        return 2
    fn = chart1 if args.command == "chart1" else chart2
    out = fn(args.inputs[0], args.inputs[1], dpi=args.dpi)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
