import json
from datetime import date, datetime

import pytest

from recgov_monitor.cli import (
    build_parser,
    compute_sleep_seconds,
    format_poll_timestamp,
    has_full_site_match,
    is_rate_limited_error,
    looks_like_plain_username_tag,
    load_campground_catalog,
    load_monitor_requests,
    parse_campground_ids,
    parse_stay_dates,
)
from recgov_monitor.models import AvailabilityMatch


def test_parse_campground_ids_parses_csv() -> None:
    parsed = parse_campground_ids("256892, 232447")
    assert parsed == ["256892", "232447"]


def test_parse_campground_ids_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_campground_ids(" , ")


def test_parse_stay_dates_generates_nights_between_check_in_and_out() -> None:
    parsed = parse_stay_dates("2026-07-05", "2026-07-08")
    assert parsed == {date(2026, 7, 5), date(2026, 7, 6), date(2026, 7, 7)}


def test_parse_stay_dates_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        parse_stay_dates("2026-07-05", "2026-07-05")


def test_load_monitor_requests_from_json_config(tmp_path) -> None:
    config_path = tmp_path / "monitor.json"
    config_path.write_text(
        json.dumps(
            {
                "discord_webhook_url": "https://discord.com/api/webhooks/test",
                "poll_seconds": 45,
                "monitors": [
                    {
                        "campground_ids": [256892],
                        "check_in": "2026-03-05",
                        "check_out": "2026-03-07",
                        "discord_tag": "@user1",
                        "full_matches_only": True,
                    },
                    {
                        "campground_ids": [251869, 232492],
                        "check_in": "2026-07-02",
                        "check_out": "2026-07-05",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    webhook, poll_seconds, monitors = load_monitor_requests(str(config_path))

    assert webhook == "https://discord.com/api/webhooks/test"
    assert poll_seconds == 45
    assert len(monitors) == 2
    assert monitors[0].campground_ids == ["256892"]
    assert monitors[0].requested_dates == {date(2026, 3, 5), date(2026, 3, 6)}
    assert monitors[0].discord_tag == "@user1"
    assert monitors[0].full_matches_only is True
    assert monitors[1].campground_ids == ["251869", "232492"]
    assert monitors[1].requested_dates == {date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 4)}
    assert monitors[1].discord_tag is None
    assert monitors[1].full_matches_only is False


def test_is_rate_limited_error_detects_429() -> None:
    assert is_rate_limited_error("HTTP Error 429: Too Many Requests")


def test_is_rate_limited_error_ignores_other_errors() -> None:
    assert not is_rate_limited_error("HTTP Error 403: Forbidden")


def test_compute_sleep_seconds_aligns_60s_poll_to_second_5() -> None:
    sleep_seconds = compute_sleep_seconds(60, now=datetime(2026, 2, 21, 18, 4, 18))
    assert sleep_seconds == pytest.approx(47.0)


def test_compute_sleep_seconds_moves_to_next_minute_when_past_second_5() -> None:
    sleep_seconds = compute_sleep_seconds(60, now=datetime(2026, 2, 21, 18, 5, 7))
    assert sleep_seconds == pytest.approx(58.0)


def test_compute_sleep_seconds_keeps_non_60_polls_unchanged() -> None:
    assert compute_sleep_seconds(45, now=datetime(2026, 2, 21, 18, 4, 18)) == 45.0


def test_format_poll_timestamp_matches_expected_style() -> None:
    stamp = format_poll_timestamp(now=datetime(2026, 2, 20, 18, 5, 5))
    assert stamp == "2/20/26 6:05:05 PM"


def test_looks_like_plain_username_tag_identifies_non_ping_style() -> None:
    assert looks_like_plain_username_tag("@kpb17")
    assert not looks_like_plain_username_tag("<@123456789012345678>")
    assert not looks_like_plain_username_tag("123456789012345678")
    assert not looks_like_plain_username_tag("@everyone")


def test_parser_uses_discord_webhook_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK", "https://discord.com/api/webhooks/from-discord-webhook")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/from-discord-webhook-url")
    parser = build_parser()
    args = parser.parse_args([])
    assert args.discord_webhook_url == "https://discord.com/api/webhooks/from-discord-webhook"


def test_parser_uses_discord_logger_webhook_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DISCORD_LOGGER_WEBHOOK",
        "https://discord.com/api/webhooks/from-discord-logger-webhook",
    )
    parser = build_parser()
    args = parser.parse_args([])
    assert (
        args.discord_logger_webhook_url
        == "https://discord.com/api/webhooks/from-discord-logger-webhook"
    )


def test_load_monitor_requests_rejects_negative_poll_seconds(tmp_path) -> None:
    config_path = tmp_path / "monitor.json"
    config_path.write_text(
        json.dumps(
            {
                "discord_webhook_url": "https://discord.com/api/webhooks/test",
                "poll_seconds": -1,
                "monitors": [
                    {
                        "campground_ids": [256892],
                        "check_in": "2026-03-05",
                        "check_out": "2026-03-07",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="'poll_seconds' must be a non-negative integer."):
        load_monitor_requests(str(config_path))


def test_load_monitor_requests_rejects_campground_name_mapping(tmp_path) -> None:
    config_path = tmp_path / "monitor.json"
    config_path.write_text(
        json.dumps(
            {
                "discord_webhook_url": "https://discord.com/api/webhooks/test",
                "campground_names": {"256892": "Simpson Springs Campground"},
                "monitors": [
                    {
                        "campground_ids": [256892],
                        "check_in": "2026-03-05",
                        "check_out": "2026-03-07",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="'campground_names' is no longer supported"):
        load_monitor_requests(str(config_path))


def test_load_campground_catalog_reads_name_mapping(tmp_path) -> None:
    catalog_path = tmp_path / "campgrounds.json"
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "name": "Simpson Springs Campground",
                    "id": 256892,
                    "url": "https://www.recreation.gov/camping/campgrounds/256892",
                }
            ]
        ),
        encoding="utf-8",
    )

    names = load_campground_catalog(str(catalog_path))
    assert names == {"256892": "Simpson Springs Campground"}


def test_has_full_site_match_detects_full_coverage() -> None:
    requested = {date(2026, 6, 18), date(2026, 6, 19)}
    matches = [
        AvailabilityMatch(
            campsite_id="1001",
            campsite_name="001",
            date=date(2026, 6, 18),
            status="Available",
        ),
        AvailabilityMatch(
            campsite_id="1001",
            campsite_name="001",
            date=date(2026, 6, 19),
            status="Available",
        ),
    ]
    assert has_full_site_match(matches, requested)


def test_has_full_site_match_rejects_partial_only() -> None:
    requested = {date(2026, 6, 18), date(2026, 6, 19)}
    matches = [
        AvailabilityMatch(
            campsite_id="1001",
            campsite_name="001",
            date=date(2026, 6, 18),
            status="Available",
        ),
        AvailabilityMatch(
            campsite_id="1002",
            campsite_name="002",
            date=date(2026, 6, 19),
            status="Available",
        ),
    ]
    assert not has_full_site_match(matches, requested)
