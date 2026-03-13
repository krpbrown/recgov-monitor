from datetime import date

from recgov_monitor.recreation import (
    extract_campground_name,
    find_available_campsites,
    find_available_ticket_slots,
)


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


def test_extract_campground_name_uses_payload_name_fields() -> None:
    payload = {"facility_name": "Simpson Springs Campground"}
    assert extract_campground_name(payload, "campground 256892") == "Simpson Springs Campground"


def test_extract_campground_name_falls_back_when_missing() -> None:
    assert extract_campground_name({}, "campground 256892") == "campground 256892"


def test_find_available_ticket_slots_parses_bucket_inventory() -> None:
    payload = {
        "inventory": {
            "10086943": {
                "buckets": {
                    "09:00 AM": {"remaining": 4},
                    "11:00 AM": {"remaining": 0},
                    "01:00 PM": {"available": True},
                }
            }
        }
    }
    matches = find_available_ticket_slots(payload, "10086943", date(2026, 6, 10))
    assert len(matches) == 2
    labels = {m.slot_label for m in matches}
    assert labels == {"09:00 AM", "01:00 PM"}
