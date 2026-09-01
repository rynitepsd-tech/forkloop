#!/usr/bin/env bash
# Restore state/ into the running stack — the local analogue of
# Desktop.revert(). Timed from "docker compose down" to both HTTP health
# endpoints returning 200 and the DB answering SELECT 1. Appends one JSON line
# to results.jsonl.
set -euo pipefail
cd "$(dirname "$0")"
. ./_lib.sh
[ -f state/mariadb-all.sql.gz ] || { echo "no state/ — run ./snapshot.sh first" >&2; exit 2; }

t0=$(now)

docker compose down --timeout 10 >/dev/null 2>&1
docker compose up -d mysql >/dev/null 2>&1
wait_db
gunzip -c state/mariadb-all.sql.gz | docker compose exec -T mysql mariadb -uroot -proot

docker compose up -d portal openemr >/dev/null 2>&1
# Replace, don't merge: `docker cp` of a directory onto an existing directory
# copies it *inside*; rm first and copy the contents (trailing "/.").
docker compose exec -T portal sh -c 'rm -rf /data/uploads /data/portal.db && mkdir -p /data/uploads'
docker compose cp state/portal.db portal:/data/portal.db
docker compose cp state/uploads/. portal:/data/uploads
docker compose exec -T openemr sh -c "rm -rf '$OPENEMR_DOCS' && mkdir -p '$OPENEMR_DOCS'"
docker compose cp state/openemr-documents/. "openemr:$OPENEMR_DOCS"
rm -rf run/chrome-profile && cp -a state/chrome-profile run/chrome-profile

# uvicorn holds the old sqlite handle; OpenEMR serves documents from disk and needs no restart.
docker compose restart portal >/dev/null 2>&1
wait_healthy

python3 - <<EOF
import json, time
elapsed = time.time() - $t0
print(f"restore: {elapsed:.1f}s")
with open("results.jsonl", "a") as f:
    f.write(json.dumps({"spike": "local_baseline", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "metric": "restore_wall", "value": round(elapsed, 3), "unit": "s",
                        "notes": "docker compose down -> mariadb load -> files -> both HTTP 200 + SELECT 1"}) + "\n")
EOF
