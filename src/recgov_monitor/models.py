from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AvailabilityMatch:
    """An available campsite on a specific date."""

    campsite_id: str
    campsite_name: str
    date: date
    status: str


@dataclass(frozen=True)
class TicketAvailabilityMatch:
    """An available ticket slot for a specific date."""

    slot_id: str
    slot_label: str
    date: date
    remaining: int | None
