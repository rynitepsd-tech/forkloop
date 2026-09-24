"""Exploratory follow-up: is gpt-6-luna's misreading a prompt problem or a perception problem?

gpt-6-luna only, on the 20 frozen authorization states of runs/reading-model-upgrade-20260924.py.
Three arms, one request each per state, arm order rotated by state:
  base     the unchanged Phase 1 arm-B request (v5 prompt), re-run to measure sampling noise;
  careful  the v5 system prompt plus one paragraph asking for character-by-character transcription;
  tiles    the base request plus the Sept 16 magnified tiles (2x2 tiles of both images, 2x nearest).
Frozen in docs/protocol-gpt6-reading-probe.md before any request. No VM, no action executed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import runpy
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
P1 = runpy.run_path(str(ROOT / "runs/reading-model-upgrade-20260924.py"))
TILED = ROOT / "runs/tiled-detail-diagnostic-20260916.py"
INTRO = runpy.run_path(str(TILED))["INTRO"]
MODEL = "gpt-6-luna"
ARMS = ("base", "careful", "tiles")
CAREFUL = ("\n\nReading identifiers: when you copy a number or code from a document (an authorization number, "
           "member ID or claim number), read it one character at a time from the image, write every character "
           "in your reasoning, and check that repeated characters (for example 55 or 00) are all there before "
           "you type it. Type it exactly as printed; never shorten or normalize it.")


class ProbePolicy(P1["RecordedPolicy"]):
    arm = "base"

    def build_request(self, obs, n=1):
        body, ctx = super().build_request(obs, n=n)
        if self.arm == "tiles":
            tiles = []
            for role, png in (("previous", obs.previous_screenshot), ("current", obs.screenshot)):
                with Image.open(io.BytesIO(png)) as image:
                    width, height = image.size
                    for top, bottom in ((0, height // 2), (height // 2, height)):
                        for left, right in ((0, width // 2), (width // 2, width)):
                            tile = image.crop((left, top, right, bottom)).resize(
                                ((right - left) * 2, (bottom - top) * 2), Image.Resampling.NEAREST)
                            out = io.BytesIO()
                            tile.save(out, format="PNG")
                            tiles += [{"type": "text", "text": f"{role} screenshot, original region x={left}..{right-1}, y={top}..{bottom-1}."},
                                      {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode(), "detail": "high"}}]
            body["messages"][-1]["content"].extend([{"type": "text", "text": INTRO}, *tiles])
        return body, ctx


def policy(arm, ledger, *, credential=False):
    prompt = P1["PROMPT"].read_text() + (CAREFUL if arm == "careful" else "")
    p = ProbePolicy(**P1["OPTIONS"], model=MODEL, image_detail="high", system_prompt=prompt,
                    session_ledger=str(ledger), api_key=os.environ["OPENAI_API_KEY"] if credential else None)
    p.arm = arm
    return p


async def freeze(out):
    _, cases, labels = P1["verify_dataset"](P1["PACKAGE"])
    selected = [c for c in cases if labels[c["case_id"]]["kind"] == "authorization"]
    assert len(selected) == 20
    out.mkdir(parents=True, exist_ok=False)
    phase1 = json.loads((ROOT / "runs/reading-model-upgrade-20260924/protocol.json").read_text())
    phase1_b = {c["case_id"]: c["request_sha256"] for c in phase1["cells"] if c["arm"] == "B"}
    protocol = {"schema": "forkloop.gpt6-reading-probe.v1", "frozen_at": time.time(), "model": MODEL,
                "arms": list(ARMS), "careful_addendum": CAREFUL, "tile_intro": INTRO,
                "runner_sha256": P1["file_digest"](Path(__file__)), "prompt_sha256": P1["file_digest"](P1["PROMPT"]),
                "scope": "Exploratory, after the confirmatory results; not pre-registered as confirmatory evidence.",
                "stop_rule": "One transport retry per cell (Phase 1 rule); any other failure stops the run.", "cells": []}
    for index, case in enumerate(selected):
        order = ARMS[index % 3:] + ARMS[:index % 3]
        for arm in order:
            p = policy(arm, out / "unused.sqlite")
            try:
                body, _ = p.build_request(P1["observation"](case))
            finally:
                await p.aclose()
            if arm == "base":
                assert P1["digest"](body) == phase1_b[case["case_id"]], "base arm must equal Phase 1 arm B"
            protocol["cells"].append({"case_id": case["case_id"], "seed": labels[case["case_id"]]["seed"], "arm": arm,
                                      "model": MODEL, "request_sha256": P1["digest"](body)})
    P1["save"](out / "protocol.json", protocol)
    (out / "protocol.sha256").write_text(P1["file_digest"](out / "protocol.json") + "\n")
    print(json.dumps({"prepared": str(out), "cells": len(protocol["cells"]), "protocol_sha256": P1["file_digest"](out / "protocol.json")}))


async def run(out):
    protocol = json.loads((out / "protocol.json").read_text())
    assert P1["file_digest"](out / "protocol.json") == (out / "protocol.sha256").read_text().strip()
    assert P1["file_digest"](Path(__file__)) == protocol["runner_sha256"]
    _, cases, labels = P1["verify_dataset"](P1["PACKAGE"])
    cases = {c["case_id"]: c for c in cases}
    ledger = P1["SessionLedger"](os.environ["FORKLOOP_SESSION_LEDGER"])
    result_path = out / "results.json"
    assert not result_path.exists(), "results already exist; cells are never rerun"
    results = {"protocol_sha256": P1["file_digest"](out / "protocol.json"), "started_at": time.time(),
               "stop_reason": None, "cells": [{**c, "status": "missing"} for c in protocol["cells"]]}
    P1["save"](result_path, results)
    for row in results["cells"]:
        if results["stop_reason"]:
            continue
        row.update(status="started", attempts=[])
        try:
            for attempt in range(2):
                p = policy(row["arm"], ledger.path, credential=True)
                p.response = p.exception = None
                try:
                    obs = P1["observation"](cases[row["case_id"]])
                    body, _ = p.build_request(obs)
                    assert P1["digest"](body) == row["request_sha256"], "request drift"
                    async with asyncio.timeout(120):
                        action, meta = await p.act(obs)
                finally:
                    await p.aclose()
                exc = p.exception
                row["attempts"].append({"error": f"{type(exc).__name__}: {exc}" if exc else None})
                if meta.get("error") and exc is not None and P1["is_transport_error"](exc) and attempt == 0:
                    continue
                break
            row.update(action=action.to_dict() if action else None, meta=meta, response=p.response,
                       retried=len(row["attempts"]) > 1)
            if meta.get("error") or not p.response or p.response.get("model") != MODEL:
                raise RuntimeError(f"provider or identity failure: {meta.get('note')}")
            row.update(status="completed", metrics=P1["metrics"](row["action"], meta, labels[row["case_id"]]))
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {exc}")
            results["stop_reason"] = "request_failed_after_rule"
        finally:
            P1["save"](result_path, results)
        print(json.dumps({"seed": row["seed"], "arm": row["arm"], "status": row["status"]}), flush=True)
    results["finished_at"] = time.time()
    P1["save"](result_path, results)
    P1["save"](out / "ledger.json", ledger.summary())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "run"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(freeze(args.out) if args.mode == "prepare" else run(args.out))
