"""Shareable evidence bundle over correction stores (``forkloop evidence``).

A static, script-free ``index.html`` plus ``img/`` thumbnails that lets a reviewer follow one
repaired failure end to end — the failed attempt, its checkpoints and verifier status, the chosen
restart point and why, every branch (restore fidelity, steps, verdict), the dataset records the
verified branch produced — and then the aggregate collection, training and evaluation tables.
Failed branches, unscored cells and missing evidence are shown, never dropped.

Screenshots are downscaled copies with the browser address bar masked (OpenEMR puts a session
token in the URL). They are real captures; nothing is re-rendered or staged.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import shutil
from pathlib import Path
from typing import Any, Iterable, Optional

from PIL import Image, ImageDraw

from ..trajectories import load_episode

STYLE = """
:root{--ground:#eef1f4;--paper:#ffffff;--ink:#172333;--muted:#4b5563;--line:#cbd2da;--soft:#f5f7f9;
--accent:#1d4ed8;--good:#176447;--good-bg:#e6f4ec;--bad:#9b2525;--bad-bg:#fbeaea;--warn:#795211;--warn-bg:#fbf3e0;
--track:#dde3ea;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#0f141b;--paper:#161c24;--ink:#e6ebf1;
--muted:#9aa6b4;--line:#2c3642;--soft:#1c2430;--accent:#8fb0ff;--good:#6fd0a0;--good-bg:#12291f;--bad:#ff9a92;
--bad-bg:#2e1616;--warn:#f2c14e;--warn-bg:#2d2412;--track:#2a3441;color-scheme:dark}}
:root[data-theme="dark"]{--ground:#0f141b;--paper:#161c24;--ink:#e6ebf1;--muted:#9aa6b4;--line:#2c3642;--soft:#1c2430;
--accent:#8fb0ff;--good:#6fd0a0;--good-bg:#12291f;--bad:#ff9a92;--bad-bg:#2e1616;--warn:#f2c14e;--warn-bg:#2d2412;
--track:#2a3441;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1120px;margin:0 auto;background:var(--paper);padding-block:28px 72px;padding-inline:clamp(16px,4vw,40px)}
h1{font-size:clamp(1.8rem,4.5vw,2.8rem);line-height:1.12;letter-spacing:-.02em;margin:8px 0 12px;text-wrap:balance}
h2{font-size:1.4rem;margin:44px 0 10px;text-wrap:balance}h3{font-size:1.05rem;margin:26px 0 8px}
p{max-width:72ch;margin:10px 0}a{color:var(--accent);text-underline-offset:3px}
:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.eyebrow{font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:650}
.muted{color:var(--muted)}code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.86em;overflow-wrap:anywhere}
.pill{display:inline-block;font-size:.8rem;font-weight:650;padding:2px 9px;border-radius:999px;border:1px solid transparent;white-space:nowrap}
.ok{color:var(--good);background:var(--good-bg)}.fail{color:var(--bad);background:var(--bad-bg)}.unscored{color:var(--warn);background:var(--warn-bg)}
.neutral{color:var(--muted);border-color:var(--line)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
.fact{border-top:2px solid var(--line);padding-top:8px}.fact b{display:block;font-size:1.35rem;font-variant-numeric:tabular-nums}
.fact span{color:var(--muted);font-size:.86rem}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}th{font-size:.8rem;color:var(--muted);font-weight:650}
td.num,th.num{text-align:right}
.strip{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px}.strip figure{margin:0;flex:0 0 232px}
.strip img{width:232px;height:auto;border:1px solid var(--line);border-radius:4px;display:block}
figcaption{font-size:.8rem;color:var(--muted);margin-top:4px;overflow-wrap:anywhere}
.branches{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin:12px 0}
.branch{border:1px solid var(--line);border-radius:6px;padding:12px 14px;background:var(--paper)}
.branch.win{border-color:var(--good)}.branch h4{margin:0 0 6px;font-size:.98rem}
.branch dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:8px 0 0;font-size:.88rem}.branch dt{color:var(--muted)}.branch dd{margin:0}
pre{white-space:pre-wrap;background:var(--soft);padding:12px 14px;border-radius:4px;font-size:.84rem;margin:8px 0;overflow-wrap:anywhere}
svg text{fill:var(--muted);font-size:11px}svg .axis{stroke:var(--line)}
details{margin:10px 0}summary{cursor:pointer;min-height:40px;padding:8px 0;font-weight:600}
.note{border-left:3px solid var(--line);padding:4px 0 4px 12px;color:var(--muted)}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _pill(status: str, reward: Optional[float]) -> str:
    if status == "finished" and reward is not None:
        return '<span class="pill ok">verified</span>' if reward >= 1 else '<span class="pill fail">failed</span>'
    return f'<span class="pill unscored">{_e(status)} · unscored</span>'


