# HK Aviation Briefing

A Streamlit briefing tool for Hong Kong (VHHH), with a NOTAM tab and a weather
tab, built from live Civil Aviation Department and Hong Kong Observatory feeds.

> **Not for operational flight use.** This is an unofficial convenience viewer.
> The authoritative sources are the CAD AIS briefing system and the HKO
> aviation weather service.

## Features

**NOTAM tab** — C-series NOTAMs plotted on a map, with a filter bar for All /
Active Today / Active within 2 Hours / Active Now. Areas are colour-coded by
urgency, and clicking a row in the list below pans the map to that NOTAM.

**Weather tab** — METAR, TAF and ATIS (arrival and departure) as formatted raw
text, an animated weather radar loop with four range settings, and a station
map that toggles between visibility and wind. Wind speeds are shown in knots.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open <http://localhost:8501>.

## Deploying

See [DEPLOY.md](DEPLOY.md). The short version: this is a Python server, not a
static site, so it needs a Python host. Streamlit Community Cloud is free and
works well.

## Overlay icons (optional)

Drop image files into these folders and they are picked up automatically:

- `assets/icons/wind/` — stems `N NE E SE S SW W NW`, plus optional `var`, `calm`
- `assets/icons/vis/` — stems `good moderate poor very_poor unknown`

PNG, SVG, JPG, WEBP and GIF all work, and matching is case-insensitive. Draw
wind arrows pointing north/up; the map rotates them to fly downwind. Without
icons the map draws its own fallback markers.

## Project layout

```
app.py              router, page config, shared CSS
views/notam.py      NOTAM tab UI
views/weather.py    Weather tab UI
notam_core.py       NOTAM fetching, parsing, geometry, time filtering
weather_core.py     weather fetching, parsing, unit conversion
test_notam.py       NOTAM test suite
test_weather.py     weather test suite
```

The `*_core.py` modules import no Streamlit, so both test suites run headless:

```bash
python test_notam.py
python test_weather.py
```

## Documentation

- [NOTAM_EXPLAINED.md](NOTAM_EXPLAINED.md) — how NOTAM parsing, area geometry and time filtering work
- [WEATHER_EXPLAINED.md](WEATHER_EXPLAINED.md) — the six weather feeds, plus bugs found in the original reference implementation

## Data sources

Hong Kong Observatory (METAR, TAF, radar, visibility, wind) and Civil Aviation
Department (ATIS, NOTAM). Radar imagery is served directly by HKO and carries
its own copyright notice. Check the relevant terms before deploying publicly.
