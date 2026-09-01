#!/usr/bin/env bash
# Capture the local "golden" state into state/ — the local analogue of
# Desktop.snapshot(). Dumps MariaDB, copies the portal SQLite DB + uploads,
# the OpenEMR documents tree and the Chrome profile directory.
set -euo pipefail
cd "$(dirname "$0")"
. ./_lib.sh

rm -rf state && mkdir -p state
t0=$(now)

docker compose exec -T mysql mariadb-dump -uroot -proot --single-transaction --routines --all-databases \
  | gzip -1 > state/mariadb-all.sql.gz

docker compose cp portal:/data/portal.db state/portal.db
docker compose cp portal:/data/uploads state/uploads 2>/dev/null || mkdir -p state/uploads
docker compose cp "openemr:$OPENEMR_DOCS" state/openemr-documents 2>/dev/null || mkdir -p state/openemr-documents
mkdir -p run/chrome-profile
cp -a run/chrome-profile state/chrome-profile

python3 - <<EOF
import time
print(f"snapshot captured in {time.time() - $t0:.1f}s")
EOF
du -sh state
