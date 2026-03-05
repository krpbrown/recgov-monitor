from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from recgov_monitor.http import HttpClient
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
    discord_tag: str | None = None


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


def compute_sleep_seconds(poll_seconds: int, now: datetime | None = None) -> float:
    if poll_seconds != 60:
        return float(poll_seconds)

    current = now or datetime.now()
    next_aligned = current.replace(second=5, microsecond=0)
    if next_aligned <= current:
        next_aligned += timedelta(minutes=1)
    return (next_aligned - current).total_seconds()


def format_poll_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now()
    hour = current.hour % 12
    if hour == 0:
        hour = 12
    meridiem = "AM" if current.hour < 12 else "PM"
    return (
        f"{current.month}/{current.day}/{current.year % 100:02d} "
        f"{hour}:{current.minute:02d}:{current.second:02d} {meridiem}"
    )


def format_requested_date_span(requested_dates: set[date]) -> str:
    if not requested_dates:
        return "unknown dates"
    check_in = min(requested_dates)
    check_out = max(requested_dates) + timedelta(days=1)
    return f"{check_in.isoformat()} to {check_out.isoformat()}"


class _RunLogger:
    def __init__(self, path: Path) -> None:
        self._file = path.open("a", encoding="utf-8")

    def close(self) -> None:
        self._file.close()

    def info(self, message: str) -> None:
        print(message)
        self._write(message)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr)
        self._write(message)

    def file_only(self, message: str) -> None:
        self._write(message)

    def _write(self, message: str) -> None:
        self._file.write(f"{message}\n")
        self._file.flush()


