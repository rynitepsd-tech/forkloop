#!/usr/bin/env bash
# Log Chrome into the portal (agent/agent) and OpenEMR (admin/pass) once, maximise the window,
# and leave the portal claims list open. Runs as the desktop session user (DISPLAY=:0) — Chrome
# refuses to run as root. Field positions are for the 1280x720 layout and were read off real
# screenshots (docs/spikes.md); if the layout changes, re-measure them.
set -uo pipefail
export DISPLAY="${DISPLAY:-:0}"
PORTAL=http://localhost:8080
OPENEMR="http://localhost/openemr/interface/login/login.php?site=default"
PROFILE="$HOME/.config/forkloop-chrome"

pkill -x chrome 2>/dev/null; pkill chrome 2>/dev/null || true
sleep 2
# Deterministic layout: fixed position/size, no first-run dialogs. Bubbles (save password,
# translate, sign-in) are disabled by the enterprise policy build.sh installs.
nohup google-chrome --no-first-run --no-default-browser-check --user-data-dir="$PROFILE" \
  --password-store=basic --window-position=0,0 --window-size=1280,720 --disable-session-crashed-bubble \
  --disable-infobars "$PORTAL/login" >"$HOME/chrome.log" 2>&1 &
sleep 9
WID=$(xdotool search --onlyvisible --class chrome | head -1 || true)
[[ -n "$WID" ]] && wmctrl -i -r "$WID" -b add,maximized_vert,maximized_horz || true
sleep 1

goto() {  # navigate by clicking the omnibox (keyboard focus is not guaranteed to be in Chrome)
  xdotool mousemove 640 90 click 1; sleep 0.3; xdotool key ctrl+a; xdotool type --delay 15 "$1"; xdotool key Return; sleep 3
}
click_type() { xdotool mousemove "$1" "$2" click 1; sleep 0.3; xdotool type --delay 25 "$3"; }

# portal login form: username (340,309), password (340,391), button (90,449)
goto "$PORTAL/login"
click_type 340 309 agent
click_type 340 391 agent
xdotool mousemove 90 449 click 1; sleep 3
# openemr login form: username (706,422), password (684,476), Login (640,585)
goto "$OPENEMR"
click_type 706 422 admin
click_type 684 476 pass
xdotool mousemove 640 585 click 1; sleep 6
# canonical initial screen
goto "$PORTAL/claims"
echo "browser setup finished: windows=$(xdotool search --onlyvisible --class chrome | wc -l)"
