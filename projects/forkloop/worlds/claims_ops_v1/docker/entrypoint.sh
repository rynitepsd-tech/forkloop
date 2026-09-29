#!/usr/bin/env bash
# PID-1 script (under tini) of the claims-ops-v1 Docker image: bring the world up the way the Solari
# golden snapshot holds it, then write /run/forkloop/ready.
#
#   services  MariaDB, php-fpm, Apache (OpenEMR on :80), the portal unit on :8080   (svc.sh)
#   desktop   Xvfb :0 1280x720x24, a session D-Bus at /run/desktop/bus, XFCE as user `desktop`
#   browser   Google Chrome with exactly browser_setup.sh's flags and profile, on the portal claims
#             list, 1280x720 under the top panel (omnibox at ~(640, 90)), as on Solari episode screens
#
# The container filesystem is the world: an image made by `docker commit` of a running container
# (DockerMachine.snapshot) boots through this same script, so stale pid files, X locks and Chrome's
# singleton files left in a committed image are removed first. Boot timings go to
# /run/forkloop/boot.json; the log is /var/log/forkloop-boot.log (and `docker logs`).
set -uo pipefail

RUN=/run/forkloop
SCREEN="${FORKLOOP_SCREEN:-1280x720}"
DUSER=desktop
DHOME=/home/$DUSER
DRUN=/run/$DUSER
PROFILE=$DHOME/.config/forkloop-chrome
PORTAL=http://localhost:8080
OPENEMR_HEALTH="http://localhost/openemr/interface/login/login.php?site=default"
SVC=/usr/local/forkloop/svc.sh

mkdir -p "$RUN"
exec > >(tee /var/log/forkloop-boot.log) 2>&1   # this boot only (an image may carry an older log)

# --- world clock. Tasks are generated relative to ANCHOR = 2026-09-07 (tasks/common.py); on Solari the VM
# clock comes back from the snapshot. Here every process (MariaDB NOW(), PHP, the portal, the XFCE clock,
# Chrome's Date) sees one clock that starts at FORKLOOP_WORLD_CLOCK when the container boots and advances
# in real time: libfaketime is preloaded into every process (LD_PRELOAD in the image env; Chrome gets a
# variant through /usr/bin/google-chrome, see chrome-wrapper.sh) and reads ONE relative offset from
# /etc/faketimerc, computed here before anything else starts. The offset is deliberately NOT exported as
# FAKETIME: a process that starts with FAKETIME set and later clears its environment (php-fpm's clear_env)
# falls back to the REAL clock once libfaketime's 10 s cache expires (measured 2026-09-29), while one that
# never had it keeps re-reading the file. Only Chrome gets FAKETIME (its sandboxed renderers cannot read
# files, and it never clears its environment). The monotonic clock stays real
# (FAKETIME_DONT_FAKE_MONOTONIC=1 in the image env). FORKLOOP_WORLD_CLOCK=real disables it.
rm -f /etc/faketimerc   # a committed checkpoint carries the offset of its own boot
unset FAKETIME
WORLD_CLOCK="${FORKLOOP_WORLD_CLOCK:-2026-09-07T09:00:00Z}"
if [[ "$WORLD_CLOCK" != real && "${LD_PRELOAD:-}" == *libfaketime* ]]; then
  target=$(date -u -d "$WORLD_CLOCK" +%s) || { echo "[boot] bad FORKLOOP_WORLD_CLOCK=$WORLD_CLOCK"; exit 2; }
  offset=$(( target - $(date -u +%s) ))
  printf '%+ds\n' "$offset" > /etc/faketimerc.tmp && chmod 644 /etc/faketimerc.tmp && mv /etc/faketimerc.tmp /etc/faketimerc
  export FAKETIME_DONT_FAKE_MONOTONIC=1
  echo "[boot] world clock $WORLD_CLOCK (/etc/faketimerc $(cat /etc/faketimerc)); now $(date -u +%FT%TZ)"
fi
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
  "$SVC" stop-all
  pkill -TERM -u "$DUSER" -x xfce4-panel 2>/dev/null; pkill -TERM -u "$DUSER" -x xfwm4 2>/dev/null
  pkill -TERM -x Xvfb 2>/dev/null
  exit 0
}
trap shutdown TERM INT

