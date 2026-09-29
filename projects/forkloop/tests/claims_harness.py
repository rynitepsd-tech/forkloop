"""A fast, faithful stand-in for one claims-ops-v1 episode, for verifier tests.

Everything that decides a verdict is the production code: the world's database config and
``feasibility`` gate, ``forkloop.seed.apply_seeding``, ``DbAccess`` (same SQL, same helper
scripts), ``Baseline.capture`` with the reset's arguments, the portal's FastAPI routes (the UI
path), and ``Oracle.evaluate`` with ``world.oracle_context``. The only difference from
``Env`` on the fake backend is that ``DbAccess``'s ``python3 -c`` helper scripts run in this
process instead of a subprocess (~25 ms each), which makes a verdict ~20x cheaper. OpenEMR's
UI is simulated as the tests in ``test_claims_ops_world.py`` do: the row edit plus the
``log`` row OpenEMR's EventAuditLogger writes.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from fastapi.testclient import TestClient

from forkloop.backends.fake import _PATH_RE
from forkloop.dbaccess import DbAccess
from forkloop.oracle import Baseline, Oracle, Verdict
from forkloop.seed import apply_seeding
from forkloop.world import load_world
from worlds.claims_ops_v1.openemr import openemr_sql as osql
from worlds.claims_ops_v1.portal.app import create_app

WORLD = load_world("claims-ops-v1")
_TEMPLATE: Optional[Path] = None


class InProcMachine:
    """Enough of a fake machine for DbAccess, seeding and feasibility; helper scripts run in-process."""

    backend_name = "fake"
    capabilities = frozenset({"shell"})

    def __init__(self, root: Path) -> None:
        self.root = root
        self.id = f"inproc-{root.name}"

    def rewrite(self, s: str) -> str:
        return _PATH_RE.sub(lambda m: str(self.root) + m.group(1), s)

    def _local(self, path: str) -> Path:
        rewritten = self.rewrite(path)
        return Path(rewritten if rewritten != path else str(self.root) + path)

    async def exec(self, cmd: str, args: Optional[list[str]] = None, **_: Any) -> Any:
        args = list(args or [])
        if cmd != "python3" or not args or args[0] != "-c":
            raise NotImplementedError(f"InProcMachine runs only DbAccess helper scripts, not {cmd} {args[:1]}")
        script, argv = args[1], [self.rewrite(a) for a in args[2:]]
        for a in argv:
            if a.startswith(str(self.root)):
                Path(a).parent.mkdir(parents=True, exist_ok=True)
        out, err, code = io.StringIO(), io.StringIO(), 0
        old = sys.argv
        sys.argv = ["-c", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(compile(script, "<dbaccess-helper>", "exec"), {"__name__": "__main__"})  # noqa: S102 - our own scripts
        except SystemExit as e:
            code = int(e.code or 0) if not isinstance(e.code, str) else 1
        except Exception as e:  # noqa: BLE001 - mirror a failing subprocess
            err.write(f"{type(e).__name__}: {e}")
            code = 1
        finally:
            sys.argv = old
        return SimpleNamespace(exit_code=code, stdout=out.getvalue(), stderr=err.getvalue())

    async def read_file(self, path: str) -> bytes:
        return self._local(path).read_bytes()

    async def write_file(self, path: str, data: bytes | str, mode: Optional[int] = None) -> None:
        p = self._local(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)


async def _template() -> Path:
    """The golden world (portal SQLite + OpenEMR shim with base data), built once per process."""
    global _TEMPLATE
    if _TEMPLATE is None:
        from worlds.claims_ops_v1.openemr.base_data import load_base_data, render_base_sql
        from worlds.claims_ops_v1.portal.db import init_db, seed_base

        root = Path(tempfile.mkdtemp(prefix="claims-golden-"))
        atexit.register(shutil.rmtree, root, True)
        m = InProcMachine(root)
        portal = m._local(WORLD.config.paths["portal_db"])
        portal.parent.mkdir(parents=True, exist_ok=True)
        init_db(portal)
        seed_base(portal)
        dbs = WORLD.databases(m)
        here = Path(__file__).resolve().parents[1] / "worlds" / "claims_ops_v1" / "openemr"
        await dbs["openemr"].execute_script((here / "shim_schema.sql").read_text())
        await dbs["openemr"].execute_script(render_base_sql(load_base_data()))
        _TEMPLATE = root
    return _TEMPLATE


@dataclass
class Episode:
    task: Any
    machine: InProcMachine
    dbs: dict[str, DbAccess]
    baseline: Baseline
    root: Path
    feasibility: Any
    _next_log: int = 990001

    # ------------------------------------------------------------------ portal (the UI path)
    def portal(self) -> TestClient:
        app = create_app(db_path=self.machine._local(WORLD.config.paths["portal_db"]),
                         uploads_dir=self.machine._local(WORLD.config.paths["portal_uploads"]), secret="test")
        c = TestClient(app)
        r = c.post("/login", data={"username": "agent", "password": "agent"})
        assert r.status_code in (200, 303), r.status_code
        return c

    def appeal(self, claim_number: str, auth: Optional[str], *, reason: str = "PRECERT_OBTAINED",
               attachment: Optional[bytes] = None, client: Optional[TestClient] = None) -> int:
        c = client or self.portal()
        files = {"attachment": ("authorization_letter.pdf", attachment, "application/pdf")} if attachment else None
        r = c.post(f"/claims/{claim_number}/appeal", data={"reason_code": reason, "authorization_number": auth or "",
                                                           "narrative": "Prior authorization was obtained before the service date."},
                   files=files, follow_redirects=False)
        return r.status_code

    def resubmit(self, claim_number: str, member: str, client: Optional[TestClient] = None) -> int:
        c = client or self.portal()
        return c.post(f"/claims/{claim_number}/resubmit", data={"member_id": member, "note": "corrected"},
                      follow_redirects=False).status_code

    # ------------------------------------------------------------------ OpenEMR (row edit + the audit row its UI writes)
    async def _log(self, event: str, patient_id: Any, comments: str, category: str = "") -> str:
        """An audit row as OpenEMR writes it: the next auto-increment id (above every seeded id)."""
        top = await self.dbs["openemr"].scalar("SELECT MAX(id) AS m FROM log")
        self._next_log = max(self._next_log, int(top or 0)) + 1
        return osql.insert_log(id=self._next_log, event=event, category=category, user="admin", patient_id=patient_id,
                               comments=comments, date="2026-09-08 10:00:00")

    async def openemr_log(self, event: str, patient_id: Any, comments: str = "audit") -> None:
        await self.dbs["openemr"].execute_script(await self._log(event, patient_id, comments))

    async def openemr_update_insurance(self, pid: int, member: str, plan: str, *, audited: bool = True, **extra: Any) -> None:
        sql = osql.update_insurance_policy(pid=pid, policy_number=member, plan_name=plan)
        if extra:
            sql += "\n" + osql.update_row("insurance_data", extra, {"pid": pid, "type": "primary"})
        if audited:
            sql += "\n" + await self._log("patient-record-update", pid, f"UPDATE insurance_data pid={pid}", "Patient Insurance")
        await self.dbs["openemr"].execute_script(sql)

    async def openemr_move_event(self, event_id: int, patient_id: int, *, date: str, start: str, duration: int = 900,
                                 audited: bool = True, **extra: Any) -> None:
        sql = osql.update_appointment(pc_eid=event_id, **{"pc_eventDate": date, "pc_endDate": date, "pc_startTime": start,
                                                          "pc_duration": duration, **extra})
        if audited:
            # the calendar save as measured on 8.3: patient_id 0, the SQL with its bound values in comments
            sql += "\n" + await self._log("scheduling-update", 0,
                                    f"UPDATE openemr_postcalendar_events SET pc_eventDate = ? WHERE pc_eid = ? ('{date}','{event_id}')",
                                    "Scheduling")
        await self.dbs["openemr"].execute_script(sql)

    async def openemr_sql(self, sql: str) -> None:
        await self.dbs["openemr"].execute_script(sql)

    async def portal_sql(self, sql: str) -> None:
        await self.dbs["portal"].execute_script(sql)

    # ------------------------------------------------------------------ verdict
    async def verify(self) -> Verdict:
        return await Oracle(WORLD.oracle_context(self.dbs, self.baseline)).evaluate(self.task.oracle)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


async def start(task: Any) -> Episode:
    """Restore the golden world, seed the task, run the feasibility gate and capture the baseline."""
    golden = await _template()
    root = Path(tempfile.mkdtemp(prefix="claims-ep-"))
    shutil.copytree(golden, root, dirs_exist_ok=True)
    m = InProcMachine(root)
    dbs = WORLD.databases(m)
    await apply_seeding(m, dbs, task.seeding)
    feasible = await WORLD.feasibility(m, dbs, task)
    baseline = await Baseline.capture(dbs, WORLD.checksum_tables(), WORLD.primary_keys(), WORLD.watermark_tables(),
                                      ignore_columns=WORLD.ignore_columns(),
                                      preservation_checks=[*task.oracle.effects, *task.oracle.invariants])
    return Episode(task=task, machine=m, dbs=dbs, baseline=baseline, root=root, feasibility=feasible)


def generate(family: str, seed: int, split: str) -> Any:
    return WORLD.generate(family, seed, split)


_KIND = {"update_insurance_reconcile": "INS", "resolve_denial": "APL", "resolve_denial_easy": "APL",
         "reschedule_constrained": "RSC"}


def parts(task: Any) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """``[(kind, expected, difficulty)]``: one part for a base-family task, two (in instruction order) for a composition."""
    if task.family == "compose_claims":
        return [(p["kind"], p, task.difficulty["parts"][p["kind"]]) for p in task.expected["parts"]]
    if task.difficulty.get("composition"):  # the legacy heldout_compositions task (seed_world._composition)
        ex = task.expected
        plan = next(c.equals for c in task.oracle.effects if c.id == "openemr_plan")
        return [("INS", {"patient_pid": ex["patient_pid"], "claim_number": ex["c31"], "new_member": ex["new_member"],
                         "new_plan": plan}, {"partially_updated": False}),
                ("APL", {"patient_pid": ex["patient_pid"], "claim_number": ex["c197"], "auth_number": ex["auth_number"],
                         "doc_name": "authorization_letter_1.pdf"}, {"require_attachment": False})]
    return [(_KIND[task.family], task.expected, task.difficulty)]


def _minutes(t: str) -> int:
    h, m, *_ = str(t).split(":")
    return int(h) * 60 + int(m)


async def free_start(ep: Episode, provider_id: Any, date: str, window: list[str], *, duration: int = 900,
                     ignore_event: Optional[int] = None, busy_ok: bool = False) -> str:
    """The first quarter-hour start in ``window`` on ``date`` where ``provider_id`` has no other appointment
    (``busy_ok``: the first start that DOES overlap one, for negative controls)."""
    rows = await ep.dbs["openemr"].query(
        "SELECT pc_eid, pc_startTime, pc_endTime FROM openemr_postcalendar_events WHERE pc_aid = ? AND pc_eventDate = ?",
        [str(provider_id), date])
    busy = [(_minutes(r["pc_startTime"]), _minutes(r["pc_endTime"])) for r in rows if int(r["pc_eid"]) != (ignore_event or -1)]
    t, end = _minutes(window[0]), _minutes(window[1])
    while t + duration // 60 <= end + 1:
        overlaps = any(s < t + duration // 60 and e > t for s, e in busy)
        if overlaps == busy_ok:
            return f"{t // 60:02d}:{t % 60:02d}:00"
        t += 15
    raise LookupError(f"no {'busy' if busy_ok else 'free'} start for provider {provider_id} on {date} in {window}")


def attachment_bytes(task: Any, doc_name: str) -> bytes:
    return next(f.content for f in task.seeding.files if f.path.endswith("/" + doc_name))


async def complete(ep: Episode, kind: str, ex: dict[str, Any], diff: dict[str, Any], *, client: Optional[TestClient] = None,
                   **override: Any) -> None:
    """Do one part correctly through the UI path; ``override`` swaps in a wrong value (auth, member, plan, date, start, ...)."""
    if kind == "APL":
        attach = attachment_bytes(ep.task, ex["doc_name"]) if diff.get("require_attachment") else None
        status = ep.appeal(ex["claim_number"], override.get("auth", ex["auth_number"]), reason=override.get("reason", "PRECERT_OBTAINED"),
                           attachment=override.get("attachment", attach), client=client)
        assert status == 303, status
    elif kind == "INS":
        member = override.get("member", ex["new_member"])
        if not diff.get("partially_updated") and override.get("openemr", True):
            await ep.openemr_update_insurance(ex["patient_pid"], override.get("openemr_member", member),
                                              override.get("plan", ex["new_plan"]))
        if override.get("portal", True):
            assert ep.resubmit(ex["claim_number"], member, client=client) == 303
    elif kind == "RSC":
        date = override.get("date", ex["target_date"])
        start = override.get("start") or await free_start(ep, ex["provider_openemr_id"], date, ex["window"],
                                                          ignore_event=ex["event_id"])
        await ep.openemr_move_event(ex["event_id"], ex["patient_pid"], date=date, start=start,
                                    **{k: v for k, v in override.items() if k.startswith("pc_")})
    else:
        raise ValueError(kind)


async def complete_all(ep: Episode, **skip: bool) -> None:
    """Every part of the task done correctly (``skip={"INS": True}`` leaves a part undone)."""
    client = ep.portal()
    for kind, ex, diff in parts(ep.task):
        if not skip.get(kind):
            await complete(ep, kind, ex, diff, client=client)


def find_seed(family: str, split: str, pred, start_seed: int = 0, limit: int = 3000) -> int:
    for s in range(start_seed, start_seed + limit):
        if pred(generate(family, s, split)):
            return s
    raise LookupError(f"no {family}/{split} seed in [{start_seed}, {start_seed + limit}) matches")


def off_by_one(value: str) -> str:
    """The same string with its last digit changed (an off-by-one-character value)."""
    for i in range(len(value) - 1, -1, -1):
        if value[i].isdigit():
            return value[:i] + str((int(value[i]) + 1) % 10) + value[i + 1:]
    return value[:-1] + ("A" if value[-1] != "A" else "B")


__all__ = ["WORLD", "InProcMachine", "Episode", "start", "generate", "find_seed", "off_by_one", "parts", "free_start",
           "attachment_bytes", "complete", "complete_all"]