class _StatusReporter:
    def __init__(
        self,
        webhook_url: str | None,
        mention: str | None,
        started_at: datetime,
        *,
        logger: _RunLogger,
    ) -> None:
        self.webhook_url = webhook_url.strip() if webhook_url else ""
        self.mention = mention.strip() if mention else ""
        self.started_at = started_at
        self.logger = logger
        self.client = HttpClient(timeout_seconds=10)
        self.interval_total_issues = 0
        self.interval_rate_limit_issues = 0
        self.interval_last_issue_message = ""
        self.interval_last_issue_at: datetime | None = None
        self.interval_successful_queries = 0
        self.interval_total_queries = 0
        self.interval_failed_queries = 0
        self.next_emit_at = self._next_top_of_hour(started_at)

    def _next_top_of_hour(self, now: datetime) -> datetime:
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    def seconds_until_next_emit(self, now: datetime | None = None) -> float:
        current = now or datetime.now()
        return max(0.0, (self.next_emit_at - current).total_seconds())

    def record_issue(self, message: str, *, rate_limited: bool = False) -> None:
        self.interval_total_issues += 1
        if rate_limited:
            self.interval_rate_limit_issues += 1
        self.interval_last_issue_message = message.strip()
        self.interval_last_issue_at = datetime.now()

    def record_successful_query(self) -> None:
        self.interval_total_queries += 1
        self.interval_successful_queries += 1

    def record_failed_query(self) -> None:
        self.interval_total_queries += 1
        self.interval_failed_queries += 1

    def emit_if_due(self, now: datetime | None = None) -> None:
        current = now or datetime.now()
        if current < self.next_emit_at:
            return
        self.emit(current)

    def emit_startup(self, now: datetime | None = None) -> None:
        current = now or datetime.now()
        if not self.webhook_url:
            return
        content = (
            "Recgov Monitor is now polling\n"
            f"Time: {format_poll_timestamp(current)}\n"
            "Issues: none"
        )
        try:
            self.client.post_json(self.webhook_url, {"content": content[:2000]})
        except RuntimeError as exc:
            self.logger.error(f"Logger webhook error: {explain_webhook_error(str(exc))}")

    def emit_shutdown(self, reason: str, exit_code: int, now: datetime | None = None) -> None:
        current = now or datetime.now()
        if not self.webhook_url:
            return
        uptime_seconds = int(max(0.0, (current - self.started_at).total_seconds()))
        hours, rem = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        days, hours = divmod(hours, 24)
        content = (
            "recgov-monitor shutdown status\n"
            f"Time: {format_poll_timestamp(current)}\n"
            f"Uptime: {days}d {hours:02d}:{minutes:02d}:{seconds:02d}\n"
            f"Exit code: {exit_code}\n"
            f"Reason: {reason}"
        )
        try:
            self.client.post_json(self.webhook_url, {"content": content[:2000]})
        except RuntimeError as exc:
            self.logger.error(f"Logger webhook error: {explain_webhook_error(str(exc))}")

    def emit(self, now: datetime) -> None:
        self.next_emit_at = self._next_top_of_hour(now)
        if not self.webhook_url:
            self._reset_interval_counters()
            return

        uptime_seconds = int(max(0.0, (now - self.started_at).total_seconds()))
        hours, rem = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        days, hours = divmod(hours, 24)

        if self.interval_total_issues == 0:
            issue_line = "Issues: none"
        else:
            last_issue_at = (
                format_poll_timestamp(self.interval_last_issue_at)
                if self.interval_last_issue_at
                else "unknown"
            )
            issue_line = (
                "Issues: "
                f"{self.interval_total_issues} total (rate-limit: {self.interval_rate_limit_issues}, "
                f"other: {self.interval_total_issues - self.interval_rate_limit_issues}) | "
                f"Last: {last_issue_at} | {self.interval_last_issue_message}"
            )

        content = (
            "recgov-monitor hourly status\n"
            f"Time: {format_poll_timestamp(now)}\n"
            f"Uptime: {days}d {hours:02d}:{minutes:02d}:{seconds:02d}\n"
            f"Successful queries this interval: {self.interval_successful_queries}\n"
            f"Failed queries this interval: {self.interval_failed_queries}\n"
            f"{issue_line}"
        )
        if self.mention and self.interval_total_queries > 0:
            failure_ratio = self.interval_failed_queries / self.interval_total_queries
            if failure_ratio > 0.5:
                content = (
                    f"{content}\n"
                    f"Alert: {self.mention} query failure ratio exceeded 50% this interval "
                    f"({self.interval_failed_queries}/{self.interval_total_queries})."
                )
        try:
            self.client.post_json(self.webhook_url, {"content": content[:2000]})
        except RuntimeError as exc:
            self.logger.error(f"Logger webhook error: {explain_webhook_error(str(exc))}")
        finally:
            self._reset_interval_counters()

    def _reset_interval_counters(self) -> None:
        self.interval_total_issues = 0
        self.interval_rate_limit_issues = 0
        self.interval_last_issue_message = ""
        self.interval_last_issue_at = None
        self.interval_successful_queries = 0
        self.interval_total_queries = 0
        self.interval_failed_queries = 0


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
        discord_tag_raw = item.get("discord_tag")

        if not isinstance(campground_ids, list) or not all(
            isinstance(v, (str, int)) for v in campground_ids
        ):
            raise ValueError("'campground_ids' must be a non-empty list of ids.")
        if not campground_ids:
            raise ValueError("'campground_ids' must be a non-empty list of ids.")
        if not isinstance(check_in, str) or not isinstance(check_out, str):
            raise ValueError("'check_in' and 'check_out' must be date strings (YYYY-MM-DD).")
        if discord_tag_raw is not None and not isinstance(discord_tag_raw, str):
            raise ValueError("'discord_tag' must be a string when provided.")
        discord_tag = discord_tag_raw.strip() if isinstance(discord_tag_raw, str) else ""

        requests.append(
            MonitorRequest(
                campground_ids=[str(v) for v in campground_ids],
                requested_dates=parse_stay_dates(check_in, check_out),
                discord_tag=discord_tag or None,
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
        default=os.getenv("DISCORD_WEBHOOK") or os.getenv("DISCORD_WEBHOOK_URL"),
        help="Discord webhook URL. Defaults to DISCORD_WEBHOOK env var (fallback: DISCORD_WEBHOOK_URL).",
    )
    parser.add_argument(
        "--discord-logger-webhook-url",
        default=os.getenv("DISCORD_LOGGER_WEBHOOK"),
        help="Discord webhook URL for hourly status logs. Defaults to DISCORD_LOGGER_WEBHOOK env var.",
    )
    parser.add_argument(
        "--discord-logger-mention",
        default=os.getenv("DISCORD_LOGGER_MENTION"),
        help=(
            "Optional mention text added to hourly status when >50%% of interval queries fail. "
            "Defaults to DISCORD_LOGGER_MENTION env var."
        ),
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
    parser.add_argument(
        "--log-file",
        default="recgov-monitor.log",
        help="Log file path for terminal output and Discord message text.",
    )
    return parser


def run_once(
    monitors: list[MonitorRequest],
    campground_names: dict[str, str],
    discord_webhook_url: str,
    request_delay_seconds: float,
    info_log: Callable[[str], None] | None = None,
    error_log: Callable[[str], None] | None = None,
    discord_log: Callable[[str], None] | None = None,
    issue_log: Callable[[str], None] | None = None,
) -> int:
    client = RecreationGovClient()
    notifier = DiscordNotifier(discord_webhook_url)
    found_any = False
    info = info_log or print
    error = error_log or (lambda message: print(message, file=sys.stderr))

    for trip_index, monitor in enumerate(monitors, start=1):
        date_span = format_requested_date_span(monitor.requested_dates)
        trip_campground_names = [
            campground_names.get(campground_id, f"campground {campground_id}")
            for campground_id in monitor.campground_ids
        ]
        info(
            f"Trip {trip_index} ({date_span}) - querying "
            f"{len(trip_campground_names)} campground(s):"
        )
        for campground_name in trip_campground_names:
            info(f"  - {campground_name}")

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
                info(
                    f"Found {len(all_matches)} available campsite slot(s) for {campground_name}. "
                    f"Trip {trip_index} ({date_span}). Sending Discord alert..."
                )
                try:
                    notifier.notify(
                        campground_id,
                        campground_name,
                        all_matches,
                        requested_dates=monitor.requested_dates,
                        mention=monitor.discord_tag,
                        log_message=discord_log,
                    )
                except RuntimeError as exc:
                    message = (
                        f"Discord webhook error for {campground_name}: "
                        f"{explain_webhook_error(str(exc))}"
                    )
                    error(message)
                    if issue_log is not None:
                        issue_log(message)
            else:
                info(f"No availability found for {campground_name}. Trip {trip_index} ({date_span}).")

    return 0 if found_any else 1


def main() -> int:
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

    logger_webhook_url = (args.discord_logger_webhook_url or "").strip()
    logger_mention = (args.discord_logger_mention or "").strip()
    if logger_webhook_url:
        try:
            validate_discord_webhook_url(logger_webhook_url)
        except ValueError as exc:
            parser.error(f"Invalid logger webhook URL: {exc}")

    poll_seconds = (
        args.poll_seconds
        if args.poll_seconds is not None
        else (config_poll_seconds if config_poll_seconds is not None else 60)
    )
    logger = _RunLogger(Path(args.log_file))
    logger.info(f"Logging to {args.log_file}")
    status_reporter = _StatusReporter(
        webhook_url=logger_webhook_url,
        mention=logger_mention,
        started_at=datetime.now(),
        logger=logger,
    )
    logger.info(f"{format_poll_timestamp()} - Recgov Monitor is now polling")
    status_reporter.emit_startup()

    try:
        while True:
            logger.info(f"{format_poll_timestamp()} - Querying")
            try:
                exit_code = run_once(
                    monitors,
                    campground_names,
                    webhook_url,
                    args.request_delay_seconds,
                    info_log=logger.info,
                    error_log=logger.error,
                    discord_log=lambda message: logger.file_only(
                        f"Discord notification text:\n{message}"
                    ),
                    issue_log=lambda message: status_reporter.record_issue(message),
                )
                if poll_seconds <= 0:
                    status_reporter.emit_shutdown(
                        reason="One-shot polling completed.",
                        exit_code=exit_code,
                    )
                    return exit_code
                status_reporter.record_successful_query()
                sleep_seconds = compute_sleep_seconds(poll_seconds)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                sleep_seconds = float(poll_seconds)
                status_reporter.record_failed_query()
                if is_rate_limited_error(message):
                    sleep_seconds = max(poll_seconds, args.rate_limit_cooldown_seconds)
                    status_reporter.record_issue(message, rate_limited=True)
                    logger.error(
                        "Rate limited by recreation.gov (HTTP 429). "
                        f"Cooling down for {sleep_seconds} seconds before retrying."
                    )
                else:
                    status_reporter.record_issue(message)
                logger.error(f"Error while checking availability: {message}")
                if poll_seconds <= 0:
                    status_reporter.emit_shutdown(
                        reason="One-shot polling aborted after error.",
                        exit_code=2,
                    )
                    return 2
            remaining_sleep = sleep_seconds
            while remaining_sleep > 0:
                status_reporter.emit_if_due()
                until_hourly = status_reporter.seconds_until_next_emit()
                chunk = remaining_sleep
                if until_hourly > 0:
                    chunk = min(chunk, until_hourly)
                if chunk <= 0:
                    status_reporter.emit_if_due()
                    continue
                time.sleep(chunk)
                remaining_sleep -= chunk
    except KeyboardInterrupt:
        logger.info("Monitoring stopped.")
        status_reporter.emit_shutdown(reason="Monitoring stopped by user.", exit_code=0)
        return 0
    finally:
        logger.close()

if __name__ == "__main__":
    raise SystemExit(main())
