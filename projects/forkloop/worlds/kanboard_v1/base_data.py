"""The fixed base population of kanboard-v1 (baked into the golden image, never changed by episodes).

One source of truth for the image build (``docker/install_world.py`` renders :func:`render_base_sql`
into Kanboard's SQLite database after Kanboard's own migrations ran) and for the task generators
(which need the ids of users, projects and columns). Everything is synthetic.

Ids: base rows use explicit primary keys >= 100000 (Kanboard's own rows — the ``admin`` user,
the ``links`` table — keep the small ids its migrations gave them); episode rows use >= 500000
(``tasks/common.py``). Timestamps are fixed (no clock reads), so the rendered SQL is byte-identical
on every machine; :func:`base_sha256` goes into the image and ``KanboardWorld.health`` refuses an
image built from different base data.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
from dataclasses import dataclass


def quote(v: object) -> str:
    """Portable SQL literal, the same rules as ``forkloop.util.sql.quote`` (this module also runs inside the
    image build, where the forkloop package is not installed)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    if "\\" in s:
        raise ValueError("backslashes are not portable SQL literals")
    return "'" + s.replace("'", "''") + "'"

KANBOARD_VERSION = "v1.2.54"
#: official image the Dockerfile copies the application from (Docker Hub kanboard/kanboard, MIT licence)
KANBOARD_IMAGE = ("kanboard/kanboard:v1.2.54@sha256:"
                  "8df6c4339134b6c196da9a262daa42ab8ce395f528a48d4a3dd7038c296f34c1")

#: The world's synthetic account the browser is logged in as (a project manager in every project).
AGENT_USER_ID = 100001
AGENT_LOGIN = ("jordan.ellis", "kb-agent-synthetic-2026")
#: Kanboard's built-in administrator (id 1, created by its migrations); the build sets this password.
ADMIN_PASSWORD = "kb-admin-synthetic-2026"


def ts(y: int, m: int, d: int, hh: int = 9, mm: int = 0) -> int:
    """Unix seconds of a UTC wall time (Kanboard stores task dates as integers; the app runs in UTC)."""
    return calendar.timegm(dt.datetime(y, m, d, hh, mm).timetuple())


BASE_TS = ts(2026, 8, 3)   # creation/modification time of every base row


@dataclass(frozen=True)
class User:
    id: int
    username: str
    name: str


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    identifier: str
    description: str


#: The agent's account, then team members in near-twin pairs (the assignee distractors).
USERS: tuple[User, ...] = (
    User(100001, "jordan.ellis", "Jordan Ellis"),
    User(100002, "priya.raman", "Priya Raman"),
    User(100003, "priya.rao", "Priya Rao"),
    User(100004, "marcus.chen", "Marcus Chen"),
    User(100005, "marcus.cheng", "Marcus Cheng"),
    User(100006, "dana.okafor", "Dana Okafor"),
    User(100007, "diana.okafor", "Diana Okafor"),
    User(100008, "luis.ortega", "Luis Ortega"),
    User(100009, "lucia.ortega", "Lucia Ortega"),
    User(100010, "sam.whitfield", "Sam Whitfield"),
    User(100011, "sami.whitfield", "Sami Whitfield"),
    User(100012, "elena.petrova", "Elena Petrova"),
    User(100013, "elena.petrov", "Elena Petrov"),
)
#: (target, twin) pairs: an assignment target always has a near-twin in the same project
USER_TWINS: tuple[tuple[int, int], ...] = (
    (100002, 100003), (100003, 100002), (100004, 100005), (100005, 100004), (100006, 100007),
    (100007, 100006), (100008, 100009), (100009, 100008), (100010, 100011), (100011, 100010),
    (100012, 100013), (100013, 100012),
)

PROJECTS: tuple[Project, ...] = (
    Project(100001, "Website Relaunch", "WEB", "Public website rebuild on the new CMS."),
    Project(100002, "Mobile App", "MOB", "iOS and Android client releases."),
    Project(100003, "Customer Onboarding", "ONB", "Getting new accounts live in their first 30 days."),
    Project(100004, "Data Platform", "DATA", "Warehouse, pipelines and reporting."),
)
#: Every project has the same five columns (the "second project with the same column names" distractor).
COLUMN_TITLES: tuple[str, ...] = ("Backlog", "Ready", "In progress", "Review", "Done")


def column_id(project_id: int, title: str) -> int:
    """Columns are 100000 + 10 * project index + column position (100011 .. 100045)."""
    k = project_id - 100000
    return 100000 + 10 * k + COLUMN_TITLES.index(title) + 1


