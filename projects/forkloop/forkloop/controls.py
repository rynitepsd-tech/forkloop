"""Construct reportable resolve_denial verifier controls without an account.

Run after installing Forkloop with the ``world`` extra:
    forkloop demo --out runs/offline-controls

These are controller-constructed states, NOT policy navigation or model scores.
The fake desktop screenshots are blank simulator frames. Portal submissions use
real HTTP routes and SQLite transactions; verdicts come only from Env.verify.
The interrupted control exits a child process before verification, leaving the
recorder's already-flushed artifacts intact, without inventing/deleting a verdict.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile

from fastapi.testclient import TestClient

from forkloop.actions import Action
from forkloop.backends.fake import FakeBackend
from forkloop.env import Env
from forkloop.oracle import Verdict
from forkloop.trajectories import Recorder, load_episode
from forkloop.world import load_world
from worlds.claims_ops_v1.portal.app import create_app


SCENARIOS = {
    "legitimate_correct_appeal": "Offline reference: controller submits exactly one correct appeal through the portal HTTP route.",
    "wrong_authorization": "Controller submits one appeal with a seeded decoy authorization through the portal HTTP route.",
    "wrong_record": "Controller submits the correct appeal, then unintentionally appeals a distractor's claim through the portal HTTP route.",
    "duplicate_appeal": "Controller submits the correct appeal twice through the portal HTTP route.",
    "interrupted_missing_verdict": "Controller logs into the portal and records one wait, then abruptly exits the recording process before done or verify; no verdict exists.",
}


def _fixture_seed() -> int:
    world = load_world("claims-ops-v1")
    # Same bounded development-fixture convention as the portal regression tests.
    # Select one ordinary task for ALL scenarios; do not alter its oracle/labels.
    for seed in range(20, 200):
        task = world.generate("resolve_denial", seed, "train")
        if task.expected["distractor_claims"] and not task.difficulty["require_attachment"]:
            return seed
    raise RuntimeError("No development fixture with a distractor and no required attachment")


def _submit(client: TestClient, claim: str, authorization: str) -> None:
    response = client.post(
        f"/claims/{claim}/appeal",
        data={"reason_code": "PRECERT_OBTAINED", "authorization_number": authorization,
              "narrative": "Prior authorization was obtained before the service date."},
        follow_redirects=False,
    )
    if response.status_code != 303:
        raise RuntimeError(f"Appeal submission failed: HTTP {response.status_code}: {response.text[:300]}")


async def _record(out: Path, scenario: str, seed: int, fake_dir: Path) -> tuple[Path, Verdict]:
    world = load_world("claims-ops-v1")
    backend = FakeBackend(base_dir=fake_dir)
    evidence = {
        "backend": "fake", "evidence_kind": "constructed_control",
        "evidence_note": SCENARIOS[scenario] + " Expected values are controller-only; not a policy rollout or live GUI evidence.",
        "control_scenario": scenario,
    }
    recorder = Recorder(root=out, run_id=scenario, meta={**evidence, "world": world.name, "seed": seed})
    env = Env(world, backend, family="resolve_denial", split="train", settle_s=0,
              recorder=recorder, record_extra=evidence)
    try:
        await env.reset(seed, episode_id="control")
        ep = env.ep
        assert ep is not None and ep.recorder is not None
        episode_dir = ep.recorder.dir
        machine = ep.machine
        app = create_app(db_path=machine._local(world.config.paths["portal_db"]),
                         uploads_dir=machine._local(world.config.paths["portal_uploads"]), secret="test")
        with TestClient(app) as client:
            login = client.post("/login", data={"username": "agent", "password": "agent"}, follow_redirects=False)
            if login.status_code != 303 or "portal_session" not in client.cookies:
                raise RuntimeError(f"Portal login failed: HTTP {login.status_code}")
            if scenario != "interrupted_missing_verdict":
                expected = ep.task.expected
                authorization = (expected["decoy_numbers"][0] if scenario == "wrong_authorization"
                                 else expected["auth_number"])
                _submit(client, expected["claim_number"], authorization)
                if scenario == "wrong_record":
                    _submit(client, expected["distractor_claims"][0], expected["auth_number"])
                elif scenario == "duplicate_appeal":
                    _submit(client, expected["claim_number"], authorization)
        if scenario == "interrupted_missing_verdict":
            await env.step(Action.wait(0), meta={"note": evidence["evidence_note"]})
            # A real abrupt exit closes OS handles but deliberately cannot run Env.close:
            # graceful close would author an abandonment verdict. The supervising parent
            # owns the temporary directory and removes all fake machine/snapshot state.
            os._exit(0)
        await env.step(Action.done(), meta={"note": evidence["evidence_note"]})
        return episode_dir, await env.verify()
    finally:
        try:
            await env.close()
        finally:
            try:
                await backend.close()
            finally:
                backend.cleanup()


def _interrupted_worker(out: Path, seed: int, fake_dir: Path) -> None:
    asyncio.run(_record(out, "interrupted_missing_verdict", seed, fake_dir))


def _accepted(scenario: str, verdict: Verdict) -> bool:
    if scenario == "legitimate_correct_appeal":
        return verdict.success and verdict.reason_code == "OK" and not verdict.failed
    if scenario == "wrong_record":
        return (verdict.reward == 0.0
                and {"no_collateral", "distractor_0_untouched"}.issubset(verdict.failed)
                and verdict.details.get("distractor_0_untouched", {}).get("reason_code") == "WRONG_RECORD")
    failed = {
        "wrong_authorization": "appeal_auth_number",
        "duplicate_appeal": "single_appeal",
    }[scenario]
    reason = {
        "wrong_authorization": "WRONG_VALUE",
        "duplicate_appeal": "DUPLICATE_SIDE_EFFECT",
    }[scenario]
    return verdict.reward == 0.0 and verdict.reason_code == reason and failed in verdict.failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("runs/offline-controls"),
                        help="New destination directory; existing paths are never overwritten")
    args = parser.parse_args(argv)
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Destination already exists: {args.out}; choose a new --out")
    seed = _fixture_seed()
    print(f"Constructed offline controls, development seed {seed}; not model/policy performance.")
    failures = []
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix="forkloop-verification-control-") as temporary:
            fake_dir = Path(temporary) / "fake"
            if scenario == "interrupted_missing_verdict":
                process = multiprocessing.get_context("spawn").Process(
                    target=_interrupted_worker, args=(args.out, seed, fake_dir))
                process.start()
                try:
                    process.join(timeout=120)
                    if process.is_alive():
                        raise RuntimeError("Interrupted control did not finish within 120 seconds")
                    if process.exitcode != 0:
                        raise RuntimeError(f"Interrupted control failed: child exit {process.exitcode}")
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join()
                    process.close()
                episode_dir = args.out / scenario / "episodes" / "control"
                recorded = load_episode(episode_dir)
                ok = (recorded["verdict"] is None and recorded["reset"] is not None
                      and len(recorded["steps"]) == 1)
                print(f"{scenario}: {episode_dir}\n  verdict=MISSING (unscored, not a verifier rejection)")
            else:
                episode_dir, verdict = asyncio.run(_record(args.out, scenario, seed, fake_dir))
                ok = _accepted(scenario, verdict)
                print(f"{scenario}: {episode_dir}\n  reward={verdict.reward:g} reason={verdict.reason_code} failed={verdict.failed}")
                for check in verdict.failed:
                    print(f"  {check}: {json.dumps(verdict.details[check], sort_keys=True)}")
            if not ok:
                failures.append(scenario)
            from forkloop.report_html import html_report

            destination = args.out / scenario / "report.html"
            destination.write_text(html_report(episode_dir), encoding="utf-8")
            print(f"  report: {destination}")
    if failures:
        print(f"Control expectations NOT met: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("All constructed control expectations met. Report each scenario run or its episode separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
