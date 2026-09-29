"""Static, script-free HTML evidence report over a correction store (``forkloop inspect``).

Per failed attempt: the task, the verifier's verdict, the step list with screenshots, each
checkpoint (strategy, verifier status), the chosen restart points and why, every branch (restore
fidelity, steps, verdict) and which dataset records came from it. Unscored and restore-failed
branches are shown as such. Screenshots are linked relative to the report, never inlined.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any, Optional

from ..trajectories import load_episode

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#5d5d58;--line:#dcdcd6;--ok:#1f7a3a;--bad:#b3261e;--warn:#8a5a00;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecece8;--muted:#a3a39c;--line:#383835;--ok:#6fd08c;--bad:#ff8a80;--warn:#f2c14e;--card:#1f1f1d}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 8px}h3{font-size:16px;margin:20px 0 6px}
.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;font-size:13px;color:var(--muted)}code{font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:12px 0}
.shots{display:flex;flex-wrap:wrap;gap:8px}.shots figure{margin:0;width:220px}.shots img{width:220px;border:1px solid var(--line);border-radius:4px}
figcaption{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
details summary{cursor:pointer}
"""


def _e(x: Any) -> str:
    return html.escape(str(x), quote=True)


def _badge(status: str, reward: Optional[float]) -> str:
    if status == "finished" and reward is not None:
        return f'<span class="{"ok" if reward >= 1 else "bad"}">{"verified" if reward >= 1 else "failed"}</span>'
    return f'<span class="warn">{_e(status)} (unscored)</span>'


def _rel(p: Path, out_dir: Path) -> str:
    return os.path.relpath(p, out_dir)


def _steps_table(ep_dir: Path, out_dir: Path, highlight: Optional[int] = None, limit: int = 200) -> str:
    ep = load_episode(ep_dir)
    rows = []
    for s in ep["steps"][:limit]:
        a = s.get("action") or {}
        mem = (s.get("agent") or {}).get("memory_written") or []
        replay = (s.get("search") or {}).get("replayed")
        shot = ep_dir / s["shot_before"] if s.get("shot_before") else None
        link = f'<a href="{_e(_rel(shot, out_dir))}">screen</a>' if shot and shot.exists() else ""
        mark = ' style="background:rgba(242,193,78,.18)"' if highlight is not None and s["i"] == highlight else ""
        rows.append(f"<tr{mark}><td>{s['i']}</td><td><code>{_e(s.get('raw_action','')[-160:] if not a else _compact(a))}</code>"
                    f"{' <span class=muted>(replayed)</span>' if replay else ''}</td><td>{_e('; '.join(mem))}</td>"
                    f"<td>{_e(s.get('error') or '')}</td><td>{link}</td></tr>")
    return ("<table><tr><th>step</th><th>action</th><th>memory written</th><th>error</th><th>before</th></tr>"
            + "".join(rows) + "</table>")


def _compact(a: dict) -> str:
    try:
        from ..actions import Action
        return Action.parse(a).to_compact()
    except Exception:  # noqa: BLE001
        return json.dumps(a)


def _shots(ep_dir: Path, out_dir: Path, steps: list[int]) -> str:
    figs = []
    for i in steps:
        p = ep_dir / "shots" / f"{i:03d}_before.png"
        if p.exists():
            figs.append(f'<figure><img loading="lazy" src="{_e(_rel(p, out_dir))}" alt="screen before step {i}">'
                        f"<figcaption>before step {i}</figcaption></figure>")
    return f'<div class="shots">{"".join(figs)}</div>' if figs else ""


