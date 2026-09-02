"""claims-ops-v1: a synthetic payer portal + OpenEMR 8.3.0 on one Solari desktop.

``build()`` turns a fresh ``default`` desktop into the golden world (portal
installed and running on :8080, OpenEMR on :80, both DBs seeded with the base
population, Chrome logged into both, window maximised) and returns the golden
snapshot id. Everything the agent could touch lives inside the VM so that one
``revert()`` restores all of it.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable

from forkloop.dbaccess import DbAccess
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

    async def diagnostics(self, machine: Any) -> dict[str, str]:
        """Chrome's stderr log and the kernel ring buffer, so a renderer crash leaves evidence."""
        if machine.backend_name == "fake" or "gui" not in machine.capabilities:
            return {}
        out: dict[str, str] = {}
        for name, cmd in (("chrome.log", "tail -n 200 /home/desktop/chrome.log 2>/dev/null"),
                          ("dmesg.log", "dmesg 2>/dev/null | tail -n 60"),
                          ("chrome_ps.txt", "ps -eo pid,rss,etimes,args --sort=-rss | grep -a '[c]hrome' | cut -c1-200 | head -20")):
            try:
                r = await machine.exec("sh", ["-c", cmd], timeout_ms=15_000)
                out[name] = r.stdout
            except Exception as e:  # noqa: BLE001
                out[name] = f"(unavailable: {type(e).__name__}: {e})"
        return out

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
        r = await machine.exec("sh", ["-c", "ps -eo args | grep -m1 '[g]oogle-chrome' | grep -c -- '--disable-gpu'"])
        if r.stdout.strip() == "1":
            return False
        script = (
            "pkill -x chrome; sleep 1.5; runuser -u desktop -- env DISPLAY=:0 HOME=/home/desktop XDG_RUNTIME_DIR=/run/desktop "
            "setsid -f google-chrome --no-first-run --no-default-browser-check --user-data-dir=/home/desktop/.config/forkloop-chrome "
            "--password-store=basic --window-position=0,0 --window-size=1280,720 --disable-session-crashed-bubble "
            "--disk-cache-size=1 --media-cache-size=1 --disable-infobars --disable-gpu --disable-dev-shm-usage "
            "--enable-logging=stderr --v=0 'http://localhost:8080/claims' "
            ">/home/desktop/chrome.log 2>&1; sleep 6"
        )
        await machine.exec("bash", ["-c", script], timeout_ms=60_000)
        return True


__all__ = ["ClaimsOpsWorld"]
