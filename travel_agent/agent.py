"""The Travel Optimization AI Agent: a Claude tool-use loop that iteratively
proposes, validates and refines a route.

Outer loop (max 3 iterations):
    1. Ask Claude to optimize the route (it may call get_travel_time /
       validate_constraints any number of times along the way).
    2. Parse Claude's final JSON itinerary.
    3. Independently re-validate it in Python (never just trust the model).
    4. If violations remain and iterations are left, tell Claude exactly what
       failed and ask it to try again; otherwise stop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import anthropic

from .models import Stop
from .routing import TravelMatrix
from .tools import TOOL_DEFINITIONS, execute_tool, validate_constraints

DEFAULT_MODEL = "claude-opus-5"
MAX_ITERATIONS = 3
MAX_TOOL_ROUNDS_PER_ITERATION = 6  # guards against a runaway tool-call loop

SYSTEM_PROMPT = """You are a Travel Optimization AI Agent. You sequence a fixed list of \
stops into the best possible visiting order, respecting each stop's time window and the \
overall trip time budget.

You have two tools:
- get_travel_time(from_stop, to_stop): the authoritative real-world travel time/distance \
between two stops. Use it instead of guessing travel times.
- validate_constraints(itinerary): checks a candidate itinerary for violations. Call it on \
your proposed plan BEFORE giving your final answer, and fix any violations it reports.

Rules:
- Every stop given to you must appear exactly once in the itinerary.
- "arrival" and "departure" are HH:MM 24-hour times. departure - arrival must be at least \
that stop's required visit duration.
- A stop's arrival must be at/after its earliest time and its departure at/before its \
latest time, when those are given.
- Departure at one stop plus the travel time to the next stop must be at/before the next \
stop's arrival time.
- The total trip span (first arrival to last departure) must fit within the given time budget.

When you are done reasoning and tool-calling, respond with ONLY a single JSON object \
(no markdown fences, no commentary before or after it) in exactly this shape:
{
  "optimized_stops": ["stop name 1", "stop name 2", ...],
  "timing": [{"stop": "...", "arrival": "HH:MM", "departure": "HH:MM"}, ...],
  "total_time": "H h M min",
  "violations": [],
  "confidence": 0.0,
  "explanation": "one short paragraph justifying the ordering"
}
Set "violations" to your own best-effort list (it will be independently re-checked), and \
"confidence" to a 0.0-1.0 estimate of how good and how feasible this plan is.
"""


@dataclass
class ToolCallRecord:
    name: str
    input: Dict
    result: Dict


@dataclass
class IterationRecord:
    index: int
    thinking: List[str] = field(default_factory=list)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    raw_text: Optional[str] = None
    parsed_plan: Optional[Dict] = None
    violations: List[str] = field(default_factory=list)
    parse_error: Optional[str] = None


@dataclass
class OptimizationResult:
    success: bool
    final_plan: Optional[Dict]
    iterations: List[IterationRecord]
    model: str


def _build_user_prompt(
    stops: List[Stop], start_time: str, total_budget_minutes: int, matrix: TravelMatrix
) -> str:
    lines = [
        f"Trip start time: {start_time}",
        f"Total time budget: {total_budget_minutes} minutes",
        "",
        "Stops (name | earliest | latest | required visit duration):",
    ]
    for s in stops:
        earliest = s.earliest or "none"
        latest = s.latest or "none"
        lines.append(f"- {s.name} | earliest={earliest} | latest={latest} | duration={s.duration_minutes} min")

    lines.append("")
    lines.append("Precomputed travel times (minutes) between every pair of stops:")
    for a in stops:
        for b in stops:
            if a.name == b.name:
                continue
            duration, distance = matrix.lookup(a.name, b.name)
            lines.append(f"- {a.name} -> {b.name}: {duration:.1f} min ({distance:.1f} km)")
    if matrix.warning:
        lines.append("")
        lines.append(f"Note on travel data: {matrix.warning}")

    lines.append("")
    lines.append(
        "Find the best order to visit ALL of these stops starting at the trip start time, "
        "respecting every constraint above, and return the final JSON described in your instructions."
    )
    return "\n".join(lines)


def _extract_json(text: str) -> Dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in Claude's response.")


class TravelOptimizationAgent:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, max_iterations: int = MAX_ITERATIONS):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_iterations = max_iterations

    def optimize(
        self,
        stops: List[Stop],
        start_time: str,
        total_budget_minutes: int,
        matrix: TravelMatrix,
    ) -> OptimizationResult:
        stops_by_name = {s.name: s for s in stops}
        messages: List[Dict] = [
            {"role": "user", "content": _build_user_prompt(stops, start_time, total_budget_minutes, matrix)}
        ]

        iterations: List[IterationRecord] = []
        final_plan: Optional[Dict] = None
        success = False

        for i in range(1, self.max_iterations + 1):
            record = IterationRecord(index=i)
            response = None

            for _ in range(MAX_TOOL_ROUNDS_PER_ITERATION):
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    thinking={"type": "adaptive", "display": "summarized"},
                    system=SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                )

                for block in response.content:
                    if block.type == "thinking" and getattr(block, "thinking", ""):
                        record.thinking.append(block.thinking)
                    elif block.type == "text" and block.text.strip():
                        record.raw_text = block.text

                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    break

                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
                tool_results = []
                for tb in tool_use_blocks:
                    result = execute_tool(tb.name, tb.input, stops_by_name, matrix, total_budget_minutes)
                    record.tool_calls.append(ToolCallRecord(name=tb.name, input=tb.input, result=result))
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": tb.id, "content": json.dumps(result)}
                    )
                messages.append({"role": "user", "content": tool_results})

            if record.raw_text:
                try:
                    record.parsed_plan = _extract_json(record.raw_text)
                except (ValueError, json.JSONDecodeError) as exc:
                    record.parse_error = str(exc)
            else:
                record.parse_error = "Claude did not return a text response."

            if record.parsed_plan is not None:
                final_plan = record.parsed_plan
                record.violations = validate_constraints(
                    record.parsed_plan.get("timing", []), stops_by_name, matrix, total_budget_minutes
                )
            else:
                record.violations = [record.parse_error or "Could not parse a plan."]

            iterations.append(record)

            if not record.violations:
                success = True
                break

            if i < self.max_iterations:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "These constraints failed: "
                            f"{json.dumps(record.violations)}. "
                            "Fix the itinerary and try again. Respond again with ONLY the final JSON object."
                        ),
                    }
                )

        return OptimizationResult(success=success, final_plan=final_plan, iterations=iterations, model=self.model)
