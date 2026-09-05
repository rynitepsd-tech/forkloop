"""Classify every failed episode of a run into the student bake-off failure classes.

Classes (docs/student-2026-09-05.md, step 4): invalid/parse, wrong-record, transcription
(WRONG_VALUE on the authorization number that is not a decoy), decoy (the value is one of
the task's decoy numbers), budget with sane actions, budget with looping. Reads only the
run directory (verdict.json, manifest.json, steps.jsonl); prints a markdown table and the
per-class counts. Superseded attempts are skipped unless --all-attempts.

    PYTHONPATH=. python scripts/classify_failures.py runs/<run-id> [--all-attempts] [--tail 3]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from forkloop.policies.action_parse import to_compact

CLASSES = ("invalid/parse", "wrong-record", "transcription", "decoy", "budget-sane", "budget-looping")
_XY = re.compile(r"-?\d+")


def _load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def compact(step: dict) -> str:
    a = step.get("action")
    if isinstance(a, dict):
        try:
            return to_compact(a)
        except Exception:
            return json.dumps(a)[:60]
    if step.get("valid") is False:
        note = step.get("policy_note") or ""
        if "unsupported Fara action '" in note:
            return "INVALID(" + note.split("'")[1] + ")"
        return "INVALID(" + note[:30] + ")"
    raw = (step.get("raw_action") or "").strip().splitlines()
    return raw[-1][:60] if raw else "<none>"


def is_looping(actions: list[str], *, window: int = 24, px: int = 20) -> bool:
    """The tail of the episode repeats itself: the same pointer action (within px) five
    or more times, an alternating pair filling the window, or fewer than 40 % distinct
    actions in the last `window` steps."""
    tail = actions[-window:]
    if len(tail) < 8:
        return False
    if len(set(tail)) / len(tail) < 0.4:
        return True
    pts = []
    for a in tail:
        m = re.match(r"^(\w+)\((.*)\)$", a)
        if not m:
            pts.append(None)
            continue
        nums = [int(x) for x in _XY.findall(m.group(2))[:2]]
        pts.append((m.group(1), tuple(nums)) if len(nums) == 2 else (m.group(1), None))
    for i, p in enumerate(pts):
        if p is None or p[1] is None:
            continue
        same = sum(1 for q in pts if q and q[0] == p[0] and q[1] and abs(q[1][0] - p[1][0]) + abs(q[1][1] - p[1][1]) <= px)
        if same >= 5:
            return True
    if len(tail) >= 8 and all(tail[i] == tail[i % 2] for i in range(8)) and tail[0] != tail[1]:
        return True
    return False


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (a transcription slip is 1-2 edits; a different number is many)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def classify(ep: Path) -> dict | None:
    vp, mp = ep / "verdict.json", ep / "manifest.json"
    if not vp.exists() or not mp.exists():
        return None
    v = json.loads(vp.read_text())
    m = json.loads(mp.read_text())
    steps = _load_jsonl(ep / "steps.jsonl")
    n_steps = len(steps) or int(v.get("n_steps") or 0)
    n_invalid = sum(1 for s in steps if s.get("valid") is False) or int(v.get("n_invalid") or 0)
    actions = [compact(s) for s in steps]
    reason = v.get("reason_code")
    end = v.get("end_reason")
    details = v.get("details") or {}
    failed = list(v.get("failed") or [])
    expected = m.get("expected") or {}
    row = {"episode": ep.name, "seed": m.get("seed"), "attempt": m.get("attempt"), "reward": v.get("reward"),
           "reason": reason, "end_reason": end, "n_steps": n_steps, "n_invalid": n_invalid,
           "actions": actions, "superseded": bool(m.get("superseded"))}
    if v.get("reward", 0) >= 1.0:
        row["class"] = "OK"
        row["signature"] = ""
        return row
    inv_rate = n_invalid / n_steps if n_steps else 0.0
    auth = details.get("appeal_auth_number") or {}
    if reason == "INVALID_ACTION_LIMIT" or end == "invalid_actions" or inv_rate > 0.5:
        # the episode was ended by the env's invalid-action limit, or most of its actions were unparseable
        notes = Counter((s.get("policy_note") or "").split("'")[1] if "unsupported Fara action '" in (s.get("policy_note") or "")
                        else "other" for s in steps if s.get("valid") is False)
        cls, sig = "invalid/parse", f"{n_invalid}/{n_steps} invalid: " + ", ".join(f"{k}×{v}" for k, v in notes.most_common(3))
    elif reason in ("WRONG_RECORD", "COLLATERAL_EDIT", "DUPLICATE_SIDE_EFFECT") or \
            ((details.get("no_collateral") or {}).get("unexpected_changes")) or \
            int((details.get("single_appeal") or {}).get("actual") or 0) > 1:
        # a side effect on the wrong row, a collateral edit, or more than one appeal; an
        # appeal count of 0 is "nothing filed" and falls through to the budget classes
        cls = "wrong-record"
        sig = f"no_collateral={ (details.get('no_collateral') or {}).get('unexpected_changes') } single_appeal={(details.get('single_appeal') or {}).get('actual')}"
    elif reason == "WRONG_VALUE" and "appeal_auth_number" in failed:
        actual, exp = str(auth.get("actual")), str(auth.get("expected"))
        if actual in (expected.get("decoy_numbers") or []) or edit_distance(actual, exp) > 2:
            # a listed decoy, or a whole different number (another letter's code): the wrong source
            cls, sig = "decoy", f"typed {actual} (expected {exp})"
        else:
            cls, sig = "transcription", f"typed {actual!r} (expected {exp!r}, edit distance {edit_distance(actual, exp)})"
    elif reason == "WRONG_VALUE":
        cls, sig = "wrong-record", f"failed={failed}"
    else:
        looping = is_looping(actions)
        cls = "budget-looping" if looping else "budget-sane"
        sig = f"end={end} failed={failed} milestones={v.get('milestones')}"
    row["class"], row["signature"] = cls, sig
    return row


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run")
    p.add_argument("--all-attempts", action="store_true")
    p.add_argument("--tail", type=int, default=3, help="last N actions shown per failed episode")
    p.add_argument("--json", default=None, help="write the rows here")
    a = p.parse_args()
    rows = [r for r in (classify(ep) for ep in sorted((Path(a.run) / "episodes").iterdir()) if ep.is_dir()) if r]
    if not a.all_attempts:
        rows = [r for r in rows if not r["superseded"]]
    failed = [r for r in rows if r["class"] != "OK"]
    n = len(rows)
    tot_steps = sum(r["n_steps"] for r in rows)
    tot_inv = sum(r["n_invalid"] for r in rows)
    print(f"episodes {n}, success {n - len(failed)}/{n}, invalid actions {tot_inv}/{tot_steps} "
          f"({100.0 * tot_inv / tot_steps if tot_steps else 0:.1f} %)")
    print("reason codes:", dict(sorted(Counter(r["reason"] for r in rows).items())))
    print("classes:", {c: sum(1 for r in failed if r["class"] == c) for c in CLASSES})
    print()
    print("| seed | reason | end | steps | invalid | class | signature | last actions |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in failed:
        tail = " → ".join(x.replace("|", "\\|") for x in r["actions"][-a.tail:])
        print(f"| {r['seed']} | {r['reason']} | {r['end_reason']} | {r['n_steps']} | {r['n_invalid']} | {r['class']} | "
              f"{r['signature'].replace('|', '/')} | {tail} |")
    if a.json:
        Path(a.json).write_text(json.dumps([{k: v for k, v in r.items() if k != 'actions'} for r in rows], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
