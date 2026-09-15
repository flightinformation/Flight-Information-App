"""
Weather tab -- METAR / TAF / ATIS / Radar + a Visibility-or-Wind station map.

Reached from the top tab bar; see app.py for the router.  Do not run this file
directly -- run `streamlit run app.py`.

All fetching, parsing and unit conversion lives in `weather_core`; this module
is presentation only.  That split is deliberate: the reference Kivy app mixed
its HTTP calls into the widget classes, which is what let several parsing bugs
hide for so long (see WEATHER_EXPLAINED.md).  Here the engine is testable
without a browser -- `test_weather.py` exercises it directly.

Layout, top to bottom:
    summary metrics  ->  METAR  ->  TAF  ->  ATIS  ->  Radar  ->  station map
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

import folium
import streamlit as st
import streamlit.components.v1 as components
from folium.plugins import Fullscreen
from streamlit_folium import st_folium

import weather_core as wc

HK_CENTER = (22.30, 114.10)
MAP_HEIGHT = 520

# Overlay icons are optional.  If the folders are empty the map falls back to
# drawn markers, so the page works out of the box.
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "icons"
WIND_ICON_DIR = ASSETS / "wind"
VIS_ICON_DIR = ASSETS / "vis"
ICON_EXTS = (".png", ".svg", ".jpg", ".jpeg", ".webp", ".gif")

MIME = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


# --------------------------------------------------------------------------
# Page-local styling
# --------------------------------------------------------------------------
st.markdown("""
<style>
  .wx-code {
      font: .80rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
      white-space: pre-wrap; word-break: break-word;
      background: rgba(128,128,128,.10);
      border-left: 4px solid var(--wxc, #888);
      padding: .55rem .7rem; border-radius: 4px; margin: 0;
  }
  .wx-stale {color:#e08a00; font-size:.78rem;}
  .wx-legend {
      display:flex; flex-wrap:wrap; gap:.55rem; align-items:center;
      font-size:.76rem; opacity:.85; margin:.2rem 0 .45rem;
  }
  .wx-legend span.sw {
      display:inline-block; width:.72rem; height:.72rem;
      border-radius:2px; margin-right:.25rem; vertical-align:-1px;
  }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------
@st.cache_data(ttl=wc.METAR_REFRESH_MINUTES * 60,
               show_spinner="Fetching weather\u2026")
def load_weather(_bust: int = 0) -> wc.WeatherBundle:
    """One pass over all six feeds.

    Cached on `_bust` so the Refresh button can force a re-fetch.  TTL is the
    METAR cadence (15 min, reference line 272); radar moves faster but a stale
    frame list is harmless -- the images themselves are served live.
    """
    return wc.fetch_all()


def refresh_weather() -> None:
    st.session_state.wx_bust = st.session_state.get("wx_bust", 0) + 1
    load_weather.clear()


# --------------------------------------------------------------------------
# Icon loading
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def find_icon(directory_str: str, stem: str) -> str | None:
    """Return a base64 data URI for <dir>/<stem>.<ext>, or None.

    Data URIs rather than a Streamlit static route because the folium map is
    rendered inside a sandboxed iframe that cannot resolve Streamlit's own
    URLs.  Matching is case-insensitive so 'NE.png' and 'ne.png' both work.
    """
    directory = Path(directory_str)
    if not directory.is_dir():
        return None
    want = stem.lower()
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in ICON_EXTS and path.stem.lower() == want:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{MIME.get(path.suffix.lower(), 'image/png')};base64,{data}"
    return None


def available_icons(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted({p.stem for p in directory.iterdir()
                   if p.suffix.lower() in ICON_EXTS})


# --------------------------------------------------------------------------
# Text rendering
# --------------------------------------------------------------------------
def code_block(text: str, colour: str = "#888") -> str:
    """Render fixed-width text safely through Streamlit's markdown pass.

    Same trap as the NOTAM list: `st.markdown(unsafe_allow_html=True)` runs
    the string through a markdown renderer that collapses real newlines even
    inside <pre>.  Escape, then emit explicit <br>.
    """
    lines = text.split("\n")
    body = "<br>".join(html.escape(ln) for ln in lines)
    return f"<div class='wx-code' style='--wxc:{colour}'>{body}</div>"


def report_block(report: wc.TextReport, pretty, colour: str) -> None:
    """Draw one text product, or its error."""
    if report.error:
        st.error(f"{report.label}: {report.error}", icon="\u26a0\ufe0f")
        return
    if not report.raw:
        st.warning(f"{report.label}: nothing returned.", icon="\u26a0\ufe0f")
        return
    st.markdown(code_block(pretty(report.raw), colour), unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Station map
# --------------------------------------------------------------------------
def build_station_map(bundle: wc.WeatherBundle, layer: str,
                      basemap: str) -> folium.Map:
    tiles, attr = {
        "OpenStreetMap": ("OpenStreetMap", None),
        "Satellite": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}", "Esri"),
        "Terrain": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Topo_Map/MapServer/tile/{z}/{y}/{x}", "Esri"),
    }[basemap]

    fmap = folium.Map(location=HK_CENTER, zoom_start=10, tiles=tiles,
                      attr=attr, control_scale=True)
    Fullscreen().add_to(fmap)

    if layer == "Visibility":
        _draw_visibility(fmap, bundle)
    else:
        _draw_wind(fmap, bundle)
    return fmap


def _draw_visibility(fmap: folium.Map, bundle: wc.WeatherBundle) -> None:
    """Value above the station name, coloured by band."""
    for v in bundle.vis_mapped():
        colour = v.colour
        icon_uri = find_icon(str(VIS_ICON_DIR), v.band.replace(" ", "_"))

        if icon_uri:
            glyph = (f"<img src='{icon_uri}' style='width:30px;height:30px;"
                     f"display:block;margin:0 auto;'>")
        else:
            glyph = (
                f"<div style='width:26px;height:26px;border-radius:50%;"
                f"background:{colour};border:2px solid #fff;margin:0 auto;"
                f"box-shadow:0 1px 3px rgba(0,0,0,.5);'></div>")

        label = html.escape(v.label())
        name = html.escape(v.station)
        htm = (
            f"<div style='text-align:center;width:120px;"
            f"transform:translate(-60px,-46px);'>"
            f"<div style='font:700 .78rem/1.1 system-ui;color:{colour};"
            f"text-shadow:0 0 3px #000,0 0 3px #000;'>{label}</div>"
            f"{glyph}"
            f"<div style='font:600 .64rem/1.15 system-ui;color:#fff;"
            f"text-shadow:0 0 3px #000,0 0 3px #000;'>{name}</div></div>")

        folium.Marker(
            [v.lat, v.lon],
            icon=folium.DivIcon(html=htm, icon_size=(0, 0)),
            tooltip=f"{v.station}: {v.label()} ({v.band})",
        ).add_to(fmap)


def _draw_wind(fmap: folium.Map, bundle: wc.WeatherBundle) -> None:
    """Arrow rotated to fly downwind, speed in knots beside the station name.

    `direction_deg` is the bearing the wind blows FROM, so the arrow glyph is
    rotated by +180 to point where the air is going -- the convention pilots
    read off a windsock rather than a met chart barb.
    """
    for w in bundle.wind_mapped():
        colour = wc.wind_colour(w.speed_kmh)
        abbr = w.icon_abbr
        icon_uri = find_icon(str(WIND_ICON_DIR), abbr) if abbr else None

        if w.is_unknown:
            glyph = ("<div style='width:22px;height:22px;border-radius:50%;"
                     "background:#808080;border:2px dashed #fff;margin:0 auto;'"
                     "></div>")
        elif icon_uri:
            rot = ("" if w.direction_deg is None
                   else f"transform:rotate({(w.direction_deg + 180) % 360:.0f}deg);")
            glyph = (f"<img src='{icon_uri}' style='width:34px;height:34px;"
                     f"display:block;margin:0 auto;{rot}'>")
        elif w.direction_deg is None:
            # Calm or variable with no icon supplied.
            glyph = (f"<div style='width:22px;height:22px;border-radius:50%;"
                     f"background:{colour};border:2px solid #fff;"
                     f"margin:0 auto;'></div>")
        else:
            rot = (w.direction_deg + 180) % 360
            glyph = (
                f"<div style='width:30px;height:30px;margin:0 auto;"
                f"transform:rotate({rot:.0f}deg);'>"
                f"<div style='font:900 1.5rem/30px system-ui;color:{colour};"
                f"text-shadow:0 0 3px #000,0 0 3px #000;'>&#10148;</div></div>")

        if w.is_unknown:
            top = "no data"
        elif w.is_calm:
            top = "Calm"
        else:
            kt = w.speed_kt
            top = f"{kt:.0f} kt" if kt is not None else "?"
            if w.gust_kt and w.speed_kt and w.gust_kt > w.speed_kt:
                top += f" G{w.gust_kt:.0f}"

        name = html.escape(w.station)
        htm = (
            f"<div style='text-align:center;width:130px;"
            f"transform:translate(-65px,-50px);'>"
            f"<div style='font:700 .76rem/1.1 system-ui;color:{colour};"
            f"text-shadow:0 0 3px #000,0 0 3px #000;'>{html.escape(top)}</div>"
            f"{glyph}"
            f"<div style='font:600 .64rem/1.15 system-ui;color:#fff;"
            f"text-shadow:0 0 3px #000,0 0 3px #000;'>{name}</div></div>")

        folium.Marker(
            [w.lat, w.lon],
            icon=folium.DivIcon(html=htm, icon_size=(0, 0)),
            tooltip=f"{w.station}: {w.label()}",
        ).add_to(fmap)


# ==========================================================================
# Sidebar
# ==========================================================================
st.sidebar.title("\U0001f326\ufe0f Weather")

basemap = st.sidebar.selectbox("Basemap",
                               ["OpenStreetMap", "Satellite", "Terrain"],
                               key="wx_basemap")
st.sidebar.button("\U0001f504 Refresh now", width="stretch",
                  on_click=refresh_weather)

with st.sidebar.expander("Overlay icons"):
    wind_found = available_icons(WIND_ICON_DIR)
    vis_found = available_icons(VIS_ICON_DIR)
    st.caption("Drop image files in these folders and they are picked up "
               "automatically. Until then the map draws its own markers.")
    st.markdown(
        f"**`assets/icons/wind/`** \u2014 {len(wind_found)} found  \n"
        f"expects: `N NE E SE S SW W NW var calm`  \n"
        f"{('found: `' + ' '.join(wind_found) + '`') if wind_found else '_empty_'}")
    st.markdown(
        f"**`assets/icons/vis/`** \u2014 {len(vis_found)} found  \n"
        f"expects: `good moderate poor very_poor unknown`  \n"
        f"{('found: `' + ' '.join(vis_found) + '`') if vis_found else '_empty_'}")

# ==========================================================================
# Load
# ==========================================================================
bundle = load_weather(st.session_state.get("wx_bust", 0))

arr = bundle.atis.get("arrival")
dep = bundle.atis.get("departure")
arr_sum = wc.atis_summary(arr.raw) if arr and arr.ok else {}
dep_sum = wc.atis_summary(dep.raw) if dep and dep.ok else {}

# --------------------------------------------------------------------------
# Summary strip -- the numbers a pilot wants before reading any raw text.
# --------------------------------------------------------------------------
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("ATIS arrival",
          arr_sum.get("letter") or "\u2014",
          f"RWY {arr_sum['runway']}" if arr_sum.get("runway") else None,
          delta_color="off")
c2.metric("ATIS departure",
          dep_sum.get("letter") or "\u2014",
          f"RWY {dep_sum['runway']}" if dep_sum.get("runway") else None,
          delta_color="off")
DASH = "\u2014"
_temp = arr_sum.get("temp") or dep_sum.get("temp") or DASH
_dewp = arr_sum.get("dewpoint") or dep_sum.get("dewpoint") or DASH

c3.metric("QNH", arr_sum.get("qnh") or dep_sum.get("qnh") or DASH)
c4.metric("Temp / DP", f"{_temp} / {_dewp}")
c5.metric("Wind", arr_sum.get("wind") or dep_sum.get("wind") or DASH)

st.divider()

# ==========================================================================
# METAR
# ==========================================================================
st.subheader("METAR")
report_block(bundle.metar, wc.pretty_metar, "#2e86de")

# ==========================================================================
# TAF
# ==========================================================================
st.subheader("TAF")
report_block(bundle.taf, wc.pretty_taf, "#8e44ad")

# ==========================================================================
# ATIS
# ==========================================================================
st.subheader("ATIS")
a_col, d_col = st.columns(2)
with a_col:
    st.caption("**Arrival**" + (f" \u2014 {arr_sum['time']}"
                                if arr_sum.get("time") else ""))
    if arr:
        report_block(arr, wc.pretty_atis, "#27ae60")
    else:
        st.warning("Arrival ATIS unavailable.", icon="\u26a0\ufe0f")
with d_col:
    st.caption("**Departure**" + (f" \u2014 {dep_sum['time']}"
                                  if dep_sum.get("time") else ""))
    if dep:
        report_block(dep, wc.pretty_atis, "#e67e22")
    else:
        st.warning("Departure ATIS unavailable.", icon="\u26a0\ufe0f")

# ==========================================================================
# Weather radar
# ==========================================================================
st.subheader("Weather Radar")

radar_labels = [lbl for lbl in wc.RADAR_RANGES if lbl in bundle.radar]
if not radar_labels:
    st.error("Radar imagery unavailable.", icon="\u26a0\ufe0f")
else:
    default_idx = (radar_labels.index(wc.RADAR_DEFAULT)
                   if wc.RADAR_DEFAULT in radar_labels else 0)
    rc1, rc2 = st.columns([1, 2])
    rng = rc1.selectbox("Range", radar_labels, index=default_idx,
                        key="wx_radar_range")
    animate = rc2.toggle("Animate loop", value=True, key="wx_radar_animate")

    frames = bundle.radar[rng]
    if not frames.ok:
        st.error(f"Radar {rng}: {frames.error or 'no frames'}",
                 icon="\u26a0\ufe0f")
    else:
        times = frames.timestamps()
        if animate:
            # Cycle client-side: a rerun-driven loop would fight Streamlit's
            # execution model and re-fetch on every tick.
            urls_js = ",".join(f'"{u}"' for u in frames.urls)
            labels_js = ",".join(
                f'"{wc.fmt_hkt(t) if t else "?"}"' for t in times)
            components.html(f"""
<div style="text-align:center;font-family:system-ui">
  <img id="rdr" style="max-width:100%;border-radius:6px"/>
  <div id="cap" style="font-size:.78rem;opacity:.75;margin-top:.3rem"></div>
</div>
<script>
  const urls = [{urls_js}], labels = [{labels_js}];
  let i = 0;
  const img = document.getElementById('rdr');
  const cap = document.getElementById('cap');
  urls.forEach(u => {{ const p = new Image(); p.src = u; }});
  function tick() {{
    img.src = urls[i];
    cap.textContent = 'Frame ' + (i + 1) + ' / ' + urls.length +
                      ' \\u00b7 ' + labels[i];
    i = (i + 1) % urls.length;
    setTimeout(tick, i === 0 ? 1200 : 400);
  }}
  tick();
</script>
""", height=430)
        else:
            idx = st.slider("Frame", 1, len(frames.urls), len(frames.urls),
                            key="wx_radar_frame") - 1
            stamp = times[idx]
            st.image(frames.urls[idx],
                     caption=(f"{rng} \u00b7 frame {idx + 1}/{len(frames.urls)}"
                              f" \u00b7 {wc.fmt_hkt(stamp)}"
                              if stamp else f"{rng} \u00b7 frame {idx + 1}"))

        latest = frames.latest_time()
        if latest:
            st.caption(f"Latest frame {wc.fmt_hkt(latest)} \u00b7 "
                       f"{len(frames.urls)} frames \u00b7 refreshes every "
                       f"{wc.RADAR_REFRESH_MINUTES} min")

# ==========================================================================
# Station map -- Visibility / Wind toggle
# ==========================================================================
st.subheader("Station Map")

layer = st.segmented_control("Overlay", ["Visibility", "Wind"],
                             default="Visibility", key="wx_layer") or "Visibility"

if layer == "Visibility":
    if bundle.vis_error:
        st.error(f"Visibility feed: {bundle.vis_error}", icon="\u26a0\ufe0f")
    legend = "".join(
        f"<span><span class='sw' style='background:{col}'></span>{name}</span>"
        for name, col in wc.VIS_COLOURS.items())
    st.markdown(f"<div class='wx-legend'>{legend}</div>",
                unsafe_allow_html=True)
else:
    if bundle.wind_error:
        st.error(f"Wind feed: {bundle.wind_error}", icon="\u26a0\ufe0f")
    bands = [("light (\u226420 km/h)", "#33cc33"),
             ("moderate (21\u201340)", "#ffcc33"),
             ("strong (>40)", "#ff3333"),
             ("no data", "#808080")]
    legend = "".join(
        f"<span><span class='sw' style='background:{col}'></span>{name}</span>"
        for name, col in bands)
    st.markdown(f"<div class='wx-legend'>{legend}</div>"
                "<div class='wx-legend'>Arrows fly downwind \u00b7 "
                "speeds in knots</div>", unsafe_allow_html=True)

fmap = build_station_map(bundle, layer, basemap)
st_folium(fmap, use_container_width=True, height=MAP_HEIGHT,
          returned_objects=[], key=f"wx-map-{layer}")

n_stations = (len(bundle.vis_mapped()) if layer == "Visibility"
              else len(bundle.wind_mapped()))
st.caption(f"{n_stations} stations plotted \u00b7 fetched "
           f"{wc.fmt_hkt(bundle.fetched_at)}")

with st.expander("Readings table"):
    if layer == "Visibility":
        st.dataframe(
            [{"Station": v.station, "Visibility": v.label(),
              "Band": v.band, "Raw": v.raw} for v in bundle.vis],
            width="stretch", hide_index=True)
    else:
        st.dataframe(
            [{"Station": w.station,
              "Direction": w.direction_str,
              "Speed (km/h)": w.speed_kmh,
              "Speed (kt)": None if w.speed_kt is None else round(w.speed_kt),
              "Gust (km/h)": w.gust_kmh} for w in bundle.wind],
            width="stretch", hide_index=True)
        missing = bundle.wind_unmapped()
        if missing:
            st.caption("No coordinates on file (not plotted): "
                       + ", ".join(missing))
