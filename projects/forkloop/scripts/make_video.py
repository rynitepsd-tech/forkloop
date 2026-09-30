"""Build the demo video from real recorded evidence (no staged or re-rendered screens).

Sequence: title → the student's failed attempt (real screenshots, time-compressed and labelled) →
the checkpoint the evidence picked → independent branches restored from it (real screens) → the
database verdicts → what the dataset received → (optional) aggregate evaluation card with the
registered numbers. Every clip carries a caption bar stating what it is and how compressed it is.

    python scripts/make_video.py --config configs/loop-solari-student.yaml --repair rep-… \
        --out runs/video/forkloop-demo.mp4 [--results docs/results-exp1.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG, INK, MUTED, ACCENT, GOOD, BAD = (15, 20, 27), (230, 235, 241), (154, 166, 180), (143, 176, 255), (111, 208, 160), (255, 154, 146)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for p in ("/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size, index=(1 if bold else 0) if p.endswith(".ttc") else 0)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    rows, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and d.textlength(trial, font=font) > width:
            rows.append(cur)
            cur = word
        else:
            cur = trial
    return rows + [cur] if cur else rows or [""]


def card(lines: list[tuple[str, int, tuple, bool]], sub: str = "") -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    y = 200
    for text, size, color, bold in lines:
        f = _font(size, bold)
        for row in _wrap(d, text, f, W - 192):
            d.text((96, y), row, font=f, fill=color)
            y += int(size * 1.35)
    if sub:
        rows = _wrap(d, sub, _font(20), W - 192)
        for k, row in enumerate(rows):
            d.text((96, H - 60 - 26 * (len(rows) - k)), row, font=_font(20), fill=MUTED)
    return im


def framed(shot: Path, caption: str, tag: str, tag_color: tuple) -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    s = Image.open(shot).convert("RGB")
    dd = ImageDraw.Draw(s)
    # mask the address bar (session tokens live in OpenEMR URLs)
    dd.rectangle((int(s.width * 0.20), int(s.height * 0.105), int(s.width * 0.90), int(s.height * 0.145)), fill=(236, 239, 243))
    s = s.resize((1120, 630), Image.LANCZOS)
    im.paste(s, (80, 20))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 660, W, H), fill=(22, 28, 36))
    d.text((80, 672), tag, font=_font(24, True), fill=tag_color)
    d.text((80 + int(_font(24, True).getlength(tag)) + 18, 675), caption, font=_font(20), fill=INK)
    return im


def main(a: argparse.Namespace) -> None:
    import imageio_ffmpeg
    from forkloop.correction.project import load_project
    from forkloop.trajectories import load_episode

    proj = load_project(a.config, require_env=False)
    store = proj.store()
    from forkloop.correction.evidence import pick_example
    rep = next(r for r in store.repairs() if r["repair_id"] == a.repair) if a.repair else pick_example(store)
    att = store.attempt(rep["attempt_id"])
    ep = load_episode(Path(att["run_dir"]))
    steps = ep["steps"]
    branches = store.branches(repair_id=rep["repair_id"])
    wins = [b for b in branches if b["status"] == "finished" and (b["reward"] or 0) >= 1]
    ck = store.checkpoint(wins[0]["ckpt_id"])
    backend = proj.backend_name
    frames: list[tuple[Image.Image, float]] = []
    frames.append((card([("Forkloop", 72, INK, True),
                         ("failure · checkpoint · verified corrections · dataset", 34, ACCENT, False),
                         (f"real {('Solari desktops' if backend == 'solari' else 'Docker worlds')}, real OpenEMR 8.3 + synthetic payer portal", 26, MUTED, False)],
                        "All screens are real captures from recorded runs. Synthetic patient data."), 4.0))
    man = ep["manifest"]
    frames.append((card([("The task", 44, INK, True), (man["instruction"], 24, INK, False)],
                        f"{man['task_id']}"), 6.0))
    n = len(steps)
    idx = sorted(set([0, 2, 4, ck["step"], n // 3, n // 2, (2 * n) // 3, n - 1]))
    for i in idx:
        shot = Path(att["run_dir"]) / "shots" / f"{i:03d}_before.png"
        if shot.exists():
            act = next((s for s in steps if s["i"] == i), {}).get("raw_action", "").strip().splitlines()[-1:] or [""]
            frames.append((framed(shot, f"student, step {i}/{n}: {act[0][:70]}  (time-compressed)", "STUDENT", MUTED), 1.4))
    v = ep["verdict"] or {}
    found = []
    for cid in (v.get("failed") or [])[:2]:
        d = (v.get("details") or {}).get(cid) or {}
        if "actual" in d:
            has = "no matching row" if d.get("actual") in (None, "") else d.get("actual")
            found.append((f"{cid}: database has {has}, task needs {d.get('expected')}", 26, INK, False))
    frames.append((card([("The student failed", 48, BAD, True),
                         (f"{n} steps, verdict {v.get('reason_code')}", 30, INK, False), *found,
                         ("The database, not the agent's own account, decides.", 26, MUTED, False)]), 4.5))
    pts = rep["config"].get("restart_points", [])
    why = next((p for p in pts if p["ckpt_id"] == ck["ckpt_id"]), {})
    same_ck = [b for b in branches if b["ckpt_id"] == ck["ckpt_id"]]
    last = [((b.get("restore") or {}).get("attempts") or [{}])[-1] for b in same_ck]
    equal = sum(1 for r in last if r.get("ok") and (r.get("fidelity") or {}).get("tables_equal"))
    overall = []
    if a.results:
        rr = (json.loads(Path(a.results).read_text()).get("checkpoints") or {}).get("exp1-round1", {}).get("restore", {})
        rp = rr.get(ck["strategy"])
        if rp:
            overall = [(f"across exp1: {rp['ok_rate']:.0%} of {rp['n']:,} {ck['strategy']} restores passed the fidelity "
                        f"check (failed ones retried, then unscored)", 22, MUTED, False)]
    frames.append((card([("Restore a checkpoint", 48, INK, True),
                         (f"step {ck['step']} · {ck['strategy']} · chosen by evidence: {why.get('reason')}", 28, ACCENT, False),
                         (f"{len(same_ck)} independent copies: world and agent memory restored", 26, INK, False),
                         (f"restore fidelity here: checksummed tables identical to the checkpoint in {equal} of {len(same_ck)}",
                          24, MUTED, False), *overall]), 5.0))
    for b in wins[:2]:
        bep = load_episode(Path(b["run_dir"]))
        acted = [s for s in bep["steps"] if not (s.get("search") or {}).get("replayed")]
        for s in [acted[0], acted[len(acted) // 3], acted[(2 * len(acted)) // 3], acted[-1]]:
            shot = Path(b["run_dir"]) / s["shot_before"]
            frames.append((framed(shot, f"branch {b['idx']} from step {ck['step']}, step {s['i']}  (time-compressed)",
                                  "TEACHER", ACCENT), 1.3))
    frames.append((card([("Verified by the database", 48, GOOD, True)] +
                        [(f"branch {b['idx']}: {b['status'] if b['status'] != 'finished' else ('OK' if (b['reward'] or 0) >= 1 else b['reason_code'])}"
                          f" after {b['n_steps']} steps", 28, INK, False) for b in branches[:4]] +
                        [("failed branches are kept as evidence, never as training demonstrations", 24, MUTED, False)]), 4.0))
    if a.dataset:
        m = json.loads((Path(a.dataset) / "manifest.json").read_text())
        frames.append((card([("Exported dataset", 48, INK, True),
                             (f"{m['counts']['records']} demonstrations · {m['counts']['preferences']} preference pairs", 28, INK, False),
                             (f"{m['dataset_id']} · sha256 lineage to every source trajectory", 24, ACCENT, False),
                             ("memory provenance audited · final-test tasks refused", 24, MUTED, False)]), 4.0))
    if a.results:
        r = json.loads(Path(a.results).read_text())
        lines = [("Did the student improve on unseen tasks?", 40, INK, True)]
        lines += [(ln, 26, INK, False) for ln in r.get("video_lines", [])]
        if r.get("video_conclusion"):
            lines.append((r["video_conclusion"], 26, ACCENT, True))
        frames.append((card(lines, r.get("video_footer", "")), 9.0))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        lst = []
        for k, (im, dur) in enumerate(frames):
            p = Path(tmp) / f"f{k:04d}.png"
            im.save(p)
            lst.append(f"file '{p}'\nduration {dur}\n")
        lst.append(f"file '{Path(tmp) / f'f{len(frames) - 1:04d}.png'}'\n")
        (Path(tmp) / "list.txt").write_text("".join(lst))
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(Path(tmp) / "list.txt"), "-vf", "fps=24,format=yuv420p", "-c:v", "libx264",
                        "-crf", "20", str(out)], check=True)
    print(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--repair", default=None)
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--results", default=None)
    ap.add_argument("--out", required=True)
    main(ap.parse_args())
