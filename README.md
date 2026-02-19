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

> `pip install -e .` is what creates the `recbot2` shell command.

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

Alternative equivalent invocation:

```bash
python -m recbot2 --config monitor.yaml
```

This matches your example of monitoring:
- `256892` from `3/5` to `3/7`
- `251869, 232492` from `7/2` to `7/5`
using the same Discord webhook.

`monitor.json`:

```json
{
  "discord_webhook_url": "https://discord.com/api/webhooks/...",
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

Run:

```bash
recbot2 --config monitor.json
```

Alternative equivalent invocation:

```bash
python -m recbot2 --config monitor.json
```

### Direct CLI mode (single range)

```bash
recbot2 \
  --campground-ids 256892,232447 \
  --check-in 2026-07-05 \
  --check-out 2026-07-07 \
  --discord-webhook-url https://discord.com/api/webhooks/...
```

Alternative equivalent invocation:

```bash
python -m recbot2 \
  --campground-ids 256892,232447 \
  --check-in 2026-07-05 \
  --check-out 2026-07-07 \
  --discord-webhook-url https://discord.com/api/webhooks/...
```

The tool polls every 60 seconds by default. You can change that with `--poll-seconds`.

To reduce recreation.gov throttling risk, the CLI also supports:
- `--request-delay-seconds` (default `1.0`) delay between API requests
- `--rate-limit-cooldown-seconds` (default `300`) cooldown when a 429 is hit

In all modes, `DISCORD_WEBHOOK_URL` can be set as an environment variable instead of passing `--discord-webhook-url`.

## Notes

- In one-shot mode (`--poll-seconds 0`), a non-zero exit code means no availability was found.
- recreation.gov API details may change; this tool currently uses:
  `GET /api/camps/availability/campground/{campground_id}/month`

## Troubleshooting

If you see:

```bash
bash: recbot2: command not found
```

it means the CLI entrypoint is not installed in your current environment yet. From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Then re-run `recbot2 --config monitor.json`.

If you see a Discord webhook error like `403 error code: 1010`, verify you are using the exact webhook URL from Discord channel **Integrations** (it must look like `https://discord.com/api/webhooks/<id>/<token>`), and confirm your network/proxy allows outbound requests to `discord.com`.

If you see `HTTP Error 429: Too Many Requests`, recreation.gov is throttling requests. Increase `--request-delay-seconds` (for example `2` or `3`) and/or increase `--rate-limit-cooldown-seconds` (for example `600`). The app now automatically cools down when it detects 429 responses.
