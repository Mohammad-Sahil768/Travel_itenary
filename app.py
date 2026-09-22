"""Streamlit app: Travel Optimization AI Agent.

Lets a user define stops (with time windows and visit durations), fetches real
travel times from a routing API, then runs a Claude tool-use agent that
iteratively proposes, validates, and refines an optimized itinerary.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from travel_agent.agent import MAX_ITERATIONS, TravelOptimizationAgent
from travel_agent.models import Stop, try_parse_hhmm
from travel_agent.routing import get_travel_matrix
from travel_agent.sample_data import get_scenario_names, load_scenario

st.set_page_config(page_title="Travel Optimization AI Agent", page_icon="🧭", layout="wide")

MODEL_OPTIONS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]

STOP_COLUMNS = ["name", "lat", "lon", "earliest", "latest", "duration_minutes"]


def _default_api_key() -> str:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _stops_to_df(stops: list[Stop]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "name": s.name,
                "lat": s.lat,
                "lon": s.lon,
                "earliest": s.earliest or "",
                "latest": s.latest or "",
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
        lat = row.get("lat")
        lon = row.get("lon")
        stops.append(
            Stop(
                name=name,
                lat=float(lat) if pd.notna(lat) else None,
                lon=float(lon) if pd.notna(lon) else None,
                earliest=(str(row.get("earliest")).strip() or None) if pd.notna(row.get("earliest")) else None,
                latest=(str(row.get("latest")).strip() or None) if pd.notna(row.get("latest")) else None,
                duration_minutes=int(row.get("duration_minutes") or 30),
            )
        )
    return stops


def _validate_stop_inputs(stops: list[Stop], start_time: str, total_budget_minutes: int) -> list[str]:
    errors = []
    if len(stops) < 2:
        errors.append("Add at least 2 stops.")
    names = [s.name for s in stops]
    if len(names) != len(set(names)):
        errors.append("Stop names must be unique.")
    if try_parse_hhmm(start_time) is None:
        errors.append(f"Start time '{start_time}' must be in HH:MM 24-hour format.")
    for s in stops:
        if s.earliest and try_parse_hhmm(s.earliest) is None:
            errors.append(f"Stop '{s.name}': earliest time '{s.earliest}' is not valid HH:MM.")
        if s.latest and try_parse_hhmm(s.latest) is None:
            errors.append(f"Stop '{s.name}': latest time '{s.latest}' is not valid HH:MM.")
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

if "result" not in st.session_state:
    st.session_state.result = None
if "matrix" not in st.session_state:
    st.session_state.matrix = None
if "used_stops" not in st.session_state:
    st.session_state.used_stops = None


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
    st.caption("Lat/lon enable real routing via OSRM/ORS; leave blank to use a rough estimate.")
    edited_df = st.data_editor(
        st.session_state.stops_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Name", required=True),
            "lat": st.column_config.NumberColumn("Lat", format="%.5f"),
            "lon": st.column_config.NumberColumn("Lon", format="%.5f"),
            "earliest": st.column_config.TextColumn("Earliest (HH:MM)"),
            "latest": st.column_config.TextColumn("Latest (HH:MM)"),
            "duration_minutes": st.column_config.NumberColumn("Visit (min)", min_value=1, step=5),
        },
        key="stops_editor",
    )
    st.session_state.stops_df = edited_df

    st.divider()
    start_time = st.text_input("Trip start time (HH:MM)", value=st.session_state.start_time)
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
    engine = {"OSRM (free, public demo server)": "osrm", "OpenRouteService (needs API key)": "ors", "Estimated (no network call)": "estimated"}[engine_label]
    ors_api_key = ""
    if engine == "ors":
        ors_api_key = st.text_input("OpenRouteService API key", type="password")

    st.divider()
    st.subheader("Claude Agent")
    api_key = st.text_input("Anthropic API key", value=_default_api_key(), type="password")
    model = st.selectbox("Model", MODEL_OPTIONS, index=0)

    st.divider()
    optimize_clicked = st.button("🚀 Optimize Route", type="primary", use_container_width=True)


# --- main area ----------------------------------------------------------------

st.title("🧭 Travel Optimization AI Agent")
st.caption(
    "Give it stops with time windows, fetch real travel times, and let a Claude agent "
    "iteratively sequence and validate the itinerary (up to "
    f"{MAX_ITERATIONS} refinement passes)."
)

if optimize_clicked:
    stops = _df_to_stops(st.session_state.stops_df)
    errors = _validate_stop_inputs(stops, start_time, int(total_budget_minutes))
    if not api_key:
        errors.append("An Anthropic API key is required.")
    if errors:
        for e in errors:
            st.error(e)
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
        for idx, entry in enumerate(timing, start=1):
            name = entry.get("stop", "?")
            travel_min = travel_km = None
            if prev_stop is not None:
                try:
                    travel_min, travel_km = matrix.lookup(prev_stop, name)
                except KeyError:
                    pass
            stop_obj = stops_by_name.get(name)
            rows.append(
                {
                    "Order": idx,
                    "Stop": name,
                    "Arrival": entry.get("arrival"),
                    "Departure": entry.get("departure"),
                    "Visit (min)": stop_obj.duration_minutes if stop_obj else None,
                    "Travel from prev (min)": round(travel_min, 1) if travel_min is not None else None,
                    "Distance from prev (km)": round(travel_km, 2) if travel_km is not None else None,
                    "Window": f"{stop_obj.earliest or 'any'}–{stop_obj.latest or 'any'}" if stop_obj else "",
                }
            )
            prev_stop = name

        itinerary_df = pd.DataFrame(rows)
        st.dataframe(itinerary_df, use_container_width=True, hide_index=True)

        final_violations = result.iterations[-1].violations if result.iterations else []
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total time", plan.get("total_time", "n/a"))
        col2.metric("Confidence", f"{float(plan.get('confidence', 0)):.0%}")
        col3.metric("Iterations used", f"{len(result.iterations)}/{MAX_ITERATIONS}")
        col4.metric("Status", "✅ Valid" if not final_violations else f"⚠️ {len(final_violations)} issue(s)")

        if plan.get("explanation"):
            st.markdown(f"**Explanation:** {plan['explanation']}")

        if final_violations:
            st.warning(
                "The final plan still has unresolved constraint violations after "
                f"{MAX_ITERATIONS} iterations:\n\n" + "\n".join(f"- {v}" for v in final_violations)
            )

        st.download_button(
            "⬇️ Download itinerary as CSV",
            data=itinerary_df.to_csv(index=False).encode("utf-8"),
            file_name="optimized_itinerary.csv",
            mime="text/csv",
        )
else:
    st.info("Set up your stops in the sidebar and click **Optimize Route** to run the agent.")
