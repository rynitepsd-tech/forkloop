# OpenEMR layer of `claims-ops-v1`

This directory is everything forkloop needs to (a) put a real **OpenEMR 8.3.0**
into the golden Solari snapshot and (b) seed / read it deterministically per
episode, while still being testable on a laptop that cannot run MariaDB.
Spec: `docs/contracts.md` §8 (plus §1, §5, §6, §9).

| File | What it is |
| --- | --- |
| `install.sh` | Unattended, idempotent native installer (apache2 + php8.3-fpm + mariadb) for Ubuntu 22.04/24.04 in a Solari desktop VM. Run by the world's `build.sh` while producing the golden snapshot. |
| `shim_schema.sql` | SQLite DDL for the OpenEMR tables forkloop touches, same table/column names, plus the reference rows OpenEMR ships (calendar categories, document category tree). |
| `openemr_sql.py` | Builders that return **portable** SQL strings (`insert_patient`, `insert_insurance`, `insert_appointment`, `insert_document`, `insert_log`, `update_insurance_policy`, `update_appointment`, generic `insert_row` / `update_row` / `delete_rows`, and `quote`). |
| `base_data.py` | Deterministic generator (`random.Random(20260901)`) of the base dataset that lives in the golden snapshot; `render_base_sql(data)` turns it into portable SQL. |
| `base_data.json` | Output of `python -m worlds.claims_ops_v1.openemr.base_data`; loaded by task families as `BaseData` (contract §9). Committed; the test suite fails if it is stale. |
| `providers.json` | The six providers (name, NPI, specialty, OpenEMR user id). **The portal must load its `providers` table from this list** so a provider named in an instruction is the same person in both apps. |
| `docs_paths.py` | `document_fs_path(pid, name)` / `document_url(pid, name)` per §8. |

## How `install.sh` is used by `build.sh`

The world's `build.sh` creates a fresh Solari desktop, copies this directory
into the VM (e.g. `/opt/forkloop/openemr/`) and runs, over the controller
channel:

```sh
sudo /opt/forkloop/openemr/install.sh --with-demo-data
```

Then it logs the Chrome profile in as `admin / pass`, and snapshots. The
script is safe to re-run (every step checks a marker: package state,
`/etc/forkloop/openemr.pw`, the cached tarball's sha256,
`sites/default/sqlconf.php` having `$config = 1`, `patient_data` already
containing pids >= 100000, ...).

What it does, in order (all logged to stdout and
`/var/log/forkloop-openemr-install.log`):

1. `apt-get` apache2, mariadb-server, php8.3-fpm + `mysql gd curl xml mbstring
   zip soap intl ldap bcmath` (`sockets` comes with `php8.3-common`;
   `imagick` is attempted and skipped if unavailable). On 22.04, where the
   archive has no php8.3, it adds `ppa:ondrej/php`.
2. Writes OpenEMR's recommended php.ini values for fpm and cli.
3. Ensures the `openemr` database and `openemr@localhost` user exist (over the
   root unix socket), generating an alphanumeric password that is written to
   `/etc/forkloop/openemr.pw` (mode 600, root). The controller reads that file
   (`world.yaml: databases.openemr.password_file`).
4. Downloads `openemr-8.3.0.tar.gz` from the official GitHub release into
   `/var/cache/forkloop/`, verifies sha256 against `OPENEMR_SHA256` (pinned to
   the digest published in the release's `.sha256` asset; `--skip-verify` to
   bypass), and unpacks `openemr-8.3.0/` to `/var/www/openemr`.
