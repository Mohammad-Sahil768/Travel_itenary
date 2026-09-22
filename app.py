"""Streamlit app: Travel Optimization AI Agent.

Lets a user define stops as real addresses with real time windows, geocodes
them, fetches real travel times from a routing API, then runs a Claude
tool-use agent that iteratively proposes, validates, and refines an
optimized itinerary.
"""

from __future__ import annotations

import os
from datetime import date, time as dtime
from typing import Optional

import pandas as pd
import streamlit as st

from travel_agent.agent import MAX_ITERATIONS, TravelOptimizationAgent
from travel_agent.geocoding import geocode_stops
from travel_agent.models import Stop, to_12h, try_parse_hhmm
from travel_agent.routing import get_travel_matrix
from travel_agent.sample_data import get_scenario_names, load_scenario

st.set_page_config(page_title="Travel Optimization AI Agent", page_icon="🧭", layout="wide")

MODEL_OPTIONS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


def _default_api_key() -> str:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _hhmm_to_time(value: Optional[str]) -> Optional[dtime]:
    minutes = try_parse_hhmm(value) if value else None
    if minutes is None:
        return None
    return dtime(minutes // 60, minutes % 60)


def _time_to_hhmm(value) -> Optional[str]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, dtime):
        return value.strftime("%H:%M")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stops_to_df(stops: list[Stop]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "name": s.name,
                "address": s.address,
                "earliest": _hhmm_to_time(s.earliest) or dtime(0, 0),
                "latest": _hhmm_to_time(s.latest) or dtime(23, 59),
                "duration_minutes": s.duration_minutes,
            }
            for s in stops
        ]
    )


def _df_to_stops(df: pd.DataFrame) -> list[Stop]:
    stops = []
    for _, row in df.iterrows():
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        stops.append(
            Stop(
                name=name,
                address=str(row.get("address") or "").strip(),
                earliest=_time_to_hhmm(row.get("earliest")),
                latest=_time_to_hhmm(row.get("latest")),
                duration_minutes=int(row.get("duration_minutes") or 30),
            )
        )
    return stops


def _validate_stop_inputs(stops: list[Stop], total_budget_minutes: int) -> list[str]:
    errors = []
    if len(stops) < 2:
        errors.append("Add at least 2 stops.")
    names = [s.name for s in stops]
    if len(names) != len(set(names)):
        errors.append("Stop names must be unique.")
    for s in stops:
        if not s.address:
            errors.append(f"Stop '{s.name}': an address/place name is required so it can be geocoded.")
        if s.earliest is None or s.latest is None:
            errors.append(f"Stop '{s.name}': both an earliest and a latest time are required.")
        elif try_parse_hhmm(s.earliest) > try_parse_hhmm(s.latest):
            errors.append(f"Stop '{s.name}': earliest time must be before latest time.")
        if s.duration_minutes <= 0:
            errors.append(f"Stop '{s.name}': visit duration must be positive.")
    if total_budget_minutes <= 0:
        errors.append("Total time budget must be positive.")
    return errors


# --- session state defaults -------------------------------------------------

if "stops_df" not in st.session_state:
    initial = load_scenario(get_scenario_names()[0])
    st.session_state.stops_df = _stops_to_df(initial["stops"])
    st.session_state.start_time = initial["start_time"]
    st.session_state.total_budget_minutes = initial["total_budget_minutes"]

if "trip_date" not in st.session_state:
    st.session_state.trip_date = date.today()
if "result" not in st.session_state:
    st.session_state.result = None
if "matrix" not in st.session_state:
    st.session_state.matrix = None
if "used_stops" not in st.session_state:
    st.session_state.used_stops = None
if "geocode_errors" not in st.session_state:
    st.session_state.geocode_errors = None


# --- sidebar: trip setup -----------------------------------------------------

