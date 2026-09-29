"""kanboard-v1: Kanboard v1.2.54 (SQLite) on the Docker desktop — the second Forkloop world.

It plugs into Forkloop only through the public world interface (``forkloop/world.py``): ``world.yaml``
declares the database, the checksummed tables, the audit trail and the families; this class adds health,
feasibility, the initial screen and analysis-only UI milestones. The image is built by
``docker/build_image.sh`` (docs/second-world.md); everything the agent can touch — the SQLite database,
Kanboard's sessions, the Chrome profile — lives in the container filesystem.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional

from forkloop.dbaccess import DbAccess
from forkloop.world import HealthReport, World

from . import base_data as bd

HERE = Path(__file__).resolve().parent
#: window title suffix of a loaded Chrome page; Kanboard titles pages with the project name or the page name
CHROME = " - Google Chrome"
LOGIN_TITLE = "Login" + CHROME


class KanboardWorld(World):
    # ----------------------------------------------------------------- build
    async def build(self, machine: Any, *, log: Callable[[str], None] = print) -> str:
        if machine.backend_name == "fake":
            return await self._build_fake(machine, log)
        if machine.backend_name == "docker":
            health = await self.health(machine, self.databases(machine))
            if not health.ok:
                raise RuntimeError(f"kanboard image {machine.image} is unhealthy: {health.checks}")
            log(f"golden image: {machine.image}")
            return machine.image
        raise NotImplementedError("kanboard-v1 is built as a Docker image (worlds/kanboard_v1/docker/build_image.sh); "
                                  "there is no Solari golden for it")

    async def _build_fake(self, machine: Any, log: Callable[[str], None]) -> str:
        """Offline stand-in: Kanboard 1.2.54's real SQLite schema (kanboard_schema.sql, dumped from the image),
        its admin user, the world's base population, the audit view and the meta row. No web server."""
        db = self.databases(machine)["kanboard"]
        await machine.write_file(self.config.paths["kanboard_root"] + "/data/.keep", b"")
        admin = bd.insert("users", {"id": 1, "username": "admin", "password": "!", "is_admin": 1, "role": "app-admin",
                                    "name": "Administrator"})
        settings = "\n".join(bd.insert("settings", {"option": k, "value": v}) for k, v in sorted(bd.SETTINGS.items()))
        await db.execute_script((HERE / "kanboard_schema.sql").read_text())
        await db.execute_script("\n".join([admin, settings, bd.render_base_sql(), bd.AUDIT_VIEW_SQL, bd.META_SQL,
                                           bd.insert("forkloop_meta", {"key": "base_sha256", "value": bd.base_sha256()})]))
        log("fake kanboard world built (Kanboard 1.2.54 schema + base data, no server)")
        return await machine.snapshot(f"{self.name}-golden-fake")

    # ---------------------------------------------------------------- health
    async def health(self, machine: Any, dbs: dict[str, DbAccess]) -> HealthReport:
        rep = await super().health(machine, dbs)
        db = dbs["kanboard"]
        try:
            sha = await db.scalar("SELECT value FROM forkloop_meta WHERE key = 'base_sha256'")
            rep.checks["base_sha256"] = (sha or "")[:16]
            if sha != bd.base_sha256():
                # the image was built from other base data than the generators assume: rebuild it
                rep.ok = False
                rep.checks["base_sha256_expected"] = bd.base_sha256()[:16]
            n_users = int(await db.scalar("SELECT COUNT(*) FROM users WHERE id >= 100000") or 0)
            n_projects = int(await db.scalar("SELECT COUNT(*) FROM projects WHERE id >= 100000") or 0)
            rep.checks.update({"kanboard.users": n_users, "kanboard.projects": n_projects})
            if n_users != len(bd.USERS) or n_projects != len(bd.PROJECTS):
                rep.ok = False
        except Exception as e:  # noqa: BLE001
            rep.ok = False
            rep.checks["error"] = f"{type(e).__name__}: {e}"
        return rep

    # ----------------------------------------------------------- feasibility
    async def feasibility(self, machine: Any, dbs: dict[str, DbAccess], task: Any) -> HealthReport:
        """The task's own records exist with the generated values, and the instruction names them: the target
        (title, project, open), its near-twin and the same-titled task in the other project, the target
        column and assignee (a member of the project, so the assignee list offers them), the comment floor."""
        e, checks, db = task.expected, {}, dbs["kanboard"]

        def check(name: str, good: bool, detail: Any = None) -> None:
            checks[name] = True if good else f"failed: {detail}"

        try:
            rows = await db.query("SELECT t.id, t.title, t.project_id, t.is_active, p.name AS project FROM tasks t "
                                  "JOIN projects p ON p.id = t.project_id WHERE t.id IN (?, ?, ?) ORDER BY t.id",
                                  [e["target_task_id"], e["twin_task_id"], e["same_title_task_id"]])
            by_id = {int(r["id"]): r for r in rows}
            tgt = by_id.get(int(e["target_task_id"]))
            check("target", tgt is not None and tgt["title"] == e["title"] and int(tgt["project_id"]) == e["project_id"]
                  and int(tgt["is_active"]) == 1, tgt)
            if tgt is not None:
                check("instruction_names_target", f'"{tgt["title"]}"' in task.instruction
                      and f'"{tgt["project"]}"' in task.instruction, task.instruction[:120])
            twin = by_id.get(int(e["twin_task_id"]))
            check("twin", twin is not None and twin["title"] == e["twin_title"]
                  and int(twin["project_id"]) == e["project_id"], twin)
            same = by_id.get(int(e["same_title_task_id"]))
            check("same_title_other_project", same is not None and same["title"] == e["title"]
                  and int(same["project_id"]) == e["same_title_project_id"], same)
            n_same = await db.scalar("SELECT COUNT(*) FROM tasks WHERE project_id = ? AND title = ?",
                                     [e["project_id"], e["title"]])
            check("title_unique_in_project", int(n_same or 0) == 1, n_same)
            if task.family == "move_and_assign":
                col = await db.scalar("SELECT title FROM columns WHERE id = ? AND project_id = ?",
                                      [e["column_id"], e["project_id"]])
                check("target_column", col == e["column"], col)
                member = await db.scalar("SELECT COUNT(*) FROM project_has_users WHERE project_id = ? AND user_id = ?",
                                         [e["project_id"], e["owner_id"]])
                check("assignee_is_member", int(member or 0) == 1, member)
                name = await db.scalar("SELECT name FROM users WHERE id = ?", [e["owner_id"]])
                check("instruction_names_assignee", bool(name) and f"assign it to {name}." in task.instruction, name)
            if task.family == "due_and_comment":
                top = await db.scalar("SELECT MAX(id) FROM comments")
                check("comment_floor", int(top or 0) == int(e["comment_floor"]), top)
        except Exception as ex:  # noqa: BLE001
            checks["error"] = f"{type(ex).__name__}: {ex}"
        return HealthReport(ok=bool(checks) and all(v is True for v in checks.values()), checks=checks)

    # ------------------------------------------------------- initial screen
    async def open_initial_screen(self, machine: Any, screen: dict[str, Any]) -> None:
        """Agent channel only: navigate Chrome to the board; if Kanboard shows its login page (a session lost
        with the browser profile), log in as the world's own account (never part of a task) and navigate
        again. The reset fails (unscored) unless the board of the expected project is on screen."""
        if machine.backend_name == "fake" or "gui" not in machine.capabilities:
            return
        url = screen.get("url")
        if not url:
            return
        await self._navigate(machine, url)
        title = await self._settled_title(machine)
        if title == LOGIN_TITLE:
            # the login form puts the focus in the username field; "Remember Me" is checked by default
            await machine.type_text(bd.AGENT_LOGIN[0])
            await machine.press(["Tab"])
            await machine.type_text(bd.AGENT_LOGIN[1])
            await machine.press(["Return"])
            await asyncio.sleep(2)
            await self._navigate(machine, url)
            title = await self._settled_title(machine)
        pid = screen.get("project_id")
        want = bd.project_by_id(int(pid)).name + CHROME if pid is not None else None
        if want is not None and title != want:
            raise RuntimeError(f"could not confirm the {want!r} board after reset (window title {title[:80]!r})")
        # move the pointer off the board so no card starts in its hover state
        await machine.move(1270, 700)

    @staticmethod
    async def _navigate(machine: Any, url: str) -> None:
        await machine.click(640, 90)          # the omnibox (Chrome at 0,0 under the 27 px panel)
        await machine.press(["ctrl", "a"])
        await machine.type_text(url)
        await machine.press(["Return"])

    async def _settled_title(self, machine: Any, timeout_s: float = 10.0) -> str:
        """The active window's title once the page has loaded (not "Untitled"/the bare URL)."""
        loop = asyncio.get_running_loop()
        deadline, title = loop.time() + timeout_s, ""
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            title = await self._window_title(machine)
            if title.endswith(CHROME) and not title.startswith(("Untitled", "localhost")):
                return title
        return title

    @staticmethod
    async def _window_title(machine: Any) -> str:
        r = await machine.exec("runuser", ["-u", "desktop", "--", "env", "DISPLAY=:0",
                                           "xdotool", "getactivewindow", "getwindowname"])
        return (r.stdout or "").strip() if r is not None else ""

    # ------------------------------------------------------- UI milestones
    async def ui_milestones(self, dbs: dict[str, DbAccess], baseline: Any, task: Any) -> Optional[dict[str, Any]]:
        """Analysis only (never the reward): which Kanboard activity events the episode produced, on the target
        and on the distractor tasks, read from the activity stream after the seed-time watermark."""
        e = dict(getattr(task, "expected", None) or {})
        wm = int((getattr(baseline, "watermarks", None) or {}).get("kanboard.forkloop_task_audit", 0) or 0)
        rows = await dbs["kanboard"].query("SELECT entity_id AS task_id, event_name AS ev FROM forkloop_task_audit "
                                           "WHERE id > ? ORDER BY id", [wm])
        target = int(e.get("target_task_id", -1))
        others = {int(e[k]) for k in ("twin_task_id", "same_title_task_id") if k in e}
        on_target = [r["ev"] for r in rows if int(r["task_id"]) == target]
        on_distractor = [r["ev"] for r in rows if int(r["task_id"]) in others]
        rungs = {"target_touched": bool(on_target),
                 "target_moved": "task.move.column" in on_target,
                 "target_updated": any(ev in ("task.update", "task.assignee_change") for ev in on_target),
                 "target_commented": "comment.create" in on_target,
                 "distractor_touched": bool(on_distractor)}
        order = ["target_touched", "target_moved", "target_updated", "target_commented", "distractor_touched"]
        reached = [r for r in order[:4] if rungs[r]]
        return {"rungs": rungs, "order": order, "highest": reached[-1] if reached else None, "n_reached": len(reached),
                "evidence": {"events_target": on_target[:20], "events_distractors": on_distractor[:20],
                             "events_total": len(rows)}}

    async def diagnostics(self, machine: Any) -> dict[str, str]:
        if machine.backend_name == "fake":
            return {}
        out: dict[str, str] = {}
        for name, cmd in (("kanboard.log", "tail -n 100 /var/log/kanboard.log 2>/dev/null; "
                                           "tail -n 50 /var/log/forkloop-kanboard.log /var/log/nginx/kanboard-error.log 2>/dev/null; tail -n 60 /var/log/nginx/kanboard-access.log 2>/dev/null"),
                          ("chrome.log", "tail -n 100 /home/desktop/chrome.log 2>/dev/null")):
            try:
                r = await machine.exec("sh", ["-c", cmd], timeout_ms=15_000)
                out[name] = r.stdout
            except Exception as ex:  # noqa: BLE001
                out[name] = f"(unavailable: {type(ex).__name__}: {ex})"
        return out


__all__ = ["KanboardWorld"]
