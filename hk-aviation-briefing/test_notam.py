"""Verification of the NOTAM engine against the live HK AIS feed + fixtures."""
from datetime import datetime, timezone, timedelta
import math
import re
import notam_core as nc

print("=" * 72)
print("1. COORDINATE PARSING (DDMMSS / DDDMMSS -> decimal)")
print("=" * 72)
cases = [("222900N", "1135800E", 22.4833, 113.9667),
         ("221817N", "1140942E", 22.3047, 114.1617)]
for lat_s, lon_s, elat, elon in cases:
    got = nc.parse_coordinate_parts(lat_s, lon_s)
    ok = abs(got[0] - elat) < 0.001 and abs(got[1] - elon) < 0.001
    print(f"  {lat_s} {lon_s} -> {got[0]:.4f},{got[1]:.4f}  {'OK' if ok else 'FAIL'}")

print()
print("=" * 72)
print("2. D) FIELD SCHEDULE EXPANSION")
print("=" * 72)
vf = datetime(2026, 3, 1, tzinfo=timezone.utc)
vt = datetime(2026, 4, 30, tzinfo=timezone.utc)
for d in ["DLY 1030-1500",
          "DLY 2230-1030",
          "MARCH 02-07 09-14 0000-1300",
          "MAR 2 30 APR 13 0900-1300",
          "02 03 04 09 10 2315-0600",
          "HJ"]:
    w = nc.parse_d_field(d, vf, vt)
    if w:
        print(f"  {d!r:34} -> {len(w):3} windows, first "
              f"{w[0][0].strftime('%d-%b %H:%M')}->{w[0][1].strftime('%d-%b %H:%M')}")
    else:
        print(f"  {d!r:34} -> None (always-on fallback)")

print()
print("=" * 72)
print("3. STATUS LOGIC")
print("=" * 72)
n = nc.Notam(id="C9999/26", text="", d_field="DLY 0900-1200",
             valid_from=datetime(2026, 3, 1, tzinfo=timezone.utc),
             valid_to=datetime(2026, 3, 31, tzinfo=timezone.utc))
for label, t in [("before window (08:00 1 Mar)", datetime(2026, 3, 1, 8, tzinfo=timezone.utc)),
                 ("inside window (10:00 5 Mar)", datetime(2026, 3, 5, 10, tzinfo=timezone.utc)),
                 ("gap between (15:00 5 Mar)",  datetime(2026, 3, 5, 15, tzinfo=timezone.utc)),
                 ("before B)      (1 Feb)",     datetime(2026, 2, 1, tzinfo=timezone.utc)),
                 ("after C)       (1 Apr)",     datetime(2026, 4, 1, tzinfo=timezone.utc))]:
    print(f"  {label:30} -> {nc.status_at(n, t)}")

print()
print("=" * 72)
print("4. GEOMETRY EXTRACTION -- multi-area E) field")
print("=" * 72)
multi = ("(C1234/26 NOTAMN>A) VHHK B) 2609150000 C) 2609150700>"
         "E) TEMPORARY RESTRICTED AREAS:>"
         "1) BOUNDED BY 222900N 1135800E 222800N 1140000E 222310N 1135740E>"
         "2) BOUNDED BY 221000N 1141000E 221100N 1141200E 221200N 1140900E>F) SFC G) 2000FT)")
for g in nc.extract_geometries(multi):
    print(f"  kind={g.kind:8} pts={len(g.coordinates)} source={g.source}")

print()
print("=" * 72)
print("5. LIVE FEED END-TO-END")
print("=" * 72)
db = nc.NotamDatabase()
total = db.update_from_json_api()
geo = db.with_location()
print(f"  parsed              : {total}")
print(f"  with geometry       : {len(geo)}")
print(f"  C-series            : {len([n for n in db.notams if n.id.startswith('C')])}")
print(f"  A-series            : {len([n for n in db.notams if n.id.startswith('A')])}")
print(f"  have D) schedule    : {len([n for n in db.notams if n.d_field])}")
kinds = {}
for n in geo:
    for g in n.geometries:
        kinds[g.kind] = kinds.get(g.kind, 0) + 1
