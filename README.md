# recgov-monitor

`recgov-monitor` checks recreation.gov campground availability for date ranges and sends a Discord webhook notification when campsites are available.

## Features

- Query recreation.gov monthly availability API for one or more campgrounds.
- Accept trip dates as check-in and check-out.
- Support multiple monitor groups through a JSON config file.
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

Run with local config/catalog mounted:

```bash
docker run --rm \
  -v "$PWD/monitor.json:/data/monitor.json:ro" \
  -v "$PWD/campgrounds.json:/data/campgrounds.json" \
  -e RIDB_API_KEY=your_key_here \
  -e CAMPGROUNDS_FILE=/data/campgrounds.json \
  recgov-monitor:latest \
  --config /data/monitor.json --campgrounds-file /data/campgrounds.json
```

Equivalent with Podman:

```bash
podman build -t recgov-monitor:latest .
podman run --rm \
  -v "$PWD/monitor.json:/data/monitor.json:ro" \
  -v "$PWD/campgrounds.json:/data/campgrounds.json" \
  -e RIDB_API_KEY=your_key_here \
  -e CAMPGROUNDS_FILE=/data/campgrounds.json \
  recgov-monitor:latest \
  --config /data/monitor.json --campgrounds-file /data/campgrounds.json
```

Container behavior:

- Starts monitoring immediately.
- Refreshes `campgrounds.json` daily at local midnight inside the container.
- Runs a startup refresh by default, then daily midnight refreshes.

Optional environment variables:

- `AUTO_REFRESH_CAMPGROUNDS=1` enable/disable daily refresh loop (`0` disables).
- `REFRESH_AT_STARTUP=1` run refresh once during container startup (`0` disables startup refresh).
- `REFRESH_SKIP_VALIDATION=1` pass `--skip-validation` to refresh job for faster updates.
- `TZ=America/Denver` set timezone used for "midnight" scheduling.

### Push to GitHub Packages (GHCR, local)

```bash
docker login ghcr.io -u <github-username>
docker build -t ghcr.io/<owner>/<repo>:latest .
docker push ghcr.io/<owner>/<repo>:latest
```

### CI Pipeline

- GitHub Actions workflow is provided in `.github/workflows/container-image.yml` and pushes to `ghcr.io/<owner>/<repo>`.

## Usage

### Config file mode (recommended)

`monitor.json`:

```json
{
  "discord_webhook_url": "https://discord.com/api/webhooks/...",
  "poll_seconds": 60,
  "monitors": [
    {
      "campground_ids": [256892],
      "check_in": "2026-03-05",
      "check_out": "2026-03-07"
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
You can create multiple trip groups (each with its own campground set and date range), and each group is saved as a separate entry in `monitor.json` `monitors`.
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

In all modes, `DISCORD_WEBHOOK_URL` can be set as an environment variable instead of passing `--discord-webhook-url`.
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

