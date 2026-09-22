# Travel Optimization AI Agent

A Streamlit app that sequences a list of stops (each with a time window and a
required visit duration) into an optimized itinerary. A Claude tool-use agent
fetches real travel times and validates its own plan, refining it over up to
3 iterations until every constraint is satisfied. You only ever provide an
API key — the provider and model are fixed in code, not a sidebar choice.

## How it works

1. **You set a trip window** — Start date + time and End date + time, shared
   above both tabs. The agent computes **available time = End − Start**
   itself; there's no separate "budget in hours" field to fill in or keep in
   sync. This can span midnight (e.g. 9 PM → 5 AM next day computes correctly
   as 8 hours) — see the caveat under "Trip window" below for what that does
   and doesn't mean for stop time windows.
2. **You provide stops**, one of two ways (sidebar tabs):
   - **📂 Load Scenario** — pick one of three sample scenarios, preview its
     stops/times/budget, and click **Load This Scenario**. This also pushes
     the scenario's own start time and (start + its budget) into the shared
     Start/End fields above, so the sidebar and the run always agree, then
     geocodes, routes, and optimizes immediately.
   - **✏️ Custom Stops** — build your own trip: an optional fixed **start
     location** (a real depot/origin the route must begin from — if set, real
     travel time from it to your first stop counts against the available
     time) and a list of stops (address, earliest/latest time pickers,
     service duration in minutes) with **➕ Add Stop** / 🗑️ remove-per-row
     controls, then click **🚀 Optimize**.

   Either path converges on the exact same pipeline below, and the same
   results section.
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
4. **Claude agent loop** — the model is given the stops, constraints, and
   travel matrix, and can call two tools:
   - `get_travel_time(from_stop, to_stop)` — look up real travel time/distance.
   - `validate_constraints(itinerary)` — check a candidate plan for violations.

   It proposes a sequence, timing, and confidence score as JSON. The app
   *independently* re-validates that JSON in Python (never just trusting the
   model's self-report). If violations remain, the specific failures are sent
   back and it tries again — up to 3 iterations total.
5. **Results** — an expandable "Agent Reasoning" panel shows each iteration's
   reasoning, tool calls, and violations. A "Geocoded locations" panel shows
   exactly what address each stop resolved to (so you can catch a wrong match,
   e.g. the wrong city). A **"Trip window"** row shows exactly what was
   asked for: **Start** (e.g. "Dec 25, 9:00 AM"), **End** (e.g.
   "Dec 25, 5:00 PM"), **Available time** (e.g. "8h"), and **Plan feasible**
   (✅ YES / ❌ NO — based on the same independent constraint check as
   everywhere else in this app, not the model's own self-report). The final
   itinerary is shown as a table with real resolved addresses, 12-hour
   arrival/departure times, and per-leg travel time/distance, plus a second
   summary row (total distance, total travel time, total service time, grand
   total) and a date-stamped CSV export.

### Trip window: what "Start/End" does and doesn't cover

Available time = End − Start is a real datetime subtraction (it handles an
overnight window correctly, e.g. 9 PM → 5 AM next day = 8 hours). Individual
**stop** time windows (each stop's own earliest/latest) are still plain
HH:MM values on a single calendar day, unchanged — this app doesn't (yet)
support a stop's own window spanning past midnight. For a same-day trip
(by far the common case) this is a non-issue; for an overnight window, the
*budget* is computed correctly across the day boundary, but per-stop
earliest/latest still can't express "this stop's window is on day 2."

### What "real" does and doesn't mean here

- Geocoding and routing distances/base travel times are real, live API data.
- **Traffic is not modeled.** OSRM's free demo server and OpenRouteService's
  free tier return typical/average driving times, not time-of-day or
  live/historical traffic for your specific trip date — there's no free API
  that reliably does this. The picked trip date and start time are used for
  scheduling and display, not to change the travel-time numbers. Don't treat
  travel times as guarantees, especially around rush hour.

## Provider and model are fixed, not user-selectable

The sidebar only ever asks for an **Anthropic API key** — there's no
provider or model dropdown. Internally, `app.py` hardcodes:

```python
PROVIDER = "anthropic"
MODEL = DEFAULT_MODEL  # "claude-opus-5", from travel_agent/agent.py
```

⚠️ Note on the spec this was built from: it named `claude-3-5-sonnet-20241022`
as the hardcoded model — a dated snapshot one generation behind what this
app already uses elsewhere. Rather than reintroduce an aging/soon-retired
model ID, the hardcode points at the same current default (`claude-opus-5`)
already used throughout this app. Change the `MODEL` constant near the top
of `app.py` if you want a different fixed model.

The app can still technically talk to other OpenAI-compatible providers —
`travel_agent/llm_providers.py` and `TravelOptimizationAgent`'s
`provider`/`base_url` parameters are untouched, since removing the *frontend*
picker doesn't require deleting the working backend abstraction behind it —
but nothing in the UI exposes that anymore.

**Your key never touches disk** — it lives only in Streamlit's in-memory
`st.session_state` for the browser tab's session; it's not written to a file
or logged. It *is* sent to Anthropic's API.

**API key validation** — clicking **🔑 Test API Key**, or clicking **Load
This Scenario** / **Optimize**, always makes one minimal test call first
(`max_tokens` capped low, no tools attached) before spending anything on
geocoding or the real optimization run. A bad key is reported immediately
instead of failing deep into the pipeline.

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

Or paste the API key directly into the sidebar's **Anthropic API key** field
at runtime.

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), create a new app
   pointing at this repo and `app.py`.
3. In the app's **Settings → Secrets**, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Deploy. `requirements.txt` is picked up automatically (`openai` is still
   a listed dependency even though the UI no longer exposes it — see
   "Provider and model are fixed" above; the module that imports it is
   always loaded).

## Project layout

```
app.py                     Streamlit UI
travel_agent/
  models.py                Stop dataclass + time-string helpers
  geocoding.py              Nominatim (OpenStreetMap) address -> coordinates, with caching
  routing.py                OSRM / OpenRouteService clients + haversine fallback
  tools.py                  Tool schemas + get_travel_time / validate_constraints
  llm_providers.py          Per-provider backends (Anthropic / OpenAI-compatible)
  agent.py                  The provider-agnostic tool-use optimization loop
  sample_data.py            3 preloaded scenarios (Urban, Regional, Same-day), given as real addresses
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
- Per-stop earliest/latest windows are still same-day HH:MM values — see
  "Trip window" above for what that means when the overall trip Start/End
  spans midnight.