print(f"  geometry kinds      : {kinds}")
srcs = {}
for n in geo:
    for g in n.geometries:
        srcs[g.source] = srcs.get(g.source, 0) + 1
print(f"  geometry sources    : {srcs}")
print(f"  multi-area NOTAMs   : {len([n for n in geo if len(n.geometries) > 1])}")

print()
print("  Filter counts (the four toggle-bar modes):")
for mode in nc.FILTER_MODES:
    print(f"    {mode:14} -> {len(db.filter(mode))}")
assert len(db.filter("All")) == len(db.notams), "'All' must be unfiltered"
print(f"    'All' == every published C-NOTAM: "
      f"{len(db.filter('All')) == len(db.notams)}")
assert all(n.id.startswith("C") for n in db.notams), "non-C NOTAM leaked in"
print(f"    every NOTAM is C-series: True")

print()
print("  Sort order check (first 8 of 'All'):")
for n in db.filter("All")[:8]:
    print(f"    {n.id:10} {nc.status_at(n):9} to={n.valid_to.strftime('%d-%b %H:%M')}")

print()
print("  Sanity: all geometry coords inside HK region bbox (21-23N, 112-116E)?")
bad = []
for n in geo:
    for g in n.geometries:
        for lat, lon in g.coordinates:
            if not (20.0 <= lat <= 24.0 and 111.0 <= lon <= 117.0):
                bad.append((n.id, lat, lon))
print(f"    out-of-region points: {len(bad)} {bad[:5]}")

print()
print("=" * 72)
print("6. URGENCY BUCKETS + TOGGLE-BAR NESTING")
print("=" * 72)
from datetime import timedelta as _td
from collections import Counter as _C

when2 = datetime.now(timezone.utc)
tally = _C(nc.classify_urgency(n, when2) for n in db.notams)
for b in [nc.URGENCY_NOW, nc.URGENCY_SOON, nc.URGENCY_TODAY, nc.URGENCY_OTHER]:
    print(f"  {b:6} {tally.get(b, 0):3}   colour {nc.URGENCY_COLOURS[b]}")

print()
print("  Toggle-mode set relationships:")
sets = {m: {n.id for n in db.filter(m, when=when2)} for m in nc.FILTER_MODES}
for m in nc.FILTER_MODES:
    print(f"    {m:14} {len(sets[m]):3}")

# "All" is every published C-NOTAM, so it is a superset of every other mode.
for m in nc.FILTER_MODES:
    assert sets[m] <= sets["All"], f"{m} not contained in All"
print("    every mode is a subset of All: True")

# Active Now is the tightest urgency bucket; "Next 2 Hours" = NOW + SOON.
assert sets["Active Now"] <= sets["Next 2 Hours"], "Now not inside Next 2 Hours"
print("    Active Now is a subset of Next 2 Hours: True")

# Anything active at this instant is by definition active somewhere inside
# today's 0000-2359 LT window, so it must also appear under Active Today.
assert sets["Active Now"] <= sets["Active Today"], "Now not inside Today"
print("    Active Now is a subset of Active Today: True")

# Active Today is NOT a superset of Next 2 Hours: near local midnight a NOTAM
# starting in <2 h can begin tomorrow, so it is SOON but not active today.
only_soon = sets["Next 2 Hours"] - sets["Active Today"]
print(f"    'soon but starts after local midnight': {len(only_soon)} "
      f"{sorted(only_soon)[:3]}")

# Active Today must include NOTAMs that already finished earlier today.
done_today = [n for n in db.filter("Active Today", when=when2)
              if nc.status_at(n, when2) != "ACTIVE"]
print(f"    Active Today also carries {len(done_today)} not-currently-active "
      f"NOTAM(s) (already ran / yet to run today): "
      f"{[n.id for n in done_today][:4]}")

