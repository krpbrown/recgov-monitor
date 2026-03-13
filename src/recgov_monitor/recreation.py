from __future__ import annotations

from datetime import date

from recgov_monitor.http import HttpClient
from recgov_monitor.models import AvailabilityMatch, TicketAvailabilityMatch

AVAILABLE_STATUSES = {"Available", "Reserveable"}


class RecreationGovClient:
    """Client for retrieving campsite availability from recreation.gov."""

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.client = HttpClient(timeout_seconds=timeout_seconds)

    def fetch_month(self, campground_id: str, month_start: date) -> dict:
        url = (
            "https://www.recreation.gov/api/camps/availability/"
            f"campground/{campground_id}/month"
        )
        params = {"start_date": f"{month_start.isoformat()}T00:00:00.000Z"}
        return self.client.get_json(url, params=params)

    def fetch_ticket_day(self, facility_id: str, day: date) -> dict:
        url = f"https://www.recreation.gov/api/ticket/availability/facility/{facility_id}"
        params = {"date": day.isoformat()}
        return self.client.get_json(url, params=params)


def extract_campground_name(payload: dict, fallback: str) -> str:
    """Best-effort campground name extraction from recreation.gov payload."""

    for key in ("campground_name", "facility_name", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    campground = payload.get("campground")
    if isinstance(campground, dict):
        for key in ("name", "facility_name", "campground_name"):
            value = campground.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return fallback


def find_available_campsites(payload: dict, requested_dates: set[date]) -> list[AvailabilityMatch]:
    """Extract all campsites that are available on requested dates."""

    matches: list[AvailabilityMatch] = []
    for campsite_id, campsite_data in payload.get("campsites", {}).items():
        campsite_name = campsite_data.get("site", campsite_id)
        availabilities = campsite_data.get("availabilities", {})
        for iso_datetime, status in availabilities.items():
            try:
                day = date.fromisoformat(iso_datetime.split("T")[0])
            except ValueError:
                continue
            if day in requested_dates and status in AVAILABLE_STATUSES:
                matches.append(
                    AvailabilityMatch(
                        campsite_id=campsite_id,
                        campsite_name=campsite_name,
                        date=day,
                        status=status,
                    )
                )

    return sorted(matches, key=lambda item: (item.date, item.campsite_name))


def find_available_ticket_slots(
    payload: dict,
    ticket_id: str,
    target_date: date,
) -> list[TicketAvailabilityMatch]:
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict):
        return []

    ticket_data = inventory.get(str(ticket_id))
    if ticket_data is None:
        try:
            ticket_data = inventory.get(str(int(ticket_id)))
        except ValueError:
            ticket_data = None
    if not isinstance(ticket_data, dict):
        return []

    slots: list[TicketAvailabilityMatch] = []
    for slot_id, slot_data in _iter_ticket_slots(ticket_data):
        remaining = _extract_remaining(slot_data)
        if remaining is not None and remaining <= 0:
            continue
        if remaining is None and not _looks_available(slot_data):
            continue
        label = _extract_slot_label(slot_id, slot_data)
        slots.append(
            TicketAvailabilityMatch(
                slot_id=str(slot_id),
                slot_label=label,
                date=target_date,
                remaining=remaining,
            )
        )

    return sorted(slots, key=lambda item: item.slot_label)


def _iter_ticket_slots(ticket_data: dict) -> list[tuple[str, dict]]:
    for key in ("buckets", "timeslots", "slots", "availability", "availabilities"):
        value = ticket_data.get(key)
        if isinstance(value, dict):
            result: list[tuple[str, dict]] = []
            for slot_id, slot_data in value.items():
                if isinstance(slot_data, dict):
                    result.append((str(slot_id), slot_data))
                else:
                    result.append((str(slot_id), {"value": slot_data}))
            return result
    return []


def _extract_remaining(slot_data: dict) -> int | None:
    for key in ("remaining", "available", "remaining_count", "quantity", "spots", "value"):
        value = slot_data.get(key)
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _looks_available(slot_data: dict) -> bool:
    for key in ("is_available", "available", "enabled", "bookable"):
        value = slot_data.get(key)
        if isinstance(value, bool):
            return value
    status = slot_data.get("status")
    if isinstance(status, str):
        return status.lower() in {"available", "open", "reserveable", "reservable"}
    return False


def _extract_slot_label(slot_id: str, slot_data: dict) -> str:
    for key in ("display", "name", "label", "start_time", "time"):
        value = slot_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return slot_id
