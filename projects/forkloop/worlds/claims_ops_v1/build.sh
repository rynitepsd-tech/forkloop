#!/usr/bin/env bash
# One-time golden world build, run INSIDE a fresh Solari `default` desktop (Ubuntu) as root.
# Produces: portal on :8080 (systemd), OpenEMR 8.3.0 on :80, both DBs seeded with the base
# population, Chrome profile logged into both apps, window maximised. Take the golden snapshot
# right after this finishes (ClaimsOpsWorld.build does that).
#
#   sudo bash /opt/forkloop/build.sh --build-dir /opt/forkloop [--skip-openemr]
#
set -euo pipefail

BUILD_DIR=/opt/forkloop
SKIP_OPENEMR=0
PORTAL_DB=/var/lib/forkloop/portal/portal.db
PORTAL_UPLOADS=/var/lib/forkloop/portal/uploads
DESKTOP_USER="${DESKTOP_USER:-user}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --skip-openemr) SKIP_OPENEMR=1; shift ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

log() { printf '[build %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
export DEBIAN_FRONTEND=noninteractive

log "apt packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip sqlite3 curl xdotool wmctrl >/dev/null

log "portal venv + deps"
mkdir -p "$BUILD_DIR" /var/lib/forkloop/portal "$PORTAL_UPLOADS" /etc/forkloop
python3 -m venv "$BUILD_DIR/venv"
"$BUILD_DIR/venv/bin/pip" install -q --upgrade pip
"$BUILD_DIR/venv/bin/pip" install -q fastapi uvicorn jinja2 python-multipart itsdangerous pyyaml pillow numpy httpx

log "portal database"
cd "$BUILD_DIR"
rm -f "$PORTAL_DB"
PYTHONPATH="$BUILD_DIR" "$BUILD_DIR/venv/bin/python" -m worlds.claims_ops_v1.portal.db init --db "$PORTAL_DB"
PYTHONPATH="$BUILD_DIR" "$BUILD_DIR/venv/bin/python" -m worlds.claims_ops_v1.portal.db seed-base --db "$PORTAL_DB"
chown -R "$DESKTOP_USER":"$DESKTOP_USER" /var/lib/forkloop || true

log "portal service"
systemctl daemon-reload
systemctl enable --now forkloop-portal.service
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8080/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://localhost:8080/healthz

if [[ "$SKIP_OPENEMR" == "0" ]]; then
  log "OpenEMR 8.3.0 (native LAMP install)"
  bash "$BUILD_DIR/worlds/claims_ops_v1/openemr/install.sh" --with-demo-data
  # the installer writes /etc/forkloop/openemr.pw; make sure the password file is readable by the controller's exec user
  chmod 600 /etc/forkloop/openemr.pw
  log "OpenEMR base population"
  PYTHONPATH="$BUILD_DIR" "$BUILD_DIR/venv/bin/python" -m worlds.claims_ops_v1.openemr.base_data --sql > /tmp/openemr_base.sql
  mysql -u openemr --password="$(cat /etc/forkloop/openemr.pw)" openemr < /tmp/openemr_base.sql
  mkdir -p /var/www/openemr/sites/default/documents
  chown -R www-data:www-data /var/www/openemr/sites/default/documents
  chmod -R 775 /var/www/openemr/sites/default/documents
fi

log "browser profile: log into both apps and pin the window layout"
# Runs as the desktop user so the Chrome profile lives in their home. The controller's
# initial-screen step later only needs ctrl+l + URL because both sessions are already valid.
sudo -u "$DESKTOP_USER" bash "$BUILD_DIR/worlds/claims_ops_v1/browser_setup.sh" || log "browser setup reported a problem (check manually over VNC)"

log "done"