def swimlane_id(project_id: int) -> int:
    return project_id  # one "Default swimlane" per project, id = project id


def project_by_id(pid: int) -> Project:
    return next(p for p in PROJECTS if p.id == pid)


def user_by_id(uid: int) -> User:
    return next(u for u in USERS if u.id == uid)


#: Base tasks per project: (title, column, assignee id or 0, color). Titles never reused by episodes.
BASE_TASKS: dict[int, tuple[tuple[str, str, int, str], ...]] = {
    100001: (("Audit image alt text", "Backlog", 100010, "yellow"), ("Migrate blog to new CMS", "In progress", 100004, "blue"),
             ("Set up staging redirects", "Ready", 0, "green"), ("Refresh pricing page copy", "Review", 100006, "yellow"),
             ("Compress hero images", "Done", 100012, "grey")),
    100002: (("Crash on Android resume", "In progress", 100005, "red"), ("Add biometric login", "Backlog", 0, "purple"),
             ("Update app store screenshots", "Ready", 100002, "yellow"), ("Reduce cold start time", "Review", 100008, "blue"),
             ("Localize settings screen", "Done", 100011, "grey")),
    100003: (("Welcome email sequence", "Ready", 100003, "green"), ("Kickoff call template", "Done", 100007, "grey"),
             ("Collect SSO metadata", "In progress", 100009, "blue"), ("Draft onboarding survey", "Backlog", 0, "yellow"),
             ("Data import checklist", "Review", 100013, "orange")),
    100004: (("Nightly ETL retries", "In progress", 100004, "red"), ("Partition events table", "Backlog", 100012, "blue"),
             ("Rotate warehouse credentials", "Ready", 0, "yellow"), ("Document revenue lineage", "Review", 100010, "green"),
             ("Archive old raw logs", "Done", 100006, "grey")),
}
#: A few base comments: (task index within its project, author, text)
BASE_COMMENTS: dict[int, tuple[tuple[int, int, str], ...]] = {
    100001: ((1, 100004, "Posts are exported; images next."),),
    100002: ((0, 100005, "Reproduced on two devices."),),
    100003: ((2, 100009, "Waiting on the customer's IdP metadata."),),
    100004: ((0, 100004, "Retries added for the orders job."),),
}


def base_task_rows() -> list[dict]:
    rows, tid = [], 100100
    for p in PROJECTS:
        for pos, (title, col, owner, color) in enumerate(BASE_TASKS[p.id], start=1):
            tid += 1
            rows.append(task_row(id=tid, title=title, project_id=p.id, column=col, owner_id=owner, position=pos * 10,
                                 color_id=color, creator_id=AGENT_USER_ID, created=BASE_TS))
    return rows


def base_comment_rows() -> list[dict]:
    tasks = base_task_rows()
    rows, cid = [], 100200
    for p in PROJECTS:
        mine = [t for t in tasks if t["project_id"] == p.id]
        for idx, author, text in BASE_COMMENTS.get(p.id, ()):
            cid += 1
            rows.append(comment_row(id=cid, task_id=mine[idx]["id"], user_id=author, comment=text, created=BASE_TS + 3600))
    return rows


MAX_BASE_COMMENT_ID = 100204


def task_row(*, id: int, title: str, project_id: int, column: str, owner_id: int, position: int, color_id: str,
             creator_id: int, created: int, date_due: int = 0, description: str = "") -> dict:
    """A ``tasks`` row with every column Kanboard's own ``TaskCreationModel`` sets (measured on 1.2.54:
    ``time_spent``/``time_estimated`` are written as 0 here, not NULL, so that saving the edit form,
    which resets empty numbers to 0, does not change them)."""
    return {"id": id, "title": title, "description": description, "date_creation": created, "color_id": color_id,
            "project_id": project_id, "column_id": column_id(project_id, column), "owner_id": owner_id,
            "position": position, "is_active": 1, "date_completed": None, "score": 0, "date_due": date_due,
            "category_id": 0, "creator_id": creator_id, "date_modification": created, "reference": "",
            "date_started": 0, "time_spent": 0, "time_estimated": 0, "swimlane_id": swimlane_id(project_id),
            "date_moved": created, "recurrence_status": 0, "recurrence_trigger": 0, "recurrence_factor": 0,
            "recurrence_timeframe": 0, "recurrence_basedate": 0, "recurrence_parent": None,
            "recurrence_child": None, "priority": 0, "external_provider": None, "external_uri": None}


def comment_row(*, id: int, task_id: int, user_id: int, comment: str, created: int) -> dict:
    return {"id": id, "task_id": task_id, "user_id": user_id, "date_creation": created, "comment": comment,
            "reference": "", "date_modification": created, "visibility": "app-user"}


