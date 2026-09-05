"""claims-ops-v1: a synthetic payer portal + OpenEMR 8.3.0 on one Solari desktop.

``build()`` turns a fresh ``default`` desktop into the golden world (portal
installed and running on :8080, OpenEMR on :80, both DBs seeded with the base
population, Chrome logged into both, window maximised) and returns the golden
snapshot id. Everything the agent could touch lives inside the VM so that one
``revert()`` restores all of it.
"""

from __future__ import annotations

import os
import base64
import json
from pathlib import Path
from typing import Any, Callable

from forkloop.dbaccess import DbAccess
from forkloop.oracle import _comment_texts
from forkloop.world import HealthReport, World

HERE = Path(__file__).resolve().parent

PORTAL_SYSTEMD = """[Unit]
Description=Forkloop payer portal
After=network.target

[Service]
Environment=PORTAL_DB=/var/lib/forkloop/portal/portal.db
Environment=PORTAL_UPLOADS=/var/lib/forkloop/portal/uploads
Environment=PORTAL_PORT=8080
Environment=PORTAL_SECRET=forkloop-golden-secret
Environment=PYTHONPATH=/opt/forkloop
WorkingDirectory=/opt/forkloop
ExecStart=/opt/forkloop/venv/bin/python -m worlds.claims_ops_v1.portal.app
Restart=always

[Install]
WantedBy=multi-user.target
"""


def document_view_path(path: str) -> bool:
    """True for an audited OpenEMR request path that opens a patient document: the documents
    controller with ``retrieve`` or ``view`` and a real document id. Not the ``/Documentation/``
    help pages (the word "document" is in their path), not the dashboard's automatic patient
    picture fetch (``document&retrieve … document_id=-1 … context=patient_picture``), not the
    ``document&list`` page (that is the Documents section, recorded separately). Measured on the
    2026-09-05 9B run, where the help pages and the picture fetch were the only matches in 1/4 and
    the list page in 3/4 chart episodes."""
    p = str(path or "")
    low = p.lower()
    if "/documentation/" in low or "context=patient_picture" in low or "document_id=-1" in low:
        return False
    if "controller.php?document" not in low:
        return False
    return ("&retrieve" in low or "&view" in low) and ("document_id=" in low or "doc_id=" in low)


def documents_list_path(path: str) -> bool:
    """The Documents section of a chart (``controller.php?document&list&patient_id=…``)."""
    low = str(path or "").lower()
    return "controller.php?document&list" in low


def _truthy(v: Any) -> bool:
    """``success`` as MariaDB's TSV ("1") or SQLite's int; NULL counts as success (OpenEMR's default)."""
    if v is None:
        return True
    try:
        return int(float(v)) != 0
    except (TypeError, ValueError):
        return str(v).strip().lower() in ("true", "t", "yes")


