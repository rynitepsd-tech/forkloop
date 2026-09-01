#!/usr/bin/env bash
# forkloop / claims-ops-v1 -- unattended, idempotent OpenEMR 8.3.0 installer.
#
# Target: Ubuntu 22.04 / 24.04 inside a Solari desktop VM (Cloud Hypervisor
# microVM, root via sudo, NO Docker).  Native stack: apache2 + php8.3-fpm +
# mariadb-server.  Safe to re-run: every step checks its own marker and skips.
#
#   sudo ./install.sh [--with-demo-data] [--skip-verify] [--db-root-pass PASS] [--help]
#
#   --with-demo-data   also load the forkloop base dataset (base_data.sql next to
#                      this script, or rendered from base_data.py) into the DB
#   --skip-verify      do not check the tarball sha256 (see OPENEMR_SHA256)
#   --db-root-pass P   let OpenEMR's installer create the DB/user itself using
#                      MariaDB root + this password (default: this script
#                      pre-creates them over the unix socket and runs the
#                      installer with no_root_db_access=1)
#
# Overridable via environment: OPENEMR_VERSION, OPENEMR_TARBALL_URL,
# OPENEMR_SHA256, OPENEMR_DB_PASS, FORKLOOP_BASE_SQL, PHP_VERSION.
#
# Verified against the v8_3_0 tag:
#   release assets : https://api.github.com/repos/openemr/openemr/releases/tags/v8_3_0
#   installer args : https://raw.githubusercontent.com/openemr/openemr/v8_3_0/contrib/util/installScripts/InstallerAuto.php
#                    (key=value argv, requires env OPENEMR_ENABLE_INSTALLER_AUTO=1)
#   Installer class: https://raw.githubusercontent.com/openemr/openemr/v8_3_0/library/classes/Installer.class.php
set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
OPENEMR_VERSION="${OPENEMR_VERSION:-8.3.0}"
OPENEMR_TAG="v${OPENEMR_VERSION//./_}"
TARBALL="openemr-${OPENEMR_VERSION}.tar.gz"
OPENEMR_TARBALL_URL="${OPENEMR_TARBALL_URL:-https://github.com/openemr/openemr/releases/download/${OPENEMR_TAG}/${TARBALL}}"
# sha256 of openemr-8.3.0.tar.gz as published in the release's .sha256 asset
# (https://github.com/openemr/openemr/releases/download/v8_3_0/openemr-8.3.0.tar.gz.sha256).
# If you change OPENEMR_VERSION, replace this with the new digest (or --skip-verify once
# and copy the value the script logs).
OPENEMR_SHA256="${OPENEMR_SHA256:-5c73aa961a8ca5c37a12a812a96eb99451b807d7657029a34d22d20551de301e}"

PHP_VERSION="${PHP_VERSION:-8.3}"
WEB_ROOT=/var/www/openemr
SITE=default
DB_NAME=openemr
DB_USER=openemr
DB_HOST=localhost
DB_PORT=3306
ADMIN_USER=admin
ADMIN_PASS=pass                     # demo default (contract §8); synthetic world only
ADMIN_LNAME=Administrator

FORKLOOP_ETC=/etc/forkloop
DB_PW_FILE="${FORKLOOP_ETC}/openemr.pw"
INSTALL_MARKER="${FORKLOOP_ETC}/openemr.installed"
DEMO_MARKER="${FORKLOOP_ETC}/openemr.base_data_loaded"
CACHE_DIR=/var/cache/forkloop
LOG_FILE=/var/log/forkloop-openemr-install.log
HEALTH_URL="http://localhost/openemr/interface/login/login.php?site=${SITE}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WITH_DEMO_DATA=0
SKIP_VERIFY=0
DB_ROOT_PASS="${DB_ROOT_PASS:-}"
ORIG_ARGS=("$@")   # kept for the sudo re-exec below (the parse loop shifts $@ away)

usage() { sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-demo-data) WITH_DEMO_DATA=1 ;;
    --skip-verify)    SKIP_VERIFY=1 ;;
    --db-root-pass)   shift; DB_ROOT_PASS="${1:-}" ;;
    --db-root-pass=*) DB_ROOT_PASS="${1#*=}" ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ----------------------------------------------------------------------------
