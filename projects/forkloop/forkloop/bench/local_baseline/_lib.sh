# Shared shell helpers for the local baseline scripts (sourced, not run).

OPENEMR_DOCS=/var/www/localhost/htdocs/openemr/sites/default/documents
PORTAL_URL=${PORTAL_URL:-http://localhost:8080/healthz}
OPENEMR_URL=${OPENEMR_URL:-"http://localhost:8300/interface/login/login.php?site=default"}

now() { python3 -c 'import time; print(time.time())'; }

wait_db() {
  local i
  for i in $(seq 1 120); do
    docker compose exec -T mysql mariadb -uroot -proot -N -e 'SELECT 1' >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "mysql never answered SELECT 1" >&2; return 1
}

wait_http() {
  local url=$1 i
  for i in $(seq 1 240); do
    curl -sf -o /dev/null "$url" && return 0
    sleep 0.5
  done
  echo "no 200 from $url" >&2; return 1
}

# Both HTTP health endpoints 200 and the DB answering SELECT 1 — the same
# readiness gate forkloop's reset() uses on Solari (contracts.md §11 step 3).
wait_healthy() {
  wait_db
  wait_http "$PORTAL_URL"
  wait_http "$OPENEMR_URL"
}