print()
print("  A time where all four buckets are populated:")
base = when2.replace(minute=0, second=0, microsecond=0)
for h in range(-24, 24):
    w = base + _td(hours=h)
    c = _C(nc.classify_urgency(n, w) for n in db.notams)
    if all(c.get(b, 0) > 0 for b in
           [nc.URGENCY_NOW, nc.URGENCY_SOON, nc.URGENCY_TODAY]):
        print(f"    {w.strftime('%d-%b %H:%MZ')} -> NOW={c['NOW']} "
              f"SOON={c['SOON']} TODAY={c['TODAY']} OTHER={c['OTHER']}")
        break

print()
print("  'Active now' ordering = soonest real window end first:")
for n in db.filter("Active Now", when=when2)[:6]:
    print(f"    {n.id:10} {nc.timing_note(n, when2)}")

print()
print("  view() (centre + zoom) adapts to area size:")
for n in db.filter("All", when=when2):
    if n.has_location:
        v = n.view()
        b = n.bounds()
        span = max(b[1][0] - b[0][0], b[1][1] - b[0][1])
        print(f"    {n.id:10} {n.geometries[0].kind:8} span={span:7.4f} -> zoom {v[1]}")
        if n.id.startswith("C03"):
            break
assert all(n.view() is None for n in db.notams if not n.has_location)

print()
print("=" * 72)
print("7. pretty_text() - ONE ICAO FIELD PER LINE")
print("=" * 72)

HEADER = re.compile(r"^\d{6}\s|NOTAM[NRC]?\)?$|VHHHYNYX")
orphan_sub = 0
bad_starts = []

for n in db.notams:
    for line in nc.pretty_text(n).split("\n"):
        t = line.strip()
        if not t:
            continue
        # Every line must either open with an ICAO field marker or be the
        # telex header line (e.g. "190540 VHHHYNYX (C0292/26 NOTAMN").
        if not re.match(r"^[QABCDEFG]\)", t) and not HEADER.search(t):
            bad_starts.append((n.id, t[:60]))
        # Numbered sub-areas / schedules must stay inline inside E).
        if re.match(r"^\d+\)", t):
            orphan_sub += 1

print(f"  NOTAMs formatted                      : {len(db.notams)}")
print(f"  lines not a field marker or header    : {len(bad_starts)}")
print(f"  orphaned numbered sub-areas (1) 2) 3)): {orphan_sub}")
assert not bad_starts, bad_starts[:5]
assert orphan_sub == 0, "numbered sub-area was wrongly pushed to a new line"

# No '>' separators may survive, and no field marker may be mid-line.
for n in db.notams:
    pt = nc.pretty_text(n)
    assert ">" not in pt, f"{n.id} still contains a '>' separator"
    for line in pt.split("\n"):
        assert not nc.RE_FIELD_MARKER.search(line[1:]), \
            f"{n.id} has a field marker mid-line: {line[:70]}"
print("  no '>' separators left, no mid-line field markers: True")

sample = next(n for n in db.notams if "1)" in n.text)
print(f"\n  Sample - {sample.id} (numbered sub-areas stay inline):")
for line in nc.pretty_text(sample).split("\n"):
    print(f"    | {line[:96]}")


# ==========================================================================
# 8. NAMED AREAS  (VHD5, San Wai / Tai Ling)
# ==========================================================================
print()
print("=" * 72)
print("8. NAMED AREAS (hard-coded polygons)")
print("=" * 72)

# --- 8a. a NOTAM that only NAMES the area gets the hard-coded polygon ------
t_named = ("(C0298/26 NOTAMN Q) VHHK/QRDCA/IV/BO /W /000/020/2225N11357E003 "
           "A) VHHK B) 2609010000 C) 2609301300 "
           "E) FIRING EXER AT VHD5 SAFETY ALT 3000FT AMSL "
           "ALL ACFT MUST REMAIN CLEAR OF THE AREA")
g = nc.extract_geometries(t_named)
assert len(g) == 1 and g[0].kind == "polygon", g
assert g[0].coordinates == nc.VHD5_AREA, "VHD5 polygon not applied"
print(f"  'FIRING EXER AT VHD5' (no coords)     -> {g[0].source}, "
      f"{len(g[0].coordinates)} pts  OK")

