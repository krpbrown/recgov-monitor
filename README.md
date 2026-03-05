# recgov-monitor

`recgov-monitor` checks recreation.gov campground availability for date ranges and sends a Discord webhook notification when campsites are available.

## Features

- Query recreation.gov monthly availability API for one or more campgrounds.
- Accept trip dates as check-in and check-out.
- Support multiple monitor groups through a JSON config file.
- Optional per-trip `discord_tag` so alerts can mention different users/roles per trip group.
- Poll every 60 seconds by default.
- Send formatted notifications to a Discord webhook.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

> `pip install -e .` is what creates the `recgov-monitor` shell command.

## Container

Build a local image:

```bash
docker build -t recgov-monitor:latest .
```

Run with baked-in defaults (no CLI args needed):

```bash
docker run --rm \
  -e RIDB_API_KEY=your_key_here \
  -e DISCORD_WEBHOOK=https://discord.com/api/webhooks/... \
  recgov-monitor:latest
```

Equivalent with Podman:

```bash
podman build -t recgov-monitor:latest .
podman run --rm \
  -e RIDB_API_KEY=your_key_here \
  -e DISCORD_WEBHOOK=https://discord.com/api/webhooks/... \
  recgov-monitor:latest
```

Container behavior:

- Starts monitoring immediately.
- Refreshes `campgrounds.json` daily at local midnight inside the container.
- Does not refresh on startup by default.

Optional host mounts (to override defaults and persist nightly updates):

```bash
docker run --rm \
  -v "$PWD/monitor.json:/data/monitor.json:ro" \
  -v "$PWD/campgrounds.json:/data/campgrounds.json" \
  -e RIDB_API_KEY=your_key_here \
  -e DISCORD_WEBHOOK=https://discord.com/api/webhooks/... \
  recgov-monitor:latest
```

Optional environment variables:

- `MONITOR_FILE=/data/monitor.json` path to monitor config (default shown).
- `CAMPGROUNDS_FILE=/data/campgrounds.json` path to campground catalog (default shown).
- `AUTO_REFRESH_CAMPGROUNDS=1` enable/disable daily refresh loop (`0` disables).
- `REFRESH_AT_STARTUP=0` run refresh once during container startup (`1` enables startup refresh).
- `REFRESH_SKIP_VALIDATION=1` pass `--skip-validation` to refresh job for faster updates.
- `MONITOR_SYNC_URL=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/monitor.json` enable periodic remote monitor sync.
- `MONITOR_SYNC_INTERVAL_SECONDS=300` sync interval in seconds.
- `MONITOR_SYNC_AT_STARTUP=1` pull monitor config immediately on container start.
- `MONITOR_SYNC_ETAG_FILE=/data/.monitor_sync.etag` path for ETag cache used by conditional sync requests.
- `GITHUB_TOKEN=<token>` optional auth token for private repo monitor sync URL.
- `DISCORD_WEBHOOK=https://discord.com/api/webhooks/...` webhook passed via env (recommended).
- `DISCORD_LOGGER_WEBHOOK=https://discord.com/api/webhooks/...` optional separate webhook for hourly status logs (time, uptime, successful query count, and issue summary for that hour).
- `TZ=America/Denver` set timezone used for "midnight" scheduling.

### Push to GitHub Packages (GHCR, local)

```bash
docker login ghcr.io -u <github-username>
docker build -t ghcr.io/<owner>/<repo>:latest .
docker push ghcr.io/<owner>/<repo>:latest
```

Build ARM64 image locally (Raspberry Pi compatible):

```bash
docker buildx build --platform linux/arm64/v8 -t ghcr.io/<owner>/<repo>:arm64 --push .
```

### CI Pipeline

- GitHub Actions workflow is provided in `.github/workflows/container-image.yml` and pushes to `ghcr.io/<owner>/<repo>`.

## GitHub Pages Editor

This repo includes a static web editor in `docs/` for editing `monitor.json` in GitHub from a browser.

1. In GitHub: **Settings -> Pages**
2. Set source to **Deploy from a branch**
3. Select branch `main` and folder `/docs`
4. Open the Pages editor URL (for example `https://<owner>.github.io/<repo>/editor/`)
5. Enter repo/branch/path values and a fine-grained GitHub token
6. Click **Load from GitHub**, edit trips, then **Save monitor.json to GitHub**
7. For campground preview images in the editor, provide a RIDB API key in the page field.

Token permissions:

- Repository access: this repo only
- Permissions: **Contents** read and write

Notes:

- The editor is fully static (runs on GitHub Pages, no backend).
- If your repo is private, you must use a token to load/save.
- The Raspberry Pi container can then pull updated `monitor.json` from GitHub.

## Usage

### Config file mode (recommended)

`monitor.json`:

