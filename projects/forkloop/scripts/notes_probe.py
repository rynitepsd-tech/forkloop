"""Offline recipe-v4 (history notes) probe on frozen teacher-reached states.

Runs StudentPolicy -- the production serving path -- against a loopback server
(scripts/lambda_serve.py) for each frozen case and condition. Expected values are
read only by the scorer after generation. See runs/v4-notes-20260922/eval/protocol.json.

    python -m scripts.notes_probe --cases CASES.jsonl --label v4 --notes --out OUT.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from forkloop.policies.student import StudentPolicy
from forkloop.types import Observation

NOTE = ("Credentials for OpenEMR: username admin, password pass. Click the Username field, type admin, "
        "click the Password field, type pass, click Login.")
CONDITIONS = {"type_paired": ("type", True), "type_current_only": ("type", False), "read": ("read", True)}


async def run(a: argparse.Namespace) -> None:
    cases = [json.loads(line) for line in Path(a.cases).read_text().splitlines() if line.strip()]
    out = Path(a.out)
    if out.exists():
        raise SystemExit("refusing to overwrite an existing result")
    rows = []
    for condition, (kind, paired) in CONDITIONS.items():
        for case in (c for c in cases if c["kind"] == kind):
            policy = StudentPolicy(a.base_url, "fara", prompt_style="fara", coord_space="norm1000", max_tokens=512,
                                   image_max_side=1280, history_k=8, prev_screenshot=paired,
                                   system_prompt=Path("forkloop/policies/prompts/fara_no_user_v1.md").read_text(),
                                   nav_macro=True, instruction_note=NOTE, timeout_s=180, history_notes=a.notes)
            try:
                history = case["history"]
                if a.notes:
                    base = case["step"] - len(history)
                    policy._notes = {base + i: n for i, n in enumerate(case["notes"])}
                current = Path(case["images"][-1]).read_bytes()
                previous = Path(case["images"][0]).read_bytes() if paired and len(case["images"]) == 2 else b""
                obs = Observation(current, case["instruction"], case["step"], history, *case["screen_size"], previous)
                request, _ = policy.build_request(obs)
                action, meta = await policy.act(obs)
            finally:
                await policy.aclose()
            # Scoring: the only place the expected value is read.
            auth = case["expected_authorization"]
            got = action.to_dict() if action is not None else None
            raw = str(meta.get("raw_action") or "")
            row = {"condition": condition, "seed": case["seed"], "episode_id": case["episode_id"], "step": case["step"],
                   "action": got, "raw": raw, "error": meta.get("error"),
                   "exact_type": bool(got and got.get("type") == "type" and got.get("text") == auth),
                   "wrong_type": bool(got and got.get("type") == "type" and str(got.get("text", "")).startswith("AUTH")
                                      and got.get("text") != auth),
                   "number_in_reply": auth in raw,
                   "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()}
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("condition", "seed", "exact_type", "number_in_reply", "error")}), flush=True)
            out.write_text(json.dumps({"label": a.label, "notes": a.notes, "cases_sha256":
                                       hashlib.sha256(Path(a.cases).read_bytes()).hexdigest(), "rows": rows}, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--notes", action="store_true", help="serve with history notes (recipe v4)")
    p.add_argument("--base-url", default="http://127.0.0.1:8011/v1")
    p.add_argument("--out", required=True)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