# Logging / privilege
# ----------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  exec sudo -E bash "${BASH_SOURCE[0]}" "${ORIG_ARGS[@]}"
fi
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
step() { log "==> $*"; }
die()  { log "ERROR: $*"; exit 1; }
trap 'die "failed at line $LINENO (exit $?)"' ERR

export DEBIAN_FRONTEND=noninteractive
log "forkloop OpenEMR ${OPENEMR_VERSION} installer starting (demo_data=${WITH_DEMO_DATA} skip_verify=${SKIP_VERIFY})"

# ----------------------------------------------------------------------------
# 1. OS check
# ----------------------------------------------------------------------------
step "Detecting OS"
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
  ubuntu:22.04|ubuntu:24.04) log "Ubuntu ${VERSION_ID} detected" ;;
  *) log "WARNING: untested OS ${ID:-?} ${VERSION_ID:-?}; continuing" ;;
esac

# ----------------------------------------------------------------------------
# 2. Packages
# ----------------------------------------------------------------------------
step "Installing packages"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg tar software-properties-common lsb-release

if ! apt-cache show "php${PHP_VERSION}-fpm" >/dev/null 2>&1; then
  log "php${PHP_VERSION} not in the distro archive; adding ppa:ondrej/php"
  add-apt-repository -y ppa:ondrej/php
  apt-get update -q
fi

PHP_PKGS=(
  "php${PHP_VERSION}-fpm" "php${PHP_VERSION}-cli" "php${PHP_VERSION}-common"
  "php${PHP_VERSION}-mysql" "php${PHP_VERSION}-gd" "php${PHP_VERSION}-curl"
  "php${PHP_VERSION}-xml" "php${PHP_VERSION}-mbstring" "php${PHP_VERSION}-zip"
  "php${PHP_VERSION}-soap" "php${PHP_VERSION}-intl" "php${PHP_VERSION}-ldap"
  "php${PHP_VERSION}-bcmath"
)
apt-get install -y -q apache2 mariadb-server mariadb-client "${PHP_PKGS[@]}"
if apt-get install -y -q "php${PHP_VERSION}-imagick"; then
  log "php${PHP_VERSION}-imagick installed (optional)"
else
  log "php${PHP_VERSION}-imagick unavailable; skipping (optional)"
fi

step "Checking PHP extensions"
for ext in mysqli gd curl xml mbstring zip soap intl ldap bcmath sockets openssl json; do
  if php -m | grep -qix "$ext"; then
    log "  php ext ok: $ext"
  else
    die "required PHP extension missing: $ext"
  fi
done

# ----------------------------------------------------------------------------
# 3. PHP configuration (OpenEMR recommended values)
# ----------------------------------------------------------------------------
step "Writing PHP settings"
for sapi in fpm cli; do
  ini="/etc/php/${PHP_VERSION}/${sapi}/conf.d/90-forkloop-openemr.ini"
  cat > "$ini" <<'EOF'
; forkloop: OpenEMR 8.x recommended php settings
short_open_tag = Off
max_execution_time = 60
max_input_time = -1
max_input_vars = 3000
memory_limit = 512M
display_errors = Off
log_errors = On
post_max_size = 30M
file_uploads = On
upload_max_filesize = 30M
mysqli.allow_local_infile = On
date.timezone = UTC
session.gc_maxlifetime = 14400
EOF
  log "  wrote $ini"
done

# ----------------------------------------------------------------------------
# 4. MariaDB: service, database, user, password file
# ----------------------------------------------------------------------------
step "Configuring MariaDB"
systemctl enable --now mariadb
for _ in $(seq 1 30); do
  mariadb -e 'SELECT 1' >/dev/null 2>&1 && break
  sleep 1
done
mariadb -e 'SELECT VERSION()' | tail -1 | sed 's/^/  mariadb /'

mkdir -p "$FORKLOOP_ETC"
chmod 755 "$FORKLOOP_ETC"
if [[ -n "${OPENEMR_DB_PASS:-}" ]]; then
  DB_PASS="$OPENEMR_DB_PASS"
