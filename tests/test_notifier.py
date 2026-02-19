from datetime import date

from recbot2.notifier import explain_webhook_error, validate_discord_webhook_url
from recbot2.models import AvailabilityMatch
from recbot2.notifier import DiscordNotifier


def test_validate_discord_webhook_url_accepts_valid_url() -> None:
    validate_discord_webhook_url(
        "https://discord.com/api/webhooks/123456789012345678/abcdef"
    )


def test_validate_discord_webhook_url_rejects_non_webhook_path() -> None:
    try:
        validate_discord_webhook_url("https://discord.com/channels/123/456")
    except ValueError as exc:
        assert "/api/webhooks/" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-webhook URL path")


def test_explain_webhook_error_for_403_1010() -> None:
    message = "Webhook request failed: 403 error code: 1010"
    explained = explain_webhook_error(message)
    assert "403/1010" in explained
    assert "valid webhook endpoint" in explained


def test_notify_formats_campground_name_site_status_date_and_link() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def post_json(self, url: str, payload: dict) -> None:
            self.calls.append((url, payload))

    notifier = DiscordNotifier("https://discord.com/api/webhooks/123456/abcdef")
    stub = StubClient()
    notifier.client = stub  # type: ignore[assignment]

    notifier.notify(
        campground_id="256892",
        campground_name="Simpson Springs Campground",
        matches=[
            AvailabilityMatch(
                campsite_id="10019342",
                campsite_name="001",
                date=date(2026, 3, 5),
                status="Available",
            )
        ],
    )

    assert len(stub.calls) == 1
    _, payload = stub.calls[0]
    content = payload["content"]
    assert "Availability found for Simpson Springs Campground (256892)" in content
    assert "- Site: 001 | Status: Available | Dates: 3/5/2026 | Reserve:" in content
    assert "<https://www.recreation.gov/camping/campsites/10019342>" in content


def test_notify_groups_same_site_across_multiple_dates() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def post_json(self, url: str, payload: dict) -> None:
            self.calls.append((url, payload))

    notifier = DiscordNotifier("https://discord.com/api/webhooks/123456/abcdef")
    stub = StubClient()
    notifier.client = stub  # type: ignore[assignment]

    notifier.notify(
        campground_id="256892",
        campground_name="Simpson Springs Campground",
        matches=[
            AvailabilityMatch(
                campsite_id="10041494",
                campsite_name="Simpson Springs Group Site",
                date=date(2026, 3, 5),
                status="Available",
            ),
            AvailabilityMatch(
                campsite_id="10041494",
                campsite_name="Simpson Springs Group Site",
                date=date(2026, 3, 6),
                status="Available",
            ),
        ],
    )

    assert len(stub.calls) == 1
    _, payload = stub.calls[0]
    content = payload["content"]
    assert content.count("Site: Simpson Springs Group Site") == 1
    assert "Dates: 3/5/2026, 3/6/2026" in content


def test_notify_splits_content_into_multiple_messages_when_too_long() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def post_json(self, url: str, payload: dict) -> None:
            self.calls.append((url, payload))

    notifier = DiscordNotifier("https://discord.com/api/webhooks/123456/abcdef")
    stub = StubClient()
    notifier.client = stub  # type: ignore[assignment]

    matches = [
        AvailabilityMatch(
            campsite_id=str(10000000 + i),
            campsite_name=f"SITE-{i:03d}",
            date=date(2026, 3, 5),
            status="Available",
        )
        for i in range(120)
    ]

    notifier.notify(
        campground_id="256892",
        campground_name="Simpson Springs Campground",
        matches=matches,
    )

    assert len(stub.calls) > 1
    header_count = 0
    for _, payload in stub.calls:
        assert len(payload["content"]) <= 2000
        header_count += payload["content"].count(
            "Availability found for Simpson Springs Campground (256892)"
        )
    assert header_count == 1
