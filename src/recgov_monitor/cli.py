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

from recgov_monitor.notifier import (
    DiscordNotifier,
    explain_webhook_error,
    validate_discord_webhook_url,
)
from recgov_monitor.recreation import (
    RecreationGovClient,
    extract_campground_name,
    find_available_campsites,
)


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


def is_rate_limited_error(error_message: str) -> bool:
    message = error_message.lower()
    return "429" in message or "too many requests" in message


def _load_structured_file(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix == ".json":
        return json.loads(text)

    raise ValueError("Config file must end in .json")


def load_campground_catalog(catalog_path: str) -> dict[str, str]:
    raw = _load_structured_file(Path(catalog_path))
    if not isinstance(raw, list):
        raise ValueError("Campgrounds file must be a JSON array.")

    campground_names: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each campground record must be an object.")

        campground_id = item.get("id")
        campground_name = item.get("name")
        if not isinstance(campground_id, int):
            raise ValueError("Each campground record must include integer field 'id'.")
        if not isinstance(campground_name, str) or not campground_name.strip():
            raise ValueError("Each campground record must include non-empty string field 'name'.")

        campground_names[str(campground_id)] = campground_name.strip()

    return campground_names


def load_monitor_requests(
    config_path: str,
) -> tuple[str | None, int | None, list[MonitorRequest]]:
    raw = _load_structured_file(Path(config_path))

    webhook = raw.get("discord_webhook_url")
    poll_seconds_raw = raw.get("poll_seconds")
    poll_seconds: int | None = None
    if poll_seconds_raw is not None:
        if not isinstance(poll_seconds_raw, int) or poll_seconds_raw < 0:
            raise ValueError("'poll_seconds' must be a non-negative integer.")
        poll_seconds = poll_seconds_raw

    if "campground_names" in raw:
        raise ValueError(
            "'campground_names' is no longer supported in monitor config. "
            "Use --campgrounds-file with exported RIDB JSON."
        )

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

        if not isinstance(campground_ids, list) or not all(
            isinstance(v, (str, int)) for v in campground_ids
        ):
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

    return webhook if isinstance(webhook, str) else None, poll_seconds, requests


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check recreation.gov campsite availability and alert Discord",
    )
    parser.add_argument(
        "--config",
        help="Path to JSON config defining monitor targets.",
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
        default=None,
        help="Polling interval in seconds. Defaults to config poll_seconds or 60.",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=1.0,
        help="Delay between recreation.gov API requests. Defaults to 1.0.",
    )
    parser.add_argument(
        "--campgrounds-file",
        default="campgrounds.json",
        help="Path to exported RIDB campgrounds JSON. Defaults to campgrounds.json.",
    )
    parser.add_argument(
        "--rate-limit-cooldown-seconds",
        type=int,
        default=300,
        help="Cooldown after HTTP 429 before next cycle. Defaults to 300.",
    )
    return parser


def run_once(
    monitors: list[MonitorRequest],
    campground_names: dict[str, str],
    discord_webhook_url: str,
    request_delay_seconds: float,
) -> int:
    client = RecreationGovClient()
    notifier = DiscordNotifier(discord_webhook_url)
    found_any = False

    for monitor in monitors:
        months = sorted({date(d.year, d.month, 1) for d in monitor.requested_dates})
        for campground_id in monitor.campground_ids:
            all_matches = []
            campground_name = campground_names.get(campground_id, f"campground {campground_id}")
            for month in months:
                payload = client.fetch_month(campground_id, month)
                campground_name = extract_campground_name(payload, campground_name)
                all_matches.extend(
                    find_available_campsites(payload, monitor.requested_dates)
                )
                if request_delay_seconds > 0:
                    time.sleep(request_delay_seconds)

            if all_matches:
                found_any = True
                print(
                    f"Found {len(all_matches)} available campsite slot(s) for campground "
                    f"{campground_id}. Sending Discord alert..."
                )
                try:
                    notifier.notify(
                        campground_id,
                        campground_name,
                        all_matches,
                        requested_dates=monitor.requested_dates,
                    )
                except RuntimeError as exc:
                    print(
                        f"Discord webhook error for campground {campground_id}: "
                        f"{explain_webhook_error(str(exc))}",
                        file=sys.stderr,
                    )
            else:
                print(f"No availability found for campground {campground_id}.")

    return 0 if found_any else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    monitors: list[MonitorRequest]
    config_webhook: str | None = None
    config_poll_seconds: int | None = None
    campground_names: dict[str, str]
    if args.config:
        try:
            config_webhook, config_poll_seconds, monitors = load_monitor_requests(args.config)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        if not (args.campground_ids and args.check_in and args.check_out):
            parser.error(
                "Either --config or all of --campground-ids, --check-in, --check-out are required."
            )
        try:
            monitors = [
                MonitorRequest(
                    campground_ids=parse_campground_ids(args.campground_ids),
                    requested_dates=parse_stay_dates(args.check_in, args.check_out),
                )
            ]
        except ValueError as exc:
            parser.error(str(exc))

    try:
        campground_names = load_campground_catalog(args.campgrounds_file)
    except ValueError as exc:
        parser.error(str(exc))

    webhook_url = args.discord_webhook_url or config_webhook
    if not webhook_url:
        parser.error("A Discord webhook URL is required (CLI arg, env var, or config field).")

    try:
        validate_discord_webhook_url(webhook_url)
    except ValueError as exc:
        parser.error(str(exc))

    poll_seconds = (
        args.poll_seconds
        if args.poll_seconds is not None
        else (config_poll_seconds if config_poll_seconds is not None else 60)
    )

    while True:
        sleep_seconds = poll_seconds
        try:
            exit_code = run_once(
                monitors,
                campground_names,
                webhook_url,
                args.request_delay_seconds,
            )
            if poll_seconds <= 0:
                raise SystemExit(exit_code)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if is_rate_limited_error(message):
                sleep_seconds = max(poll_seconds, args.rate_limit_cooldown_seconds)
                print(
                    "Rate limited by recreation.gov (HTTP 429). "
                    f"Cooling down for {sleep_seconds} seconds before retrying.",
                    file=sys.stderr,
                )
            print(f"Error while checking availability: {message}", file=sys.stderr)
            if poll_seconds <= 0:
                raise SystemExit(2) from exc
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