class ClaimsOpsWorld(World):
    # ----------------------------------------------------------------- build
    async def build(self, machine: Any, *, log: Callable[[str], None] = print, with_openemr: bool = True) -> str:
        """One-time golden build. Idempotent where the underlying scripts are."""
        if machine.backend_name == "fake":
            return await self._build_fake(machine, log)
        paths = self.config.paths
        build_dir = paths["build_dir"]
        log("uploading world sources")
        await self._upload_tree(machine, HERE.parent.parent / "worlds", f"{build_dir}/worlds")
        await self._upload_tree(machine, HERE.parent.parent / "forkloop", f"{build_dir}/forkloop")
        await machine.write_file(f"{build_dir}/build.sh", (HERE / "build.sh").read_bytes(), mode=0o755)
        await machine.write_file("/etc/systemd/system/forkloop-portal.service", PORTAL_SYSTEMD)
        args = ["--build-dir", build_dir]
        if not with_openemr:
            args.append("--skip-openemr")
        if "gui" not in machine.capabilities:
            args.append("--headless")
        who = await machine.exec("id", ["-u"])
        runner = ["bash"] if who.stdout.strip() == "0" else ["sudo", "bash"]
        log(f"running build.sh as {'root' if runner == ['bash'] else 'sudo'} (this takes minutes: apt, OpenEMR, portal)")
        r = await machine.exec(runner[0], [*runner[1:], f"{build_dir}/build.sh", *args], timeout_ms=45 * 60_000)
        log(r.stdout[-4000:])
        if r.exit_code != 0:
            raise RuntimeError(f"build.sh failed ({r.exit_code}): {r.stderr[-2000:]}")
        dbs = self.databases(machine)
        health = await self.health(machine, dbs)
        if not health.ok:
            raise RuntimeError(f"world unhealthy after build: {health.checks}")
        log("taking golden snapshot")
        sid = await machine.snapshot(f"{self.name}-golden-v{self.config.version}")
        log(f"golden snapshot: {sid}  (export {self.config.golden_snapshot_env}={sid})")
        return sid

    async def _build_fake(self, machine: Any, log: Callable[[str], None]) -> str:
        """Local build: real portal SQLite + the OpenEMR SQLite shim, no servers."""
        from .openemr.base_data import load_base_data, render_base_sql
        from .portal.db import init_db, seed_base

        p = self.config.paths
        portal_local = machine._local(p["portal_db"])  # fake backend exposes the local path
        portal_local.parent.mkdir(parents=True, exist_ok=True)
        init_db(portal_local)
        seed_base(portal_local)
        dbs = self.databases(machine)
        shim_sql = (HERE / "openemr" / "shim_schema.sql").read_text()
        await dbs["openemr"].execute_script(shim_sql)
        await dbs["openemr"].execute_script(render_base_sql(load_base_data()))
        await machine.write_file(p["openemr_docs"] + "/.keep", b"")
        await machine.write_file(p["portal_uploads"] + "/.keep", b"")
        log("fake claims-ops world built (portal sqlite + openemr shim)")
        return await machine.snapshot(f"{self.name}-golden-fake")

    async def _upload_tree(self, machine: Any, src: Path, dst: str) -> None:
        for f in sorted(src.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts or f.suffix == ".pyc":
                continue
            rel = f.relative_to(src).as_posix()
            await machine.write_file(f"{dst}/{rel}", f.read_bytes())

    # ---------------------------------------------------------------- health
    async def health(self, machine: Any, dbs: dict[str, DbAccess]) -> HealthReport:
        rep = await super().health(machine, dbs)
        try:
            n_pat = await dbs["portal"].scalar("SELECT COUNT(*) AS n FROM patients")
            rep.checks["portal.patients"] = n_pat
            if int(n_pat or 0) < 40:
                rep.ok = False
            n_emr = await dbs["openemr"].scalar("SELECT COUNT(*) AS n FROM patient_data")
            rep.checks["openemr.patients"] = n_emr
            if int(n_emr or 0) < 40:
                rep.ok = False
        except Exception as e:  # noqa: BLE001
            rep.ok = False
            rep.checks["error"] = f"{type(e).__name__}: {e}"
        return rep

    # ------------------------------------------------------- initial screen
    async def open_initial_screen(self, machine: Any, screen: dict[str, Any]) -> None:
        if machine.backend_name == "fake" or "gui" not in machine.capabilities:
            return
        url = screen.get("url")
        if not url:
            return
        # Agent-channel only: click the omnibox (keyboard focus is not guaranteed to be in
        # Chrome right after a fork), replace the URL, go.
        await machine.click(640, 90)
        await machine.press(["ctrl", "a"])
        await machine.type_text(url)
        await machine.press(["Return"])

    async def before_episode(self, machine: Any) -> None:
        if machine.backend_name == "fake":
            return
        # Clear the Downloads dir so an attachment from a previous branch cannot leak in
        # (revert already does this; this is belt-and-braces for fork-mode reuse).
        await machine.exec("sh", ["-c", f"rm -rf {self.config.paths['downloads']}/* 2>/dev/null; true"])
        if "gui" in machine.capabilities:
            await self.ensure_chrome_gpu_flag(machine)

    # ------------------------------------------------------- UI milestones
    #: The staircase rungs in order, for every family (a rung a family cannot reach stays False).
    MILESTONE_RUNGS: tuple[str, ...] = ("openemr_login", "openemr_chart", "openemr_document",
                                        "portal_claim", "portal_appeal_form", "appeal_submitted")

    async def ui_milestones(self, dbs: dict[str, DbAccess], baseline: Any, task: Any) -> dict[str, Any] | None:
        """Which screens the agent reached, read from the two audit trails after the episode.

        OpenEMR's ``log`` (rows after the seed-time watermark): a successful ``login`` row for
        the task's user; any row keyed by the target patient (the session's active chart);
        an ``http-request`` row whose decoded path is a document view for that patient (8.3
        base64-encodes ``comments``). The portal's ``page_views``: the claim page and the appeal
        form of the target claim; ``appeal_submitted`` is an appeal row for the target claim.
        Added 2026-09-05 for the student staircase (docs/student-2026-09-06.md); analysis only.
        """
        expected = dict(getattr(task, "expected", None) or {})
        wm = dict(getattr(baseline, "watermarks", None) or {})
        pid = expected.get("patient_pid")
        claim_number = expected.get("claim_number")
        claim_id = expected.get("claim_id")
        rungs = {r: False for r in self.MILESTONE_RUNGS}
        ev: dict[str, Any] = {}
        emr, portal = dbs.get("openemr"), dbs.get("portal")
        if emr is not None:
            wm_log = int(wm.get("openemr.log", 0) or 0)
            rows = await emr.query("SELECT event AS e, success AS s, COUNT(*) AS n FROM log WHERE id > ? "
                                   "AND event LIKE 'login%' GROUP BY event, success", [wm_log])
            ok_logins = sum(int(r["n"]) for r in rows if _truthy(r.get("s")))
            ev["openemr_logins"] = ok_logins
            ev["openemr_login_failures"] = sum(int(r["n"]) for r in rows if not _truthy(r.get("s")))
            rungs["openemr_login"] = ok_logins > 0
            if pid is not None:
                n = await emr.scalar("SELECT COUNT(*) AS n FROM log WHERE id > ? AND patient_id = ?", [wm_log, int(pid)])
                ev["openemr_rows_for_patient"] = int(n or 0)
                rungs["openemr_chart"] = int(n or 0) > 0
            # request paths OpenEMR audited (comments = the path, base64 on 8.3): a document view
            # names the documents controller; keep a few decoded samples as evidence.
            req = await emr.query("SELECT comments AS c FROM log WHERE id > ? AND event LIKE 'http-request%' "
                                  "ORDER BY id LIMIT 2000", [wm_log])
            paths = []
            for r in req:
                for text in _comment_texts(r.get("c")):
                    if text.startswith("/") or "://" in text:
                        paths.append(text.strip())
                        break
            doc_paths = [p for p in paths if document_view_path(p)]
            list_paths = [p for p in paths if documents_list_path(p)]
            chart_paths = [p for p in paths if "patient_file" in p or "demographics" in p]
            ev["openemr_request_paths"] = len(paths)
            ev["openemr_document_paths"] = doc_paths[:8]
            ev["openemr_documents_list"] = bool(list_paths)
            if chart_paths and not rungs["openemr_chart"]:
                rungs["openemr_chart"] = True
            rungs["openemr_document"] = bool(doc_paths) and (rungs["openemr_chart"] or pid is None)
        if portal is not None:
            wm_pv = int(wm.get("portal.page_views", 0) or 0)
            rows = await portal.query("SELECT path AS p FROM page_views WHERE id > ? ORDER BY id", [wm_pv])
            paths = [str(r["p"]) for r in rows]
            ev["portal_page_views"] = len(paths)
            if claim_number:
                claim_path = f"/claims/{claim_number}"
                rungs["portal_claim"] = any(p == claim_path or p.startswith(claim_path + "/") for p in paths)
                rungs["portal_appeal_form"] = any(p == claim_path + "/appeal" for p in paths)
            if claim_id is not None:
                n = await portal.scalar("SELECT COUNT(*) AS n FROM appeals WHERE claim_id = ?", [int(claim_id)])
                ev["appeals_for_claim"] = int(n or 0)
                rungs["appeal_submitted"] = int(n or 0) > 0
        reached = [r for r in self.MILESTONE_RUNGS if rungs[r]]
        return {"rungs": rungs, "order": list(self.MILESTONE_RUNGS), "highest": reached[-1] if reached else None,
                "n_reached": len(reached), "evidence": ev}

    async def diagnostics(self, machine: Any) -> dict[str, str]:
        """Chrome's stderr log and the kernel ring buffer, so a renderer crash leaves evidence."""
        if machine.backend_name == "fake" or "gui" not in machine.capabilities:
            return {}
        out: dict[str, str] = {}
        for name, cmd in (("chrome.log", "tail -n 200 /home/desktop/chrome.log 2>/dev/null"),
                          ("sys.txt", "uptime; echo; nproc; free -m; echo; df -h / | tail -1; echo; cat /proc/loadavg"),
                          ("dmesg.log", "dmesg 2>/dev/null | grep -E 'rcu:|stall|Out of memory|oom-kill|segfault|traps:|hung task' | tail -n 30; echo '--- tail'; dmesg 2>/dev/null | tail -n 60"),
                          ("chrome_ps.txt", "ps -eo pid,rss,etimes,args --sort=-rss | grep -a '[c]hrome' | cut -c1-600 | head -20")):
            try:
                r = await machine.exec("sh", ["-c", cmd], timeout_ms=15_000)
                out[name] = r.stdout
            except Exception as e:  # noqa: BLE001
                out[name] = f"(unavailable: {type(e).__name__}: {e})"
        return out

    chrome_base_flags = ("--no-first-run", "--no-default-browser-check", "--user-data-dir=/home/desktop/.config/forkloop-chrome",
                         "--password-store=basic", "--window-position=0,0", "--window-size=1280,720",
                         "--disable-session-crashed-bubble", "--disk-cache-size=1", "--media-cache-size=1",
                         "--disable-infobars", "--disable-gpu", "--disable-dev-shm-usage", "--enable-logging=stderr", "--v=0")

    async def ensure_chrome_gpu_flag(self, machine: Any) -> bool:
        """Relaunch Chrome with ``--disable-gpu`` if the snapshot's Chrome lacks it.

        The Solari ``default`` desktop has no working GPU process, and without the
        flag every authenticated OpenEMR page kills the renderer ("Aw, Snap! Error
        code: 5"). Golden images built after 2026-09-02 start Chrome with the flag
        (``browser_setup.sh``), so this is a no-op costing one ``ps``; on older
        goldens it relaunches Chrome on the portal claims list (the canonical initial
        screen; both app logins live in the profile and survive). Returns True when
        a relaunch happened.
        """
        # Experiment hooks (docs/limitations.md, Chrome tab crashes): FORKLOOP_CHROME_FLAGS appends
        # flags, FORKLOOP_CHROME_DROP removes base flags (space-separated); either forces a relaunch.
        extra = os.environ.get("FORKLOOP_CHROME_FLAGS", "").split()
        drop = set(os.environ.get("FORKLOOP_CHROME_DROP", "").split())
        if not extra and not drop:
            r = await machine.exec("sh", ["-c", "ps -eo args | grep -m1 '[g]oogle-chrome' | grep -c -- '--disable-gpu'"])
            if r.stdout.strip() == "1":
                return False
        flags = [f for f in self.chrome_base_flags if f not in drop] + extra
        # Measured 2026-09-04 (runs/luna-v10-fam2-s10-34 seed 31): with a fixed 1.5 s sleep the new
        # Chrome sometimes attached to the dying one ("Opening in existing browser session") and
        # exited with it, leaving no browser; the agent then launched a plain Chrome from the dock
        # (no profile, no portal session) and spent 150 actions guessing credentials. So: wait for
        # the old processes to be gone, drop the profile's singleton files, launch, verify, retry
        # once, and fail the reset stage rather than start a doomed episode.
        profile = "/home/desktop/.config/forkloop-chrome"
        marker = "[g]oogle-chrome.*forkloop-chrome"
        launch = ("runuser -u desktop -- env DISPLAY=:0 HOME=/home/desktop XDG_RUNTIME_DIR=/run/desktop "
                  "setsid -f google-chrome " + " ".join(flags) + " 'http://localhost:8080/claims' "
                  ">/home/desktop/chrome.log 2>&1")
        script = (
            "for i in $(seq 1 20); do pkill -x chrome 2>/dev/null; pgrep -x chrome >/dev/null || break; sleep 0.5; done; "
            "pkill -9 -x chrome 2>/dev/null; sleep 0.5; "
            f"rm -f {profile}/SingletonLock {profile}/SingletonSocket {profile}/SingletonCookie; "
            f"{launch}; "
            f"for i in $(seq 1 24); do sleep 0.5; ps -eo args | grep -q '{marker}' && break; done; "
            f"if ! ps -eo args | grep -q '{marker}'; then echo RELAUNCH_RETRY; sleep 1; {launch}; sleep 6; fi; "
            "sleep 4; "
            f"if ps -eo args | grep -q '{marker}'; then echo CHROME_OK; else echo CHROME_MISSING; fi"
        )
        r = await machine.exec("bash", ["-c", script], timeout_ms=90_000)
        out = (r.stdout or "") if r is not None else ""
        if "CHROME_OK" not in out:
            raise RuntimeError(f"Chrome did not come up after relaunch (output: {out.strip()[:200]!r})")
        return True


__all__ = ["ClaimsOpsWorld"]
