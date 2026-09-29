#!/usr/bin/env bash
# Minimal service manager for the claims-ops-v1 Docker image (there is no systemd in a container).
#
#   svc.sh start|stop|restart|status <mariadb|php-fpm|apache2|portal>
#   svc.sh start-all | stop-all
#
# Used by the entrypoint at boot, by the systemctl shim while build.sh/install.sh run inside
# `docker build`, and by DockerMachine.snapshot (stop-commit-start mode). Every stop is clean
# (mariadb-admin shutdown, php-fpm QUIT, apache2ctl stop) so a committed image never needs recovery.
set -uo pipefail

PHP_VERSION="${PHP_VERSION:-8.3}"
RUN=/run/forkloop
PORTAL_UNIT=/etc/systemd/system/forkloop-portal.service
mkdir -p "$RUN"

log() { printf '[svc %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

wait_gone() {  # pid, tenths of a second
  local pid="$1" n="${2:-300}"
  for _ in $(seq 1 "$n"); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.1; done
  return 1
}

# ------------------------------------------------------------------ mariadb
mariadb_up() { mariadb-admin ping --silent >/dev/null 2>&1; }
start_mariadb() {
  mariadb_up && return 0
  install -d -o mysql -g mysql -m 755 /run/mysqld
  rm -f /run/mysqld/mysqld.pid /run/mysqld/mysqld.sock
  setsid /usr/sbin/mariadbd --user=mysql </dev/null >>/var/log/mysql/forkloop-mariadbd.log 2>&1 &
  echo $! > "$RUN/mariadb.pid"
  for _ in $(seq 1 600); do mariadb_up && return 0; sleep 0.1; done
  log "mariadb did not answer within 60 s"; tail -n 20 /var/log/mysql/forkloop-mariadbd.log >&2; return 1
}
stop_mariadb() {
  local pid; pid="$(cat /run/mysqld/mysqld.pid 2>/dev/null || cat "$RUN/mariadb.pid" 2>/dev/null || true)"
  mariadb_up && mariadb-admin shutdown >/dev/null 2>&1
  [[ -n "$pid" ]] && { wait_gone "$pid" 600 || { log "mariadbd $pid still running; killing"; kill -9 "$pid" 2>/dev/null; }; }
  rm -f "$RUN/mariadb.pid"; return 0
}

# ------------------------------------------------------------------ php-fpm
PHP_FPM_PID=/run/php/php${PHP_VERSION}-fpm.pid
php_up() { [[ -s "$PHP_FPM_PID" ]] && kill -0 "$(cat "$PHP_FPM_PID")" 2>/dev/null; }
start_php() {
  php_up && return 0
  install -d -m 755 /run/php
  rm -f "$PHP_FPM_PID"
  "/usr/sbin/php-fpm${PHP_VERSION}" --daemonize --pid "$PHP_FPM_PID" --fpm-config "/etc/php/${PHP_VERSION}/fpm/php-fpm.conf"
  for _ in $(seq 1 100); do [[ -S "/run/php/php${PHP_VERSION}-fpm.sock" ]] && php_up && return 0; sleep 0.1; done
  log "php-fpm did not start"; return 1
}
stop_php() {
  php_up || return 0
  local pid; pid="$(cat "$PHP_FPM_PID")"
  kill -QUIT "$pid" 2>/dev/null; wait_gone "$pid" 100 || kill -9 "$pid" 2>/dev/null
  rm -f "$PHP_FPM_PID"; return 0
}

# ------------------------------------------------------------------ apache2
APACHE_PID=/var/run/apache2/apache2.pid
apache_up() { [[ -s "$APACHE_PID" ]] && kill -0 "$(cat "$APACHE_PID")" 2>/dev/null; }
start_apache() {
  apache_up && return 0
  rm -f "$APACHE_PID"
  apache2ctl start || return 1
  for _ in $(seq 1 100); do apache_up && return 0; sleep 0.1; done
  log "apache2 did not start"; return 1
}
stop_apache() {
  apache_up || return 0
  local pid; pid="$(cat "$APACHE_PID")"
  apache2ctl stop >/dev/null 2>&1; wait_gone "$pid" 100 || kill -9 "$pid" 2>/dev/null
  return 0
}

# ------------------------------------------------------------------ portal (the systemd unit from world.py)
portal_up() { [[ -s "$RUN/portal.pid" ]] && kill -0 "$(cat "$RUN/portal.pid")" 2>/dev/null; }
start_portal() {
  portal_up && return 0
  [[ -f "$PORTAL_UNIT" ]] || { log "missing $PORTAL_UNIT"; return 1; }
  # Honour the unit's Environment=, WorkingDirectory= and ExecStart=; Restart=always is the loop.
  local envs=() wd=/ exec_start=""
  while IFS= read -r line; do
    case "$line" in
      Environment=*) envs+=("${line#Environment=}") ;;
      WorkingDirectory=*) wd="${line#WorkingDirectory=}" ;;
      ExecStart=*) exec_start="${line#ExecStart=}" ;;
    esac
  done < "$PORTAL_UNIT"
  [[ -n "$exec_start" ]] || { log "no ExecStart in $PORTAL_UNIT"; return 1; }
  ( cd "$wd" && exec setsid env "${envs[@]}" bash -c "while true; do $exec_start; sleep 1; done" \
      </dev/null >>/var/log/forkloop-portal.log 2>&1 ) &
  echo $! > "$RUN/portal.pid"
  return 0
}
stop_portal() {
  portal_up || { rm -f "$RUN/portal.pid"; return 0; }
  local pid; pid="$(cat "$RUN/portal.pid")"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  wait_gone "$pid" 100 || kill -9 -- "-$pid" 2>/dev/null
  rm -f "$RUN/portal.pid"; return 0
}

# ------------------------------------------------------------------ dispatch
canon() {
  case "${1%.service}" in
    mariadb|mysql|mysqld) echo mariadb ;;
    php-fpm|php*-fpm) echo php ;;
    apache2|apache|httpd) echo apache ;;
    forkloop-portal|portal) echo portal ;;
    *) echo "" ;;
  esac
}

verb="${1:-}"; shift || true
case "$verb" in
  start-all)
    rc=0
    start_mariadb || rc=1; start_php || rc=1; start_apache || rc=1; start_portal || rc=1
    exit $rc ;;
  stop-all)
    stop_portal; stop_apache; stop_php; stop_mariadb; exit 0 ;;
  start|stop|restart|status)
    rc=0
    for unit in "$@"; do
      s="$(canon "$unit")"
      if [[ -z "$s" ]]; then log "unknown service $unit"; rc=1; continue; fi
      case "$verb" in
        start) "start_$s" || rc=1 ;;
        stop) "stop_$s" || rc=1 ;;
        restart) "stop_$s"; "start_$s" || rc=1 ;;
        status) "${s}_up" && echo "$s: active" || { echo "$s: inactive"; rc=3; } ;;
      esac
    done
    exit $rc ;;
  *) echo "usage: svc.sh start|stop|restart|status <service...> | start-all | stop-all" >&2; exit 2 ;;
esac
