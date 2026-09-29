#!/usr/bin/env bash
# Golden bake, run INSIDE a booted base container by build_image.sh (docker exec, as root):
# the Solari build's last step (browser_setup.sh as the session user: Chrome logs into the portal as
# agent/agent and into OpenEMR as admin/pass, window maximised, portal claims list open), then a clean
# shutdown so the committed image holds a flushed Chrome profile (the portal's 30-day session cookie)
# and cleanly stopped databases.
set -euo pipefail
DUSER=desktop
as_desktop() { runuser -u "$DUSER" -- env DISPLAY=:0 HOME="/home/$DUSER" XDG_RUNTIME_DIR="/run/$DUSER" "$@"; }

for _ in $(seq 1 600); do [[ -f /run/forkloop/ready || -f /run/forkloop/failed ]] && break; sleep 0.1; done
[[ -f /run/forkloop/ready ]] || { echo "base container did not boot"; cat /run/forkloop/boot.json 2>/dev/null; exit 1; }

echo "[bake] browser_setup.sh (unchanged Solari script)"
as_desktop bash /opt/forkloop/worlds/claims_ops_v1/browser_setup.sh
sleep 2
title="$(as_desktop xdotool getactivewindow getwindowname || true)"
echo "[bake] window title after setup: $title"
# Leaving OpenEMR's main page raises its beforeunload "Leave site?" dialog, which swallows
# browser_setup.sh's last navigation (seen 2026-09-28 with Chrome stable). "Leave" has the focus:
# accept it and navigate the way browser_setup.sh does.
for attempt in 1 2 3; do
  [[ "$title" == "Claims - Meridian Provider Portal"* ]] && break
  echo "[bake] not on the claims list (attempt $attempt): accepting a pending 'Leave site?' and navigating"
  as_desktop xdotool key Return; sleep 2
  as_desktop xdotool mousemove 640 90 click 1; sleep 0.3
  as_desktop xdotool key ctrl+a; as_desktop xdotool type --delay 15 "http://localhost:8080/claims"
  as_desktop xdotool key Return; sleep 3
  title="$(as_desktop xdotool getactivewindow getwindowname || true)"
  echo "[bake] window title: $title"
done
[[ "$title" == "Claims - Meridian Provider Portal"* ]] || { echo "[bake] portal claims list not open"; exit 1; }

echo "[bake] checking the OpenEMR login landed (log table)"
mysql -N openemr -e "SELECT COUNT(*) FROM log WHERE event LIKE 'login%' AND user = 'admin' AND success = 1" || true

echo "[bake] closing Chrome cleanly (flushes cookies and window placement to the profile)"
# A window-manager close is a normal quit. SIGTERM is a "SessionEnded" fast exit that left the
# cookie store unflushed (measured 2026-09-28: empty Network/Cookies after pkill -TERM).
for wid in $(as_desktop xdotool search --onlyvisible --class chrome 2>/dev/null); do
  as_desktop wmctrl -i -c "$wid" || true
done
for _ in $(seq 1 150); do pgrep -x chrome >/dev/null || break; sleep 0.1; done
pgrep -x chrome >/dev/null && { echo "[bake] window close did not quit Chrome; sending TERM"; pkill -TERM -x chrome || true; }
for _ in $(seq 1 100); do pgrep -x chrome >/dev/null || break; sleep 0.1; done
pgrep -x chrome >/dev/null && { echo "[bake] Chrome did not exit"; exit 1; }
python3 - <<'EOF'
import json, sqlite3
p = "/home/desktop/.config/forkloop-chrome/Default"
prefs = json.load(open(f"{p}/Preferences"))
print("[bake] exit_type:", prefs.get("profile", {}).get("exit_type"),
      "window maximized:", prefs.get("browser", {}).get("window_placement", {}).get("maximized"))
import os
db = f"{p}/Network/Cookies" if os.path.exists(f"{p}/Network/Cookies") else f"{p}/Cookies"  # Chrome >= 96: Network/
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = con.execute("SELECT host_key, name, expires_utc > 0 FROM cookies ORDER BY host_key, name").fetchall()
print("[bake] cookies:", rows)
assert any(r[1] == "portal_session" and r[2] for r in rows), "persistent portal_session cookie missing"
EOF

echo "[bake] stopping services cleanly"
/usr/local/forkloop/svc.sh stop-all
rm -rf /home/desktop/.cache/google-chrome /home/desktop/.config/forkloop-chrome/Default/Cache \
       "/home/desktop/.config/forkloop-chrome/Default/Code Cache" /home/desktop/.cache/sessions
: > /home/desktop/chrome.log
echo "[bake] done"
