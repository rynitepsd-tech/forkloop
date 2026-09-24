"""One-off, frozen reading study: gpt-5.6-luna vs gpt-6-luna on the Sept 16 frozen states.

Copied from runs/detail-diagnostic-20260916.py. The only change to the request is the model
string: both arms use image_detail high and the Sept 16 options (prompt v5, previous + current
screenshot, 8-action history, reasoning_effort high, 4,096-token cap, standard tier).
Pre-registered in docs/protocol-reading-model-upgrade.md. No VM allocations or action execution.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

from forkloop.fixed_metrics import action_agreement, character_error
from forkloop.policies.student import StudentPolicy
from forkloop.spending import SessionLedger
from forkloop.types import Observation
from scripts.evaluation_contract import verify_dataset

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "runs/evaluation-readiness-20260906/saved-dev-v3"
PROMPT = ROOT / "forkloop/policies/prompts/hosted_gui_agent_v5.md"
MODELS = {"A": "gpt-5.6-luna", "B": "gpt-6-luna"}
DETAIL = "high"
OPTIONS = {
    "base_url": "https://api.openai.com/v1",
    "prompt_style": "compact", "image_max_side": 1280,
    "history_k": 8, "prev_screenshot": True, "history_notes": False,
    "hosted_reasoning": True, "max_tokens": 4096, "timeout_s": 120,
    "extra_body": {"reasoning_effort": "high"},
}
SEPT16_PROTOCOL = ROOT / "runs/detail-diagnostic-20260916/protocol.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def observation(case):
    previous, current = [(PACKAGE / name).read_bytes() for name in case["images"]]
    return Observation(current, case["instruction"], case["step"], case["history"],
                       *case["screen_size"], previous)


class RecordedPolicy(StudentPolicy):
    async def _post(self, body):
        try:
            data = await super()._post(body)
        except BaseException as exc:
            self.exception = exc
            raise
        self.response = data
        return data


def policy(model, ledger, *, credential=False):
    return RecordedPolicy(**OPTIONS, model=model, image_detail=DETAIL,
                          system_prompt=PROMPT.read_text(), session_ledger=str(ledger),
                          api_key=os.environ["OPENAI_API_KEY"] if credential else None)


def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def body_diff(left, right):
    a, b = flatten(left), flatten(right)
    return sorted(key for key in a.keys() | b.keys() if a.get(key, object()) != b.get(key, object()))


def without_model(body):
    body = copy.deepcopy(body)
    body.pop("model", None)
    return body


def metrics(action, meta, label):
    target = label.get("expected_authorization")
    typed = bool(action and action.get("type") == "type")
    return {
        "exact_authorization_type": bool(typed and action["text"] == target) if target else None,
        "wrong_authorization_type": bool(typed and action["text"] != target) if target else None,
        "authorization_type_attempt": typed if target else None,
        "wrong_type_edit_distance": character_error(target, action["text"])["edit_distance"] if target and typed and action["text"] != target else None,
        "teacher_action_agreement": action_agreement(action, label["expected_action"]) if label["kind"] == "navigation" else None,
        "parse_failure": action is None,
        "truncated": meta.get("finish_reason") == "length",
    }


def is_transport_error(exc):
    """Pre-registered retryable class: the request may never have reached inference."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, asyncio.TimeoutError, TimeoutError, OSError))


