#!/usr/bin/env python3
"""Image-build step of kanboard-v1 (runs inside ``docker build``, as root, no X, no network).

1. Kanboard's own schema migrations (``php cli db:migrate``) create ``/var/www/app/data/db.sqlite``;
2. the world's settings, its base population (``base_data.render_base_sql``) with bcrypt passwords made
   by PHP's ``password_hash``, the admin password, the read-only audit view and a ``forkloop_meta`` row
   holding ``base_data.base_sha256()`` (``KanboardWorld.health`` refuses an image whose base data differs
   from the generators').

Standard library only (the image's python3); the application code is Kanboard's, unchanged.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys

sys.path.insert(0, "/opt/forkloop")
from worlds.kanboard_v1 import base_data as bd  # noqa: E402

APP = "/var/www/app"
DB = f"{APP}/data/db.sqlite"


def php_hash(password: str) -> str:
    r = subprocess.run(["php", "-r", "echo password_hash($argv[1], PASSWORD_BCRYPT);", password],
                       check=True, capture_output=True, text=True)
    assert r.stdout.startswith("$2y$"), r.stdout
    return r.stdout


def main() -> None:
    subprocess.run(["php", f"{APP}/cli", "db:migrate"], check=True, cwd=APP)
    version = subprocess.run(["php", f"{APP}/cli", "db:version"], check=True, cwd=APP, capture_output=True,
                             text=True).stdout.strip()
    print(f"[install] kanboard schema: {version}")
    hashes = {bd.AGENT_USER_ID: php_hash(bd.AGENT_LOGIN[1])}
    for u in bd.USERS:
        hashes.setdefault(u.id, php_hash(f"synthetic-{u.username}-2026"))
    con = sqlite3.connect(DB, isolation_level=None)
    con.execute("BEGIN")
    try:
        for k, v in bd.SETTINGS.items():
            con.execute("UPDATE settings SET value = ? WHERE option = ?", (v, k))
            if con.execute("SELECT changes()").fetchone()[0] == 0:
                con.execute("INSERT INTO settings (option, value) VALUES (?, ?)", (k, v))
        con.execute("UPDATE users SET password = ?, name = 'Administrator' WHERE id = 1",
                    (php_hash(bd.ADMIN_PASSWORD),))
        n_existing = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        assert n_existing == 0, f"fresh Kanboard database expected, found {n_existing} projects"
        sql = "\n".join(ln for ln in bd.render_base_sql(hashes).splitlines() if not ln.startswith("--"))
        for stmt in sql.split(";\n"):
            if stmt.strip():
                con.execute(stmt)
        n_users = con.execute("SELECT COUNT(*) FROM users WHERE id >= 100000").fetchone()[0]
        assert n_users == len(bd.USERS), f"{n_users} base users inserted, expected {len(bd.USERS)}"
        con.execute(bd.AUDIT_VIEW_SQL)
        con.execute(bd.META_SQL)
        con.execute("INSERT INTO forkloop_meta (key, value) VALUES ('base_sha256', ?)", (bd.base_sha256(),))
        con.execute("INSERT INTO forkloop_meta (key, value) VALUES ('kanboard_version', ?)", (bd.KANBOARD_VERSION,))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    # migrations ran under Kanboard's default WAL mode; config.php turns it off, so make the file self-contained
    assert con.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("users", "projects", "columns", "tasks", "comments", "project_has_users")}
    print(f"[install] base data: {counts}  base_sha256={bd.base_sha256()[:16]}")
    con.close()


if __name__ == "__main__":
    main()
