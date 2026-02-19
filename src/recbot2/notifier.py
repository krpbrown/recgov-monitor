from __future__ import annotations

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