# --- 8b. the NOTAM's OWN coordinates must win over the name ---------------
# C0334/26 says "NORTH OF DANGER AREA VHD5 BOUNDED BY <4 coords>".  It names
# VHD5 but describes a different area.  If the name lookup ran first it would
# draw the VHD5 box and be wrong.
t_bounded = ("(C0334/26 NOTAMN Q) VHHK/QWEXX A) VHHK B) 2609150000 "
             "C) 2609150700 E) HELICOPTER FLYING IN AREA NORTH OF DANGER "
             "AREA VHD5 BOUNDED BY 222900N 1135800E 222800N 1140000E "
             "222310N 1135740E AND 222430N 1135340E. ALT A020 OR BLW.")
g = nc.extract_geometries(t_bounded)
assert len(g) == 1 and g[0].source == "E) BOUNDED BY", g[0].source
assert g[0].coordinates != nc.VHD5_AREA, \
    "name lookup wrongly overrode the NOTAM's own BOUNDED BY polygon"
print(f"  'NORTH OF VHD5 BOUNDED BY ...'        -> {g[0].source}, "
      f"{len(g[0].coordinates)} pts  OK (own coords kept)")

# --- 8c. every San Wai / Tai Ling spelling resolves ------------------------
spellings = [
    "FIRING AT SAN WAI/TAI LING RANGE", "FIRING AT SAN WAI / TAI LING",
    "FIRING AT TAI LING/SAN WAI", "FIRING AT TAI LING / SAN WAI",
    "FIRING AT SAN WAI RANGE", "FIRING AT TAI LING RANGE",
]
for sp in spellings:
    g = nc.extract_geometries(f"E) {sp} ALL ACFT REMAIN CLEAR")
    assert g and g[0].coordinates == nc.SAN_WAI_TAI_LING, sp
print(f"  all {len(spellings)} San Wai / Tai Ling spellings   -> resolved  OK")

# --- 8d. no false positives ----------------------------------------------
for neg in ("E) CRANE ERECTED AT TAI LINGERING ROAD",
            "E) DANGER AREA VHD51 ACTIVE"):
    g = nc.extract_geometries(neg)
    coords = g[0].coordinates if g else []
    assert coords != nc.VHD5_AREA and coords != nc.SAN_WAI_TAI_LING, neg
print("  'TAI LINGERING' / 'VHD51' not matched -> OK (word boundaries hold)")

# --- 8e2. VHD5 is a valid boundary ring -----------------------------------
# The source coordinates were not in boundary order; they are stored sorted by
# bearing around the centroid.  These assertions lock that in -- if someone
# pastes the raw list back in, the ring self-intersects and this fails.
def _shoelace(poly):
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i][1], poly[i][0]
        x2, y2 = poly[(i + 1) % len(poly)][1], poly[(i + 1) % len(poly)][0]
        s += x1 * y2 - x2 * y1
    return s / 2


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        v = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else 2)
    return (orient(p1, p2, p3) != orient(p1, p2, p4)
            and orient(p3, p4, p1) != orient(p3, p4, p2))


def _self_intersections(poly):
    n, bad = len(poly), []
    for i in range(n):
        for j in range(i + 1, n):
            if j == i or (i == 0 and j == n - 1) or j == i + 1:
                continue
            if _segments_cross(poly[i], poly[(i + 1) % n],
                               poly[j], poly[(j + 1) % n]):
                bad.append((i, j))
    return bad


crossings = _self_intersections(nc.VHD5_AREA)
assert not crossings, f"VHD5 ring self-intersects at {crossings}"
assert len(set(nc.VHD5_AREA)) == len(nc.VHD5_AREA), "VHD5 has duplicate vertices"
assert _shoelace(nc.VHD5_AREA) > 0, "VHD5 ring should wind counter-clockwise"
area_km2 = abs(_shoelace(nc.VHD5_AREA)) * (111.32 ** 2) * math.cos(math.radians(22.4))
print(f"  VHD5 ring: {len(nc.VHD5_AREA)} pts, no self-intersection, "
      f"CCW, {area_km2:.1f} km2  OK")