5. Apache: `Alias /openemr /var/www/openemr`, `proxy_fcgi` to php8.3-fpm,
   `AllowOverride FileInfo`, `sites/*/documents` denied (same shape as
   OpenEMR's own docker vhost).
6. Runs OpenEMR's automated installer exactly as the v8_3_0 script expects:
   `OPENEMR_ENABLE_INSTALLER_AUTO=1 php -f contrib/util/installScripts/InstallerAuto.php
   site=default server=localhost loginhost=localhost port=3306 login=openemr pass=... dbname=openemr
   collate=utf8mb4_general_ci iuser=admin iuname=Administrator iuserpass=pass no_root_db_access=1`.
   (With `--db-root-pass P` it instead passes `root=root rootpass=P` and lets
   the installer create the DB/user.) Fails on any `ERROR:` line.
7. `chown -R www-data`, `sqlconf.php` 640, `sites/default/documents` 770.
8. Enables/restarts php8.3-fpm, apache2, mariadb.
9. Health check: `GET http://localhost/openemr/interface/login/login.php?site=default`
   must return 200 and mention "openemr" (30 tries).
10. `--with-demo-data`: applies the base SQL (`$FORKLOOP_BASE_SQL`, else
    `base_data.sql` next to the script, else rendered by `python3 base_data.py --sql`)
    inside one transaction, logs per-table counts, then asks OpenEMR to
    backfill `uuid` columns (`UuidRegistry::populateAllMissingUuids()`,
    non-fatal).

## The SQLite shim

Contract §5 says every `Seeding.openemr_sql` string must run **unchanged** on
MariaDB in the VM and on SQLite locally. `shim_schema.sql` is the local side:
`sqlite3.connect(":memory:").executescript(open("shim_schema.sql").read())`
gives you tables with the exact OpenEMR 8.3.0 names, so generators, the
oracle's `?`-parameterised queries and the baseline-checksum code can all be
unit-tested without a VM.

Table subset (contract §8), columns copied from
`sql/database.sql` at tag `v8_3_0`:

- `users(id, username, password, authorized, fname, lname, facility_id, calendar, active)` + `npi, specialty`
- `patient_data(id, pid, pubpid, fname, lname, DOB, sex, street, city, state, postal_code, phone_home, providerID)`
- `insurance_companies(id, name)`
- `insurance_data(id, type, provider, plan_name, policy_number, group_number, subscriber_fname, subscriber_lname, subscriber_DOB, subscriber_relationship, pid, date)`
- `openemr_postcalendar_categories(pc_catid, pc_catname, pc_catcolor, pc_duration)` + `pc_constant_id, pc_cattype`
- `openemr_postcalendar_events(pc_eid, pc_catid, pc_aid, pc_pid, pc_title, pc_eventDate, pc_endDate, pc_startTime, pc_endTime, pc_duration, pc_apptstatus, pc_facility, pc_hometext)` + `pc_multiple, pc_informant, pc_recurrtype, pc_alldayevent, pc_eventstatus, pc_sharing`
- `documents(id, type, url, mimetype, docdate, foreign_id, name, hash, size)` + `date, revision`
- `categories(id, name, parent)`, `categories_to_documents(category_id, document_id)`
- `log(id, date, event, category, user, patient_id, comments, success)`

The "+" columns are real OpenEMR columns that a portable INSERT must set on
MariaDB (`pc_multiple` and `documents.revision` are NOT NULL without a
default, and MariaDB >= 10.10 rejects a missing `TIMESTAMP NOT NULL` under
strict mode) or that OpenEMR's own code sets and its UI relies on
(`pc_eventstatus = 1`, `pc_sharing = 1`). `users.npi/specialty` carry the
provider identity shared with the portal.

Things the shim deliberately does not model: OpenEMR's `uuid` columns
(binary(16), not portable; `install.sh` backfills them once for the base data,
and per-episode rows work without them in the classic UI), `users_secure`
(login hashes; seeded providers never log in), and every other column, all of
which have defaults in MariaDB.

### Adding a table

1. Find `CREATE TABLE \`<name>\`` in
   https://raw.githubusercontent.com/openemr/openemr/v8_3_0/sql/database.sql.
2. Copy the columns forkloop reads or writes, keeping their exact spelling and
   case; simplify types to INTEGER / TEXT / REAL; keep the PRIMARY KEY and any
   UNIQUE key you rely on; add every NOT-NULL-without-default column.
3. If database.sql ships reference rows the world depends on, copy them with
   their original ids (as done for the two category tables).
4. Add a typed helper in `openemr_sql.py` (return one `;`-terminated
   statement string, explicit id, `quote()` every literal) and a test in
   `tests/test_openemr_layer.py` that executes it on the shim.
5. Add the table to §8 of `docs/contracts.md` in the same commit.

## Base data at a glance

Fixed anchor **Monday 2026-09-07**, 6 weeks of weekday appointments,
08:00-17:00 in 15-minute slots, no lunch-hour starts, no provider double
booking, at most one visit per patient per day. Ids: providers 100001-100006,
insurance companies 100001-100004, patients (pid = id) 100001-100040,
insurance_data 100001-100040 (one `primary` per patient, `provider` = company
id as a string, effective `date` in 2024-2025), events from 100001, log rows
100001-100040 (`patient-record-insert` / `Patient Demographics`, user `admin`).

Regenerate after changing the generator:

```sh
python -m worlds.claims_ops_v1.openemr.base_data          # rewrites json
python -m worlds.claims_ops_v1.openemr.base_data --check  # CI-style staleness check
python -m worlds.claims_ops_v1.openemr.base_data --sql    # what install.sh loads
```

## Facts verified against upstream (and where)

- Release assets for 8.3.0 (published 2026-08-18): `openemr-8.3.0.tar.gz`
  (+ `.md5/.sha256/.sha512`), `openemr-8.3.0.zip` (+ digests), `changelog.md` —
  https://api.github.com/repos/openemr/openemr/releases/tags/v8_3_0 and
  https://github.com/openemr/openemr/releases/tag/v8_3_0 (PHP 8.3+, MariaDB 10.6+).
- tarball sha256 `5c73aa96...de301e` —
  https://github.com/openemr/openemr/releases/download/v8_3_0/openemr-8.3.0.tar.gz.sha256;
  the archive's top-level directory is `openemr-8.3.0/`.
- Installer arguments (`key=value`; `iuser iuname iuserpass igroup server
  loginhost port root rootpass login pass dbname collate site source_site_id
  clone_database no_root_db_access development_translations`) and the
  `OPENEMR_ENABLE_INSTALLER_AUTO=1` gate —
  https://raw.githubusercontent.com/openemr/openemr/v8_3_0/contrib/util/installScripts/InstallerAuto.php
- `quick_install()` behaviour with `no_root_db_access` (skips only root DB
  connection / create database / create user / grants; still loads
  `sql/database.sql`, translations, `official_additional_users.sql`, writes
  `sqlconf.php` with `$config = 1`, creates the admin user and ACLs) —
  https://raw.githubusercontent.com/openemr/openemr/v8_3_0/library/classes/Installer.class.php
- Table/column names, calendar categories 1-15 (`Office Visit` = 5, 900 s),
  document categories 1-34 (`Categories` = 1 root; `Lab Report` 2, `Medical
  Record` 3, `Patient Information` 4, `Advance Directive` 6), default facility
  id 3 — https://raw.githubusercontent.com/openemr/openemr/v8_3_0/sql/database.sql
- Calendar's own insert sets `pc_eventstatus = 1, pc_sharing = 1`, stores
  `pc_duration` in seconds and `pc_time = NOW()` —
  https://raw.githubusercontent.com/openemr/openemr/v8_3_0/library/encounter_events.inc.php
  (called from https://raw.githubusercontent.com/openemr/openemr/v8_3_0/interface/main/calendar/add_edit_event.php).
- Audit-log vocabulary (`event` = `patient-record-update` / `scheduling-update`
  ..., `category` = `Patient Demographics` / `Patient Insurance` / `Scheduling`) —
  https://raw.githubusercontent.com/openemr/openemr/v8_3_0/src/Common/Logging/EventAuditLogger.php