async def freeze(out):
    manifest, cases, labels = verify_dataset(PACKAGE)
    assert len(cases) == 40 and len(labels) == 40
    out.mkdir(parents=True, exist_ok=False)
    source_files = [Path(__file__), ROOT / "forkloop/policies/student.py",
                    ROOT / "forkloop/policies/observation.py", ROOT / "forkloop/policies/action_parse.py",
                    ROOT / "forkloop/spending.py", ROOT / "forkloop/fixed_metrics.py"]
    sept16 = json.loads(SEPT16_PROTOCOL.read_text())
    sept16_high = {cell["case_id"]: cell["request_sha256"] for cell in sept16["cells"] if cell["arm"] == "B"}
    protocol = {
        "schema": "forkloop.reading-model-upgrade.v1", "frozen_at": time.time(),
        "question": "Does gpt-6-luna type the exact authorization less often than gpt-5.6-luna on the same frozen teacher-reached states at high image detail?",
        "models": MODELS, "image_detail": DETAIL, "options": OPTIONS, "prompt_sha256": file_digest(PROMPT),
        "dataset_manifest_sha256": file_digest(PACKAGE / "manifest.json"),
        "cases_sha256": file_digest(PACKAGE / "cases.jsonl"),
        "labels_sha256": file_digest(PACKAGE / "labels.jsonl"),
        "review_sha256": file_digest(PACKAGE / "review.json"),
        "source_sha256": {str(path.relative_to(ROOT)): file_digest(path) for path in source_files},
        "sept16_protocol_sha256": file_digest(SEPT16_PROTOCOL),
        "primary_metric": "exact parsed runtime type action on the 20 authorization observations; exact two-sided McNemar, alpha 0.05",
        "secondary_metrics": ["teacher navigation agreement on the 20 navigation states (imitation, not correctness)",
                              "wrong typed values and their pattern (dropped repeated character, letter/digit confusion, other)",
                              "unparsable/truncated responses", "provider-reported tokens and usage-derived cost"],
        "population": "the Sept 16 package: 20 gpt-5.6-luna workflow-v5 success episodes; 40 frozen observations; not independent navigation or held-out workflow evaluation",
        "arms": {"A": MODELS["A"], "B": MODELS["B"]}, "best_of": 1,
        "retries": "one retry per cell, only for a transport error (httpx transport error, timeout, OSError, HTTP 429 or 5xx); logged in the cell",
        "order": "A/B then B/A, alternating within each observation kind in original package order",
        "stop_rule": "Stop after a non-transport provider/identity/accounting failure, or a transport error that fails its one retry; remaining planned cells stay missing. Invalid or truncated model actions are scored failures, not retried.",
        "max_seconds": 7200, "per_request_seconds": 120,
        "openai_ledger": "runs/session-20260924/session-ledger.sqlite (ceiling $20, stop $18)",
        "no_tools_or_actions_executed": True, "cells": [],
    }
    kind_indices = {}
    reproduced = 0
    for case in cases:
        label = labels[case["case_id"]]
        kind = label["kind"]
        index = kind_indices.get(kind, 0)
        kind_indices[kind] = index + 1
        order = ["A", "B"] if index % 2 == 0 else ["B", "A"]
        bodies = {}
        for arm in order:
            p = policy(MODELS[arm], out / "unused-ledger.sqlite")
            try:
                body, _ = p.build_request(observation(case))
            finally:
                await p.aclose()
            bodies[arm] = body
            protocol["cells"].append({"case_id": case["case_id"], "arm": arm, "model": MODELS[arm],
                "kind": kind, "seed": label["seed"], "request_sha256": digest(body),
                "without_model_sha256": digest(without_model(body)),
                "image_sha256": [file_digest(PACKAGE / name) for name in case["images"]]})
        assert body_diff(bodies["A"], bodies["B"]) == ["model"], body_diff(bodies["A"], bodies["B"])
        reproduced += digest(bodies["A"]) == sept16_high[case["case_id"]]
    assert kind_indices == {"navigation": 20, "authorization": 20}
    protocol["arm_body_diff"] = ["model"]
    protocol["arm_A_bodies_equal_sept16_high_arm"] = f"{reproduced}/40"
    save(out / "protocol.json", protocol)
    (out / "protocol.sha256").write_text(file_digest(out / "protocol.json") + "\n")
    save(out / "preflight.json", {"calls_made": 0, "matched_inputs_except_model": True,
        "differing_body_fields": ["model"], "planned_cells": len(protocol["cells"]), "case_counts": kind_indices,
        "arm_A_bodies_equal_sept16_high_arm": f"{reproduced}/40"})
    print(json.dumps({"prepared": str(out), "cells": len(protocol["cells"]), "reproduced_sept16": reproduced,
                      "protocol_sha256": file_digest(out / "protocol.json")}), flush=True)