with st.sidebar:
    st.header("🧭 Trip Setup")

    scenario_name = st.selectbox("Sample scenario", get_scenario_names())
    if st.button("Load scenario", use_container_width=True):
        scenario = load_scenario(scenario_name)
        st.session_state.stops_df = _stops_to_df(scenario["stops"])
        st.session_state.start_time = scenario["start_time"]
        st.session_state.total_budget_minutes = scenario["total_budget_minutes"]
        st.session_state.result = None
        st.rerun()

    st.divider()
    st.subheader("Stops")
    st.caption(
        "Enter a real address or place name per stop (e.g. \"Times Square, New York, NY\"). "
        "It's geocoded to coordinates automatically when you click Optimize. Well-known "
        "landmarks usually work, but if one fails to geocode, use its street address instead "
        "(e.g. \"30 Rockefeller Plaza, New York, NY\" rather than \"Top of the Rock\")."
    )
    edited_df = st.data_editor(
        st.session_state.stops_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Name", required=True),
            "address": st.column_config.TextColumn("Address / Place", required=True, width="large"),
            "earliest": st.column_config.TimeColumn("Earliest", format="hh:mm a", step=300, required=True),
            "latest": st.column_config.TimeColumn("Latest", format="hh:mm a", step=300, required=True),
            "duration_minutes": st.column_config.NumberColumn("Visit (min)", min_value=1, step=5),
        },
        key="stops_editor",
    )
    st.session_state.stops_df = edited_df

    st.divider()
    trip_date = st.date_input("Trip date", value=st.session_state.trip_date)
    start_time_widget = st.time_input(
        "Trip start time", value=_hhmm_to_time(st.session_state.start_time) or dtime(9, 0)
    )
    start_time = start_time_widget.strftime("%H:%M")
    total_budget_minutes = st.number_input(
        "Total time budget (minutes)", min_value=1, value=int(st.session_state.total_budget_minutes), step=15
    )

    st.divider()
    st.subheader("Routing API")
    engine_label = st.radio(
        "Travel-time source",
        ["OSRM (free, public demo server)", "OpenRouteService (needs API key)", "Estimated (no network call)"],
        index=0,
    )
    engine = {
        "OSRM (free, public demo server)": "osrm",
        "OpenRouteService (needs API key)": "ors",
        "Estimated (no network call)": "estimated",
    }[engine_label]
    ors_api_key = ""
    if engine == "ors":
        ors_api_key = st.text_input("OpenRouteService API key", type="password")
    st.caption(
        "Note: free routing APIs return typical driving times, not live/predicted traffic for a "
        "specific future date and time — treat travel times as good estimates, not guarantees."
    )

    st.divider()
    st.subheader("Claude Agent")
    api_key = st.text_input("Anthropic API key", value=_default_api_key(), type="password")
    model = st.selectbox("Model", MODEL_OPTIONS, index=0)

    st.divider()
    optimize_clicked = st.button("🚀 Optimize Route", type="primary", use_container_width=True)


# --- main area ----------------------------------------------------------------

st.title("🧭 Travel Optimization AI Agent")
st.caption(
    "Give it real addresses with time windows, geocode + fetch real travel times, and let a Claude "
    f"agent iteratively sequence and validate the itinerary (up to {MAX_ITERATIONS} refinement passes)."
)

if optimize_clicked:
    st.session_state.trip_date = trip_date
    st.session_state.start_time = start_time
    st.session_state.total_budget_minutes = int(total_budget_minutes)

    stops = _df_to_stops(st.session_state.stops_df)
    errors = _validate_stop_inputs(stops, int(total_budget_minutes))
    if not api_key:
        errors.append("An Anthropic API key is required.")
    if errors:
        st.session_state.result = None
        for e in errors:
            st.error(e)
    else:
        with st.spinner("Geocoding addresses..."):
            geocode_errors = geocode_stops(stops)
        st.session_state.geocode_errors = geocode_errors

        if geocode_errors:
            st.session_state.result = None
            st.error("Could not geocode the following stop(s) — fix the address and try again:")
            for e in geocode_errors:
                st.error(f"• {e}")
        else:
            with st.spinner(f"Fetching travel times ({engine.upper()})..."):
                matrix = get_travel_matrix(stops, engine=engine, ors_api_key=ors_api_key)
            if matrix.warning:
                st.warning(matrix.warning)

            with st.spinner(f"Running optimization agent ({model})..."):
                agent = TravelOptimizationAgent(api_key=api_key, model=model)
                try:
                    result = agent.optimize(
                        stops=stops,
                        start_time=start_time,
                        total_budget_minutes=int(total_budget_minutes),
                        matrix=matrix,
                        trip_date=trip_date.strftime("%A, %B %d, %Y"),
                    )
                    st.session_state.result = result
                    st.session_state.matrix = matrix
                    st.session_state.used_stops = stops
                except Exception as exc:  # surfaced to the user, not swallowed
                    st.session_state.result = None
                    st.exception(exc)


result = st.session_state.result

