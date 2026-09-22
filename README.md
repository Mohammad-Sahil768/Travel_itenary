# Travel Optimization AI Agent

A Streamlit app that sequences a list of stops (each with a time window and a
required visit duration) into an optimized itinerary. A Claude agent uses
tool calls to fetch real travel times and validate its own plan, refining it
over up to 3 iterations until every constraint is satisfied.

## How it works

1. **You provide stops**, one of two ways (sidebar tabs):
   - **📂 Load Scenario** — pick one of three sample scenarios, preview its
     stops/times/budget, and click **Load This Scenario** to geocode, route,
     and optimize it immediately.
   - **✏️ Custom Stops** — build your own trip: an optional fixed **start
     location** (a real depot/origin the route must begin from — if set, real
     travel time from it to your first stop counts against the budget), a
     total time budget in hours, and a list of stops (address, earliest/latest
     time pickers, service duration in minutes) with **➕ Add Stop** /
     🗑️ remove-per-row controls, then click **🚀 Optimize**.

   A shared trip date and trip start time sit above both tabs. Either path
   converges on the exact same pipeline below, and the same results section.
2. **Geocoding** — each address is resolved to (latitude, longitude) via
   [OpenStreetMap Nominatim](https://nominatim.org/) (free, no API key, using
   `geopy`). Unresolvable addresses are reported per-stop and block the run
   until fixed — bad coordinates would silently corrupt every travel-time
   calculation downstream, so this fails loudly instead. Resolved addresses
   are cached in-process, so re-optimizing after editing one stop only
   re-geocodes what changed.
3. **Routing API** — the app calls [OSRM](https://project-osrm.org/) (default,
   no API key) or [OpenRouteService](https://openrouteservice.org/) (optional
   key) with the geocoded coordinates to get a real pairwise travel-time/
   distance matrix. If the API is unreachable, it falls back to a
   haversine-distance estimate (clearly flagged in the UI) so the app still
   runs.
4. **Claude agent loop** — Claude is given the stops, constraints, and travel
   matrix, and can call two tools:
   - `get_travel_time(from_stop, to_stop)` — look up real travel time/distance.
   - `validate_constraints(itinerary)` — check a candidate plan for violations.

   Claude proposes a sequence, timing, and confidence score as JSON. The app
   *independently* re-validates that JSON in Python (never just trusting
   Claude's self-report). If violations remain, the specific failures are sent
   back to Claude and it tries again — up to 3 iterations total.
5. **Results** — an expandable "Agent Reasoning" panel shows each iteration's
   reasoning, tool calls, and violations. A "Geocoded locations" panel shows
   exactly what address each stop resolved to (so you can catch a wrong match,
   e.g. the wrong city). The final itinerary is shown as a table with real
   resolved addresses, 12-hour arrival/departure times, and per-leg travel
   time/distance, plus trip-summary metrics (total distance, total travel
   time, total service time, grand total, feasibility YES/NO) and a
   date-stamped CSV export.

### What "real" does and doesn't mean here

- Geocoding and routing distances/base travel times are real, live API data.
- **Traffic is not modeled.** OSRM's free demo server and OpenRouteService's
  free tier return typical/average driving times, not time-of-day or
  live/historical traffic for your specific trip date — there's no free API
  that reliably does this. The picked trip date and start time are used for
  scheduling and display, not to change the travel-time numbers. Don't treat
  travel times as guarantees, especially around rush hour.

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

Or paste the API key directly into the sidebar field at runtime.

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), create a new app
   pointing at this repo and `app.py`.
3. In the app's **Settings → Secrets**, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Deploy. `requirements.txt` is picked up automatically.

## Project layout

```
app.py                     Streamlit UI
travel_agent/
  models.py                Stop dataclass + time-string helpers
  geocoding.py              Nominatim (OpenStreetMap) address -> coordinates, with caching
  routing.py                OSRM / OpenRouteService clients + haversine fallback
  tools.py                  Tool schemas + get_travel_time / validate_constraints
  agent.py                  The Claude tool-use optimization loop
  sample_data.py            3 preloaded scenarios (Urban, Regional, Urgent), given as real addresses
```

## Sample scenarios

- **Urban Delivery** — 5 NYC landmarks, tight 8am–5pm window.
- **Regional Logistics** — 8 stops across 3 nearby cities, 10am–6pm.
- **Same-day Service** — 6 same-day service/delivery stops, 9am–5pm.

## Custom stops and the start location

A custom trip can optionally pin a **start location** — a real address the
route must depart from at the trip start time. Internally this is added as a
zero-duration stop with `is_start=True`; the optimizer is instructed it must
be first in the itinerary, and the independent Python validator enforces that
ordering itself rather than trusting Claude to comply. Leave the field blank
to fall back to the original behavior: the route just begins at whichever
stop Claude picks first, with no dedicated depot leg.

## Notes

- The public OSRM demo server is rate-limited and driving-only; for production
  use, self-host OSRM or use OpenRouteService with your own key.
- Each optimization run makes 1–3 outer iterations, each with a handful of
  Claude tool-use turns — factor that into API cost when testing with larger
  stop lists.
- Nominatim's usage policy caps unauthenticated geocoding at ~1 request/second;
  the app respects that with a rate limiter and caches resolved addresses in
  memory for the life of the process, so editing one stop and re-optimizing
  doesn't re-geocode every stop again.
- Earliest/latest are now required time pickers (not free text). If a stop
  truly has no time restriction, set its window wide (e.g. 12:00 AM–11:59 PM).
- This app plans a **single calendar day** per run — all stops share one trip
  date, and times don't roll over into a next day.