def write_report(store: Any, world: Any, out: Path, *, experiment_id: Optional[str] = None,
                 attempt_id: Optional[str] = None, max_attempts: int = 50) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_dir = out.parent.resolve()
    where = {"experiment_id": experiment_id} if experiment_id else {}
    attempts = store.attempts(**where)
    if attempt_id:
        attempts = [a for a in attempts if a["attempt_id"] == attempt_id]
    repairs = store.repairs()
    by_attempt: dict[str, list] = {}
    for r in repairs:
        by_attempt.setdefault(r["attempt_id"], []).append(r)
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>Forkloop evidence</title><style>{CSS}</style></head><body><main>",
             f"<h1>Forkloop evidence</h1><p class=muted>store <code>{_e(store.path)}</code>"
             f"{' · experiment <code>' + _e(experiment_id) + '</code>' if experiment_id else ''}</p>"]
    n_scored = sum(1 for a in attempts if a["status"] == "finished")
    n_ok = sum(1 for a in attempts if a["status"] == "finished" and (a["reward"] or 0) >= 1)
    parts.append(f"<p>{len(attempts)} attempts · {n_scored} scored · {n_ok} verified · "
                 f"{len(attempts) - n_scored} unscored (infrastructure, interrupted) are listed, never counted as failures.</p>")
    shown = 0
    for a in attempts:
        reps = by_attempt.get(a["attempt_id"], [])
        if not attempt_id and not reps and shown >= max_attempts:
            continue
        shown += 1
        ep_dir = Path(a["run_dir"])
        manifest = json.loads((ep_dir / "manifest.json").read_text()) if (ep_dir / "manifest.json").exists() else {}
        parts.append(f'<section class=card id="{_e(a["attempt_id"])}"><h2>{_e(a["task_id"])}</h2>'
                     f"<p>attempt <code>{_e(a['attempt_id'])}</code> · role {_e(a['info'].get('role'))} · "
                     f"{_badge(a['status'], a['reward'])} {_e(a['reason_code'] or '')} · {a['n_steps']} steps</p>"
                     f"<p><strong>Instruction.</strong> {_e(manifest.get('instruction', ''))}</p>")
        ckpts = store.checkpoints(a["attempt_id"])
        if ckpts:
            parts.append("<h3>Checkpoints</h3><table><tr><th>step</th><th>strategy</th><th>world ref</th><th>verifier status</th>"
                         "<th>boundary</th></tr>" + "".join(
                             f"<tr><td>{c['step']}</td><td>{_e(c['strategy'])}</td><td><code>{_e(c['world_ref'])}</code></td>"
                             f"<td class={'ok' if c['status']=='clean' else 'bad' if c['status']=='damaged' else 'warn'}>{_e(c['status'])}"
                             f"{(' — ' + _e(', '.join(c['oracle'].get('why') or []))) if c['oracle'].get('why') else ''}</td>"
                             f"<td>{_e(c['oracle'].get('boundary'))}</td></tr>" for c in ckpts) + "</table>")
        for rep in reps:
            pts = rep["config"].get("restart_points", [])
            parts.append(f"<h3>Repair <code>{_e(rep['repair_id'])}</code> — {_e(rep['mode'])}: {_e(rep['status'])}</h3>"
                         "<p>Restart points: " + "; ".join(f"step {p['step']} ({_e(p['reason'])})" for p in pts) + "</p>")
            rows = []
            for b in store.branches(repair_id=rep["repair_id"]):
                rest = (b["restore"].get("attempts") or [{}])[-1]
                fid = rest.get("fidelity") or {}
                rows.append(f"<tr><td><a href='#{_e(b['branch_id'])}'>{_e(b['branch_id'])}</a></td><td>{b['idx']}</td>"
                            f"<td>{_badge(b['status'], b['reward'])} {_e(b['reason_code'] or '')}</td><td>{b['n_steps'] or ''}</td>"
                            f"<td>{_e(rest.get('strategy'))} {'ok' if rest.get('ok') else 'FAILED'}; tables equal "
                            f"{_e(fid.get('tables_equal'))}; screen distance {_e(fid.get('screen_distance'))}; "
                            f"{_e(rest.get('seconds'))} s</td></tr>")
            parts.append("<table><tr><th>branch</th><th>#</th><th>verdict</th><th>steps</th><th>restore</th></tr>"
                         + "".join(rows) + "</table>")
        parts.append("<details><summary>Attempt steps</summary>" + _steps_table(ep_dir, out_dir) + "</details>")
        for rep in reps:
            for b in store.branches(repair_id=rep["repair_id"]):
                bdir = Path(b["run_dir"])
                if (bdir / "steps.jsonl").exists():
                    parts.append(f"<details id='{_e(b['branch_id'])}'><summary>Branch {_e(b['branch_id'])} steps "
                                 f"({_badge(b['status'], b['reward'])})</summary>" + _steps_table(bdir, out_dir) + "</details>")
        parts.append("</section>")
    ds = store.datasets()
    if ds:
        parts.append("<h2>Datasets</h2><table><tr><th>id</th><th>records</th><th>sha256 (records.jsonl)</th><th>path</th></tr>"
                     + "".join(f"<tr><td><code>{_e(d['dataset_id'])}</code></td><td>{d['n_records']}</td>"
                               f"<td><code>{_e(d['sha256'][:16])}…</code></td><td>{_e(d['path'])}</td></tr>" for d in ds) + "</table>")
    costs = store.cost_summary(experiment_id)
    parts.append("<h2>Cumulative charges</h2><p class=muted>Append-only; restoring a checkpoint never removes a charge.</p>"
                 "<table><tr><th>kind</th><th>amount</th><th>USD (where priced)</th><th>rows</th></tr>" + "".join(
                     f"<tr><td>{_e(k)}</td><td>{v['amount']:.2f}</td><td>{v['usd']:.4f}</td><td>{v['n']}</td></tr>"
                     for k, v in sorted(costs.items())) + "</table>")
    parts.append("</main></body></html>")
    out.write_text("".join(parts), encoding="utf-8")
    return out


__all__ = ["write_report"]
