#!/bin/sh
# /usr/bin/google-chrome in the claims-ops-v1 Docker image (world clock, docs/docker-world.md).
# Every process runs with libfaketime (LD_PRELOAD, image env). Chrome must get the build WITHOUT
# FAKE_PTHREAD: with it, headed Chrome segfaulted at start (2026-09-29). Chrome's own timed waits use the
# monotonic clock, which is never faked, so it does not need FAKE_PTHREAD; MariaDB does (its 1 s realtime
# waits otherwise return at once and InnoDB aborts). Same command line as a plain `google-chrome`.
# FAKETIME is set from /etc/faketimerc for Chrome only: its sandboxed renderers cannot re-read the file.
case "${LD_PRELOAD:-}" in
  *libfaketime*)
    LD_PRELOAD=/usr/local/lib/faketime/libfaketime-nopthread.so.1; export LD_PRELOAD
    if [ -r /etc/faketimerc ]; then FAKETIME="$(head -n 1 /etc/faketimerc)"; export FAKETIME; fi ;;
esac
exec /opt/google/chrome/google-chrome "$@"
