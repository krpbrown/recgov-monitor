from __future__ import annotations

import json
from urllib.parse import urlparse

from recbot2.http import HttpClient
from recbot2.models import AvailabilityMatch


class DiscordNotifier:
    def __init__(self, webhook_url: str, timeout_seconds: int = 10) -> None:
        self.webhook_url = webhook_url
        self.client = HttpClient(timeout_seconds=timeout_seconds)

    def notify(self, campground_id: str, matches: list[AvailabilityMatch]) -> None:
        if not matches:
            return

        lines = [f"🏕️ Availability found for campground `{campground_id}`:"]
        for match in matches:
            lines.append(
                f"- **{match.date.isoformat()}** — {match.campsite_name} "
                f"(`{match.campsite_id}`) [{match.status}]"
            )

        payload = {"content": "\n".join(lines)}
        self.client.post_json(self.webhook_url, payload)


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
