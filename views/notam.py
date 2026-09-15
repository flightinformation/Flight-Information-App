"""
NOTAM tab -- Streamlit port of the Kivy reference app's NOTAM module.

Reached from the top tab bar; see app.py for the router.  Do not run this file
directly -- run `streamlit run app.py`.

Layout (top to bottom)
----------------------
    1. map            -- fixed position, never moves
    2. toggle bar     -- All / Active Today / Next 2 Hours / Active Now
    3. scrollable list -- full NOTAM info; click a row to pan the map to it

Overlay colours are driven by urgency, not raw validity:
    active now            -> red
    active within 2 hours -> light red / pink
    active later today    -> amber
    anything else         -> grey

Page config and the shared CSS live in app.py so they apply to every tab.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

import folium
import streamlit as st
from folium.plugins import Fullscreen, MeasureControl
from streamlit_folium import st_folium

import notam_core as nc

HK_CENTER = (22.3, 114.17)
MAP_HEIGHT = 520
LIST_HEIGHT = 620


# --------------------------------------------------------------------------
# Data loading (replaces Clock.schedule_interval + notam_cache.txt)
# --------------------------------------------------------------------------
@st.cache_data(ttl=nc.NOTAM_REFRESH_MINUTES * 60, show_spinner="Fetching NOTAMs…")
def load_notams(_bust: int = 0):
    """Fetch the C-series NOTAMs currently published on the AIS website."""
    db = nc.NotamDatabase()
    try:
        db.update_from_json_api(series="C")
    except Exception as exc:
        return None, str(exc)
    return db, None


def body_html(notam) -> str:
    """ICAO fields one per line, safe for Streamlit's markdown->HTML pass.

    ``st.markdown(..., unsafe_allow_html=True)`` runs the string through a
    markdown renderer first, which collapses the newlines produced by
    ``nc.pretty_text()`` even inside a <pre> block.  So escape the text and
    emit explicit <br> breaks instead of relying on real newlines.
    """
    lines = nc.pretty_text(notam).split("\n")
    return "<br>".join(html.escape(ln) for ln in lines)


def refresh():
    st.session_state.bust = st.session_state.get("bust", 0) + 1
    load_notams.clear()


def select(notam_id: str, view: tuple[tuple[float, float], int] | None):
    """Row click -> remember selection, then pan/zoom to frame the area."""
    st.session_state.selected = notam_id
    if view:
        st.session_state.pan, st.session_state.pan_zoom = view


# --------------------------------------------------------------------------
# Map -- equivalent of NotamMapHandler + the three NotamLayers
# --------------------------------------------------------------------------
def build_map(notams: list[nc.Notam], selected_id: str | None,
              when: datetime, basemap: str, soon_hours: int) -> folium.Map:
    tiles, attr = {
        "OpenStreetMap": ("OpenStreetMap", None),
        "Satellite": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}", "Esri"),
        "Terrain": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Topo_Map/MapServer/tile/{z}/{y}/{x}", "Esri"),
    }[basemap]

    centre = st.session_state.get("pan") or HK_CENTER
    zoom = st.session_state.get("pan_zoom", 10)

    fmap = folium.Map(location=centre, zoom_start=zoom, tiles=tiles,
                      attr=attr, control_scale=True)
    Fullscreen().add_to(fmap)
    MeasureControl(primary_length_unit="kilometers").add_to(fmap)

    # Draw least-urgent first so red ends up on top of grey.
    ordered = sorted(notams,
                     key=lambda n: -nc.URGENCY_ORDER[
                         nc.classify_urgency(n, when, soon_hours)])

    for n in ordered:
        bucket = nc.classify_urgency(n, when, soon_hours)
        colour = nc.URGENCY_COLOURS[bucket]
        selected = (n.id == selected_id)
        weight = 5 if selected else 2
        fill_opacity = 0.50 if selected else (
            0.32 if bucket == nc.URGENCY_NOW else 0.18)
        dash = None if bucket == nc.URGENCY_NOW else "8,6"

        popup_html = (
            f"<div style='font-family:ui-monospace,monospace;font-size:11px;"
            f"max-height:320px;overflow:auto;width:340px'>"
            f"<b style='font-size:13px'>{n.id}</b><br>"
            f"<b>{nc.URGENCY_BADGES[bucket]}</b><br>"
            f"{nc.timing_note(n, when, soon_hours)}<br>"
            f"<b>From:</b> {nc.fmt_utc_hkt(n.valid_from)}<br>"
            f"<b>To:</b> {'PERM' if n.is_perm else nc.fmt_utc_hkt(n.valid_to)}<br>"
            + (f"<b>D)</b> {n.d_field}<br>" if n.d_field else "")
            + f"<hr style='margin:4px 0'><pre style='white-space:pre-wrap;"
              f"margin:0'>{nc.pretty_text(n)[:1200]}</pre></div>"
        )

        for gi, g in enumerate(n.geometries):
            label = n.id if len(n.geometries) == 1 else f"{n.id}({gi + 1})"
            tooltip = f"{label} — {nc.URGENCY_SHORT[bucket]}"

            # Build a FRESH Popup per geometry.  A folium Element can only have
            # one parent, so attaching one Popup object to several shapes makes
            # folium emit the same `var html_<hash>` declaration twice.  The
            # duplicate breaks streamlit-folium's script at runtime, so it never
            # calls setFrameHeight() and the map iframe collapses to 0 px --
            # which is why "All" and "Active Today" (the only filters that
            # include the 2-area NOTAM C0334/26) showed no map at all.
            popup = folium.Popup(popup_html, max_width=380)

            if g.kind == "polygon" and len(g.coordinates) >= 3:
                folium.Polygon(
                    locations=g.coordinates, color=colour, weight=weight,
                    fill=True, fill_color=colour, fill_opacity=fill_opacity,
                    dash_array=dash, popup=popup, tooltip=tooltip).add_to(fmap)
            elif g.kind == "circle" and g.radius_m:
                folium.Circle(
                    location=g.coordinates[0], radius=g.radius_m,
                    color=colour, weight=weight, fill=True, fill_color=colour,
                    fill_opacity=fill_opacity, dash_array=dash,
                    popup=popup, tooltip=tooltip).add_to(fmap)
            elif g.kind == "point":
                folium.CircleMarker(
                    location=g.coordinates[0], radius=8, color=colour,
                    weight=weight, fill=True, fill_color=colour,
                    fill_opacity=0.75, popup=popup,
                    tooltip=tooltip).add_to(fmap)

            # ID label, like NotamLayer.draw_label()
            c = g.centroid()
            if c:
                ring = ";box-shadow:0 0 0 2px #fff" if selected else ""
                folium.Marker(
                    location=c,
                    icon=folium.DivIcon(
                        html=f"<div style='font:bold 10px ui-monospace,"
                             f"monospace;color:#fff;background:{colour};"
                             f"padding:1px 4px;border-radius:3px;"
                             f"white-space:nowrap{ring}'>{label}</div>",
                        icon_size=(0, 0), icon_anchor=(-6, 8))).add_to(fmap)

    return fmap


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
st.sidebar.title("🛩️ NOTAM")
st.sidebar.caption("C-series only — area & navigation warnings.")
basemap = st.sidebar.selectbox("Basemap", ["OpenStreetMap", "Satellite", "Terrain"])
soon_hours = st.sidebar.slider("“Soon” horizon (hours)", 1, 12,
                               nc.SOON_HOURS_DEFAULT)
only_mapped = st.sidebar.checkbox("Only NOTAMs with a plotted area", value=False)

use_custom_time = st.sidebar.checkbox("Evaluate at a specific time (UTC)")
if use_custom_time:
    c1, c2 = st.sidebar.columns(2)
    d = c1.date_input("Date", value=datetime.now(timezone.utc).date())
    t = c2.time_input("Time", value=datetime.now(timezone.utc).time())
    when = datetime.combine(d, t).replace(tzinfo=timezone.utc)
else:
    when = datetime.now(timezone.utc)

st.sidebar.button("🔄 Refresh now", width="stretch", on_click=refresh)
if st.sidebar.button("🎯 Reset map view", width="stretch"):
    st.session_state.pop("pan", None)
    st.session_state.pop("pan_zoom", None)

# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------
db, err = load_notams(st.session_state.get("bust", 0))
if err:
    st.error(f"Could not fetch NOTAMs from {nc.NOTAM_URL}\n\n{err}")
    st.stop()

buckets = {n.id: nc.classify_urgency(n, when, soon_hours) for n in db.notams}
tally = {b: sum(1 for v in buckets.values() if v == b)
         for b in (nc.URGENCY_NOW, nc.URGENCY_SOON,
                   nc.URGENCY_TODAY, nc.URGENCY_OTHER)}

# ==========================================================================
# 1. MAP  (fixed at the top)
# ==========================================================================
today_count = len(db.filter("Active Today", when=when, soon_hours=soon_hours))

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("🔴 Active now", tally[nc.URGENCY_NOW])
m2.metric(f"🌸 Within {soon_hours} h", tally[nc.URGENCY_SOON])
m3.metric("🟠 Active today", today_count)
m4.metric("Published (C)", len(db.notams))
m5.metric("Mapped", len(db.with_location()))

map_slot = st.empty()

# ==========================================================================
# 2. TOGGLE BAR
# ==========================================================================
modes = ["All", "Active Today", f"Next {soon_hours} Hours", "Active Now"]
mode_key = {f"Next {soon_hours} Hours": "Next 2 Hours"}

tcol, lcol = st.columns([2, 3])
with tcol:
    picked = st.segmented_control(
        "Show", modes, default="Active Now", label_visibility="collapsed")
with lcol:
    st.markdown(
        "<div style='padding-top:.45rem;font-size:.82rem;opacity:.85'>"
        + " &nbsp;·&nbsp; ".join(
            f"<span style='color:{nc.URGENCY_COLOURS[b]};font-size:1.1rem'>●</span> "
            f"{lbl}"
            for b, lbl in [(nc.URGENCY_NOW, "now"),
                           (nc.URGENCY_SOON, f"≤{soon_hours} h"),
                           (nc.URGENCY_TODAY, "today"),
                           (nc.URGENCY_OTHER, "other")])
        + "</div>", unsafe_allow_html=True)

mode = mode_key.get(picked or "Active Now", picked or "Active Now")
notams = db.filter(mode, when=when, soon_hours=soon_hours)
if only_mapped:
    notams = [n for n in notams if n.has_location]

if st.session_state.get("selected") not in {n.id for n in notams}:
    st.session_state.selected = notams[0].id if notams else None

# Render the map now that the filter is known, into the slot above.
with map_slot.container():
    fmap = build_map(notams, st.session_state.selected, when,
                     basemap, soon_hours)
    # st_folium is third-party and does not accept Streamlit's newer
    # width="stretch"; it still expects use_container_width.
    st_folium(fmap, use_container_width=True, height=MAP_HEIGHT,
              returned_objects=[],
              center=st.session_state.get("pan"),
              zoom=st.session_state.get("pan_zoom"),
              key="notam-map")

st.caption(
    f"Source: {nc.NOTAM_URL} · updated "
    f"{db.last_updated.astimezone(nc.HKT).strftime('%d-%b %H:%M')} HKT · "
    f"evaluating at {when.strftime('%d-%b-%Y %H:%M')}Z "
    f"({when.astimezone(nc.HKT).strftime('%d-%b %H:%M')} HKT) · "
    f"auto-refresh every {nc.NOTAM_REFRESH_MINUTES // 60} h")

# ==========================================================================
# 3. SCROLLABLE LIST
# ==========================================================================
st.subheader(f"{len(notams)} NOTAM{'s' if len(notams) != 1 else ''} — {picked}")

if not notams:
    st.info(f"Nothing matches “{picked}”. Try a wider filter.")
else:
    with st.container(height=LIST_HEIGHT, border=False):
        for n in notams:
            bucket = buckets[n.id]
            colour = nc.URGENCY_COLOURS[bucket]
            selected = (n.id == st.session_state.selected)
            view = n.view()

            with st.container(border=False):
                st.markdown(
                    f"<div class='nrow {'nrow-sel' if selected else ''}' "
                    f"style='--c:{colour}'>"
                    f"<span class='nid'>{n.id}</span>"
                    f"<span class='nbadge'>{nc.URGENCY_BADGES[bucket]}</span>"
                    f"<div class='nnote'>{nc.timing_note(n, when, soon_hours)}</div>"
                    f"<div class='nmeta'>"
                    f"B) {nc.fmt_utc_hkt(n.valid_from)}<br>"
                    f"C) {'PERM' if n.is_perm else nc.fmt_utc_hkt(n.valid_to)}"
                    + (f"<br>D) {n.d_field}" if n.d_field else "")
                    + f"</div>"
                    f"<div class='nbody'>{body_html(n)}</div>"
                    f"</div>", unsafe_allow_html=True)

                b1, b2, _ = st.columns([1, 1, 6])
                b1.button(
                    "📍 Show on map" if view else "📍 No area",
                    key=f"go-{n.id}", disabled=view is None,
                    on_click=select, args=(n.id, view),
                    width="stretch")

                if n.d_field:
                    windows = nc.parse_d_field(n.d_field, n.valid_from,
                                               n.valid_to)
                    with b2.popover("🕑 Windows", width="stretch"):
                        if windows:
                            st.caption(f"{len(windows)} active window(s) "
                                       f"from D) “{n.d_field}”")
                            st.dataframe(
                                [{"from (UTC)": w[0].strftime("%d-%b %H:%M"),
                                  "to (UTC)": w[1].strftime("%d-%b %H:%M"),
                                  "from (HKT)": w[0].astimezone(nc.HKT)
                                                 .strftime("%d-%b %H:%M"),
                                  "to (HKT)": w[1].astimezone(nc.HKT)
                                               .strftime("%d-%b %H:%M")}
                                 for w in windows[:200]],
                                width="stretch", hide_index=True)
                        else:
                            st.info(f"“{n.d_field}” is not a recognised "
                                    f"schedule (e.g. HJ/HN) — treated as "
                                    f"continuously active.")
