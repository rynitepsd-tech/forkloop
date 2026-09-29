#!/usr/bin/env bash
# Golden bake of kanboard-v1, run INSIDE a booted base container by build_image.sh (docker exec, as root):
# log Chrome into Kanboard as the world's synthetic account through the login form (keyboard only: the
# username field has autofocus), check the dashboard, then quit Chrome cleanly so the committed image
# holds a flushed profile with the persistent session cookie (config.php SESSION_DURATION) next to the
# matching `sessions` row in the database.
set -euo pipefail
DUSER=desktop
as_desktop() { runuser -u "$DUSER" -- env DISPLAY=:0 HOME="/home/$DUSER" XDG_RUNTIME_DIR="/run/$DUSER" "$@"; }
LOGIN_USER="${1:?username}"
LOGIN_PASS="${2:?password}"

for _ in $(seq 1 600); do [[ -f /run/forkloop/ready || -f /run/forkloop/failed ]] && break; sleep 0.1; done
[[ -f /run/forkloop/ready ]] || { echo "[bake] base container did not boot"; cat /run/forkloop/boot.json 2>/dev/null; exit 1; }

title() { as_desktop xdotool getactivewindow getwindowname 2>/dev/null || true; }
goto() {
  as_desktop xdotool mousemove 640 90 click 1; sleep 0.3
  as_desktop xdotool key ctrl+a; as_desktop xdotool type --delay 12 "$1"
  as_desktop xdotool key Return; sleep 3
}

echo "[bake] window title at boot: $(title)"
goto "http://localhost/?controller=AuthController&action=login"
echo "[bake] login page title: $(title)"
# username (autofocus) -> Tab -> password -> Return submits ("Remember Me" is checked by default)
as_desktop xdotool type --delay 12 "$LOGIN_USER"; as_desktop xdotool key Tab
as_desktop xdotool type --delay 12 "$LOGIN_PASS"; as_desktop xdotool key Return
sleep 4
t="$(title)"
echo "[bake] after login: $t"
n_sessions="$(sqlite3 /var/www/app/data/db.sqlite "SELECT COUNT(*) FROM sessions WHERE data LIKE '%${LOGIN_USER}%'")"
echo "[bake] sessions for ${LOGIN_USER}: $n_sessions"
[[ "$n_sessions" -ge 1 ]] || { echo "[bake] login did not create a session"; exit 1; }
goto "http://localhost/"
echo "[bake] dashboard: $(title)"

echo "[bake] closing Chrome cleanly (flushes the cookie store to the profile)"
for wid in $(as_desktop xdotool search --onlyvisible --class chrome 2>/dev/null); do
  as_desktop wmctrl -i -c "$wid" || true
done
for _ in $(seq 1 150); do pgrep -x chrome >/dev/null || break; sleep 0.1; done
pgrep -x chrome >/dev/null && { echo "[bake] window close did not quit Chrome; sending TERM"; pkill -TERM -x chrome || true; }
for _ in $(seq 1 100); do pgrep -x chrome >/dev/null || break; sleep 0.1; done
pgrep -x chrome >/dev/null && { echo "[bake] Chrome did not exit"; exit 1; }
python3 - <<'EOF'
import os, sqlite3
p = "/home/desktop/.config/forkloop-chrome/Default"
db = f"{p}/Network/Cookies" if os.path.exists(f"{p}/Network/Cookies") else f"{p}/Cookies"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = con.execute("SELECT host_key, name, expires_utc > 0, is_persistent FROM cookies ORDER BY host_key, name").fetchall()
print("[bake] cookies:", rows)
assert any(r[1] == "KB_SID" and r[3] for r in rows), "no persistent Kanboard session cookie"
EOF
# The remember-me row and session rows written during the bake are part of the golden state.
sqlite3 /var/www/app/data/db.sqlite "SELECT 'sessions', COUNT(*) FROM sessions; SELECT 'remember_me', COUNT(*) FROM remember_me;"
sync
echo "[bake] done"
