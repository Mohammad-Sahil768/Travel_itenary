"""Address -> coordinates geocoding via OpenStreetMap Nominatim (free, no API key).

Nominatim's usage policy caps unauthenticated use at ~1 request/second and
requires a descriptive User-Agent, so calls go through a RateLimiter, and
results are cached in-process so re-optimizing after editing a couple of
stops only geocodes what actually changed.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from .models import Stop

_USER_AGENT = "travel-optimization-ai-agent"

_geolocator = Nominatim(user_agent=_USER_AGENT)
_geocode = RateLimiter(_geolocator.geocode, min_delay_seconds=1.0, max_retries=2, error_wait_seconds=2.0)

# address (lowercased, stripped) -> (lat, lon, resolved display address)
_cache: Dict[str, Tuple[float, float, str]] = {}


class GeocodingError(Exception):
    """Raised when an address can't be resolved to coordinates."""


def geocode_address(address: str, timeout: float = 10.0) -> Tuple[float, float, str]:
    """Resolve a free-text address/place name to (lat, lon, display_address).

    Cached per unique address string for the lifetime of the process.
    """
    key = address.strip().lower()
    if not key:
        raise GeocodingError("Address is empty.")
    if key in _cache:
        return _cache[key]

    try:
        location = _geocode(address, timeout=timeout)
    except GeocoderTimedOut as exc:
        raise GeocodingError(f"Geocoding timed out for '{address}'. Try again.") from exc
    except GeocoderServiceError as exc:
        raise GeocodingError(f"Geocoding service error for '{address}': {exc}") from exc

    if location is None:
        raise GeocodingError(
            f"Could not find a location for '{address}'. Try a more specific address "
            "(e.g. add a city and state/country)."
        )

    result = (location.latitude, location.longitude, location.address)
    _cache[key] = result
    return result


def geocode_stops(stops: List[Stop]) -> List[str]:
    """Geocode every stop's address in place (fills lat/lon/display_address).

    Returns a list of "<stop name>: <reason>" error strings for stops that
    could not be geocoded; an empty list means every stop resolved.
    """
    errors: List[str] = []
    for stop in stops:
        if not stop.address or not stop.address.strip():
            errors.append(f"{stop.name}: no address given.")
            continue
        try:
            lat, lon, display = geocode_address(stop.address)
        except GeocodingError as exc:
            errors.append(f"{stop.name}: {exc}")
            continue
        stop.lat, stop.lon, stop.display_address = lat, lon, display
    return errors


def clear_cache() -> None:
    _cache.clear()
