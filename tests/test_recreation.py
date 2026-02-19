from datetime import date

from recbot2.recreation import find_available_campsites


def test_find_available_campsites_filters_requested_days_and_status() -> None:
    payload = {
        "campsites": {
            "123": {
                "site": "A1",
                "availabilities": {
                    "2026-07-05T00:00:00Z": "Available",
                    "2026-07-06T00:00:00Z": "Not Available",
                },
            },
            "124": {
                "site": "A2",
                "availabilities": {
                    "2026-07-05T00:00:00Z": "Reserveable",
                },
            },
        }
    }

    matches = find_available_campsites(payload, {date(2026, 7, 5), date(2026, 7, 6)})

    assert len(matches) == 2
    assert {m.campsite_name for m in matches} == {"A1", "A2"}
