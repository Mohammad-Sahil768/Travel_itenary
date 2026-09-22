# Travel Optimization AI Agent

A Streamlit app that sequences a list of stops (each with a time window and a
required visit duration) into an optimized itinerary. A Claude agent uses
tool calls to fetch real travel times and validate its own plan, refining it
over up to 3 iterations until every constraint is satisfied.

## How it works

1. **You provide stops** — name, optional lat/lon, earliest/latest time, and
   how long to spend there — plus a trip start time and total time budget.
2. **Routing API** — the app calls [OSRM](https://project-osrm.org/) (default,
   no API key) or [OpenRouteService](https://openrouteservice.org/) (optional
   key) to get a real pairwise travel-time/distance matrix. If stops have no
   coordinates or the API is unreachable, it falls back to a haversine-distance
   estimate so the app still runs.
3. **Claude agent loop** — Claude is given the stops, constraints, and travel
   matrix, and can call two tools:
   - `get_travel_time(from_stop, to_stop)` — look up real travel time/distance.
   - `validate_constraints(itinerary)` — check a candidate plan for violations.

   Claude proposes a sequence, timing, and confidence score as JSON. The app
   *independently* re-validates that JSON in Python (never just trusting
   Claude's self-report). If violations remain, the specific failures are sent
   back to Claude and it tries again — up to 3 iterations total.
4. **Results** — an expandable "Agent Reasoning" panel shows each iteration's
   reasoning, tool calls, and violations. The final itinerary is shown as a
   table with arrival/departure times and per-leg travel time/distance, with
   metrics (total time, confidence, iterations used, validity) and a CSV
   export.

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
  routing.py                OSRM / OpenRouteService clients + haversine fallback
  tools.py                  Tool schemas + get_travel_time / validate_constraints
  agent.py                  The Claude tool-use optimization loop
  sample_data.py            3 preloaded scenarios (Urban, Regional, Urgent)
```

## Sample scenarios

- **Urban** — 5 NYC landmarks, tight 8am–5pm window.
- **Regional** — 8 stops across 3 nearby cities, 10am–6pm.
- **Urgent** — 3 errands that must all wrap up by 2pm.

## Notes

- The public OSRM demo server is rate-limited and driving-only; for production
  use, self-host OSRM or use OpenRouteService with your own key.
- Each optimization run makes 1–3 outer iterations, each with a handful of
  Claude tool-use turns — factor that into API cost when testing with larger
  stop lists.
