"""The two tools the agent exposes to Claude, plus their Python implementations.

1. get_travel_time(from_stop, to_stop) -> looks up the precomputed routing matrix.
2. validate_constraints(itinerary)     -> checks time windows, visit durations,
   travel feasibility between consecutive stops, and the total trip budget.

validate_constraints is also called directly (outside the tool-use loop) by the
agent after Claude returns a final plan, so the plan is checked independently
rather than only trusting Claude's self-reported "violations" field.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import Stop, format_minutes, try_parse_hhmm
from .routing import TravelMatrix

GET_TRAVEL_TIME_TOOL = {
    "name": "get_travel_time",
    "description": (
        "Look up the real-world travel duration (minutes) and distance (km) between "
        "two stops, using precomputed routing data. Stop names must exactly match "
        "the names given in the problem statement."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "from_stop": {"type": "string", "description": "Name of the departure stop."},
            "to_stop": {"type": "string", "description": "Name of the destination stop."},
        },
        "required": ["from_stop", "to_stop"],
        "additionalProperties": False,
    },
}

VALIDATE_CONSTRAINTS_TOOL = {
    "name": "validate_constraints",
    "description": (
        "Validate a candidate itinerary against every stop's time window, its "
        "required visit duration, travel feasibility between consecutive stops, "
        "and the overall trip time budget. Returns a list of violation strings; "
        "an empty list means the itinerary is fully valid. Call this before giving "
        "your final answer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "itinerary": {
                "type": "array",
                "description": "Ordered list of stops with planned arrival/departure times.",
                "items": {
                    "type": "object",
                    "properties": {
                        "stop": {"type": "string"},
                        "arrival": {"type": "string", "description": "HH:MM 24-hour"},
                        "departure": {"type": "string", "description": "HH:MM 24-hour"},
                    },
                    "required": ["stop", "arrival", "departure"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["itinerary"],
        "additionalProperties": False,
    },
}

TOOL_DEFINITIONS = [GET_TRAVEL_TIME_TOOL, VALIDATE_CONSTRAINTS_TOOL]


def validate_constraints(
    itinerary: List[Dict],
    stops_by_name: Dict[str, Stop],
    matrix: TravelMatrix,
    total_budget_minutes: int,
) -> List[str]:
    """Independently check a proposed itinerary. Returns a list of violation messages."""
    violations: List[str] = []

    if not itinerary:
        return ["Itinerary is empty."]

    start_stops = [s for s in stops_by_name.values() if s.is_start]
    if start_stops and itinerary[0].get("stop") != start_stops[0].name:
        violations.append(f"The itinerary must begin at the start location '{start_stops[0].name}'.")

    seen = set()
    for entry in itinerary:
        name = entry.get("stop")
        if name not in stops_by_name:
            violations.append(f"Unknown stop '{name}' is not in the requested stop list.")
            continue
        if name in seen:
            violations.append(f"Stop '{name}' appears more than once in the itinerary.")
        seen.add(name)

    missing = set(stops_by_name) - seen
    if missing:
        violations.append(f"Itinerary is missing required stop(s): {', '.join(sorted(missing))}.")

    parsed_times: List[Optional[Dict]] = []
    for entry in itinerary:
        name = entry.get("stop")
        arrival = try_parse_hhmm(entry.get("arrival", ""))
        departure = try_parse_hhmm(entry.get("departure", ""))
        if arrival is None or departure is None:
            violations.append(f"Stop '{name}' has an unparseable arrival/departure time.")
            parsed_times.append(None)
            continue
        if departure < arrival:
            violations.append(f"Stop '{name}' departs ({entry['departure']}) before it arrives ({entry['arrival']}).")
        parsed_times.append({"stop": name, "arrival": arrival, "departure": departure})

        stop = stops_by_name.get(name)
        if stop is None:
            continue
        if stop.earliest is not None:
            earliest = try_parse_hhmm(stop.earliest)
            if earliest is not None and arrival is not None and arrival < earliest:
                violations.append(
                    f"Stop '{name}' arrives at {entry['arrival']}, before its earliest allowed time {stop.earliest}."
                )
        if stop.latest is not None:
            latest = try_parse_hhmm(stop.latest)
            if latest is not None and departure is not None and departure > latest:
                violations.append(
                    f"Stop '{name}' departs at {entry['departure']}, after its latest allowed time {stop.latest}."
                )
        if arrival is not None and departure is not None:
            visited_for = departure - arrival
            if visited_for < stop.duration_minutes:
                violations.append(
                    f"Stop '{name}' is only visited for {visited_for} min, "
                    f"less than its required {stop.duration_minutes} min."
                )

    valid_times = [t for t in parsed_times if t is not None]
    for i in range(len(valid_times) - 1):
        cur, nxt = valid_times[i], valid_times[i + 1]
        try:
            travel_min, _ = matrix.lookup(cur["stop"], nxt["stop"])
        except KeyError:
            continue
        earliest_possible_arrival = cur["departure"] + travel_min
        if nxt["arrival"] < earliest_possible_arrival - 0.5:  # tolerate rounding
            violations.append(
                f"Travel from '{cur['stop']}' (departs {format_minutes(cur['departure'])}) to "
                f"'{nxt['stop']}' takes ~{travel_min:.0f} min, but arrival is set to "
                f"{format_minutes(nxt['arrival'])} (needs to be at/after "
                f"{format_minutes(earliest_possible_arrival)})."
            )

    if len(valid_times) >= 2:
        total_span = valid_times[-1]["departure"] - valid_times[0]["arrival"]
        if total_span > total_budget_minutes:
            violations.append(
                f"Total trip time is {total_span} min, exceeding the {total_budget_minutes} min budget."
            )

    return violations


def execute_tool(
    name: str,
    tool_input: Dict,
    stops_by_name: Dict[str, Stop],
    matrix: TravelMatrix,
    total_budget_minutes: int,
) -> Dict:
    """Run a tool call issued by Claude and return a JSON-serializable result."""
    if name == "get_travel_time":
        from_stop, to_stop = tool_input.get("from_stop"), tool_input.get("to_stop")
        if from_stop not in stops_by_name or to_stop not in stops_by_name:
            return {"error": f"Unknown stop name(s): {from_stop!r}, {to_stop!r}"}
        duration_min, distance_km = matrix.lookup(from_stop, to_stop)
        return {
            "from_stop": from_stop,
            "to_stop": to_stop,
            "duration_minutes": round(duration_min, 1),
            "distance_km": round(distance_km, 2),
        }

    if name == "validate_constraints":
        itinerary = tool_input.get("itinerary", [])
        violations = validate_constraints(itinerary, stops_by_name, matrix, total_budget_minutes)
        return {"valid": len(violations) == 0, "violations": violations}

    return {"error": f"Unknown tool '{name}'"}
