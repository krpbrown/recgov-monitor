# recbot2

`recbot2` checks recreation.gov campground availability for date ranges and sends a Discord webhook notification when campsites are available.

## Features

- Query recreation.gov monthly availability API for one or more campgrounds.
- Accept trip dates as check-in and check-out.
- Support multiple monitor groups through a config file (JSON or YAML).
- Poll every 60 seconds by default.
- Send formatted notifications to a Discord webhook.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you want YAML config support, also install PyYAML:

```bash
pip install pyyaml
```

## Usage

### Config file mode (recommended)

`monitor.yaml`:

```yaml
discord_webhook_url: https://discord.com/api/webhooks/...
monitors:
  - campground_ids: [256892]
    check_in: 2026-03-05
    check_out: 2026-03-07
  - campground_ids: [251869, 232492]
    check_in: 2026-07-02
    check_out: 2026-07-05
```

Run:

```bash
recbot2 --config monitor.yaml
```

This matches your example of monitoring:
- `256892` from `3/5` to `3/7`
- `251869, 232492` from `7/2` to `7/5`
using the same Discord webhook.

### Direct CLI mode (single range)

```bash
recbot2 \
  --campground-ids 256892,232447 \
  --check-in 2026-07-05 \
  --check-out 2026-07-07 \
  --discord-webhook-url https://discord.com/api/webhooks/...
```

The tool polls every 60 seconds by default. You can change that with `--poll-seconds`.

In all modes, `DISCORD_WEBHOOK_URL` can be set as an environment variable instead of passing `--discord-webhook-url`.

## Notes

- In one-shot mode (`--poll-seconds 0`), a non-zero exit code means no availability was found.
- recreation.gov API details may change; this tool currently uses:
  `GET /api/camps/availability/campground/{campground_id}/month`