# --- 0. state a `docker commit` may have captured from a running container
rm -f "$RUN/ready" "$RUN/failed" "$RUN/boot.json" "$RUN"/*.pid /tmp/.X0-lock /tmp/.X11-unix/X0
rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie"
rm -rf "$DRUN" "$DHOME/.cache/sessions"
install -d -o "$DUSER" -g "$DUSER" -m 700 "$DRUN"
# Chrome's log belongs to the session user (browser_setup.sh truncates it as `desktop`); a redirect by
# root below or in ensure_chrome_gpu_flag keeps an existing file's owner.
[[ -f "$DHOME/chrome.log" ]] || install -o "$DUSER" -g "$DUSER" -m 644 /dev/null "$DHOME/chrome.log"
chown "$DUSER:$DUSER" "$DHOME/chrome.log"
mark cleaned

# --- 1. services, in the background (MariaDB is the slow one)
( "$SVC" start mariadb && echo "[boot +$(elapsed)ms] mariadb up" ) &
PID_DB=$!
( "$SVC" start php-fpm && "$SVC" start apache2 && echo "[boot +$(elapsed)ms] apache+php up" ) &
PID_WEB=$!
( "$SVC" start portal && echo "[boot +$(elapsed)ms] portal started" ) &
PID_PORTAL=$!
# OpenEMR's first request compiles PHP (~1 s); take it now, in parallel with the desktop.
( for _ in $(seq 1 300); do
    [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$OPENEMR_HEALTH" || true)" == 200 ]] && break
    sleep 0.1
  done; echo "[boot +$(elapsed)ms] openemr answering" ) &
PID_EMR=$!

# A profile captured from a running Chrome (docker commit of a live world) says "not exited cleanly"
# and Chrome would open with a "Restore pages?" bubble. Mark it the way a clean quit leaves it, so every
# boot (golden or checkpoint) shows the same screen.
python3 - "$PROFILE" <<'PYEOF' || echo "[boot] could not normalise the Chrome profile exit state"
import json, os, sys
for rel, keys in (("Default/Preferences", (("profile", "exit_type", "Normal"), ("profile", "exited_cleanly", True))),
                  ("Local State", (("user_experience_metrics", "stability", None),))):
    path = os.path.join(sys.argv[1], rel)
    if not os.path.exists(path):
        continue
    data = json.load(open(path))
    changed = False
    for a, b, v in keys:
        if v is None:
            continue
        if data.setdefault(a, {}).get(b) != v:
            data[a][b] = v
            changed = True
    if changed:
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
# The XFCE session's components, started directly and in xfce4-session's own failsafe order
# (xfce/xfconf/.../xfce4-session.xml), each waited for: settings daemon (owns the XSETTINGS selection, so
# the panel draws with the Yaru theme), window manager, panel (reserves the top strut), desktop. Started
# through xfce4-session, 2 of ~100 boots on 2026-09-29 stalled ~25 s (a D-Bus call timing out) before any
# client started; NO_AT_BRIDGE=1 also keeps GTK from waiting on the accessibility bus.
XENV=(env DBUS_SESSION_BUS_ADDRESS="unix:path=$DRUN/bus" NO_AT_BRIDGE=1)
selection_owned() {  # X selection has an owner (e.g. _XSETTINGS_S0)
  as_desktop python3 -c "
import ctypes, sys
x = ctypes.CDLL('libX11.so.6'); x.XOpenDisplay.restype = ctypes.c_void_p
d = x.XOpenDisplay(None)
x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]; x.XInternAtom.restype = ctypes.c_ulong
x.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]; x.XGetSelectionOwner.restype = ctypes.c_ulong
sys.exit(0 if d and x.XGetSelectionOwner(d, x.XInternAtom(d, sys.argv[1].encode(), 0)) else 1)" "$1" 2>/dev/null
}
wm_ready() { as_desktop xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q "window id"; }
workarea_ready() {
  wa="$(as_desktop xprop -root _NET_WORKAREA 2>/dev/null || true)"
  [[ "$wa" =~ =\ 0,\ [1-9][0-9]*, ]]
}
desktop_ready() { as_desktop xdotool search --classname xfdesktop >/dev/null 2>&1; }
wait_for() {  # wait_for <tenths of a second> <check command...>
  local n="$1"; shift
  for _ in $(seq 1 "$n"); do "$@" && return 0; sleep 0.1; done; return 1
}
start_xfce() {
  as_desktop "${XENV[@]}" setsid xfsettingsd >>"$DHOME/.xfce4-session.log" 2>&1 &
  wait_for 50 selection_owned _XSETTINGS_S0 || echo "[boot] xfsettingsd did not take _XSETTINGS_S0"
  as_desktop "${XENV[@]}" setsid xfwm4 >>"$DHOME/.xfce4-session.log" 2>&1 &
  wait_for 50 wm_ready || echo "[boot] xfwm4 not managing"
  as_desktop "${XENV[@]}" setsid xfce4-panel >>"$DHOME/.xfce4-session.log" 2>&1 &
  wait_for 80 workarea_ready || echo "[boot] panel strut missing"
  as_desktop "${XENV[@]}" setsid xfdesktop >>"$DHOME/.xfce4-session.log" 2>&1 &
  wait_for 50 desktop_ready || echo "[boot] xfdesktop window missing"
}
start_xfce
if ! workarea_ready; then
  echo "[boot] desktop not up; restarting its components once"
  pkill -u "$DUSER" -x xfwm4; pkill -u "$DUSER" -x xfce4-panel; pkill -u "$DUSER" -x xfsettingsd; pkill -u "$DUSER" -x xfdesktop
  sleep 0.3; start_xfce
fi
mark xfce
echo "[boot] workarea: ${wa:-none}"

# --- 3. Chrome, once the portal answers (so the first load is the page, not an error)
for _ in $(seq 1 300); do curl -fsS -o /dev/null "$PORTAL/healthz" 2>/dev/null && break; sleep 0.05; done
mark portal_http
# Flags: identical to worlds/claims_ops_v1/browser_setup.sh and ClaimsOpsWorld.chrome_base_flags
# (tests/test_docker_backend.py checks that they stay identical).
as_desktop setsid -f google-chrome --no-first-run --no-default-browser-check --user-data-dir="$PROFILE" \
  --password-store=basic --window-position=0,0 --window-size=1280,720 --disable-session-crashed-bubble \
  --disk-cache-size=1 --media-cache-size=1 --disable-infobars --disable-gpu --disable-dev-shm-usage \
  --enable-logging=stderr --v=0 "$PORTAL/claims" >"$DHOME/chrome.log" 2>&1
WID=""
for _ in $(seq 1 400); do
  WID="$(as_desktop xdotool search --onlyvisible --class chrome 2>/dev/null | head -1)"
  [[ -n "$WID" ]] && break
  sleep 0.05
done
mark chrome_window
if [[ -n "$WID" ]]; then
  # Default: leave the window as those flags place it (1280x720 at the top-left of the work area, i.e.
  # just under the panel), which is what every Solari episode screen shows: the Sept-15 golden relaunches
  # Chrome with these flags on each reset (ClaimsOpsWorld.ensure_chrome_gpu_flag) and nothing maximises
  # it. FORKLOOP_CHROME_MAXIMIZE=1 gives browser_setup.sh's maximised window instead.
  if [[ "${FORKLOOP_CHROME_MAXIMIZE:-0}" == 1 ]]; then
    as_desktop wmctrl -i -r "$WID" -b add,maximized_vert,maximized_horz
  fi
  as_desktop xdotool windowactivate "$WID" 2>/dev/null || true
fi
TITLE=""
for _ in $(seq 1 400); do
  TITLE="$(as_desktop xdotool getactivewindow getwindowname 2>/dev/null || true)"
  [[ "$TITLE" == *"Meridian Provider Portal"* ]] && break
  sleep 0.05
done
mark chrome_page
echo "[boot] window title: $TITLE"

# --- 4. every service answering
wait "$PID_DB" "$PID_WEB" "$PID_PORTAL" "$PID_EMR"
for _ in $(seq 1 300); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$OPENEMR_HEALTH" || true)"
  [[ "$code" == 200 ]] && break
  sleep 0.1
done
mark services
ok=true
[[ -n "$WID" && "$TITLE" == *"Meridian Provider Portal"* && "$code" == 200 ]] || ok=false
printf '{"ok": %s, "title": "%s", "openemr_http": "%s", "world_clock": "%s", "faketime": "%s", "stages_ms": {' \
  "$ok" "${TITLE//\"/}" "$code" "$WORLD_CLOCK" "$(cat /etc/faketimerc 2>/dev/null)" > "$RUN/boot.json"
first=1
for k in cleaned xvfb xfce portal_http chrome_window chrome_page services; do
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

# --- 5. stay up as PID 1's child; `docker stop` sends TERM -> shutdown()
sleep infinity &
wait $!
