#!/usr/bin/env bash
# PID-1 script (under tini) of the kanboard-v1 image: bring the world up, then write /run/forkloop/ready
# (the contract forkloop/backends/docker.py waits on; same files as the claims-ops-v1 image).
#
#   app       Kanboard v1.2.54: nginx on :80 + php8.1-fpm (the official image's layout), SQLite in /var/www/app/data
#   desktop   Xvfb :0 1280x720x24, a session D-Bus at /run/desktop/bus, XFCE as user `desktop`
#   browser   Google Chrome with the claims-ops-v1 flags and profile path, on the Kanboard dashboard
#
# A container started from a `docker commit` of a running world boots through this same script, so
# stale X locks and Chrome singleton files are removed first. Timings: /run/forkloop/boot.json.
set -uo pipefail

RUN=/run/forkloop
SCREEN="${FORKLOOP_SCREEN:-1280x720}"
DUSER=desktop
DHOME=/home/$DUSER
DRUN=/run/$DUSER
PROFILE=$DHOME/.config/forkloop-chrome
APP_URL=http://localhost
START_URL="${FORKLOOP_START_URL:-http://localhost/}"

mkdir -p "$RUN"
exec > >(tee -a /var/log/forkloop-boot.log) 2>&1
T0=$(date +%s%N)
declare -A STAGE
elapsed() { echo $(( ($(date +%s%N) - T0) / 1000000 )); }
mark() { STAGE[$1]=$(elapsed); echo "[boot +${STAGE[$1]}ms] $1"; }
as_desktop() { runuser -u "$DUSER" -- env DISPLAY=:0 HOME="$DHOME" XDG_RUNTIME_DIR="$DRUN" "$@"; }

shutdown() {
  echo "[boot] stopping"
  rm -f "$RUN/ready"
  pkill -TERM -x chrome 2>/dev/null
  for _ in $(seq 1 50); do pgrep -x chrome >/dev/null || break; sleep 0.1; done
  nginx -s quit 2>/dev/null
  [[ -s /run/php/php8.1-fpm.pid ]] && kill -QUIT "$(cat /run/php/php8.1-fpm.pid)" 2>/dev/null
  pkill -TERM -f xfce4-session 2>/dev/null
  pkill -TERM -x Xvfb 2>/dev/null
  exit 0
}
trap shutdown TERM INT