elif [[ -s "$DB_PW_FILE" ]]; then
  DB_PASS="$(tr -d '\n' < "$DB_PW_FILE")"
  log "  reusing DB password from $DB_PW_FILE"
else
  # alphanumeric only: InstallerAuto.php splits argv on '=' and the value is
  # interpolated into SQL/PHP by the installer.
  DB_PASS="$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 28)"
fi
umask 077
printf '%s\n' "$DB_PASS" > "$DB_PW_FILE"
chmod 600 "$DB_PW_FILE"
chown root:root "$DB_PW_FILE"
umask 022
log "  DB password stored at $DB_PW_FILE (mode 600)"

if [[ -z "$DB_ROOT_PASS" ]]; then
  # Pre-create DB + user over the root unix socket; installer runs with
  # no_root_db_access=1 and still loads the schema, creates the admin user,
  # the ACLs and translations (verified in Installer::quick_install).
  mariadb <<EOF
CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'${DB_HOST}' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'${DB_HOST}' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'${DB_HOST}';
FLUSH PRIVILEGES;
EOF
  log "  database ${DB_NAME} and user ${DB_USER}@${DB_HOST} ensured"
else
  # Root-password mode: give root a real password (keeping unix_socket auth)
  # so the installer can connect as root over TCP/socket with rootpass=...
  mariadb <<EOF
ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('${DB_ROOT_PASS}') OR unix_socket;
FLUSH PRIVILEGES;
EOF
  log "  MariaDB root password set; installer will create the DB/user itself"
fi

# ----------------------------------------------------------------------------
# 5. Download + verify tarball
# ----------------------------------------------------------------------------
step "Fetching ${TARBALL}"
mkdir -p "$CACHE_DIR"
TARBALL_PATH="${CACHE_DIR}/${TARBALL}"
verify_tarball() {
  local actual
  actual="$(sha256sum "$TARBALL_PATH" | awk '{print $1}')"
  log "  sha256 ${actual}"
  if [[ $SKIP_VERIFY -eq 1 ]]; then
    log "  --skip-verify given; not checking against pinned digest"
    return 0
  fi
  [[ -n "$OPENEMR_SHA256" ]] || die "OPENEMR_SHA256 is empty; set it or pass --skip-verify"
  [[ "$actual" == "$OPENEMR_SHA256" ]] || die "sha256 mismatch for ${TARBALL_PATH}: expected ${OPENEMR_SHA256}"
  log "  sha256 verified"
}
if [[ -s "$TARBALL_PATH" ]] && { [[ $SKIP_VERIFY -eq 1 ]] || [[ "$(sha256sum "$TARBALL_PATH" | awk '{print $1}')" == "$OPENEMR_SHA256" ]]; }; then
  log "  cached tarball ok: $TARBALL_PATH"
else
  log "  downloading ${OPENEMR_TARBALL_URL}"
  curl -fL --retry 5 --retry-delay 5 -o "${TARBALL_PATH}.part" "$OPENEMR_TARBALL_URL"
  mv "${TARBALL_PATH}.part" "$TARBALL_PATH"
fi
verify_tarball

# ----------------------------------------------------------------------------
# 6. Unpack to /var/www/openemr
# ----------------------------------------------------------------------------
step "Unpacking to ${WEB_ROOT}"
if [[ -f "${WEB_ROOT}/interface/login/login.php" && -f "${WEB_ROOT}/version.php" ]]; then
  log "  ${WEB_ROOT} already populated; skipping unpack"
else
  tmp="$(mktemp -d)"
  tar -xzf "$TARBALL_PATH" -C "$tmp"
  top="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)"   # openemr-8.3.0/
  [[ -n "$top" && -f "$top/interface/login/login.php" ]] || die "unexpected tarball layout under $tmp"
  mkdir -p "$(dirname "$WEB_ROOT")"
  rm -rf "$WEB_ROOT"
  mv "$top" "$WEB_ROOT"
  rm -rf "$tmp"
  log "  unpacked $(basename "$top") -> ${WEB_ROOT}"