class _Thumbs:
    """Downscaled copies with the address bar masked, content-addressed under img/."""

    def __init__(self, out: Path, width: int = 480) -> None:
        self.dir = out / "img"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.width = width

    def add(self, src: Optional[Path]) -> Optional[str]:
        if src is None or not src.exists():
            return None
        raw = src.read_bytes()
        name = hashlib.sha256(raw).hexdigest()[:20] + ".jpg"
        dst = self.dir / name
        if not dst.exists():
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = im.size
            d = ImageDraw.Draw(im)
            # mask the omnibox text (session tokens live in OpenEMR URLs); keep the origin readable
            d.rectangle((int(w * 0.20), int(h * 0.105), int(w * 0.90), int(h * 0.145)), fill=(236, 239, 243))
            im = im.resize((self.width, int(h * self.width / w)), Image.LANCZOS)
            im.save(dst, "JPEG", quality=82)
        return f"img/{name}"


def _compact(a: Optional[dict]) -> str:
    if not a:
        return "(invalid action)"
    try:
        from ..actions import Action
        return Action.parse(a).to_compact()
    except Exception:  # noqa: BLE001
        return json.dumps(a)


def _timeline(n_steps: int, ckpts: list[dict], chosen: Iterable[int], end_reason: str) -> str:
    """Checkpoint timeline drawn to scale: x = step index 0..n_steps."""
    W, H, L, R = 900, 86, 30, 20
    span = max(1, n_steps)
    x = lambda s: L + (W - L - R) * s / span  # noqa: E731
    chosen = set(chosen)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Checkpoints along the failed attempt" '
             f'style="width:100%;max-width:{W}px;height:auto">',
             f'<line class="axis" x1="{L}" y1="40" x2="{W - R}" y2="40" stroke-width="6" stroke-linecap="round"/>']
    for c in ckpts:
        col = {"clean": "var(--good)", "damaged": "var(--bad)"}.get(c["status"], "var(--warn)")
        cx = x(c["step"])
        shape = (f'<rect x="{cx - 5:.1f}" y="35" width="10" height="10" fill="{col}"/>' if c["strategy"] == "snapshot"
                 else f'<circle cx="{cx:.1f}" cy="40" r="5" fill="{col}"/>')
        parts.append(shape)
        if c["step"] in chosen:
            parts.append(f'<path d="M{cx:.1f} 14 L{cx - 6:.1f} 4 L{cx + 6:.1f} 4 Z" fill="var(--accent)"/>'
                         f'<text x="{cx:.1f}" y="28" text-anchor="middle">restart @ {c["step"]}</text>')
    for s in range(0, span + 1, max(1, span // 10)):
        parts.append(f'<text x="{x(s):.1f}" y="66" text-anchor="middle">{s}</text>')
    parts.append(f'<text x="{W - R:.1f}" y="82" text-anchor="end">step → end: {_e(end_reason)}</text>')
    parts.append("</svg>")
    legend = ('<p class="muted" style="font-size:.84rem">● replay/reset checkpoint &nbsp; ■ provider VM snapshot &nbsp; '
              'green = clean, red = damaged (a safety violation or a new wrong record already persisted), amber = unknown.</p>')
    return "".join(parts) + legend


def pick_example(store: Any) -> Optional[dict]:
    """The most informative verified repair: a restart point after step 0 preferred, then the latest
    restart step, then the longest failed attempt."""
    best, key = None, None
    for rep in store.repairs():
        if rep["status"] != "verified":
            continue
        pts = rep["config"].get("restart_points", [])
        branches = store.branches(repair_id=rep["repair_id"])
        ok = [b for b in branches if b["status"] == "finished" and (b["reward"] or 0) >= 1]
        if not ok:
            continue
        ck_step = max(store.checkpoint(b["ckpt_id"])["step"] for b in ok)
        k = (ck_step > 0, ck_step, len(branches))
        if key is None or k > key:
            best, key = rep, k
    return best


def _example_section(store: Any, rep: dict, thumbs: _Thumbs, records: list[dict]) -> str:
    att = store.attempt(rep["attempt_id"])
    ep = load_episode(Path(att["run_dir"]))
    steps, verdict, manifest = ep["steps"], ep["verdict"] or {}, ep["manifest"]
    ckpts = store.checkpoints(att["attempt_id"])
    branches = store.branches(repair_id=rep["repair_id"])
    pts = rep["config"].get("restart_points", [])
    tried = sorted({store.checkpoint(b["ckpt_id"])["step"] for b in branches})
    n = len(steps)
    pick = sorted(set([0, max(0, n // 4), max(0, n // 2), max(0, n - 1)] + tried))
    figs = []
    for i in pick:
        src = Path(att["run_dir"]) / "shots" / f"{i:03d}_before.png"
        t = thumbs.add(src)
        if t:
            act = next((s for s in steps if s["i"] == i), {})
            figs.append(f'<figure><img loading="lazy" src="{t}" alt="Student screen before step {i}">'
                        f'<figcaption>step {i}: <code>{_e(_compact(act.get("action")))}</code></figcaption></figure>')
    failed = [c for c in (verdict.get("failed") or [])]
    ev = []
    for cid in failed[:6]:
        d = (verdict.get("details") or {}).get(cid) or {}
        if "actual" in d:
            has = "no matching row" if d.get("actual") in (None, "") else f"<code>{_e(d.get('actual'))}</code>"
            ev.append(f"<li><code>{_e(cid)}</code>: database has {has}, "
                      f"task needs <code>{_e(d.get('expected'))}</code></li>")
        else:
            ev.append(f"<li><code>{_e(cid)}</code> failed ({_e(d.get('reason_code'))})</li>")
    out = [f'<section id="example"><p class="eyebrow">One failure, repaired</p><h2>{_e(manifest.get("task_id"))}</h2>',
           f'<p><strong>Task.</strong> {_e(manifest.get("instruction"))}</p>',
           f'<p>The agent under repair ({_e((att["info"] or {}).get("role"))}) took {n} steps and ended '
           f'{_pill(att["status"], att["reward"])} with <code>{_e(att["reason_code"])}</code>. What the verifier read from the databases:</p>'
           f'<ul>{"".join(ev) or "<li>(no failed check details recorded)</li>"}</ul>',
           f'<div class="strip">{"".join(figs)}</div>',
           "<h3>Checkpoints and the chosen restart point</h3>",
           _timeline(n, ckpts, tried, (verdict.get("end_reason") or att["reason_code"] or "")),
           "<ul>" + "".join(f"<li>step {p['step']} — <strong>{_e(p['reason'])}</strong>: "
                            f"<span class='muted'>{_e(json.dumps(p.get('evidence') or {}))[:220]}</span></li>" for p in pts) + "</ul>",
           f'<p class="note">Restart points come from recorded evidence (controller-side; the teacher never sees '
           f'why the attempt failed). Each branch restores the world and the agent memory independently.</p>',
           f"<h3>{len(branches)} branches</h3><div class='branches'>"]
    for b in branches:
        rest = (b["restore"].get("attempts") or [{}])[-1]
        fid = rest.get("fidelity") or {}
        win = b["status"] == "finished" and (b["reward"] or 0) >= 1
        ck = store.checkpoint(b["ckpt_id"])
        bshots = []
        if win:
            bep = load_episode(Path(b["run_dir"]))
            acted = [s for s in bep["steps"] if not (s.get("search") or {}).get("replayed")]
            for s in [acted[0], acted[len(acted) // 2], acted[-1]] if acted else []:
                t = thumbs.add(Path(b["run_dir"]) / s["shot_before"]) if s.get("shot_before") else None
                if t:
                    bshots.append(f'<figure><img loading="lazy" src="{t}" alt="Teacher branch screen at step {s["i"]}">'
                                  f'<figcaption>step {s["i"]}: <code>{_e(_compact(s.get("action")))}</code></figcaption></figure>')
        out.append(f'<div class="branch{" win" if win else ""}"><h4>{_pill(b["status"], b["reward"])} '
                   f'<code>{_e(b["branch_id"])}</code></h4><dl>'
                   f'<dt>from</dt><dd>step {ck["step"]} ({_e(ck["strategy"])})</dd>'
                   f'<dt>restore</dt><dd>{"ok" if rest.get("ok") else "failed"} in {_e(rest.get("seconds"))} s'
                   f'{", replayed " + str(rest.get("replayed_steps")) + " steps" if rest.get("replayed_steps") else ""}</dd>'
                   f'<dt>fidelity</dt><dd>tables equal: {_e(fid.get("tables_equal"))}; screen distance {_e(fid.get("screen_distance"))}</dd>'
                   f'<dt>verdict</dt><dd><code>{_e(b["reason_code"])}</code> after {_e(b["n_steps"])} steps</dd></dl>'
                   + (f'<div class="strip" style="margin-top:10px">{"".join(bshots)}</div>' if bshots else "") + "</div>")
    out.append("</div>")
    mine = [r for r in records if r["source"].get("repair_id") == rep["repair_id"]]
    if mine:
        r0 = min(mine, key=lambda r: r["input"]["step"])
        from .dataset import render_target
        tgt = render_target(r0["target"], screen=tuple(r0["input"]["screen"]), coords=tuple(r0["input"]["screen"]))
        nb = len({r["source"].get("branch_id") for r in mine})
        out.append(f"<h3>What the dataset received</h3><p>{len(mine)} action-demonstration records from the "
                   f"{nb} verified branch{'es' if nb != 1 else ''} (steps {min(r['input']['step'] for r in mine)}–"
                   f"{max(r['input']['step'] for r in mine)}); "
                   f"each carries the exact input the teacher saw on that path. The first one:</p>"
                   f"<pre>INPUT memory: {_e(json.dumps(r0['input']['memory']))}\nINPUT last actions: "
                   f"{_e(json.dumps(r0['input']['history'][-4:]))}\nTARGET (screen pixels):\n{_e(tgt)}</pre>"
                   f"<p class='muted'>record <code>{_e(r0['record_id'])}</code> · source branch "
                   f"<code>{_e(r0['source'].get('branch_id'))}</code> · memory provenance "
                   f"{'ok' if r0['audit'].get('memory_provenance_ok') else 'MISMATCH'}</p>")
    out.append("</section>")
    return "".join(out)


def write_evidence(stores: dict[str, Any], out: Path, *, title: str, example_store: Optional[str] = None,
                   example_repair: Optional[str] = None, datasets: Iterable[Path] = (), tables_html: str = "",
                   intro: str = "") -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    thumbs = _Thumbs(out)
    records: list[dict] = []
    ds_rows = []
    for d in datasets:
        d = Path(d)
        m = json.loads((d / "manifest.json").read_text())
        ds_rows.append(m)
        if (d / "records.jsonl").exists():
            records += [json.loads(l) for l in (d / "records.jsonl").read_text().splitlines() if l.strip()]
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>'
             f"<style>{STYLE}</style></head><body><main>",
             f'<p class="eyebrow">Forkloop evidence bundle</p><h1>{_e(title)}</h1>', intro]
    ex_store = stores.get(example_store) if example_store else next(iter(stores.values()), None)
    if ex_store is not None:
        rep = next((r for r in ex_store.repairs() if r["repair_id"] == example_repair), None) if example_repair \
            else pick_example(ex_store)
        if rep is not None:
            parts.append(_example_section(ex_store, rep, thumbs, records))
        else:
            parts.append("<p class='note'>No verified repair in this store yet.</p>")
    if ds_rows:
        parts.append("<section id='datasets'><h2>Datasets and lineage</h2><div class='scroll'><table><tr><th>dataset</th>"
                     "<th class='num'>records</th><th class='num'>preference pairs</th><th>origins</th>"
                     "<th>records.jsonl sha256</th><th>audit</th></tr>")
        for m in ds_rows:
            parts.append(f"<tr><td><code>{_e(m['dataset_id'])}</code><br><span class='muted'>{_e(m.get('name'))}</span></td>"
                         f"<td class='num'>{m['counts']['records']}</td><td class='num'>{m['counts']['preferences']}</td>"
                         f"<td>{_e(json.dumps(m['counts'].get('by_origin')))}</td>"
                         f"<td><code>{_e(m['files']['records.jsonl'][:16])}…</code></td>"
                         f"<td class='muted'>{_e(json.dumps(m.get('audit')))}</td></tr>")
        parts.append("</table></div></section>")
    parts.append(tables_html)
    parts.append("</main></body></html>")
    (out / "index.html").write_text("".join(parts), encoding="utf-8")
    return out / "index.html"


__all__ = ["write_evidence", "pick_example"]
