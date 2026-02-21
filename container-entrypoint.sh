#!/bin/sh
set -eu

MONITOR_FILE="${MONITOR_FILE:-/data/monitor.json}"
CAMPGROUNDS_FILE="${CAMPGROUNDS_FILE:-/data/campgrounds.json}"
AUTO_REFRESH_CAMPGROUNDS="${AUTO_REFRESH_CAMPGROUNDS:-1}"
REFRESH_AT_STARTUP="${REFRESH_AT_STARTUP:-0}"
REFRESH_SKIP_VALIDATION="${REFRESH_SKIP_VALIDATION:-0}"

refresh_pid=""
monitor_pid=""

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

refresh_once() {
  if [ -z "${RIDB_API_KEY:-}" ]; then
    log "[refresh] RIDB_API_KEY not set; skipping campground refresh."
    return 0
  fi

  log "[refresh] Updating ${CAMPGROUNDS_FILE}..."
  if [ "$REFRESH_SKIP_VALIDATION" = "1" ]; then
    if ! python scripts/export_campgrounds.py --output "$CAMPGROUNDS_FILE" --skip-validation; then
      log "[refresh] Refresh failed."
      return 1
    fi
  else
    if ! python scripts/export_campgrounds.py --output "$CAMPGROUNDS_FILE"; then
      log "[refresh] Refresh failed."
      return 1
    fi
  fi
  log "[refresh] Refresh complete."
}

seconds_until_next_midnight() {
  python - <<'PY'
from datetime import datetime, timedelta
now = datetime.now()
next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
print(max(1, int((next_midnight - now).total_seconds())))
PY
}

refresh_loop() {
  while true; do
    wait_seconds="$(seconds_until_next_midnight)"
    log "[refresh] Sleeping ${wait_seconds}s until next midnight."
    sleep "$wait_seconds"
    refresh_once || true
  done
}

cleanup() {
  if [ -n "$monitor_pid" ]; then
    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
  fi
  if [ -n "$refresh_pid" ]; then
    kill "$refresh_pid" 2>/dev/null || true
    wait "$refresh_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM

if [ "$AUTO_REFRESH_CAMPGROUNDS" = "1" ]; then
  if [ "$REFRESH_AT_STARTUP" = "1" ] || [ ! -s "$CAMPGROUNDS_FILE" ]; then
    refresh_once || true
  fi
  refresh_loop &
  refresh_pid="$!"
else
  log "[refresh] Daily campground refresh disabled."
fi

if [ "$#" -eq 0 ]; then
  set -- --config "$MONITOR_FILE" --campgrounds-file "$CAMPGROUNDS_FILE"
fi

recgov-monitor "$@" &
monitor_pid="$!"
wait "$monitor_pid"
status="$?"

if [ -n "$refresh_pid" ]; then
  kill "$refresh_pid" 2>/dev/null || true
  wait "$refresh_pid" 2>/dev/null || true
fi

exit "$status"
