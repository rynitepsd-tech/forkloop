"""Replay a verified teacher trajectory on fresh forks and count Chrome crashes.

A deterministic, Opus-free reproducer for the tab crashes that decide family-3
episodes (docs/spikes.md). Each repeat: fork the golden, reset with the
trajectory's seed (same world state), relaunch Chrome with the flag set under
test, replay the recorded actions (waits included), then read the episode
diagnostics and count crash reports.

    FORKLOOP_CHROME_FLAGS="--use-angle=swiftshader" python scripts/chrome_crash_probe.py \
        --replay runs/teacher-f3-s10-14-8gb/episodes/resolve_denial-train-000011-* --repeat 2 --label swiftshader

Results append to runs/chrome_probe/results.jsonl; one line per repeat.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import time
from pathlib import Path

from forkloop.actions import Action
from forkloop.backends.solari import SolariBackend
from forkloop.env import Env
from forkloop.pool import WorkerPool
from forkloop.trajectories import Recorder
from forkloop.world import load_world


def stress_actions(manifest: dict, loops: int) -> list[str]:
    """Visit the pages the teacher reported crashing (tabbed frame, chart, documents, PDF viewer)
    `loops` times over, by URL, in the current tab. Assumes OpenEMR is logged in (the replay did it)."""
    import re as _re
    pid = manifest["expected"]["patient_pid"]
    m = _re.search(r"INSERT INTO documents \(id[^)]*\) VALUES \((\d+)", manifest["seeding"]["openemr_sql"])
    doc = m.group(1) if m else None
    pages = [("localhost/openemr/interface/main/tabs/main.php", 6),
             (f"localhost/openemr/interface/patient_file/summary/demographics.php?set_pid={pid}", 6),
             (f"localhost/openemr/controller.php?document&list&patient_id={pid}", 5)]
    if doc:
        pages.append((f"localhost/openemr/controller.php?document&view&patient_id={pid}&doc_id={doc}&", 6))
    out: list[str] = []
    for _ in range(loops):
        for url, wait in pages:
            out += ["click(640, 90)", 'key("ctrl+a")', f'type("{url}\\n")', f"wait({wait})"]
    return out


def load_actions(ep_dir: Path) -> tuple[int, str, list[str]]:
    manifest = json.loads((ep_dir / "manifest.json").read_text())
    raws = []
    for line in (ep_dir / "steps.jsonl").read_text().splitlines():
        s = json.loads(line)
        if s.get("valid", True) and s.get("action") and s["action"].get("type") not in ("done",):
            raws.append(s["raw_action"])
    return int(manifest["seed"]), manifest["family"], raws


async def one_repeat(world, backend, ep_dir: Path, run_id: str, label: str, idx: int, stress: int = 0) -> dict:
    seed, family, raws = load_actions(ep_dir)
    if stress:
        manifest = json.loads((ep_dir / "manifest.json").read_text())
        raws = raws + stress_actions(manifest, stress)
    pool = WorkerPool(backend, world, size=1, mode="fork", run_id=f"probe-{label}")
    rec = Recorder("runs/chrome_probe", run_id=run_id, meta={"policy": "replay", "label": label, "replay": str(ep_dir),
                                                             "chrome_flags": os.environ.get("FORKLOOP_CHROME_FLAGS", ""),
                                                             "chrome_drop": os.environ.get("FORKLOOP_CHROME_DROP", "")})
    env = Env(world, backend, family=family, pool=pool, recorder=rec, settle_s=0.8, stable_after_action=False)
    t0 = time.monotonic()
    row = {"label": label, "repeat": idx, "seed": seed, "stress": stress, "replay": ep_dir.name, "flags": os.environ.get("FORKLOOP_CHROME_FLAGS", ""),
           "drop": os.environ.get("FORKLOOP_CHROME_DROP", "")}
    try:
        obs, info = await env.reset(seed)
        row["reset_s"] = round(info["reset"]["total_seconds"], 1)
        if stress:  # the stress phase must not be cut by the task's 60-action / 600 s budget
            env.ep.task.budget = {**dict(env.ep.task.budget), "max_steps": 60 + 20 * stress, "max_seconds": 1800}
        mid = env.ep.machine.id
        row["machine"] = mid
        try:  # ids decode to "desktop-pool-<host>:vm_<n>:<account>:<ts>"
            import base64 as _b64
            dec = _b64.urlsafe_b64decode(mid.split(".")[0] + "==").decode("ascii", "ignore")
            row["host"], row["vm"] = dec.split(":")[0], dec.split(":")[1]
        except Exception:  # noqa: BLE001
            pass
        errs = 0
        for i, raw in enumerate(raws):
            a = Action.parse(raw, width=obs.width, height=obs.height)
            obs, _, term, trunc, info2 = await env.step(a, meta={"raw_action": raw})
            errs += 1 if info2.get("error") else 0
            if term or trunc:
                break
        v = await env.verify()
        row.update({"steps": i + 1, "step_errors": errs, "reward": v.reward, "reason": v.reason_code,
                    "episode_s": round(time.monotonic() - t0, 1)})
        ep = sorted(rec.episodes())[-1]
        chrome = (ep / "diagnostics" / "chrome.log").read_text() if (ep / "diagnostics" / "chrome.log").exists() else ""
        ps = (ep / "diagnostics" / "chrome_ps.txt").read_text() if (ep / "diagnostics" / "chrome_ps.txt").exists() else ""
        row.update({"crashpad": chrome.count("scaling_cur_freq"), "netsvc": chrome.count("Network service crashed"),
                    "gpu_flag_in_ps": "--disable-gpu" in ps, "episode_dir": str(ep)})
    except Exception as e:  # noqa: BLE001
        row["error"] = f"{type(e).__name__}: {e}"
    finally:
        await env.close()
        await pool.close()
    return row


async def main(a: argparse.Namespace) -> int:
    world = load_world("claims-ops-v1")
    backend = SolariBackend(kind="desktop")
    ep_dir = Path(glob.glob(a.replay)[0])
    out = Path("runs/chrome_probe"); out.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for i in range(a.repeat):
            row = await one_repeat(world, backend, ep_dir, f"{a.label}-{int(time.time())}-{i}", a.label, i, stress=a.stress)
            rows.append(row)
            print(json.dumps(row), flush=True)
            with (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
    finally:
        await backend.close()
    ok = [r for r in rows if "error" not in r]
    print(f"\n{a.label}: {len(ok)}/{len(rows)} replays completed; crashpad reports per replay "
          f"{[r['crashpad'] for r in ok]}, verified {[r['reward'] for r in ok]}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True, help="episode dir (glob ok) whose steps.jsonl is replayed")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--stress", type=int, default=0, help="after the replay, cycle the crash-prone OpenEMR pages this many times")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
