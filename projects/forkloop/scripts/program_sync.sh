#!/bin/bash
# Durable copy of a running program (store + evidence) from local disk to a persistent location.
# SQLite stays on local disk (WAL does not work on NFS); it is copied with the online backup API.
#   usage: scripts/program_sync.sh <program_dir> <durable_dir> [interval_s]
#   e.g.   tmux new -d -s sync "scripts/program_sync.sh ~/programs/p1 /lambda/nfs/forkloop-useast1/programs/p1 300"
set -u
SRC=${1:?program dir}; DST=${2:?durable dir}; EVERY=${3:-300}
mkdir -p "$DST"
while true; do
  for db in "$SRC"/*.sqlite; do
    [ -f "$db" ] || continue
    python3 - "$db" "$DST/$(basename "$db")" <<'PY'
import sqlite3, sys, os
src, dst = sys.argv[1], sys.argv[2]
tmp = dst + ".tmp"
s = sqlite3.connect(src, timeout=60); d = sqlite3.connect(tmp)
s.backup(d); d.close(); s.close(); os.replace(tmp, dst)
PY
  done
  rsync -a --exclude '*.sqlite' --exclude '*.sqlite-wal' --exclude '*.sqlite-shm' "$SRC"/ "$DST"/
  date -u +%FT%TZ > "$DST/.synced_at"
  sleep "$EVERY"
done
