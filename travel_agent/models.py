"""Data structures and small time-arithmetic helpers shared across the agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Stop:
    """A single itinerary stop."""

    name: str
    address: str = ""  # free-text address/place name, geocoded before routing
    lat: Optional[float] = None  # filled in by geocoding.geocode_stops()
    lon: Optional[float] = None
    display_address: Optional[str] = None  # the geocoder's resolved/normalized address
    earliest: Optional[str] = None  # "HH:MM", inclusive lower bound on arrival
    latest: Optional[str] = None  # "HH:MM", inclusive upper bound on departure
    duration_minutes: int = 30  # time to spend at the stop
    is_start: bool = False  # a fixed depot/origin that must be first in the itinerary

    def has_coords(self) -> bool:
        return self.lat is not None and self.lon is not None


_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> int:
    """Parse "HH:MM" into minutes since midnight. Raises ValueError if malformed."""
    if not isinstance(value, str):
        raise ValueError(f"Time must be a string like '14:30', got {value!r}")
    match = _HHMM_RE.match(value.strip())
    if not match:
        raise ValueError(f"Time '{value}' is not in HH:MM 24-hour format")
    hours, minutes = int(match.group(1)), int(match.group(2))
    return hours * 60 + minutes


def format_minutes(total_minutes: int) -> str:
    """Format minutes since midnight as "HH:MM" (wraps past 24h onto the next day)."""
    total_minutes = int(round(total_minutes)) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def try_parse_hhmm(value: str) -> Optional[int]:
    try:
        return parse_hhmm(value)
    except ValueError:
        return None


def to_12h(value: str) -> str:
    """Format a "HH:MM" 24-hour string as "H:MM AM/PM" for display. Returns the
    input unchanged if it doesn't parse."""
    minutes = try_parse_hhmm(value)
    if minutes is None:
        return value
    hour24, minute = divmod(minutes, 60)
    period = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12 or 12
    return f"{hour12}:{minute:02d} {period}"
