# CLAUDE.md — projects/forkloop

Guidance for Claude Code (and humans) working in this directory. Keep it short; the long-form explanation is `system.md`, the interface spec is `docs/contracts.md`.

**Starting a fresh session? Read [`docs/HANDOFF.md`](docs/HANDOFF.md) first** — current state, what is blocked, what to do next, and the setup steps (the `venv` symlink does not survive a session).

## What this is

A Python 3.11 library + two worlds + training scripts. `reset()` on a Solari desktop is one `revert()`; `fork()` is one `create(from_snapshot=...)`. A deterministic SQL oracle computes rewards. No LLM is ever in the reward path.

## Commands

```bash
# from projects/forkloop, with the venv active (python3.11)
pip install -e ".[dev,world]"
pytest                                   # ~2 min, all offline, no keys needed
pytest tests/test_core_toy.py -x         # fastest signal for core changes
pytest tests/test_claims_ops_world.py    # oracle + world on the fake backend
forkloop worlds                          # sanity check the CLI installs
forkloop task --family resolve_denial --seed 42 --full   # inspect a generated manifest
```

Tests hang rather than fail when a pool worker is not released; if `pytest` stalls, run with `-o faulthandler_timeout=40 -x -v`.

## Rules that are not obvious from the code

- **Two channels.** Anything reachable by the policy under evaluation is the *agent channel*: screenshots in, `Action` out. Never add shell, file, DB, or task-metadata access to `Observation`, `Env.step`, or any policy input. `info` dicts from the env must never contain `expected`, `seeding`, or the oracle spec (`TaskInstance.public_info` is the allowed subset).
- **Everything the agent can touch lives inside the VM snapshot.** If you add state outside it (a controller-side cache of DB rows, a local downloads dir), `revert()` no longer restores the world. The recorder is controller-side on purpose; it is an observer, not state.
- **`docs/contracts.md` is the spec.** Portal schema, OpenEMR table subset, action JSON, `TaskInstance`/`Seeding`, `Check`/`OracleSpec`/`Verdict`, run-directory layout, and the Solari SDK mapping. Change the contract and the code in the same commit.
- **SQL portability.** Per-episode SQL runs on MariaDB in the VM and on SQLite in tests. Explicit primary keys ≥ 100000 (base data) / ≥ 500000 (episodes), no `NOW()`, no backticks, no `ON DUPLICATE KEY`, no backslashes in literals (`forkloop.util.sql.quote` refuses them). `worlds/claims_ops_v1/openemr/openemr_sql.assert_portable` checks a script.
- **Audit keys.** Portal `audit_log.entity` is the table name and `entity_id` is the row's primary key as text (claim numbers go in `detail_json`). The oracle joins audit rows to checksum diffs by primary key. OpenEMR's `log` is patient-keyed; `world.yaml` `oracle.audit_id_lookup` maps changed rows to the patient column.
- **Generators are pure.** `generate(family, seed, split)` must be byte-identical across calls and machines: seed every `random.Random` from the `(family, split, seed)` string, never from time or `os.urandom`, and never read anything except `portal/base_data.json` and `openemr/base_data.json`.
- **Base data has one source of truth.** OpenEMR's `base_data.json` defines the people; the portal derives patients/providers from it. Regenerate the portal JSON (`python -m worlds.claims_ops_v1.portal.base_data`) after changing the OpenEMR one, and re-run `tests/test_portal.py`.
- **Solari SDK facts** (verified against solari-sandbox 0.2.0): desktops with `from_snapshot` come from `SandboxClient.create_desktop`, not `DesktopClient.create`; `commands.run` is argv, not shell; `kill()` destroys, `close()` only drops the channel; after `revert()` the guest briefly accepts one control connection, so the backend reconnects and re-polls `health()`. The PyPI package literally named `solari` is unrelated; depend on `solari-sandbox` and `solari-desktop`.
- **What the test account can do (measured 2026-09-01, Free plan).** Desktops are 402-gated; use `FORKLOOP_SOLARI_KIND=sandbox` (headless: full controller loop, no agent channel). `revert()` returns 409 `Not revertable` and *destroyed a running sandbox* — never call it on a machine you need until Solari confirms the semantics; use pool `mode="fork"` (≈ 19 s p50; the pool falls back to fork on its own when a revert is refused, logging `revert_unsupported_fell_back_to_fork`, but the first worker wastes ~30 s discovering it). `disk_gb` is not honoured (4 GB disk; golden image ~89% full). The key lives in `~/.config/forkloop/env` (never in the repo); the golden snapshot id is `FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1` there. `forkloop build-world --attach <id>` resumes a failed build on the same machine; `forkloop reap` kills leftovers (the plan allows one machine at a time).
- **Desktop facts (Starter, 2026-09-02).** Session user is `desktop` on Xvfb `:0` (Chrome must run as that user; `runuser -u desktop -- env DISPLAY=:0 HOME=/home/desktop XDG_RUNTIME_DIR=/run/desktop`). Chords are one xdotool string (`"ctrl+a"`); the backend joins them. Click the omnibox at (640, 90) before typing a URL. Golden desktop snapshot: `FORKLOOP_GOLDEN_SNAPSHOT_CLAIMS_OPS_V1=snap_dl4e90g095y2` (v5, ≈550 MB free; v4 `snap_dl4e05ciyt1p` and its parent `snap_dl4driq97904` are ancestors and cannot be deleted while children exist). A fork with a full disk breaks OpenEMR ("table 'log' is full"); keep the image lean. The OpenEMR session is not in the snapshot: that family starts on the login page. Portal field positions (1280×720): login user (340,309) pass (340,391) button (90,449); appeal reason (340,382) auth (340,464) narrative (340,595). OpenEMR login user (706,422) pass (684,476) button (640,585).
- **OpenEMR 8.3 CLI scripts refuse root** (`RootCliGuard`); the installer and the uuid backfill run as `www-data` via `su -s /bin/sh www-data -c`. Debian 12 gets PHP 8.3 from packages.sury.org. No `tr … | head -c` under `pipefail` (exit 141).
- **Never fabricate results.** Anything not measured stays "not yet measured" in README/docs. The fake backend's timings are simulator numbers and are labelled as such by `reset_benchmark.py`.
- **Anthropic API code** uses the official `anthropic` SDK, model `claude-opus-5` by default, the GA `computer_toolset_20260801` (no beta header), adaptive thinking (do not set `budget_tokens`), and server-side refusal fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`) — `forkloop/policies/teacher.py`. The student policy talks to any OpenAI-compatible vLLM endpoint through `httpx` and never imports `openai`.

## Where things are

| Need | Look in |
| --- | --- |
| add an action type | `forkloop/actions.py` (schema + parse), `forkloop/backends/base.py::apply_action`, both backends, `policies/action_parse.py` |
| add a world | `worlds/<pkg>/world.yaml` + a `World` subclass; register nothing — the registry scans `worlds/*/world.yaml` |
| add a task family | `worlds/claims_ops_v1/tasks/<family>.py::generate` + the `families:` list in `world.yaml` + `seed_world.FAMILIES` |
| add an oracle check kind | `forkloop/oracle.py::Oracle._run_check` and `CHECK_KINDS`; document it in `docs/contracts.md` §6 |
| change reset stages | `forkloop/reset.py` (every stage is timed and appears in `reset.json` and the benchmark) |
| pool / concurrency / orphans | `forkloop/pool.py` |
| run-directory format | `forkloop/trajectories.py` + `docs/contracts.md` §10; exporters and `train/make_sft.py` read only that |

## Style

`from __future__ import annotations`, dataclasses for records, plain dicts on the wire, snake_case except Solari's camelCase wire keys. Async everywhere the network is involved; `forkloop.sync` is intentionally absent — use `asyncio.run`. No new runtime dependencies without adding them to `pyproject.toml` extras. Tests must run offline.
