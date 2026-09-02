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
HEADLESS=0
PORTAL_DB=/var/lib/forkloop/portal/portal.db
PORTAL_UPLOADS=/var/lib/forkloop/portal/uploads
# The X session owner (Solari's default desktop runs XFCE as "desktop" on Xvfb :0); detect it.
DESKTOP_USER="${DESKTOP_USER:-$(ps -eo user,comm | awk '$2=="xfce4-session"{print $1; exit}')}"
DESKTOP_USER="${DESKTOP_USER:-desktop}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --skip-openemr) SKIP_OPENEMR=1; shift ;;
    --headless) HEADLESS=1; shift ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

log() { printf '[build %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
export DEBIAN_FRONTEND=noninteractive

log "disk: $(df -h / | tail -1)"
if [[ "$HEADLESS" == "0" ]]; then
  # The `default` desktop template ships VS Code (~1 GB) and LibreOffice (~0.3 GB) on a
  # 4 GB disk that cannot be enlarged (disk_gb is ignored); OpenEMR + MariaDB + PHP need
  # that space. The world only needs Chrome.
  log "slimming the desktop image (purging VS Code and LibreOffice)"
  (apt-get purge -y -qq code 'libreoffice*' >/dev/null 2>&1 || true)
  rm -rf /usr/share/code /usr/lib/libreoffice
  (apt-get autoremove -y -qq >/dev/null 2>&1 || true)
  apt-get clean
  log "disk after slimming: $(df -h / | tail -1)"
fi

log "apt packages"
# The template ships Microsoft's VS Code apt source whose signing key is not trusted any more
# (NO_PUBKEY EB3E94ADBE1229CF on 2026-09-02) and it fails `apt-get update` for every repo. We
# purge VS Code anyway, so drop every packages.microsoft.com source before updating.
grep -ls 'packages.microsoft.com' /etc/apt/sources.list.d/* 2>/dev/null | xargs -r rm -f
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip sqlite3 curl procps ca-certificates >/dev/null
if [[ "$HEADLESS" == "0" ]]; then apt-get install -y -qq xdotool wmctrl >/dev/null; fi

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
  # install.sh --with-demo-data already loads it; only apply when the base patients are absent
  N=$(mysql -N -u openemr --password="$(cat /etc/forkloop/openemr.pw)" openemr -e "SELECT COUNT(*) FROM patient_data WHERE pid BETWEEN 100001 AND 100040")
  if [[ "${N:-0}" -ge 40 ]]; then
    log "  base population already present ($N patients); skipping"
  else
    PYTHONPATH="$BUILD_DIR" "$BUILD_DIR/venv/bin/python" -m worlds.claims_ops_v1.openemr.base_data --sql > /tmp/openemr_base.sql
    mysql -u openemr --password="$(cat /etc/forkloop/openemr.pw)" openemr < /tmp/openemr_base.sql
  fi
  mkdir -p /var/www/openemr/sites/default/documents
  chown -R www-data:www-data /var/www/openemr/sites/default/documents
  chmod -R 775 /var/www/openemr/sites/default/documents
fi

if [[ "$HEADLESS" == "1" ]]; then
  log "headless build: skipping browser profile setup"
else
  # Chrome enterprise policy: no password-save / translate / sign-in bubbles that would steal focus from an agent.
  mkdir -p /etc/opt/chrome/policies/managed
  install -m 644 "$BUILD_DIR/worlds/claims_ops_v1/chrome_policy.json" /etc/opt/chrome/policies/managed/forkloop.json
  log "browser profile: log into both apps and pin the window layout"
  # Runs as the desktop user so the Chrome profile lives in their home. The controller's
  # initial-screen step later only needs ctrl+l + URL because both sessions are already valid.
  # Chrome refuses to run as root; run the setup as the session user with its display and runtime dir.
  runuser -u "$DESKTOP_USER" -- env DISPLAY=:0 HOME="/home/$DESKTOP_USER" XDG_RUNTIME_DIR="/run/$DESKTOP_USER" \
    bash "$BUILD_DIR/worlds/claims_ops_v1/browser_setup.sh" || log "browser setup reported a problem (check manually over VNC)"
fi

# Reclaim space so episodes have headroom on the 4 GB disk (a full disk breaks OpenEMR: "table 'log' is full").
apt-get purge -y -qq gcc g++ cpp gcc-11 g++-11 cpp-11 >/dev/null 2>&1 || true
apt-get autoremove -y -qq >/dev/null 2>&1 || true
apt-get clean; rm -rf /var/lib/apt/lists/* /var/cache/forkloop /tmp/openemr* /usr/share/doc/* /usr/share/man/*
journalctl --vacuum-size=8M >/dev/null 2>&1 || true
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' \) -delete 2>/dev/null || true
for f in /var/log/syslog /var/log/kern.log /var/log/auth.log /var/log/apache2/*.log /var/log/mysql/*.log; do [[ -f "$f" ]] && : > "$f"; done
rm -rf /home/*/.cache/google-chrome /home/*/.config/forkloop-chrome/Default/Cache /home/*/.config/forkloop-chrome/Default/Code\ Cache 2>/dev/null || true
log "disk at end: $(df -h / | tail -1)"
log "done"
