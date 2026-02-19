import json
from datetime import date

import pytest

from recbot2.cli import load_monitor_requests, parse_campground_ids, parse_stay_dates


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
                "monitors": [
                    {
                        "campground_ids": [256892],
                        "check_in": "2026-03-05",
                        "check_out": "2026-03-07",
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

    webhook, monitors = load_monitor_requests(str(config_path))

    assert webhook == "https://discord.com/api/webhooks/test"
    assert len(monitors) == 2
    assert monitors[0].campground_ids == ["256892"]
    assert monitors[0].requested_dates == {date(2026, 3, 5), date(2026, 3, 6)}
    assert monitors[1].campground_ids == ["251869", "232492"]
    assert monitors[1].requested_dates == {date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 4)}
