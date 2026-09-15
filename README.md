# HK Aviation Briefing

Live NOTAM and weather briefing for Hong Kong (VHHH / VHHK), built with
Streamlit.

**NOTAM tab** — C-series NOTAMs from the Hong Kong AIS, plotted on a map and
colour-coded by urgency, with filters for All / Active Today / Next 2 Hours /
Active Now.

**Weather tab** — METAR, TAF, ATIS, animated HKO radar, and a station map that
toggles between visibility and wind.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to GitHub.
2. At share.streamlit.io choose **Deploy an app**, point **Main file path** at
   `app.py`.
3. Under **Advanced settings** set **Python version 3.11**. Community Cloud
   ignores `runtime.txt`, so the dropdown is the only thing that works.

## Layout

```
app.py              router: page config, shared CSS, the top tab bar
notam_core.py       NOTAM fetching, parsing and geometry (no UI)
weather_core.py     METAR / TAF / ATIS / radar / wind / visibility (no UI)
views/notam.py      the NOTAM page
views/weather.py    the Weather page
```

`app.py` loads the pages with `st.Page("views/notam.py", ...)`, which resolves
relative to `app.py`'s own folder — so **`views/` must sit next to `app.py`**
in the repository or the app will not start.

## Optional: overlay icons

The Weather station map uses plain coloured markers unless you add PNGs:

- `assets/icons/wind/` — `N NE E SE S SW W NW var calm`
- `assets/icons/vis/` — `good moderate poor very_poor unknown`

They are picked up automatically once present.

## Disclaimer

Data comes from public Hong Kong AIS and HKO feeds. This is a convenience
viewer, not an official briefing tool — always plan flights against the
official source.