async def attempt(row, case, ledger, label):
    p = policy(row["model"], ledger.path, credential=True)
    p.response = p.exception = None
    try:
        obs = observation(case)
        body, _ = p.build_request(obs)
        assert digest(body) == row["request_sha256"], "request drift"
        async with asyncio.timeout(120):
            action, meta = await p.act(obs)
        return {"meta": meta, "action": action.to_dict() if action else None, "response": p.response,
                "calls": p.n_requests, "exception": p.exception}
    except (asyncio.TimeoutError, TimeoutError) as exc:
        return {"meta": {"error": True, "note": f"{type(exc).__name__}"}, "action": None, "response": p.response,
                "calls": p.n_requests, "exception": exc}
    finally:
        await p.aclose()


async def run(out):
    protocol_path = out / "protocol.json"
    assert file_digest(protocol_path) == (out / "protocol.sha256").read_text().strip()
    protocol = json.loads(protocol_path.read_text())
    _, cases, labels = verify_dataset(PACKAGE)
    assert file_digest(PACKAGE / "manifest.json") == protocol["dataset_manifest_sha256"]
    assert file_digest(PROMPT) == protocol["prompt_sha256"]
    for relative, expected in protocol["source_sha256"].items():
        assert file_digest(ROOT / relative) == expected, f"source drift: {relative}"
    cases = {case["case_id"]: case for case in cases}
    ledger = SessionLedger(os.environ["FORKLOOP_SESSION_LEDGER"])
    assert ledger.path.resolve() == (ROOT / "runs/session-20260924/session-ledger.sqlite").resolve()
    result_path = out / "results.json"
    assert not result_path.exists(), "results already exist; scored cells are never rerun"
    result = {"protocol_sha256": file_digest(protocol_path), "started_at": time.time(),
              "stop_reason": None, "cells": [{**cell, "status": "missing"} for cell in protocol["cells"]]}
    save(result_path, result)
    deadline = time.monotonic() + protocol["max_seconds"]
    for row in result["cells"]:
        if result["stop_reason"]:
            row["error"] = result["stop_reason"]
            continue
        if time.monotonic() + 2 * protocol["per_request_seconds"] + 5 > deadline:
            result["stop_reason"] = "experiment_deadline_reserve"
            row["error"] = result["stop_reason"]
            continue
        row.update(status="started", started_at=time.time(), attempts=[])
        save(result_path, result)
        try:
            for attempt_index in range(2):
                got = await attempt(row, cases[row["case_id"]], ledger, labels[row["case_id"]])
                exc = got.pop("exception")
                row["attempts"].append({"error": f"{type(exc).__name__}: {exc}" if exc else None,
                                        "meta_error": bool(got["meta"].get("error")), "at": time.time()})
                if got["meta"].get("error") and exc is not None and is_transport_error(exc) and attempt_index == 0:
                    continue  # the one pre-registered transport retry
                break
            row.update(meta=got["meta"], action=got["action"], response=got["response"],
                       finished_at=time.time(), calls=got["calls"], retried=len(row["attempts"]) > 1)
            if got["meta"].get("error"):
                raise RuntimeError(f"policy_provider_or_response_failure: {got['meta'].get('note')}")
            if not got["response"] or got["response"].get("model") != row["model"]:
                raise RuntimeError("provider_reported_model_mismatch")
            if got["calls"] != 1:
                raise RuntimeError("unexpected_request_count")
            row.update(status="completed", metrics=metrics(row["action"], got["meta"], labels[row["case_id"]]))
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())
            result["stop_reason"] = "request_failed_after_rule"
        finally:
            save(out / "ledger.json", ledger.summary())
            save(result_path, result)
        print(json.dumps({"case_id": row["case_id"], "arm": row["arm"], "status": row["status"],
                          "retried": row.get("retried")}), flush=True)
    result["finished_at"] = time.time()
    save(result_path, result)
    save(out / "ledger.json", ledger.summary())
    print(json.dumps({"finished": True, "completed": sum(r["status"] == "completed" for r in result["cells"]),
                      "stop_reason": result["stop_reason"], "spending": ledger.headroom()}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "run"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(freeze(args.out) if args.mode == "prepare" else run(args.out))