```json
{
  "poll_seconds": 60,
  "monitors": [
    {
      "campground_ids": [256892],
      "check_in": "2026-03-05",
      "check_out": "2026-03-07",
      "discord_tag": "@user1"
    },
    {
      "campground_ids": [251869, 232492],
      "check_in": "2026-07-02",
      "check_out": "2026-07-05"
    }
  ]
}
```

`poll_seconds` is optional. If omitted, it defaults to `60`. You can still override this with CLI `--poll-seconds`.
Campground names are now loaded from an exported RIDB JSON file (default path: `campgrounds.json`).
Each monitor entry can optionally include `discord_tag` (recommended: `<@123456789012345678>` user mention, `<@&role_id>` role mention, or numeric user ID), which is prepended to Discord availability alerts for that specific trip group.
Plain `@username` text may render but often does not generate a real ping from webhooks.
To get a numeric user ID quickly, type a mention with a leading backslash in Discord (for example `\@kpb17`) and Discord will print the raw mention form (for example `<@312027909042864130>`).

Run:

```bash
recgov-monitor --config monitor.json
```

Alternative equivalent invocation:

```bash
python -m recgov_monitor --config monitor.json

Before running monitor mode, export campgrounds:

```bash
export RIDB_API_KEY=your_key_here
python scripts/export_campgrounds.py --output campgrounds.json
```

By default, export now performs live recreation.gov validation and removes clearly invalid campground IDs
(for example dead campground links). For quicker test runs, disable this with `--skip-validation` (or `-S`).
The exported records also include a `park` field (when RIDB park/rec-area metadata is available).

Fast test run example (20 records, force include campground `232492`):

```bash
export RIDB_API_KEY=your_key_here
python scripts/export_campgrounds.py -S --test-limit 20 --test-include-id 232492 --output campgrounds.json
```

To create or update `monitor.json` with a desktop UI:

```bash
python scripts/monitor_gui.py
```

Optional overrides:

```bash
python scripts/monitor_gui.py --campgrounds-file campgrounds.json --monitor-file monitor.json --ridb-api-key "$RIDB_API_KEY"
```

The GUI lets you search and select one or more campgrounds, set check-in/check-out dates, and save.
Search matches campground name, campground ID, and park name.
When you click a campground, the GUI attempts to load a campground image using RIDB facility media.
You can create multiple trip groups (each with its own campground set, date range, and optional `discord_tag`), and each group is saved as a separate entry in `monitor.json` `monitors`.
```

### Direct CLI mode (single range)

```bash
recgov-monitor \
  --campground-ids 256892,232447 \
  --check-in 2026-07-05 \
  --check-out 2026-07-07 \
  --discord-webhook-url https://discord.com/api/webhooks/...
```

Alternative equivalent invocation:

```bash
python -m recgov_monitor \
  --campground-ids 256892,232447 \
  --check-in 2026-07-05 \
  --check-out 2026-07-07 \
  --discord-webhook-url https://discord.com/api/webhooks/...
```

The tool polls every 60 seconds by default. You can set this in `monitor.json` (`poll_seconds`) or override with `--poll-seconds`.

To reduce recreation.gov throttling risk, the CLI also supports:
- `--request-delay-seconds` (default `1.0`) delay between API requests
- `--rate-limit-cooldown-seconds` (default `300`) cooldown when a 429 is hit

In all modes, `DISCORD_WEBHOOK` can be set as an environment variable instead of passing `--discord-webhook-url`.
`DISCORD_WEBHOOK_URL` is still accepted as a backward-compatible fallback.
`DISCORD_LOGGER_WEBHOOK` can be set to send hourly health/status logs to a separate Discord channel.
You can override the campground catalog path with `--campgrounds-file`.

## Discord Message Format

When availability is found, notifications include campground name, site, status, date, and a reserve link.

Example:

```text
Availability found for Simpson Springs Campground 256892
- Site: 001 | Status: Available | Date: 3/5/2026 | Reserve: <https://www.recreation.gov/camping/campsites/10019342>
```

If only some requested nights are available, Discord alerts are sent as **partial availability** and include
`Coverage: Partial (x/y nights)` per site.

## Notes

- In one-shot mode (`--poll-seconds 0`), a non-zero exit code means no availability was found.
- recreation.gov API details may change; this tool currently uses:
  `GET /api/camps/availability/campground/{campground_id}/month`

## Troubleshooting

If you see:

```bash
bash: recgov-monitor: command not found
```

it means the CLI entrypoint is not installed in your current environment yet. From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Then re-run `recgov-monitor --config monitor.json`.

If you see a Discord webhook error like `403 error code: 1010`, verify you are using the exact webhook URL from Discord channel **Integrations** (it must look like `https://discord.com/api/webhooks/<id>/<token>`), and confirm your network/proxy allows outbound requests to `discord.com`.

If you see `HTTP Error 429: Too Many Requests`, recreation.gov is throttling requests. Increase `--request-delay-seconds` (for example `2` or `3`) and/or increase `--rate-limit-cooldown-seconds` (for example `600`). The app now automatically cools down when it detects 429 responses.

