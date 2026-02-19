from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from recbot2.notifier import DiscordNotifier
from recbot2.recreation import RecreationGovClient, find_available_campsites


@dataclass(frozen=True)
class MonitorRequest:
    campground_ids: list[str]
    requested_dates: set[date]


def parse_campground_ids(campground_ids_csv: str) -> list[str]:
    ids = [item.strip() for item in campground_ids_csv.split(",") if item.strip()]
    if not ids:
        raise ValueError("No valid campground IDs were provided.")
    return ids


def parse_stay_dates(check_in_raw: str, check_out_raw: str) -> set[date]:
    check_in = datetime.strptime(check_in_raw, "%Y-%m-%d").date()
    check_out = datetime.strptime(check_out_raw, "%Y-%m-%d").date()
    if check_out <= check_in:
        raise ValueError("Check-out date must be after check-in date.")

    nights: set[date] = set()
    day = check_in
    while day < check_out:
        nights.add(day)
        day += timedelta(days=1)
    return nights


def _load_structured_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix == ".json":
        return json.loads(text)

    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ValueError(
                "YAML config requires PyYAML. Install with: pip install pyyaml"
            ) from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Config root must be a mapping/object.")
        return data

    raise ValueError("Config file must end in .json, .yaml, or .yml")


def load_monitor_requests(config_path: str) -> tuple[str | None, list[MonitorRequest]]:
    raw = _load_structured_file(Path(config_path))

    webhook = raw.get("discord_webhook_url")
    monitors = raw.get("monitors")
    if not isinstance(monitors, list) or not monitors:
        raise ValueError("Config must include a non-empty 'monitors' list.")

    requests: list[MonitorRequest] = []
    for item in monitors:
        if not isinstance(item, dict):
            raise ValueError("Each monitor entry must be a mapping/object.")
        campground_ids = item.get("campground_ids")
        check_in = item.get("check_in")
        check_out = item.get("check_out")

        if not isinstance(campground_ids, list) or not all(isinstance(v, (str, int)) for v in campground_ids):
            raise ValueError("'campground_ids' must be a non-empty list of ids.")
        if not campground_ids:
            raise ValueError("'campground_ids' must be a non-empty list of ids.")
        if not isinstance(check_in, str) or not isinstance(check_out, str):
            raise ValueError("'check_in' and 'check_out' must be date strings (YYYY-MM-DD).")

        requests.append(
            MonitorRequest(
                campground_ids=[str(v) for v in campground_ids],
                requested_dates=parse_stay_dates(check_in, check_out),
            )
        )

    return webhook if isinstance(webhook, str) else None, requests


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check recreation.gov campsite availability and alert Discord",
    )
    parser.add_argument(
        "--config",
        help="Path to JSON or YAML config defining monitor targets.",
    )
    parser.add_argument(
        "--campground-ids",
        help="comma-separated recreation.gov campground ids, e.g. 256892,232447",
    )
    parser.add_argument("--check-in", help="check-in date (YYYY-MM-DD)")
    parser.add_argument("--check-out", help="check-out date (YYYY-MM-DD)")
    parser.add_argument(
        "--discord-webhook-url",
        default=os.getenv("DISCORD_WEBHOOK_URL"),
        help="Discord webhook URL. Defaults to DISCORD_WEBHOOK_URL env var.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Polling interval in seconds. Defaults to 60.",
    )
    return parser


def run_once(monitors: list[MonitorRequest], discord_webhook_url: str) -> int:
    client = RecreationGovClient()
    notifier = DiscordNotifier(discord_webhook_url)
    found_any = False

    for monitor in monitors:
        months = sorted({date(d.year, d.month, 1) for d in monitor.requested_dates})
        for campground_id in monitor.campground_ids:
            all_matches = []
            for month in months:
                payload = client.fetch_month(campground_id, month)
                all_matches.extend(find_available_campsites(payload, monitor.requested_dates))

            if all_matches:
                found_any = True
                print(
                    f"Found {len(all_matches)} available campsite slot(s) for campground "
                    f"{campground_id}. Sending Discord alert..."
                )
                notifier.notify(campground_id, all_matches)
            else:
                print(f"No availability found for campground {campground_id}.")

    return 0 if found_any else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    monitors: list[MonitorRequest]
    config_webhook: str | None = None
    if args.config:
        try:
            config_webhook, monitors = load_monitor_requests(args.config)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        if not (args.campground_ids and args.check_in and args.check_out):
            parser.error("Either --config or all of --campground-ids, --check-in, --check-out are required.")
        try:
            monitors = [
                MonitorRequest(
                    campground_ids=parse_campground_ids(args.campground_ids),
                    requested_dates=parse_stay_dates(args.check_in, args.check_out),
                )
            ]
        except ValueError as exc:
            parser.error(str(exc))

    webhook_url = args.discord_webhook_url or config_webhook
    if not webhook_url:
        parser.error("A Discord webhook URL is required (CLI arg, env var, or config field).")

    while True:
        try:
            exit_code = run_once(monitors, webhook_url)
            if args.poll_seconds <= 0:
                raise SystemExit(exit_code)
        except Exception as exc:  # noqa: BLE001
            print(f"Error while checking availability: {exc}", file=sys.stderr)
            if args.poll_seconds <= 0:
                raise SystemExit(2) from exc
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