# --- 0. state a `docker commit` may have captured from a running container
rm -f "$RUN/ready" "$RUN/failed" "$RUN/boot.json" "$RUN"/*.pid /tmp/.X0-lock /tmp/.X11-unix/X0
rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie"
rm -rf "$DRUN" "$DHOME/.cache/sessions"
install -d -o "$DUSER" -g "$DUSER" -m 700 "$DRUN"
[[ -f "$DHOME/chrome.log" ]] || install -o "$DUSER" -g "$DUSER" -m 644 /dev/null "$DHOME/chrome.log"
chown "$DUSER:$DUSER" "$DHOME/chrome.log"
mark cleaned

# --- 1. Kanboard. Root owns the SQLite file and every process that writes it (php-fpm workers, -R, and the
# controller's sqlite3 over the agent channel), so a journal file one of them leaves never locks out the other.
install -d -m 755 /run/php
rm -f /run/php/php8.1-fpm.pid /run/php/php8.1-fpm.sock /run/nginx.pid
/usr/sbin/php-fpm8.1 -R --daemonize --pid /run/php/php8.1-fpm.pid --fpm-config /etc/php/8.1/fpm/php-fpm.conf \
  >>/var/log/forkloop-kanboard.log 2>&1
nginx >>/var/log/forkloop-kanboard.log 2>&1
mark kanboard_start

# Chrome profile captured from a running Chrome: mark it cleanly exited (no "Restore pages?" bubble).
python3 - "$PROFILE" <<'PYEOF' || echo "[boot] could not normalise the Chrome profile exit state"
import json, os, sys
path = os.path.join(sys.argv[1], "Default/Preferences")
if os.path.exists(path):
    data = json.load(open(path))
    prof = data.setdefault("profile", {})
    if prof.get("exit_type") != "Normal" or prof.get("exited_cleanly") is not True:
        prof["exit_type"], prof["exited_cleanly"] = "Normal", True
        st = os.stat(path)
        json.dump(data, open(path, "w"))
        os.chown(path, st.st_uid, st.st_gid)
PYEOF

# --- 2. X server + session bus + XFCE
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
Xvfb :0 -screen 0 "${SCREEN}x24" -nolisten tcp -ac -s 0 -dpms +extension RANDR >/var/log/forkloop-xvfb.log 2>&1 &
for _ in $(seq 1 200); do [[ -S /tmp/.X11-unix/X0 ]] && break; sleep 0.02; done
mark xvfb
as_desktop xset s off -dpms s noblank 2>/dev/null || true
as_desktop dbus-daemon --session --address="unix:path=$DRUN/bus" --fork --nopidfile >/dev/null 2>&1 || true
start_xfce() {
  as_desktop env DBUS_SESSION_BUS_ADDRESS="unix:path=$DRUN/bus" setsid xfce4-session >>"$DHOME/.xfce4-session.log" 2>&1 &
}
wait_workarea() {
  for _ in $(seq 1 "$1"); do
    wa="$(as_desktop xprop -root _NET_WORKAREA 2>/dev/null || true)"
    [[ "$wa" =~ =\ 0,\ [1-9][0-9]*, ]] && return 0
    sleep 0.05
  done
  return 1
}
: > "$DHOME/.xfce4-session.log"; chown "$DUSER:$DUSER" "$DHOME/.xfce4-session.log"
start_xfce
# Seen 2026-09-29 in 1 of the first ~20 boots: the session never mapped its panel (no _NET_WORKAREA after
# 20 s). Start it once more before giving up, and keep the session log in the boot log.
if ! wait_workarea 200; then
  echo "[boot] no XFCE work area after 10 s; restarting the session"; tail -n 20 "$DHOME/.xfce4-session.log"
  pkill -KILL -u "$DUSER" -f 'xfce4-session|xfwm4|xfce4-panel|xfsettingsd|xfdesktop|xfconfd' 2>/dev/null; sleep 0.5
  start_xfce
  wait_workarea 300 || { echo "[boot] XFCE still without a work area"; tail -n 20 "$DHOME/.xfce4-session.log"; }
fi
mark xfce
echo "[boot] workarea: ${wa:-none}"

# --- 3. Chrome, once Kanboard answers
code=""
for _ in $(seq 1 300); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$APP_URL/healthcheck.php" || true)"
  [[ "$code" == 200 ]] && break
  sleep 0.05
done
mark kanboard_http
# Same flags as worlds/claims_ops_v1 (browser_setup.sh, ClaimsOpsWorld.chrome_base_flags).
as_desktop setsid -f google-chrome --no-first-run --no-default-browser-check --user-data-dir="$PROFILE" \
  --password-store=basic --window-position=0,0 --window-size=1280,720 --disable-session-crashed-bubble \
  --disk-cache-size=1 --media-cache-size=1 --disable-infobars --disable-gpu --disable-dev-shm-usage \
  --enable-logging=stderr --v=0 "$START_URL" >"$DHOME/chrome.log" 2>&1
WID=""
for _ in $(seq 1 400); do
  WID="$(as_desktop xdotool search --onlyvisible --class chrome 2>/dev/null | head -1)"
  [[ -n "$WID" ]] && break
  sleep 0.05
done
mark chrome_window
[[ -n "$WID" ]] && as_desktop xdotool windowactivate "$WID" 2>/dev/null || true
TITLE=""
for _ in $(seq 1 400); do
  TITLE="$(as_desktop xdotool getactivewindow getwindowname 2>/dev/null || true)"
  # a loaded page: "<page title> - Google Chrome" ("Untitled"/"localhost/..." while the page is still loading)
  [[ "$TITLE" == *" - Google Chrome" && "$TITLE" != "Untitled - Google Chrome" && "$TITLE" != localhost* ]] && break
  sleep 0.05
done
mark chrome_page
echo "[boot] window title: $TITLE"

ok=true
[[ -n "$WID" && "$code" == 200 && -n "$TITLE" ]] || ok=false
printf '{"ok": %s, "title": "%s", "kanboard_http": "%s", "stages_ms": {' "$ok" "${TITLE//\"/}" "$code" > "$RUN/boot.json"
first=1
for k in cleaned kanboard_start xvfb xfce kanboard_http chrome_window chrome_page; do
  [[ $first -eq 1 ]] || printf ', ' >> "$RUN/boot.json"; first=0
  printf '"%s": %s' "$k" "${STAGE[$k]:-null}" >> "$RUN/boot.json"
done
echo '}}' >> "$RUN/boot.json"
cat "$RUN/boot.json"
if [[ "$ok" == true ]]; then
  touch "$RUN/ready"
  echo "[boot +$(elapsed)ms] READY"
else
  echo "[boot +$(elapsed)ms] NOT READY (see boot.json); staying up for inspection"
  touch "$RUN/failed"
fi

sleep infinity &
wait $!
