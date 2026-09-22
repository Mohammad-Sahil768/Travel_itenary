"""Data structures and small time-arithmetic helpers shared across the agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Stop:
    """A single itinerary stop."""

    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    earliest: Optional[str] = None  # "HH:MM", inclusive lower bound on arrival
    latest: Optional[str] = None  # "HH:MM", inclusive upper bound on departure
    duration_minutes: int = 30  # time to spend at the stop

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
