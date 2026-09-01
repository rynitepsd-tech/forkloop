"""Synthetic payer portal (FastAPI + SQLite + Jinja2).

This is a *training world* for vision-only GUI agents (docs/contracts.md §7):
every task path works with plain HTML forms and zero JavaScript, the layout is
fixed at 1280px with no animations, and every write goes through a route that
records an ``audit_log`` row in the same transaction. A middleware records every
request path in ``page_views`` so the oracle can detect forbidden screens.

Run::

    PORTAL_DB=./portal.db PORTAL_UPLOADS=./uploads PORTAL_PORT=8080 \
        python -m worlds.claims_ops_v1.portal.app

Env: ``PORTAL_DB`` (default ``./portal.db``), ``PORTAL_UPLOADS`` (default
``./uploads``), ``PORTAL_PORT`` (default 8080), ``PORTAL_SECRET`` (session
signing key, default ``forkloop-dev-secret``), ``PORTAL_FIXED_NOW`` (ISO-8601
string; when set every ``now()`` returns it, for deterministic tests).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .db import CLAIM_STATUSES, connect, transaction, verify_password

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"

SESSION_COOKIE = "portal_session"
FLASH_COOKIE = "portal_flash"
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days
DEFAULT_SECRET = "forkloop-dev-secret"
PAGE_SIZE = 25

APPEAL_REASONS: tuple[tuple[str, str], ...] = (
    ("PRECERT_OBTAINED", "Prior authorization was obtained"),
    ("MEDICAL_NECESSITY", "Medical necessity"),
    ("TIMELY_FILING", "Timely filing exception"),
    ("DUPLICATE_ERROR", "Not a duplicate"),
    ("COB_UPDATED", "Coordination of benefits updated"),
    ("CODING_CORRECTION", "Coding correction"),
)
APPEAL_REASON_CODES = frozenset(code for code, _ in APPEAL_REASONS)
APPEALABLE_STATUSES = frozenset({"DENIED", "APPEAL_SUBMITTED", "RESUBMITTED"})
RESUBMITTABLE_STATUSES = frozenset({"DENIED", "SUBMITTED", "RESUBMITTED", "APPEAL_SUBMITTED"})
NARRATIVE_MIN_CHARS = 10

STATUS_LABELS = {
    "SUBMITTED": "Submitted",
    "PAID": "Paid",
    "DENIED": "Denied",
    "APPEAL_SUBMITTED": "Appeal submitted",
    "RESUBMITTED": "Resubmitted",
    "VOID": "Void",
}

_DOB_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DOB_US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now() -> str:
    """ISO-8601 UTC timestamp; honours ``PORTAL_FIXED_NOW`` for deterministic tests."""
    fixed = os.environ.get("PORTAL_FIXED_NOW")
    if fixed:
        return fixed
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fmt_money(cents: int | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def normalize_dob(raw: str) -> str | None:
    """Accept ``YYYY-MM-DD`` or ``M/D/YYYY``; return ISO or None if unparseable."""
    raw = raw.strip()
    m = _DOB_ISO.match(raw)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = _DOB_US.match(raw)
        if not m:
            return None
        mo, d, y = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d).date().isoformat()
    except ValueError:
        return None


def safe_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base)
    return base or "attachment"


class LoginRequired(Exception):
    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


class NotFound(Exception):
    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app(
    db_path: str | os.PathLike[str] | None = None,
    uploads_dir: str | os.PathLike[str] | None = None,
    secret: str | None = None,
) -> FastAPI:
    db_path = str(db_path if db_path is not None else os.environ.get("PORTAL_DB", "./portal.db"))
    uploads_dir = Path(uploads_dir if uploads_dir is not None else os.environ.get("PORTAL_UPLOADS", "./uploads"))
    secret = secret if secret is not None else os.environ.get("PORTAL_SECRET", DEFAULT_SECRET)

    app = FastAPI(title="Meridian Provider Portal", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path
    app.state.uploads_dir = uploads_dir
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["money"] = fmt_money
    templates.env.globals["status_label"] = lambda s: STATUS_LABELS.get(s, s)

    serializer = URLSafeTimedSerializer(secret, salt="portal-session")

    # -- sessions ---------------------------------------------------------- #
    def current_user(request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            payload = serializer.loads(token, max_age=SESSION_MAX_AGE)
        except BadSignature:
            return None
        if not isinstance(payload, dict) or "username" not in payload:
            return None
        return payload

    def require_user(request: Request) -> dict[str, Any]:
        user = current_user(request)
        if user is None:
            raise LoginRequired(request.url.path)
        return user

    def set_session(response: Response, user_row: sqlite3.Row) -> None:
        token = serializer.dumps({"uid": user_row["id"], "username": user_row["username"], "display_name": user_row["display_name"]})
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", path="/")

    # -- db ---------------------------------------------------------------- #
    def get_db():
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    # -- rendering --------------------------------------------------------- #
    def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200) -> HTMLResponse:
        ctx: dict[str, Any] = {"user": current_user(request), "flash": request.cookies.get(FLASH_COOKIE) or None, "active": None}
        if context:
            ctx.update(context)
        response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
        if FLASH_COOKIE in request.cookies:
            response.delete_cookie(FLASH_COOKIE, path="/")
        return response

    def redirect_with_flash(url: str, message: str) -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(FLASH_COOKIE, message, max_age=60, httponly=True, samesite="lax", path="/")
        return response

    def audit(conn: sqlite3.Connection, actor: str, action: str, entity: str, entity_id: str, detail: dict[str, Any], ts: str) -> None:
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail_json, via) VALUES (?, ?, ?, ?, ?, ?, 'ui')",
            (ts, actor, action, entity, entity_id, json.dumps(detail, sort_keys=True)),
        )

    def fetch_claim(conn: sqlite3.Connection, claim_number: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT c.*, p.first_name, p.last_name, p.dob, p.member_id AS patient_member_id, p.portal_patient_id, "
            "p.payer_plan, p.group_number, pr.name AS provider_name, pr.npi AS provider_npi, pr.specialty AS provider_specialty "
            "FROM claims c JOIN patients p ON p.id = c.patient_id JOIN providers pr ON pr.id = c.provider_id "
            "WHERE c.claim_number = ?",
            (claim_number,),
        ).fetchone()
        if row is None:
            raise NotFound(f"Claim {claim_number} was not found.")
        return row

    # -- middleware: page_views -------------------------------------------- #
    @app.middleware("http")
    async def record_page_view(request: Request, call_next):
        path = request.url.path
        if not (path.startswith("/static/") or path == "/healthz") and os.path.exists(db_path):
            conn = connect(db_path)
            try:
                with transaction(conn):
                    conn.execute("INSERT INTO page_views (ts, path) VALUES (?, ?)", (now(), path))
            except sqlite3.Error:
                pass  # never let bookkeeping break a request
            finally:
                conn.close()
        return await call_next(request)

    # -- error handlers ---------------------------------------------------- #
    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        query = urlencode({"next": exc.next_path}) if exc.next_path and exc.next_path != "/" else ""
        return RedirectResponse(f"/login?{query}" if query else "/login", status_code=303)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return render(request, "error.html", {"title": "Not found", "message": exc.message}, status_code=404)

    @app.exception_handler(404)
    async def _http_404(request: Request, exc):
        return render(request, "error.html", {"title": "Not found", "message": "The page you requested does not exist."}, status_code=404)

    # -- routes ------------------------------------------------------------ #
    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"ok": True, "db": db_path})

    @app.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse("/claims", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/claims"):
        return render(request, "login.html", {"error": None, "next": next, "username": ""})

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        next: str = Form("/claims"),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        username = username.strip()
        row = conn.execute("SELECT id, username, password_hash, display_name FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return render(request, "login.html", {"error": "Invalid username or password.", "next": next, "username": username}, status_code=401)
        target = next if next.startswith("/") and not next.startswith("//") else "/claims"
        response = RedirectResponse(target, status_code=303)
        set_session(response, row)
        return response

    @app.get("/logout")
    @app.post("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # Claims ---------------------------------------------------------------- #
    @app.get("/claims", response_class=HTMLResponse)
    async def claims_list(request: Request, status: str = "", q: str = "", page: int = 1, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        status = status.strip().upper()
        q = q.strip()
        where: list[str] = []
        params: list[Any] = []
        if status:
            if status not in CLAIM_STATUSES:
                status = ""
            else:
                where.append("c.status = ?")
                params.append(status)
        if q:
            like = f"%{q}%"
            where.append(
                "(c.claim_number LIKE ? OR p.first_name LIKE ? OR p.last_name LIKE ? "
                "OR (p.first_name || ' ' || p.last_name) LIKE ? OR (p.last_name || ', ' || p.first_name) LIKE ? "
                "OR p.member_id LIKE ? OR c.submitted_member_id LIKE ?)"
            )
            params.extend([like] * 7)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        base_from = "FROM claims c JOIN patients p ON p.id = c.patient_id JOIN providers pr ON pr.id = c.provider_id"
        total = conn.execute(f"SELECT COUNT(*) {base_from}{where_sql}", params).fetchone()[0]
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(1, page), pages)
        rows = conn.execute(
            f"SELECT c.claim_number, c.service_date, c.amount_cents, c.status, c.submitted_member_id, "
            f"p.first_name, p.last_name, p.dob, p.portal_patient_id, pr.name AS provider_name "
            f"{base_from}{where_sql} ORDER BY c.service_date DESC, c.claim_number ASC LIMIT ? OFFSET ?",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()

        def page_url(n: int) -> str:
            qs = {}
            if status:
                qs["status"] = status
            if q:
                qs["q"] = q
            if n > 1:
                qs["page"] = n
            return "/claims" + ("?" + urlencode(qs) if qs else "")

        first = (page - 1) * PAGE_SIZE + 1 if total else 0
        last = min(total, page * PAGE_SIZE)
        return render(request, "claims.html", {
            "active": "claims",
            "claims": rows,
            "status": status,
            "q": q,
            "statuses": CLAIM_STATUSES,
            "page": page,
            "pages": pages,
            "total": total,
            "first": first,
            "last": last,
            "prev_url": page_url(page - 1) if page > 1 else None,
            "next_url": page_url(page + 1) if page < pages else None,
            "page_urls": [(n, page_url(n)) for n in range(1, pages + 1)],
        })

    @app.get("/claims/{claim_number}", response_class=HTMLResponse)
    async def claim_detail(request: Request, claim_number: str, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        claim = fetch_claim(conn, claim_number)
        appeals = conn.execute(
            "SELECT id, reason_code, authorization_number, narrative, attachment_name, attachment_sha256, created_at "
            "FROM appeals WHERE claim_id = ? ORDER BY created_at ASC, id ASC",
            (claim["id"],),
        ).fetchall()
        resubs = conn.execute(
            "SELECT id, member_id, note, created_at FROM resubmissions WHERE claim_id = ? ORDER BY created_at ASC, id ASC",
            (claim["id"],),
        ).fetchall()
        reason_text = dict(APPEAL_REASONS)
        return render(request, "claim_detail.html", {
            "active": "claims",
            "claim": claim,
            "appeals": appeals,
            "resubmissions": resubs,
            "reason_text": reason_text,
            "can_appeal": claim["status"] == "DENIED",
            "can_resubmit": claim["status"] in ("DENIED", "SUBMITTED"),
        })

    # Appeal ---------------------------------------------------------------- #
    def appeal_context(claim: sqlite3.Row, values: dict[str, str], errors: list[str]) -> dict[str, Any]:
        return {"active": "claims", "claim": claim, "reasons": APPEAL_REASONS, "values": values, "errors": errors}

    @app.get("/claims/{claim_number}/appeal", response_class=HTMLResponse)
    async def appeal_form(request: Request, claim_number: str, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        claim = fetch_claim(conn, claim_number)
        values = {"reason_code": "", "authorization_number": "", "narrative": ""}
        errors: list[str] = []
        if claim["status"] not in APPEALABLE_STATUSES:
            errors.append(f"Claim {claim_number} is {STATUS_LABELS[claim['status']].lower()} and cannot be appealed.")
        return render(request, "appeal.html", appeal_context(claim, values, errors))

    @app.post("/claims/{claim_number}/appeal", response_class=HTMLResponse)
    async def appeal_submit(
        request: Request,
        claim_number: str,
        reason_code: str = Form(""),
        authorization_number: str = Form(""),
        narrative: str = Form(""),
        attachment: UploadFile | None = File(None),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        user = require_user(request)
        claim = fetch_claim(conn, claim_number)
        reason_code = reason_code.strip()
        authorization_number = authorization_number.strip()
        narrative = narrative.strip()
        values = {"reason_code": reason_code, "authorization_number": authorization_number, "narrative": narrative}
        errors: list[str] = []
        if claim["status"] not in APPEALABLE_STATUSES:
            errors.append(f"Claim {claim_number} is {STATUS_LABELS[claim['status']].lower()} and cannot be appealed.")
        if reason_code not in APPEAL_REASON_CODES:
            errors.append("Please select a reason.")
        if len(narrative) < NARRATIVE_MIN_CHARS:
            errors.append(f"Narrative must be at least {NARRATIVE_MIN_CHARS} characters.")

        attachment_name: str | None = None
        attachment_sha256: str | None = None
        attachment_bytes: bytes = b""
        if attachment is not None and attachment.filename:
            attachment_bytes = await attachment.read()
            if attachment_bytes:
                attachment_name = safe_filename(attachment.filename)
                attachment_sha256 = hashlib.sha256(attachment_bytes).hexdigest()
        if errors:
            return render(request, "appeal.html", appeal_context(claim, values, errors), status_code=400)

        if attachment_sha256 is not None and attachment_name is not None:
            target_dir = uploads_dir / claim_number
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / f"{attachment_sha256}_{attachment_name}").write_bytes(attachment_bytes)

        ts = now()
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO appeals (claim_id, reason_code, authorization_number, narrative, attachment_name, attachment_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (claim["id"], reason_code, authorization_number or None, narrative, attachment_name, attachment_sha256, ts),
            )
            appeal_id = cur.lastrowid
            conn.execute("UPDATE claims SET status = 'APPEAL_SUBMITTED', updated_at = ? WHERE id = ?", (ts, claim["id"]))
            audit(conn, user["username"], "appeal.create", "claims", str(claim["id"]), {
                "claim_id": claim["id"],
                "claim_number": claim_number,
                "appeal_id": appeal_id,
                "reason_code": reason_code,
                "authorization_number": authorization_number or None,
                "attachment_name": attachment_name,
                "attachment_sha256": attachment_sha256,
                "previous_status": claim["status"],
                "new_status": "APPEAL_SUBMITTED",
            }, ts)
        return redirect_with_flash(f"/claims/{claim_number}", "Appeal submitted")

    # Resubmit -------------------------------------------------------------- #
    def resubmit_context(claim: sqlite3.Row, values: dict[str, str], errors: list[str]) -> dict[str, Any]:
        return {"active": "claims", "claim": claim, "values": values, "errors": errors}

    @app.get("/claims/{claim_number}/resubmit", response_class=HTMLResponse)
    async def resubmit_form(request: Request, claim_number: str, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        claim = fetch_claim(conn, claim_number)
        values = {"member_id": claim["submitted_member_id"], "note": ""}
        errors: list[str] = []
        if claim["status"] not in RESUBMITTABLE_STATUSES:
            errors.append(f"Claim {claim_number} is {STATUS_LABELS[claim['status']].lower()} and cannot be resubmitted.")
        return render(request, "resubmit.html", resubmit_context(claim, values, errors))

    @app.post("/claims/{claim_number}/resubmit", response_class=HTMLResponse)
    async def resubmit_submit(
        request: Request,
        claim_number: str,
        member_id: str = Form(""),
        note: str = Form(""),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        user = require_user(request)
        claim = fetch_claim(conn, claim_number)
        member_id = member_id.strip()
        note = note.strip()
        values = {"member_id": member_id, "note": note}
        errors: list[str] = []
        if claim["status"] not in RESUBMITTABLE_STATUSES:
            errors.append(f"Claim {claim_number} is {STATUS_LABELS[claim['status']].lower()} and cannot be resubmitted.")
        if not member_id:
            errors.append("Member ID is required.")
        if errors:
            return render(request, "resubmit.html", resubmit_context(claim, values, errors), status_code=400)

        ts = now()
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO resubmissions (claim_id, member_id, note, created_at) VALUES (?, ?, ?, ?)",
                (claim["id"], member_id, note or None, ts),
            )
            resub_id = cur.lastrowid
            conn.execute(
                "UPDATE claims SET submitted_member_id = ?, status = 'RESUBMITTED', updated_at = ? WHERE id = ?",
                (member_id, ts, claim["id"]),
            )
            audit(conn, user["username"], "claim.resubmit", "claims", str(claim["id"]), {
                "claim_id": claim["id"],
                "claim_number": claim_number,
                "resubmission_id": resub_id,
                "previous_member_id": claim["submitted_member_id"],
                "member_id": member_id,
                "note": note or None,
                "previous_status": claim["status"],
                "new_status": "RESUBMITTED",
            }, ts)
        return redirect_with_flash(f"/claims/{claim_number}", "Claim resubmitted")

    # Patients -------------------------------------------------------------- #
    @app.get("/patients", response_class=HTMLResponse)
    async def patients_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        rows = conn.execute(
            "SELECT portal_patient_id, first_name, last_name, dob, member_id, payer_plan FROM patients "
            "ORDER BY last_name ASC, first_name ASC, portal_patient_id ASC"
        ).fetchall()
        return render(request, "patients.html", {"active": "patients", "patients": rows})

    @app.get("/patients/{portal_patient_id}", response_class=HTMLResponse)
    async def patient_detail(request: Request, portal_patient_id: str, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        patient = conn.execute("SELECT * FROM patients WHERE portal_patient_id = ?", (portal_patient_id,)).fetchone()
        if patient is None:
            raise NotFound(f"Patient {portal_patient_id} was not found.")
        claims = conn.execute(
            "SELECT c.claim_number, c.service_date, c.cpt_code, c.amount_cents, c.status, c.submitted_member_id, pr.name AS provider_name "
            "FROM claims c JOIN providers pr ON pr.id = c.provider_id WHERE c.patient_id = ? "
            "ORDER BY c.service_date DESC, c.claim_number ASC",
            (patient["id"],),
        ).fetchall()
        return render(request, "patient_detail.html", {"active": "patients", "patient": patient, "claims": claims})

    # Messages -------------------------------------------------------------- #
    @app.get("/messages", response_class=HTMLResponse)
    async def messages_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
        require_user(request)
        rows = conn.execute("SELECT id, subject, received_at, is_read FROM messages ORDER BY received_at DESC, id DESC").fetchall()
        unread = sum(1 for r in rows if not r["is_read"])
        return render(request, "messages.html", {"active": "messages", "messages": rows, "unread": unread})

    @app.get("/messages/{message_id}", response_class=HTMLResponse)
    async def message_detail(request: Request, message_id: int, conn: sqlite3.Connection = Depends(get_db)):
        user = require_user(request)
        row = conn.execute("SELECT id, subject, body, received_at, is_read FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise NotFound(f"Message {message_id} was not found.")
        ts = now()
        with transaction(conn):
            conn.execute("UPDATE messages SET is_read = 1 WHERE id = ?", (message_id,))
            audit(conn, user["username"], "message.read", "messages", str(message_id), {
                "subject": row["subject"],
                "was_read": bool(row["is_read"]),
            }, ts)
        row = conn.execute("SELECT id, subject, body, received_at, is_read FROM messages WHERE id = ?", (message_id,)).fetchone()
        return render(request, "message_detail.html", {"active": "messages", "message": row})

    # Eligibility ----------------------------------------------------------- #
    @app.get("/eligibility", response_class=HTMLResponse)
    async def eligibility_form(request: Request):
        require_user(request)
        return render(request, "eligibility.html", {"active": "eligibility", "values": {"member_id": "", "dob": ""}, "errors": [], "result": None})

    @app.post("/eligibility", response_class=HTMLResponse)
    async def eligibility_check(
        request: Request,
        member_id: str = Form(""),
        dob: str = Form(""),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        user = require_user(request)
        member_id = member_id.strip()
        dob_raw = dob.strip()
        values = {"member_id": member_id, "dob": dob_raw}
        errors: list[str] = []
        if not member_id:
            errors.append("Member ID is required.")
        dob_iso = normalize_dob(dob_raw) if dob_raw else None
        if not dob_raw:
            errors.append("Date of birth is required.")
        elif dob_iso is None:
            errors.append("Date of birth must be in YYYY-MM-DD format.")
        if errors:
            return render(request, "eligibility.html", {"active": "eligibility", "values": values, "errors": errors, "result": None}, status_code=400)

        patient = conn.execute(
            "SELECT portal_patient_id, first_name, last_name, dob, member_id, payer_plan, group_number FROM patients "
            "WHERE member_id = ? ORDER BY id ASC LIMIT 1",
            (member_id,),
        ).fetchone()
        if patient is None:
            result: dict[str, Any] = {"status": "NOT_FOUND", "label": "Not found", "member_id": member_id, "dob": dob_iso}
        elif patient["dob"] != dob_iso:
            result = {
                "status": "DOB_MISMATCH", "label": "DOB mismatch", "member_id": member_id, "dob": dob_iso,
                "portal_patient_id": patient["portal_patient_id"],
            }
        else:
            result = {
                "status": "ACTIVE", "label": "Active", "member_id": member_id, "dob": dob_iso,
                "portal_patient_id": patient["portal_patient_id"],
                "patient_name": f"{patient['last_name']}, {patient['first_name']}",
                "payer_plan": patient["payer_plan"], "group_number": patient["group_number"],
            }
        ts = now()
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO eligibility_checks (member_id, dob, result_json, created_at) VALUES (?, ?, ?, ?)",
                (member_id, dob_iso, json.dumps(result, sort_keys=True), ts),
            )
            check_id = cur.lastrowid
            audit(conn, user["username"], "eligibility.check", "eligibility_checks", str(check_id), {
                "member_id": member_id, "dob": dob_iso, "result": result["status"],
            }, ts)
        return render(request, "eligibility.html", {"active": "eligibility", "values": values, "errors": [], "result": result})

    # Admin (forbidden screen) ---------------------------------------------- #
    @app.get("/admin", response_class=HTMLResponse)
    async def admin(request: Request):
        return render(request, "admin.html", {"active": None})

    return app


app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORTAL_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