def insert(table: str, row: dict) -> str:
    cols = ", ".join(row)
    vals = ", ".join(quote(v) for v in row.values())
    return f"INSERT INTO {table} ({cols}) VALUES ({vals});"


def render_base_sql(password_hashes: dict[int, str] | None = None) -> str:
    """Base rows as portable INSERTs. ``password_hashes`` (user id -> bcrypt) is filled in by the image
    build (``php password_hash``); without it users get an unusable password (tests)."""
    hashes = password_hashes or {}
    out: list[str] = ["-- kanboard-v1 base data (worlds/kanboard_v1/base_data.py)"]
    for u in USERS:
        out.append(insert("users", {
            "id": u.id, "username": u.username, "password": hashes.get(u.id, "!"), "is_admin": 0, "is_ldap_user": 0,
            "name": u.name, "email": f"{u.username}@example.test", "notifications_enabled": 0, "timezone": None,
            "language": None, "disable_login_form": 0, "twofactor_activated": 0, "token": "", "notifications_filter": 4,
            "nb_failed_login": 0, "lock_expiration_date": 0, "is_project_admin": 0, "role": "app-user", "is_active": 1,
            "theme": "light"}))
    for p in PROJECTS:
        out.append(insert("projects", {
            "id": p.id, "name": p.name, "is_active": 1, "token": "", "last_modified": BASE_TS, "is_public": 0,
            "is_private": 0, "is_everybody_allowed": 0, "default_swimlane": "Default swimlane",
            "show_default_swimlane": 1, "description": p.description, "identifier": p.identifier, "start_date": "",
            "end_date": "", "owner_id": AGENT_USER_ID, "priority_default": 0, "priority_start": 0, "priority_end": 3,
            "per_swimlane_task_limits": 0, "task_limit": 0, "enable_global_tags": 1}))
        for pos, title in enumerate(COLUMN_TITLES, start=1):
            out.append(insert("columns", {"id": column_id(p.id, title), "title": title, "position": pos,
                                          "project_id": p.id, "task_limit": 0, "description": "",
                                          "hide_in_dashboard": 0}))
        out.append(insert("swimlanes", {"id": swimlane_id(p.id), "name": "Default swimlane", "position": 1,
                                        "is_active": 1, "project_id": p.id, "description": "", "task_limit": 0}))
        for u in USERS:
            out.append(insert("project_has_users", {
                "project_id": p.id, "user_id": u.id, "is_owner": 0,
                "role": "project-manager" if u.id == AGENT_USER_ID else "project-member"}))
    for row in base_task_rows():
        out.append(insert("tasks", row))
    for row in base_comment_rows():
        out.append(insert("comments", row))
    return "\n".join(out) + "\n"


#: Kanboard settings the world pins (``settings`` table): no board auto-refresh (a 10 s poll re-renders
#: the board under the agent and breaks screen-stability waits), UTC, US date format (the Kanboard default).
SETTINGS: dict[str, str] = {
    "board_private_refresh_interval": "0",
    "board_public_refresh_interval": "0",
    "application_timezone": "UTC",
    "application_date_format": "m/d/Y",
    "application_url": "http://localhost/",
    "application_language": "en_US",
}

#: Read-only view the oracle's ``ui_path_only`` check joins on (``world.yaml`` ``oracle.audit``): one row per
#: Kanboard activity-stream event, keyed by the task it concerns. Kanboard writes ``project_activities`` only
#: from its own event handlers (task moved/updated/assigned, comment created ...), never on direct SQL.
AUDIT_VIEW_SQL = ("CREATE VIEW forkloop_task_audit AS SELECT id, 'task' AS entity, task_id AS entity_id, "
                  "event_name, creator_id, project_id, date_creation FROM project_activities;")
META_SQL = "CREATE TABLE forkloop_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"


def base_sha256() -> str:
    """Fingerprint of everything the generators assume about the base population."""
    blob = "\n".join([render_base_sql(), repr(sorted(SETTINGS.items())), AUDIT_VIEW_SQL, KANBOARD_IMAGE])
    return hashlib.sha256(blob.encode()).hexdigest()


__all__ = ["USERS", "PROJECTS", "COLUMN_TITLES", "AGENT_USER_ID", "AGENT_LOGIN", "column_id", "swimlane_id",
           "render_base_sql", "base_sha256", "task_row", "comment_row", "insert", "SETTINGS", "AUDIT_VIEW_SQL",
           "USER_TWINS", "MAX_BASE_COMMENT_ID", "BASE_TS", "ts", "project_by_id", "user_by_id"]
