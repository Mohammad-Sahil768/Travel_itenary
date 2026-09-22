"""Streamlit app: Travel Optimization AI Agent.

Two ways to build a trip:
- Load Scenario: pick one of three preloaded sample scenarios.
- Custom Stops: build your own list of real addresses with time windows,
  plus an optional fixed start location.

Both paths converge on the same pipeline: geocode addresses, fetch real
travel times, run the tool-use agent, and render the same results. The LLM
backend is fixed (TCS GenAI Lab, via LangChain) rather than user-selectable —
the sidebar only asks for an API key.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from travel_agent.agent import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_PROVIDER, MAX_ITERATIONS, TravelOptimizationAgent
from travel_agent.geocoding import geocode_stops
from travel_agent.models import Stop, to_12h, try_parse_hhmm
from travel_agent.routing import get_travel_matrix
from travel_agent.sample_data import get_scenario_names, load_scenario

st.set_page_config(page_title="Travel Optimization AI Agent", page_icon="🧭", layout="wide")

# LLM provider/model/endpoint are fixed, not user-selectable (see sidebar).
PROVIDER = DEFAULT_PROVIDER
MODEL = DEFAULT_MODEL
BASE_URL = DEFAULT_BASE_URL

API_KEY_SECRET_NAME = "TCS_GENAI_API_KEY"


def _default_api_key() -> str:
    try:
        if API_KEY_SECRET_NAME in st.secrets:
            return st.secrets[API_KEY_SECRET_NAME]
    except Exception:
        pass
    return os.environ.get(API_KEY_SECRET_NAME, "")


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


def _format_date_time(d: date, hhmm: str) -> str:
    return f"{d.strftime('%b %d')}, {to_12h(hhmm)}"


def _format_available(minutes: int) -> str:
    hours, mins = divmod(max(minutes, 0), 60)
    return f"{hours}h" if mins == 0 else f"{hours}h {mins}m"


def _custom_entries_to_stops(entries: List[Dict]) -> List[Stop]:
    stops = []
    for i, e in enumerate(entries, start=1):
        address = (e.get("address") or "").strip()
        if not address:
            continue
        stops.append(
            Stop(
                name=f"Stop {i}",
                address=address,
                earliest=_time_to_hhmm(e.get("earliest")),
                latest=_time_to_hhmm(e.get("latest")),
                duration_minutes=int(e.get("duration_minutes") or 30),
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
        if s.duration_minutes <= 0 and not s.is_start:
            errors.append(f"Stop '{s.name}': visit duration must be positive.")
    if total_budget_minutes <= 0:
        errors.append("Total time budget must be positive.")
    return errors


def _run_pipeline(
    stops: List[Stop],
    start_time: str,
    total_budget_minutes: int,
    trip_date: date,
    end_date: date,
    end_time: str,
    api_key: str,
    engine: str,
    ors_api_key: str,
    extra_errors: Optional[List[str]] = None,
) -> None:
    """Shared geocode -> route -> optimize pipeline used by both input modes."""
    errors = list(extra_errors or [])
    errors += _validate_stop_inputs(stops, total_budget_minutes)
    if not api_key:
        errors.append("An API key is required.")
    if errors:
        st.session_state.result = None
        for e in errors:
            st.error(e)
        return

    agent = TravelOptimizationAgent(provider=PROVIDER, api_key=api_key, model=MODEL, base_url=BASE_URL)
    with st.spinner("Validating API key..."):
        key_error = agent.validate_api_key()
    if key_error:
        st.session_state.result = None
        st.error(f"API key validation failed: {key_error}")
        return

    with st.spinner("Geocoding addresses..."):
        geocode_errors = geocode_stops(stops)

    if geocode_errors:
        st.session_state.result = None
        st.error("Could not geocode the following stop(s) — fix the address and try again:")
        for e in geocode_errors:
            st.error(f"• {e}")
        return

    with st.spinner(f"Fetching travel times ({engine.upper()})..."):
        matrix = get_travel_matrix(stops, engine=engine, ors_api_key=ors_api_key)
    if matrix.warning:
        st.warning(matrix.warning)

    with st.spinner(f"Running optimization agent ({MODEL})..."):
        try:
            result = agent.optimize(
                stops=stops,
                start_time=start_time,
                total_budget_minutes=total_budget_minutes,
                matrix=matrix,
                trip_date=trip_date.strftime("%A, %B %d, %Y"),
            )
            st.session_state.result = result
            st.session_state.matrix = matrix
            st.session_state.used_stops = stops
            st.session_state.run_start_time = start_time
            st.session_state.run_end_date = end_date
            st.session_state.run_end_time = end_time
            st.session_state.run_available_minutes = total_budget_minutes
        except Exception as exc:  # surfaced to the user, not swallowed
            st.session_state.result = None
            st.exception(exc)


# --- session state defaults -------------------------------------------------

if "trip_date" not in st.session_state:
    st.session_state.trip_date = date.today()
if "start_time" not in st.session_state:
    st.session_state.start_time = dtime(9, 0)
if "end_date" not in st.session_state:
    st.session_state.end_date = date.today()
if "end_time" not in st.session_state:
    st.session_state.end_time = dtime(17, 0)
if "result" not in st.session_state:
    st.session_state.result = None
if "matrix" not in st.session_state:
    st.session_state.matrix = None
if "used_stops" not in st.session_state:
    st.session_state.used_stops = None
if "custom_stops" not in st.session_state:
    st.session_state.custom_stops = [
        {"id": 0, "address": "", "earliest": dtime(9, 0), "latest": dtime(17, 0), "duration_minutes": 30},
        {"id": 1, "address": "", "earliest": dtime(9, 0), "latest": dtime(17, 0), "duration_minutes": 30},
    ]
if "custom_next_id" not in st.session_state:
    st.session_state.custom_next_id = 2
if "custom_start_location" not in st.session_state:
    st.session_state.custom_start_location = ""


def _apply_scenario_to_widgets() -> None:
    """on_click callback for "Load This Scenario": pushes the scenario's own
    start time / budget into the shared Start/End widgets *before* this rerun
    renders them (an on_click callback runs ahead of the script body), so the
    sidebar picture stays consistent with what's about to run. A plain
    post-render assignment can't do this — Streamlit raises
    StreamlitWidgetAlreadyInstantiatedError if a key='...'-bound widget's
    session_state entry is written after that widget has already rendered
    this pass."""
    scenario = load_scenario(st.session_state["scenario_select"])
    start_t = _hhmm_to_time(scenario["start_time"])
    st.session_state.start_time = start_t
    start_dt = datetime.combine(st.session_state.trip_date, start_t)
    end_dt = start_dt + timedelta(minutes=scenario["total_budget_minutes"])
    st.session_state.end_date = end_dt.date()
    st.session_state.end_time = end_dt.time()


# --- sidebar: trip setup -----------------------------------------------------

scenario_run_stops = None
scenario_run_budget = None
custom_run_stops = None
custom_run_budget = None
custom_run_errors: List[str] = []

with st.sidebar:
    st.header("🧭 Trip Setup")

    st.caption("Start")
    sc1, sc2 = st.columns(2)
    trip_date = sc1.date_input("Start date", key="trip_date", label_visibility="collapsed")
    start_time_widget = sc2.time_input(
        "Start time", key="start_time", format="12h", label_visibility="collapsed",
    )
    start_time = start_time_widget.strftime("%H:%M")

    st.caption("End")
    ec1, ec2 = st.columns(2)
    end_date = ec1.date_input("End date", key="end_date", label_visibility="collapsed")
    end_time_widget = ec2.time_input(
        "End time", key="end_time", format="12h", label_visibility="collapsed",
    )
    end_time = end_time_widget.strftime("%H:%M")

    available_minutes = int(
        (datetime.combine(end_date, end_time_widget) - datetime.combine(trip_date, start_time_widget)).total_seconds()
        // 60
    )
    if available_minutes <= 0:
        st.error("⚠️ End date/time must be after start date/time.")
    else:
        st.caption(f"⏱️ Available time: **{_format_available(available_minutes)}**")

    st.divider()
    tab1, tab2 = st.tabs(["📂 Load Scenario", "✏️ Custom Stops"])

    with tab1:
        scenario_name = st.selectbox("Select sample scenario", get_scenario_names(), key="scenario_select")
        preview = load_scenario(scenario_name)
        with st.expander("Scenario details", expanded=True):
            st.caption(
                f"Start time: {to_12h(preview['start_time'])} · "
                f"Total budget: {preview['total_budget_minutes'] / 60:.1f} hours"
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Stop": s.name,
                            "Address": s.address,
                            "Earliest": to_12h(s.earliest),
                            "Latest": to_12h(s.latest),
                            "Visit (min)": s.duration_minutes,
                        }
                        for s in preview["stops"]
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        if st.button(
            "📂 Load This Scenario", type="primary", width="stretch", on_click=_apply_scenario_to_widgets
        ):
            # The on_click callback already pushed this scenario's start time and
            # derived end time/date into the Start/End widgets above *before* they
            # rendered this run, so trip_date/start_time/end_date/end_time (read
            # from those widgets earlier in this same pass) are already correct.
            scenario_run_stops = preview["stops"]
            scenario_run_budget = preview["total_budget_minutes"]

    with tab2:
        st.session_state.custom_start_location = st.text_input(
            "Start location (optional)",
            value=st.session_state.custom_start_location,
            placeholder="e.g. Times Square, NYC",
            help="If set, the route must begin here — real travel time to your first stop is included.",
        )
        st.caption(
            f"Uses the trip Start/End set above for the total time budget — currently "
            f"**{_format_available(available_minutes) if available_minutes > 0 else 'invalid — fix Start/End above'}**."
        )

        st.caption("Stops — address, arrival window, and how long you'll spend there:")
        for i, entry in enumerate(st.session_state.custom_stops):
            cols = st.columns([5, 2, 2, 2, 1])
            entry["address"] = cols[0].text_input(
                f"Stop {i + 1} address",
                value=entry["address"],
                key=f"custom_addr_{entry['id']}",
                placeholder="e.g. 350 5th Ave, New York, NY",
                label_visibility="collapsed" if i > 0 else "visible",
            )
            entry["earliest"] = cols[1].time_input(
                "Earliest", value=entry["earliest"], key=f"custom_earliest_{entry['id']}",
                label_visibility="collapsed" if i > 0 else "visible",
            )
            entry["latest"] = cols[2].time_input(
                "Latest", value=entry["latest"], key=f"custom_latest_{entry['id']}",
                label_visibility="collapsed" if i > 0 else "visible",
            )
            entry["duration_minutes"] = cols[3].number_input(
                "Service (min)", min_value=1, value=entry["duration_minutes"], step=5,
                key=f"custom_dur_{entry['id']}", label_visibility="collapsed" if i > 0 else "visible",
            )
            if cols[4].button("🗑️", key=f"custom_remove_{entry['id']}", help="Remove this stop"):
                st.session_state.custom_stops = [
                    e for e in st.session_state.custom_stops if e["id"] != entry["id"]
                ]
                st.rerun()

        if st.button("➕ Add Stop", width="stretch"):
            new_id = st.session_state.custom_next_id
            st.session_state.custom_stops.append(
                {"id": new_id, "address": "", "earliest": dtime(9, 0), "latest": dtime(17, 0), "duration_minutes": 30}
            )
            st.session_state.custom_next_id = new_id + 1
            st.rerun()

        st.divider()
        if st.button("🚀 Optimize", type="primary", width="stretch"):
            if available_minutes <= 0:
                custom_run_errors.append(
                    "End date/time must be after start date/time — fix the trip Start/End above."
                )
            real_stops = _custom_entries_to_stops(st.session_state.custom_stops)
            start_addr = st.session_state.custom_start_location.strip()
            if start_addr:
                real_stops = [
                    Stop(
                        name="Start Location",
                        address=start_addr,
                        earliest=start_time,
                        latest=start_time,
                        duration_minutes=0,
                        is_start=True,
                    )
                ] + real_stops
            custom_run_stops = real_stops
            custom_run_budget = max(available_minutes, 0)

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
    st.subheader("GenAI Lab")
    api_key = st.text_input("API Key", value=_default_api_key(), type="password")

    if st.button("🔑 Test API Key", width="stretch"):
        if not api_key:
            st.error("Enter an API key first.")
        else:
            test_agent = TravelOptimizationAgent(provider=PROVIDER, api_key=api_key, model=MODEL, base_url=BASE_URL)
            with st.spinner("Testing API key..."):
                test_error = test_agent.validate_api_key()
            if test_error:
                st.error(f"API key test failed: {test_error}")
            else:
                st.success("✅ API key works.")


# --- run the pipeline for whichever tab triggered it -------------------------

if scenario_run_stops is not None:
    _run_pipeline(
        scenario_run_stops, start_time, scenario_run_budget, trip_date, end_date, end_time,
        api_key, engine, ors_api_key,
    )
elif custom_run_stops is not None:
    _run_pipeline(
        custom_run_stops, start_time, custom_run_budget, trip_date, end_date, end_time,
        api_key, engine, ors_api_key,
        extra_errors=custom_run_errors,
    )


# --- main area ----------------------------------------------------------------

st.title("🧭 Travel Optimization AI Agent")
st.caption(
    "Load a sample scenario or build your own stops with real addresses and time windows, then let "
    f"an LLM agent geocode, fetch real travel times, and iteratively optimize the route (up to "
    f"{MAX_ITERATIONS} refinement passes)."
)

result = st.session_state.result

if result is not None:
    stops = st.session_state.used_stops
    matrix = st.session_state.matrix
    stops_by_name = {s.name: s for s in stops}

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
                st.markdown("**Violations reported back to the model:**")
                for v in record.violations:
                    st.markdown(f"- {v}")
            if record.parse_error:
                st.error(f"Parse error: {record.parse_error}")
            if record.raw_text:
                st.markdown("**Model's response:**")
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
        st.dataframe(itinerary_df, width="stretch", hide_index=True)

        final_violations = result.iterations[-1].violations if result.iterations else []
        total_service_min = sum(s.duration_minutes for s in stops)
        first_arrival = try_parse_hhmm(timing[0].get("arrival", "")) if timing else None
        last_departure = try_parse_hhmm(timing[-1].get("departure", "")) if timing else None
        grand_total_min = (last_departure - first_arrival) if (first_arrival is not None and last_departure is not None) else None

        st.markdown("**Trip window**")
        wcol1, wcol2, wcol3, wcol4 = st.columns(4)
        wcol1.metric("Start", _format_date_time(st.session_state.trip_date, st.session_state.run_start_time))
        wcol2.metric("End", _format_date_time(st.session_state.run_end_date, st.session_state.run_end_time))
        wcol3.metric("Available time", _format_available(st.session_state.run_available_minutes))
        wcol4.metric("Plan feasible", "✅ YES" if not final_violations else "❌ NO")

        st.markdown("**Trip summary (real data)**")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total distance", f"{total_distance_km:.1f} km")
        col2.metric("Total travel time", f"{total_travel_min:.0f} min")
        col3.metric("Total service time", f"{total_service_min} min")
        col4.metric("Grand total", f"{grand_total_min} min" if grand_total_min is not None else "n/a")

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
    st.info(
        "Load a sample scenario or build custom stops in the sidebar, then click "
        "**Load This Scenario** or **Optimize** to run the agent."
    )
