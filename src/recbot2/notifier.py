from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlparse

from recbot2.http import HttpClient
from recbot2.models import AvailabilityMatch

DISCORD_CONTENT_MAX_LENGTH = 2000


class DiscordNotifier:
    def __init__(self, webhook_url: str, timeout_seconds: int = 10) -> None:
        self.webhook_url = webhook_url
        self.client = HttpClient(timeout_seconds=timeout_seconds)

    def notify(
        self,
        campground_id: str,
        campground_name: str,
        matches: list[AvailabilityMatch],
    ) -> None:
        if not matches:
            return

        fallback_name = f"campground {campground_id}".lower()
        if campground_name.strip().lower() == fallback_name:
            header = f"Availability found for campground {campground_id}"
        else:
            header = f"Availability found for {campground_name} ({campground_id})"
        lines: list[str] = []
        grouped_matches = _group_matches_by_campsite(matches)
        for match in grouped_matches:
            reserve_url = f"https://www.recreation.gov/camping/campsites/{match.campsite_id}"
            short_dates = ", ".join(
                f"{d.month}/{d.day}/{d.year}" for d in sorted(match.dates)
            )
            status = ", ".join(sorted(match.statuses))
            lines.append(
                f"- Site: {match.campsite_name} | Status: {status} | Dates: {short_dates} | Reserve: <{reserve_url}>"
            )

        for content in _build_discord_message_chunks(header, lines):
            payload = {"content": content}
            self.client.post_json(self.webhook_url, payload)


def _truncate_line(line: str, max_len: int) -> str:
    if len(line) <= max_len:
        return line
    if max_len <= 3:
        return line[:max_len]
    return f"{line[:max_len - 3]}..."


def _build_discord_message_chunks(header: str, lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = _truncate_line(header, DISCORD_CONTENT_MAX_LENGTH)
    line_max = max(1, DISCORD_CONTENT_MAX_LENGTH - len(current) - 1)

    for line in lines:
        safe_line = _truncate_line(line, line_max)
        candidate = f"{current}\n{safe_line}"
        if len(candidate) <= DISCORD_CONTENT_MAX_LENGTH:
            current = candidate
            continue
        chunks.append(current)
        current = safe_line

    chunks.append(current)
    return chunks


class _GroupedMatch:
    def __init__(self, campsite_id: str, campsite_name: str) -> None:
        self.campsite_id = campsite_id
        self.campsite_name = campsite_name
        self.statuses: set[str] = set()
        self.dates: set[date] = set()


def _group_matches_by_campsite(matches: list[AvailabilityMatch]) -> list[_GroupedMatch]:
    grouped: dict[str, _GroupedMatch] = {}
    for match in matches:
        if match.campsite_id not in grouped:
            grouped[match.campsite_id] = _GroupedMatch(
                campsite_id=match.campsite_id,
                campsite_name=match.campsite_name,
            )
        entry = grouped[match.campsite_id]
        entry.statuses.add(match.status)
        entry.dates.add(match.date)

    return sorted(grouped.values(), key=lambda item: item.campsite_name)


def validate_discord_webhook_url(webhook_url: str) -> None:
    parsed = urlparse(webhook_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Discord webhook URL must start with http:// or https://")

    if parsed.netloc not in {"discord.com", "ptb.discord.com", "canary.discord.com"}:
        raise ValueError(
            "Discord webhook host must be discord.com (or ptb/canary subdomain)."
        )

    if not parsed.path.startswith("/api/webhooks/"):
        raise ValueError(
            "Discord webhook URL path must start with /api/webhooks/. "
            "Use the full webhook URL from Discord channel Integrations."
        )


def explain_webhook_error(error_message: str) -> str:
    detail = error_message
    lower = error_message.lower()

    try:
        json_start = error_message.index("{")
        body_obj = json.loads(error_message[json_start:])
        if isinstance(body_obj, dict):
            code = body_obj.get("code")
            message = body_obj.get("message")
            if code and message:
                detail = f"{detail} (Discord code {code}: {message})"
    except (ValueError, json.JSONDecodeError):
        pass

    if "403" in lower or "code: 1010" in lower:
        return (
            f"{detail}. Discord rejected the request (403/1010). "
            "This usually means the URL is not a valid webhook endpoint, "
            "the webhook was deleted/rotated, or network/proxy filtering blocked discord.com."
        )

    if "401" in lower or "403" in lower:
        return (
            f"{detail}. Check that the webhook URL is current and complete "
            "(it should look like https://discord.com/api/webhooks/<id>/<token>)."
        )

    if "404" in lower:
        return (
            f"{detail}. Discord webhook not found. Recreate the webhook in "
            "your channel Integrations and update your config."
        )

    return detail