fi
grep -E "v_major *= *'${OPENEMR_VERSION%%.*}'" "${WEB_ROOT}/version.php" >/dev/null \
  || log "  WARNING: ${WEB_ROOT}/version.php does not look like ${OPENEMR_VERSION}"

# ----------------------------------------------------------------------------
# 7. Apache: php-fpm proxy + /openemr alias
# ----------------------------------------------------------------------------
step "Configuring Apache"
cat > /etc/apache2/conf-available/openemr.conf <<EOF
# forkloop: OpenEMR at http://localhost/openemr (mirrors OpenEMR's own docker vhost)
Alias /openemr ${WEB_ROOT}
<Directory ${WEB_ROOT}>
    AllowOverride FileInfo
    Options -Indexes +FollowSymLinks
    Require all granted
</Directory>
<Directory ${WEB_ROOT}/sites>
    AllowOverride None
</Directory>
<Directory ${WEB_ROOT}/sites/*/documents>
    Require all denied
</Directory>
EOF
a2enmod -q proxy_fcgi setenvif rewrite headers >/dev/null
a2dismod -q mpm_prefork >/dev/null 2>&1 || true
a2enmod -q mpm_event >/dev/null
a2enconf -q "php${PHP_VERSION}-fpm" openemr >/dev/null
apache2ctl configtest
log "  apache configtest ok"

# ----------------------------------------------------------------------------
# 8. Run OpenEMR's automated installer (once)
# ----------------------------------------------------------------------------
step "Running InstallerAuto.php"
SQLCONF="${WEB_ROOT}/sites/${SITE}/sqlconf.php"
installed=0
if grep -Eq '^\$config *= *1;' "$SQLCONF" 2>/dev/null \
   && mariadb -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME}' AND table_name='users'" | grep -q '^1$'; then
  installed=1
  log "  already installed (sqlconf.php \$config=1 and ${DB_NAME}.users exists); skipping"
fi
if [[ $installed -eq 0 ]]; then
  chown -R www-data:www-data "$WEB_ROOT"
  INSTALLER_ARGS=(
    "site=${SITE}" "server=${DB_HOST}" "loginhost=${DB_HOST}" "port=${DB_PORT}"
    "login=${DB_USER}" "pass=${DB_PASS}" "dbname=${DB_NAME}"
    "collate=utf8mb4_general_ci"
    "iuser=${ADMIN_USER}" "iuname=${ADMIN_LNAME}" "iuserpass=${ADMIN_PASS}"
  )
  if [[ -z "$DB_ROOT_PASS" ]]; then
    INSTALLER_ARGS+=("no_root_db_access=1")
  else
    INSTALLER_ARGS+=("root=root" "rootpass=${DB_ROOT_PASS}")
  fi
  log "  php -f contrib/util/installScripts/InstallerAuto.php $(printf '%s ' "${INSTALLER_ARGS[@]}" | sed -E 's/(pass|rootpass|iuserpass)=[^ ]*/\1=***/g')"
  set +e
  installer_out="$(cd "$WEB_ROOT" && OPENEMR_ENABLE_INSTALLER_AUTO=1 php -f contrib/util/installScripts/InstallerAuto.php "${INSTALLER_ARGS[@]}" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "$installer_out" | sed 's/^/  installer: /'
  [[ $rc -eq 0 ]] || die "InstallerAuto.php exited $rc"
  printf '%s\n' "$installer_out" | grep -q '^ERROR:' && die "InstallerAuto.php reported an error"
  grep -Eq '^\$config *= *1;' "$SQLCONF" || die "installer finished but ${SQLCONF} has no \$config = 1"
  log "  install complete"
fi

# ----------------------------------------------------------------------------
# 9. Permissions
# ----------------------------------------------------------------------------
step "Fixing permissions"
chown -R www-data:www-data "$WEB_ROOT"
chmod 640 "$SQLCONF"
mkdir -p "${WEB_ROOT}/sites/${SITE}/documents"
chown -R www-data:www-data "${WEB_ROOT}/sites/${SITE}/documents"
chmod -R u+rwX,g+rwX,o-rwx "${WEB_ROOT}/sites/${SITE}/documents"
log "  ${WEB_ROOT} owned by www-data; sqlconf.php 640; documents/ 770"

