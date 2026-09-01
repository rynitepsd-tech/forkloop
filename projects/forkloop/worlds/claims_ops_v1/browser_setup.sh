#!/usr/bin/env bash
# Log Chrome into the portal (agent/agent) and OpenEMR (admin/pass) once, maximise the window,
# and leave the portal tab open. Runs as the desktop user inside the VM (X11 display :0).
set -uo pipefail
export DISPLAY="${DISPLAY:-:0}"
PORTAL=http://localhost:8080
OPENEMR="http://localhost/openemr/interface/login/login.php?site=default"

pkill -f "google-chrome" 2>/dev/null || true
sleep 1
# Deterministic layout: fixed position/size, no first-run dialogs, no translate/password bubbles.
nohup google-chrome --no-first-run --no-default-browser-check --disable-features=TranslateUI,PasswordManagerOnboarding \
  --password-store=basic --window-position=0,0 --window-size=1280,720 --disable-session-crashed-bubble \
  --disable-infobars "$PORTAL/login" >/tmp/chrome.log 2>&1 &
sleep 6
WID=$(xdotool search --onlyvisible --class "google-chrome" | head -1 || true)
[[ -n "$WID" ]] && wmctrl -i -r "$WID" -b add,maximized_vert,maximized_horz || true

type_login() {  # url, user, pass
  xdotool key ctrl+l; sleep 0.3; xdotool type --delay 20 "$1"; xdotool key Return; sleep 3
  xdotool type --delay 30 "$2"; xdotool key Tab; xdotool type --delay 30 "$3"; xdotool key Return; sleep 4
}
# portal: username field is auto-focused on /login
type_login "$PORTAL/login" agent agent
# openemr: the login form autofocuses the username input
type_login "$OPENEMR" admin pass
# back to the portal claims list — the canonical initial screen
xdotool key ctrl+l; sleep 0.3; xdotool type --delay 20 "$PORTAL/claims"; xdotool key Return; sleep 3
echo "browser setup finished (verify over VNC that both apps show a logged-in state)"
