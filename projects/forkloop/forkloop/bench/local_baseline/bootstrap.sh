#!/usr/bin/env bash
# One-time "golden build" for the local baseline: bring the stack up, create
# the portal schema + base data, and prepare an (empty) Chrome profile dir.
# Afterwards run ./snapshot.sh to capture state/, then ./restore.sh to time it.
set -euo pipefail
cd "$(dirname "$0")"
. ./_lib.sh

docker compose up -d --build
wait_healthy

if ! docker compose exec -T portal test -s /data/portal.db; then
  echo "initialising portal db"
  docker compose exec -T portal python -m portal.db init --db /data/portal.db
  docker compose exec -T portal python -m portal.db seed-base --db /data/portal.db
  docker compose restart portal
  wait_healthy
fi

mkdir -p run/chrome-profile
[ -f run/chrome-profile/README ] || cat > run/chrome-profile/README <<'EOF'
Put a logged-in Chrome profile here (admin/pass on OpenEMR, agent/agent on the
portal) to mirror the browser state that lives in the Solari golden snapshot:
  google-chrome --user-data-dir="$PWD" http://localhost:8080
snapshot.sh copies this directory into state/; restore.sh copies it back.
EOF

echo "bootstrap done. portal http://localhost:8080  openemr http://localhost:8300 (admin / pass)"