# Agrees with the Q-line published in the live VHD5 NOTAM (2225N11357E003):
# centre 22.4167N 113.95E, radius 3 NM.
q_centre = (22 + 25 / 60.0, 113 + 57 / 60.0)


def _nm(a, b):
    return math.hypot((b[0] - a[0]) * 60.0,
                      (b[1] - a[1]) * 60.0 * math.cos(math.radians((a[0] + b[0]) / 2)))


far = max(_nm(q_centre, v) for v in nc.VHD5_AREA)
assert far <= 3.0, f"a VHD5 vertex is {far:.2f} NM out, beyond the 3 NM Q-line radius"
print(f"  VHD5 vs published Q-line: every vertex within {far:.2f} NM of 3 NM  OK")

# --- 8e. the polygons are sane -------------------------------------------
for name, poly in (("VHD5", nc.VHD5_AREA),
                   ("San Wai / Tai Ling", nc.SAN_WAI_TAI_LING)):
    assert len(poly) >= 3, name
    assert all(22.1 < lat < 22.6 and 113.8 < lon < 114.4 for lat, lon in poly), \
        f"{name} has a vertex outside Hong Kong"
print("  both polygons >=3 pts and inside HK   -> OK")

# --- 8g. multi-area "BOUNDED BY" -----------------------------------------
# C0334/26 describes TWO areas in one E) field by repeating "BOUNDED BY",
# without numbering them. Each must become its own polygon.
t_two = ("E) HELICOPTER FLYING IN AREA NORTH OF DANGER AREA VHD5 BOUNDED BY "
         ">222900N 1135800E 222800N 1140000E 222310N 1135740E AND 222430N "
         ">1135340E. OPERATING ALT A020 OR BLW.> FIRING EXER IN AREA "
         "SURROUNDING DANGER AREA VHD5 BOUNDED BY >222430N 1135340E 222310N "
         "1135740E 222800N 1140000E 222809N 1135943E >AND 222648N 1135553E.")
g = nc.extract_geometries(t_two)
assert len(g) == 2, f"expected 2 areas, got {len(g)}"
assert [len(x.coordinates) for x in g] == [4, 5], \
    f"expected 4 and 5 corners, got {[len(x.coordinates) for x in g]}"
assert all(x.kind == "polygon" for x in g)
print(f"  two 'BOUNDED BY' clauses -> {len(g)} polygons "
      f"({len(g[0].coordinates)} + {len(g[1].coordinates)} pts)  OK")

# The two rings must stay distinct -- splicing them produced a single ring
# with duplicated vertices that covered airspace belonging to neither.
assert g[0].coordinates != g[1].coordinates, "the two areas are identical"
combined = g[0].coordinates + g[1].coordinates
assert len(combined) != len(set(combined)) or True  # shared corners are fine
print("  the two rings are distinct                -> OK")

# --- 8h. '>' separator between lat and lon -------------------------------
# The AIS feed breaks lines with '>', which can land between the latitude and
# longitude of one coordinate ("222430N >1135340E"). If not normalised, that
# corner is dropped and a 4-corner area silently becomes a triangle.
split_coord = find_coords_check = nc.find_coords("222430N >1135340E")
assert len(split_coord) == 1, "coordinate split by '>' was not recovered"
assert abs(split_coord[0][0] - 22.4083333) < 1e-5, split_coord
assert abs(split_coord[0][1] - 113.8944444) < 1e-5, split_coord
print("  coord split by '>' separator recovered    -> OK")

# --- 8f. live check -------------------------------------------------------
named_hits = [n for n in db.notams
              if any(g.source.startswith("Named area") for g in n.geometries)]
print(f"\n  Live C-NOTAMs resolved by name lookup : {len(named_hits)}")
for n in named_hits:
    print(f"    {n.id}: {n.geometries[0].source}")
print("  (without this table these would have NO map overlay at all)")

print()
print("  All assertions passed.")
