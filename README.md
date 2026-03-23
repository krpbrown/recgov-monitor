# recgov-monitor

<p align="center">
  <a href="https://krpbrown.github.io/recgov-monitor/docs/editor/">
    <img alt="Open Trip Editor" src="https://img.shields.io/badge/Open%20Trip%20Editor-Click%20Here-0f4c81?style=for-the-badge">
  </a>
</p>

Monitor Recreation.gov campgrounds and ticketed events, then send Discord alerts when availability appears.

## Table of Contents

- [What It Does](#what-it-does)
- [Quick Start (Local CLI)](#quick-start-local-cli)
- [Quick Start (Docker)](#quick-start-docker)
- [GitHub Pages Web Editor](#github-pages-web-editor)
- [Get API Keys](#get-api-keys)
- [monitor.json Format](#monitorjson-format)
- [Tickets Export](#tickets-export)
- [Discord Tag Format](#discord-tag-format)
- [Key Environment Variables](#key-environment-variables)
- [ARM64 / Raspberry Pi](#arm64--raspberry-pi)
- [Troubleshooting](#troubleshooting)

## What It Does

- Monitors one or more trip groups from `monitor.json`
- Supports campground trips and ticketed tour/event trips
- Polls every 60s by default (or your configured interval)
- Sends Discord alerts to a main webhook
- Can send hourly health/status logs to a separate webhook
- Supports per-trip Discord tags (`discord_tag`)
- Supports campsite preference filtering (`tent` or `rv` with minimum RV length)

## Quick Start (Local CLI)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Export campground catalog:

```bash
export RIDB_API_KEY=your_key_here
python scripts/export_campgrounds.py --output campgrounds.json
```

Run monitor:

```bash
export DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
recgov-monitor --config monitor.json
```

## Quick Start (Docker)

Build:

```bash
docker build -t recgov-monitor:latest .
```

Run with defaults baked into image (`/data/monitor.json`, `/data/campgrounds.json`):

```bash
docker run --name recgov-monitor \
  --restart unless-stopped \
  -e DISCORD_WEBHOOK=$DISCORD_WEBHOOK \
  -e DISCORD_LOGGER_WEBHOOK=$DISCORD_LOGGER_WEBHOOK \
  -e RIDB_API_KEY=$RIDB_API_KEY \
  -e MONITOR_SYNC_URL="https://raw.githubusercontent.com/<owner>/<repo>/main/monitor.json" \
  -e MONITOR_SYNC_INTERVAL_SECONDS=300 \
  -e MONITOR_SYNC_AT_STARTUP=1 \
  -e TZ="America/Denver" \
  ghcr.io/<owner>/<repo>:latest
```

Container behavior:

- Starts polling immediately
- Refreshes `campgrounds.json` nightly at local midnight
- Syncs `monitor.json` from `MONITOR_SYNC_URL` at your interval and restarts monitor process when changes are applied

## GitHub Pages Web Editor

Use the editor at:

- https://krpbrown.github.io/recgov-monitor/docs/editor/

Setup (one time):

1. GitHub -> Settings -> Pages
2. Deploy from branch
3. Select `main` and `/docs`

Token requirements for load/save:

- Fine-grained PAT scoped to this repo
- Permissions: `Contents` read/write

The editor can load/save:

- `monitor.json`
- `tickets.json` (optional)
- `users.json` (optional)

## Get API Keys

### RIDB API Key

1. Open https://ridb.recreation.gov/landing
2. Sign in (or create an account).
3. Create/generate an API key from your RIDB account dashboard.
4. Copy the key and set it as:

```bash
export RIDB_API_KEY=your_ridb_key_here
```

### GitHub Token (for web editor load/save)

Use a fine-grained Personal Access Token (PAT):

1. GitHub -> Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens
2. Create a new token scoped to your `recgov-monitor` repo.
3. Grant repository permission:
   - `Contents`: Read and write
4. Copy the token and paste it into the web editor GitHub token field.

Optional for CLI/container sync from private repos:

```bash
export GITHUB_TOKEN=your_github_token_here
```

## monitor.json Format

```json
{
  "poll_seconds": 60,
  "monitors": [
    {
      "type": "campground",
      "trip_title": "Zion trip",
      "campground_ids": [232490, 232492],
      "check_in": "2026-09-18",
      "check_out": "2026-09-22",
      "discord_tag": "<@312027909042864130>",
      "full_matches_only": true,
      "campsite_preference": "rv",
      "rv_length_ft": 22
    },
    {
      "type": "ticket",
      "trip_title": "Great Basin Caves",
      "ticket_facility_id": 251853,
      "ticket_id": 10086943,
      "ticket_name": "Lehman Caves Tour",
      "ticket_facility_name": "Great Basin National Park",
      "check_in": "2026-06-18",
      "check_out": "2026-06-19",
      "discord_tag": "<@312027909042864130>"
    }
  ]
}
```

Notes:

- `poll_seconds` is optional (defaults to `60`)
- Campground monitors require `campground_ids`
- Ticket monitors require `ticket_facility_id` and `ticket_id`
- `campsite_preference` can be `tent` (default) or `rv`
- When `campsite_preference` is `rv`, `rv_length_ft` is required
- `full_matches_only: true` means partial-only matches are logged but not alerted

## Tickets Export

Export all known ticketed items:

```bash
python scripts/export_tickets.py --output tickets.json
```

Filter by query (park name, facility name, id, etc.):

```bash
python scripts/export_tickets.py --query "Great Basin" --output tickets.json
```

## Discord Tag Format

Best format is a real mention:

- User: `<@123456789012345678>`
- Role: `<@&123456789012345678>`

Numeric user ID alone is also supported and normalized.

Tip: in Discord, type `\@username` to reveal the raw mention form.
Example: `\@kpb17` -> `<@312027909042864130>`.

## Key Environment Variables

- `DISCORD_WEBHOOK`: main availability webhook
- `DISCORD_LOGGER_WEBHOOK`: hourly status webhook
- `DISCORD_LOGGER_MENTION`: optional mention when hourly failures exceed threshold
- `RIDB_API_KEY`: required for campground export and image previews
- `MONITOR_SYNC_URL`: raw GitHub URL for remote `monitor.json`
- `MONITOR_SYNC_INTERVAL_SECONDS`: monitor sync interval (default `300`)
- `MONITOR_SYNC_AT_STARTUP`: pull config at startup (`1` default)
- `AUTO_REFRESH_CAMPGROUNDS`: daily campground refresh enabled (`1` default)
- `REFRESH_AT_STARTUP`: optional startup campground refresh (`0` default)
- `TZ`: timezone for polling/log timestamps and midnight refresh

## ARM64 / Raspberry Pi

Build/push ARM64 image:

```bash
docker buildx build --platform linux/arm64/v8 -t ghcr.io/<owner>/<repo>:latest --push .
```

## Troubleshooting

- `recgov-monitor: command not found`:
  - activate your venv and run `pip install -e .`
- Discord `403` webhook errors:
  - verify full webhook URL from channel Integrations
- Recreation.gov `429`:
  - increase `--request-delay-seconds`
  - increase `--rate-limit-cooldown-seconds`
- Config updates not being picked up in container:
  - ensure `MONITOR_SYNC_URL` is valid raw URL
  - ensure `MONITOR_SYNC_INTERVAL_SECONDS` is set
  - check logs for `[sync] Applied updated monitor config`
