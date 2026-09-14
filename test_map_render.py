"""Regression test: the map JS must never declare the same variable twice.

streamlit-folium ships the map as a generated JavaScript string that it eval()s
in the component iframe.  If the same `var html_<hash>` / `var popup_<hash>` is
declared twice, the script throws, setFrameHeight() is never reached and the
iframe collapses to 0 px -- the map silently disappears.

That happened whenever one folium.Popup object was attached to more than one
shape (only NOTAM C0334/26 has 2 areas, so only the "All" and "Active Today"
filters were affected).

Run:  python test_map_render.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

import folium
from streamlit_folium import _generate_leaflet_string

import notam_core as nc

MODES = ("All", "Active Today", "Next 2 Hours", "Active Now")
DECL = re.compile(r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")


def build_map(notams, when, soon_hours):
    """Mirror views/notam.py: one FRESH Popup per geometry."""
    fmap = folium.Map(location=(22.3, 114.17), zoom_start=10,
                      tiles="OpenStreetMap", control_scale=True)
    for n in notams:
        bucket = nc.classify_urgency(n, when, soon_hours)
        colour = nc.URGENCY_COLOURS[bucket]
        popup_html = f"<div><b>{n.id}</b><br><pre>{nc.pretty_text(n)[:1200]}</pre></div>"
        for gi, g in enumerate(n.geometries):
            label = n.id if len(n.geometries) == 1 else f"{n.id}({gi + 1})"
            popup = folium.Popup(popup_html, max_width=380)   # fresh each time
            if g.kind == "polygon" and len(g.coordinates) >= 3:
                folium.Polygon(locations=g.coordinates, color=colour, fill=True,
                               popup=popup, tooltip=label).add_to(fmap)
            elif g.kind == "circle" and g.radius_m:
                folium.Circle(location=g.coordinates[0], radius=g.radius_m,
                              color=colour, fill=True, popup=popup,
                              tooltip=label).add_to(fmap)
            elif g.kind == "point":
                folium.CircleMarker(location=g.coordinates[0], radius=8,
                                    color=colour, fill=True, popup=popup,
                                    tooltip=label).add_to(fmap)
            c = g.centroid()
            if c:
                folium.Marker(location=c,
                              icon=folium.DivIcon(html=f"<div>{label}</div>",
                                                  icon_size=(0, 0),
                                                  icon_anchor=(-6, 8))).add_to(fmap)
    return fmap


def duplicate_declarations(fmap) -> dict[str, int]:
    fmap.render()
    js, _ = _generate_leaflet_string(fmap)
    names = DECL.findall(js)
    return {n: names.count(n) for n in set(names) if names.count(n) > 1}


def main() -> int:
    db = nc.NotamDatabase()
    db.update_from_json_api(series="C")
    when = datetime.now(timezone.utc)
    soon = nc.SOON_HOURS_DEFAULT
    failures = 0

    print(f"{len(db.notams)} C-NOTAMs, {len(db.with_location())} mapped\n")

    multi = [n for n in db.notams if len(n.geometries) > 1]
    print(f"multi-geometry NOTAMs: {[n.id for n in multi] or 'none'}\n")

    for mode in MODES:
        notams = db.filter(mode, when=when, soon_hours=soon)
        fmap = build_map(notams, when, soon)
        dupes = duplicate_declarations(fmap)
        shapes = sum(len(n.geometries) for n in notams)
        if dupes:
            failures += 1
            print(f"FAIL {mode:<14} notams={len(notams):>3} shapes={shapes:>3} "
                  f"-> duplicate vars: {dupes}")
        else:
            print(f"ok   {mode:<14} notams={len(notams):>3} shapes={shapes:>3} "
                  f"-> no duplicate declarations")

    # Explicit guard on the NOTAM that triggered the bug.
    print()
    target = next((n for n in db.notams if len(n.geometries) > 1), None)
    if target:
        fmap = build_map([target], when, soon)
        dupes = duplicate_declarations(fmap)
        if dupes:
            failures += 1
            print(f"FAIL {target.id} alone -> {dupes}")
        else:
            print(f"ok   {target.id} alone ({len(target.geometries)} areas) "
                  f"-> no duplicate declarations")

    # Negative control: prove the test can actually catch the bug.
    print()
    if target:
        bad = folium.Map(location=(22.3, 114.17), zoom_start=10)
        shared = folium.Popup("<b>shared</b>", max_width=380)
        for g in target.geometries:
            folium.Polygon(locations=g.coordinates, popup=shared).add_to(bad)
        if duplicate_declarations(bad):
            print("ok   negative control: shared Popup IS detected as duplicate")
        else:
            failures += 1
            print("FAIL negative control: shared Popup went undetected "
                  "(test is not sensitive)")

    print()
    print("ALL PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