# ----------------------------------------------------------------------------
# 10. Services
# ----------------------------------------------------------------------------
step "Enabling services"
systemctl enable --now "php${PHP_VERSION}-fpm" apache2 mariadb
systemctl restart "php${PHP_VERSION}-fpm"
systemctl restart apache2
log "  php-fpm, apache2, mariadb enabled and (re)started"

# ----------------------------------------------------------------------------
# 11. Health check
# ----------------------------------------------------------------------------
step "Health check ${HEALTH_URL}"
body="$(mktemp)"
ok=0
for i in $(seq 1 30); do
  code="$(curl -sS -o "$body" -w '%{http_code}' "$HEALTH_URL" || true)"
  if [[ "$code" == "200" ]] && grep -qi 'openemr' "$body"; then
    ok=1; break
  fi
  log "  attempt $i: HTTP ${code:-none}; retrying"
  sleep 2
done
rm -f "$body"
[[ $ok -eq 1 ]] || die "health check failed: ${HEALTH_URL}"
log "  login page is up (HTTP 200)"

printf 'version=%s\ninstalled_at=%s\n' "$OPENEMR_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$INSTALL_MARKER"

# ----------------------------------------------------------------------------
# 12. Optional: base (demo) data for the golden snapshot
# ----------------------------------------------------------------------------
if [[ $WITH_DEMO_DATA -eq 1 ]]; then
  step "Loading forkloop base data"
  existing="$(mariadb -N "$DB_NAME" -e 'SELECT COUNT(*) FROM patient_data WHERE pid >= 100000')"
  if [[ "$existing" != "0" ]]; then
    log "  ${existing} seeded patients already present; skipping base data"
  else
    base_sql=""
    if [[ -n "${FORKLOOP_BASE_SQL:-}" && -s "${FORKLOOP_BASE_SQL}" ]]; then
      base_sql="$FORKLOOP_BASE_SQL"
    elif [[ -s "${SCRIPT_DIR}/base_data.sql" ]]; then
      base_sql="${SCRIPT_DIR}/base_data.sql"
    elif [[ -f "${SCRIPT_DIR}/base_data.py" ]] && command -v python3 >/dev/null; then
      base_sql="$(mktemp --suffix=.sql)"
      (cd "$SCRIPT_DIR" && python3 base_data.py --sql) > "$base_sql"
      log "  rendered base SQL from base_data.py ($(wc -l < "$base_sql") lines)"
    else
      die "no base data: set FORKLOOP_BASE_SQL, or place base_data.sql / base_data.py next to install.sh"
    fi
    log "  applying $base_sql in one transaction"
    { echo 'START TRANSACTION;'; cat "$base_sql"; echo 'COMMIT;'; } | mariadb "$DB_NAME"
    for t in users patient_data insurance_data openemr_postcalendar_events log; do
      n="$(mariadb -N "$DB_NAME" -e "SELECT COUNT(*) FROM ${t} WHERE $( [[ $t == openemr_postcalendar_events ]] && echo pc_eid || echo id ) >= 100000")"
      log "  ${t}: ${n} seeded rows"
    done
    # Seeded rows have no uuid (binary(16) cannot be written portably).  Ask
    # OpenEMR to backfill them the same way sql_upgrade.php does.  Non-fatal.
    step "Backfilling uuids"
    if (cd "$WEB_ROOT" && php -r '
        $_GET["site"] = "'"$SITE"'"; $ignoreAuth = true;
        require_once "interface/globals.php";
        \OpenEMR\Common\Uuid\UuidRegistry::populateAllMissingUuids();
        echo "uuid backfill done\n";' 2>&1 | sed 's/^/  /'); then
      :
    else
      log "  WARNING: uuid backfill failed (non-fatal; run sql_upgrade.php or ignore if the UI works)"
    fi
    date -u +%Y-%m-%dT%H:%M:%SZ > "$DEMO_MARKER"
  fi
fi

log "DONE: OpenEMR ${OPENEMR_VERSION} at http://localhost/openemr (site=${SITE}, admin=${ADMIN_USER}/${ADMIN_PASS}); DB creds ${DB_USER} / ${DB_PW_FILE}"