if result is not None:
    stops = st.session_state.used_stops
    matrix = st.session_state.matrix
    stops_by_name = {s.name: s for s in stops}

    st.caption(f"📅 Trip date: **{st.session_state.trip_date.strftime('%A, %B %d, %Y')}**")

    with st.expander("📍 Geocoded locations"):
        for s in stops:
            st.markdown(f"- **{s.name}** → `{s.address}` resolved to *{s.display_address}* ({s.lat:.5f}, {s.lon:.5f})")

    st.subheader("🧠 Agent Reasoning")
    for record in result.iterations:
        status = "✅ valid" if not record.violations else f"⚠️ {len(record.violations)} violation(s)"
        with st.expander(f"Iteration {record.index} — {status}", expanded=(record.index == len(result.iterations))):
            if record.thinking:
                st.markdown("**Reasoning summary:**")
                for chunk in record.thinking:
                    st.markdown(f"> {chunk}")
            if record.tool_calls:
                st.markdown("**Tool calls:**")
                for tc in record.tool_calls:
                    st.markdown(f"- `{tc.name}({tc.input})` → `{tc.result}`")
            if record.violations:
                st.markdown("**Violations reported back to Claude:**")
                for v in record.violations:
                    st.markdown(f"- {v}")
            if record.parse_error:
                st.error(f"Parse error: {record.parse_error}")
            if record.raw_text:
                st.markdown("**Claude's response:**")
                st.code(record.raw_text, language="json")

    st.subheader("📋 Optimized Itinerary")

    if result.final_plan is None:
        st.error("The agent could not produce a parseable itinerary. See the reasoning steps above.")
    else:
        plan = result.final_plan
        timing = plan.get("timing", [])

        rows = []
        prev_stop = None
        total_travel_min = 0.0
        total_distance_km = 0.0
        for idx, entry in enumerate(timing, start=1):
            name = entry.get("stop", "?")
            travel_min = travel_km = None
            if prev_stop is not None:
                try:
                    travel_min, travel_km = matrix.lookup(prev_stop, name)
                    total_travel_min += travel_min
                    total_distance_km += travel_km
                except KeyError:
                    pass
            stop_obj = stops_by_name.get(name)
            rows.append(
                {
                    "Stop #": idx,
                    "Location (Address)": stop_obj.display_address if stop_obj else name,
                    "Arrival": to_12h(entry.get("arrival", "")),
                    "Service Duration": f"{stop_obj.duration_minutes} min" if stop_obj else "",
                    "Departure": to_12h(entry.get("departure", "")),
                    "Travel to Next": f"{travel_min:.0f} min" if travel_min is not None else "—",
                    "Distance": f"{travel_km:.1f} km" if travel_km is not None else "—",
                }
            )
            prev_stop = name

        itinerary_df = pd.DataFrame(rows)
        st.dataframe(itinerary_df, use_container_width=True, hide_index=True)

        final_violations = result.iterations[-1].violations if result.iterations else []
        total_service_min = sum(s.duration_minutes for s in stops)
        first_arrival = try_parse_hhmm(timing[0].get("arrival", "")) if timing else None
        last_departure = try_parse_hhmm(timing[-1].get("departure", "")) if timing else None
        grand_total_min = (last_departure - first_arrival) if (first_arrival is not None and last_departure is not None) else None

        st.markdown("**Trip summary (real data)**")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total distance", f"{total_distance_km:.1f} km")
        col2.metric("Total travel time", f"{total_travel_min:.0f} min")
        col3.metric("Total service time", f"{total_service_min} min")
        col4.metric("Grand total", f"{grand_total_min} min" if grand_total_min is not None else "n/a")
        col5.metric("Feasible?", "✅ YES" if not final_violations else "❌ NO")

        st.markdown("**Agent metrics**")
        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("Confidence", f"{float(plan.get('confidence', 0)):.0%}")
        mcol2.metric("Iterations used", f"{len(result.iterations)}/{MAX_ITERATIONS}")
        mcol3.metric("Routing source", matrix.source.upper())

        if plan.get("explanation"):
            st.markdown(f"**Explanation:** {plan['explanation']}")

        if matrix.warning:
            st.info(f"Data-quality note: {matrix.warning}")

        if final_violations:
            st.warning(
                "The final plan still has unresolved constraint violations after "
                f"{MAX_ITERATIONS} iterations:\n\n" + "\n".join(f"- {v}" for v in final_violations)
            )

        export_df = itinerary_df.copy()
        export_df.insert(0, "Date", st.session_state.trip_date.strftime("%Y-%m-%d"))
        st.download_button(
            "⬇️ Download itinerary as CSV",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name=f"optimized_itinerary_{st.session_state.trip_date.strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
        )
else:
    st.info("Set up your stops with real addresses in the sidebar and click **Optimize Route** to run the agent.")
