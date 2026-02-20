from __future__ import annotations

from datetime import date

from recgov_monitor.http import HttpClient
from recgov_monitor.models import AvailabilityMatch

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
