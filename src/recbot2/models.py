from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AvailabilityMatch:
    """An available campsite on a specific date."""

    campsite_id: str
    campsite_name: str
    date: date
    status: str
