#!/bin/sh
set -eu

MONITOR_FILE="${MONITOR_FILE:-/data/monitor.json}"
CAMPGROUNDS_FILE="${CAMPGROUNDS_FILE:-/data/campgrounds.json}"

AUTO_REFRESH_CAMPGROUNDS="${AUTO_REFRESH_CAMPGROUNDS:-1}"
REFRESH_AT_STARTUP="${REFRESH_AT_STARTUP:-0}"
REFRESH_SKIP_VALIDATION="${REFRESH_SKIP_VALIDATION:-0}"

MONITOR_SYNC_URL="${MONITOR_SYNC_URL:-}"
MONITOR_SYNC_INTERVAL_SECONDS="${MONITOR_SYNC_INTERVAL_SECONDS:-300}"
MONITOR_SYNC_AT_STARTUP="${MONITOR_SYNC_AT_STARTUP:-1}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

refresh_pid=""
monitor_pid=""
sync_pid=""
restart_requested="0"

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

request_monitor_restart() {
  restart_requested="1"
  if [ -n "$monitor_pid" ]; then
    kill "$monitor_pid" 2>/dev/null || true
  fi
}

validate_monitor_json_file() {
  python - "$1" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit(1)
monitors = payload.get("monitors")
if not isinstance(monitors, list) or not monitors:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

download_monitor_to_temp() {
  url="$1"
  out_path="$2"
  python - "$url" "$GITHUB_TOKEN" "$out_path" <<'PY'
import pathlib
import sys
from urllib import request

url = sys.argv[1]
token = sys.argv[2]
out_path = pathlib.Path(sys.argv[3])
headers = {"User-Agent": "recgov-monitor-container/1.0"}
if token:
    headers["Authorization"] = f"Bearer {token}"
req = request.Request(url, headers=headers, method="GET")
with request.urlopen(req, timeout=30) as response:
    data = response.read()
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_bytes(data)
PY
}

sync_monitor_once() {
  if [ -z "$MONITOR_SYNC_URL" ]; then
    return 0
  fi
  tmp_file="${MONITOR_FILE}.download"
  if ! download_monitor_to_temp "$MONITOR_SYNC_URL" "$tmp_file"; then
    log "[sync] Failed to download monitor config from MONITOR_SYNC_URL."
    rm -f "$tmp_file" || true
    return 1
  fi
  if ! validate_monitor_json_file "$tmp_file"; then
    log "[sync] Downloaded monitor config is invalid JSON monitor payload; ignoring."
    rm -f "$tmp_file" || true
    return 1
  fi

  if [ ! -f "$MONITOR_FILE" ] || ! cmp -s "$tmp_file" "$MONITOR_FILE"; then
    mv "$tmp_file" "$MONITOR_FILE"
    log "[sync] Applied updated monitor config from MONITOR_SYNC_URL."
    request_monitor_restart
    return 0
  fi
  rm -f "$tmp_file" || true
  return 0
}

sync_loop() {
  while true; do
    sleep "$MONITOR_SYNC_INTERVAL_SECONDS"
    sync_monitor_once || true
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
  if [ -n "$sync_pid" ]; then
    kill "$sync_pid" 2>/dev/null || true
    wait "$sync_pid" 2>/dev/null || true
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

if [ -n "$MONITOR_SYNC_URL" ]; then
  if [ "$MONITOR_SYNC_AT_STARTUP" = "1" ]; then
    sync_monitor_once || true
  fi
  sync_loop &
  sync_pid="$!"
  log "[sync] Started monitor sync loop every ${MONITOR_SYNC_INTERVAL_SECONDS}s."
fi

if [ "$#" -eq 0 ]; then
  set -- --config "$MONITOR_FILE" --campgrounds-file "$CAMPGROUNDS_FILE"
fi

while true; do
  restart_requested="0"
  recgov-monitor "$@" &
  monitor_pid="$!"
  monitor_status="0"
  wait "$monitor_pid" || monitor_status="$?"
  monitor_pid=""

  if [ "$restart_requested" = "1" ]; then
    log "[sync] Restarting recgov-monitor with updated config."
    monitor_status="0"
    continue
  fi
  break
done

if [ -n "$refresh_pid" ]; then
  kill "$refresh_pid" 2>/dev/null || true
  wait "$refresh_pid" 2>/dev/null || true
fi
if [ -n "$sync_pid" ]; then
  kill "$sync_pid" 2>/dev/null || true
  wait "$sync_pid" 2>/dev/null || true
fi

exit "$monitor_status"
