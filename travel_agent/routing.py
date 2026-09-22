"""Routing API clients: OSRM (default, no key) and OpenRouteService (optional key),
with a haversine-distance fallback so the app still works with no network access
or when stops have no coordinates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import requests

from .models import Stop

OSRM_BASE_URL = "https://router.project-osrm.org"
ORS_BASE_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"

# Straight-line distances underestimate real road travel; a fudge factor and a
# conservative average speed keep the fallback in a realistic ballpark.
FALLBACK_ROAD_FACTOR = 1.35
FALLBACK_AVG_SPEED_KMH = 32.0


class RoutingError(Exception):
    """Raised when a routing API call fails outright (network, HTTP, bad payload)."""


@dataclass
class TravelMatrix:
    """Pairwise travel time (minutes) and distance (km) between stops, by index."""

    names: List[str]
    duration_min: List[List[float]]
    distance_km: List[List[float]]
    source: str  # "osrm" | "ors" | "estimated"
    warning: Optional[str] = None

    def lookup(self, from_name: str, to_name: str):
        try:
            i = self.names.index(from_name)
            j = self.names.index(to_name)
        except ValueError as exc:
            raise KeyError(f"Unknown stop name in travel-time lookup: {exc}") from exc
        return self.duration_min[i][j], self.distance_km[i][j]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _estimated_matrix(stops: List[Stop], warning: Optional[str] = None) -> TravelMatrix:
    n = len(stops)
    duration = [[0.0] * n for _ in range(n)]
    distance = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            a, b = stops[i], stops[j]
            if a.has_coords() and b.has_coords():
                km = haversine_km(a.lat, a.lon, b.lat, b.lon) * FALLBACK_ROAD_FACTOR
            else:
                km = 8.0  # arbitrary default hop when coordinates are missing
            distance[i][j] = km
            duration[i][j] = (km / FALLBACK_AVG_SPEED_KMH) * 60.0
    return TravelMatrix(
        names=[s.name for s in stops],
        duration_min=duration,
        distance_km=distance,
        source="estimated",
        warning=warning,
    )


def _osrm_matrix(stops: List[Stop], timeout: float = 12.0) -> TravelMatrix:
    if not all(s.has_coords() for s in stops):
        raise RoutingError("OSRM requires latitude/longitude for every stop.")
    coords = ";".join(f"{s.lon},{s.lat}" for s in stops)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords}"
    try:
        resp = requests.get(
            url, params={"annotations": "duration,distance"}, timeout=timeout
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise RoutingError(f"OSRM request failed: {exc}") from exc
    except ValueError as exc:
        raise RoutingError(f"OSRM returned invalid JSON: {exc}") from exc

    if payload.get("code") != "Ok":
        raise RoutingError(f"OSRM error: {payload.get('message', payload.get('code'))}")

    durations_s = payload.get("durations")
    distances_m = payload.get("distances")
    if durations_s is None:
        raise RoutingError("OSRM response missing 'durations' matrix.")

    n = len(stops)
    duration_min = [[(durations_s[i][j] or 0.0) / 60.0 for j in range(n)] for i in range(n)]
    if distances_m is not None:
        distance_km = [[(distances_m[i][j] or 0.0) / 1000.0 for j in range(n)] for i in range(n)]
    else:
        distance_km = [
            [
                haversine_km(stops[i].lat, stops[i].lon, stops[j].lat, stops[j].lon)
                for j in range(n)
            ]
            for i in range(n)
        ]
    return TravelMatrix(
        names=[s.name for s in stops], duration_min=duration_min, distance_km=distance_km, source="osrm"
    )


def _ors_matrix(stops: List[Stop], api_key: str, timeout: float = 12.0) -> TravelMatrix:
    if not api_key:
        raise RoutingError("OpenRouteService requires an API key.")
    if not all(s.has_coords() for s in stops):
        raise RoutingError("OpenRouteService requires latitude/longitude for every stop.")
    body = {
        "locations": [[s.lon, s.lat] for s in stops],
        "metrics": ["duration", "distance"],
    }
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(ORS_BASE_URL, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise RoutingError(f"OpenRouteService request failed: {exc}") from exc
    except ValueError as exc:
        raise RoutingError(f"OpenRouteService returned invalid JSON: {exc}") from exc

    durations_s = payload.get("durations")
    distances_m = payload.get("distances")
    if durations_s is None:
        raise RoutingError("OpenRouteService response missing 'durations' matrix.")

    n = len(stops)
    duration_min = [[(durations_s[i][j] or 0.0) / 60.0 for j in range(n)] for i in range(n)]
    if distances_m is not None:
        distance_km = [[(distances_m[i][j] or 0.0) / 1000.0 for j in range(n)] for i in range(n)]
    else:
        distance_km = [
            [
                haversine_km(stops[i].lat, stops[i].lon, stops[j].lat, stops[j].lon)
                for j in range(n)
            ]
            for i in range(n)
        ]
    return TravelMatrix(
        names=[s.name for s in stops], duration_min=duration_min, distance_km=distance_km, source="ors"
    )


def get_travel_matrix(
    stops: List[Stop], engine: str = "osrm", ors_api_key: Optional[str] = None
) -> TravelMatrix:
    """Build the pairwise travel-time/distance matrix for all stops.

    engine: "osrm" | "ors" | "estimated". Falls back to the haversine estimate
    (with a warning attached) if the chosen API is unreachable or misconfigured.
    """
    if len(stops) < 2:
        return TravelMatrix(names=[s.name for s in stops], duration_min=[[0.0]], distance_km=[[0.0]], source="n/a")

    if engine == "estimated":
        return _estimated_matrix(stops)

    try:
        if engine == "osrm":
            return _osrm_matrix(stops)
        if engine == "ors":
            return _ors_matrix(stops, ors_api_key or "")
        raise RoutingError(f"Unknown routing engine '{engine}'.")
    except RoutingError as exc:
        return _estimated_matrix(stops, warning=f"{engine.upper()} unavailable ({exc}); using estimated travel times.")
