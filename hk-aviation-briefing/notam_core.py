"""
notam_core.py
=============
Framework-independent NOTAM engine, ported from the Kivy reference app
(`NotamDatabase` + `NotamMapHandler` + the app-level filter/sort helpers).

Nothing in here imports Streamlit, Kivy or folium, so it is unit-testable
on its own and reusable from any UI.

Pipeline
--------
    fetch JSON  ->  split into sections  ->  parse header (B/C/D)
                ->  parse geometry (E field)  ->  status at time T
                ->  filter + sort  ->  hand to the map renderer
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
NOTAM_URL = "https://www.notam.ais.gov.hk/data"
NOTAM_REFRESH_MINUTES = 240          # 4 hours, same as the reference app
HKT = timezone(timedelta(hours=8))   # Hong Kong local time

# Regex library -------------------------------------------------------------
# The reference app only accepted C-series IDs. We keep both so the UI can
# choose, which fixes a silent data-loss bug (see README notes).
RE_NOTAM_ID_C = re.compile(r"(C\d{4}/\d{2})")
RE_NOTAM_ID_ANY = re.compile(r"\b([A-Z]\d{4}/\d{2})\b")
RE_BC_FIELD = re.compile(r"B\)\s*(\d{10})\s*C\)\s*(\d{10})")
RE_C_PERM = re.compile(r"C\)\s*PERM")
RE_D_FIELD = re.compile(r"D\)\s*(.+?)(?:\s*E\)|>|$)", re.DOTALL)
RE_E_FIELD = re.compile(r"E\)(.*?)(?:F\)|$)", re.DOTALL)
RE_COORD = re.compile(r"(\d{6}[NS])\s*(\d{7}[EW])")
RE_AREA_MARKER = re.compile(r"\b(\d+)\)")
# Splits an E) field that describes several areas by repeating "BOUNDED BY".
RE_BOUNDED_BY = re.compile(r"BOUNDED\s+BY", re.I)
RE_TIME_RANGE = re.compile(r"(\d{4})-(\d{4})")
RE_DAY_RANGE = re.compile(r"\b(\d{1,2})-(\d{1,2})\b")
RE_DAY_SINGLE = re.compile(r"\b(\d{1,2})\b")
# The reference used `(\d+)([M|NM]+)\s+RADIUS` -- that character class is a
# bug (it matches "|" and "MN"). Proper alternation instead:
RE_RADIUS = re.compile(r"(\d+(?:\.\d+)?)\s*(NM|KM|M|FT)\s+RADIUS", re.I)

PRD_PATTERNS = [
    re.compile(r"\b(VHP\d+)\b"),      # Prohibited
    re.compile(r"\b(VHR\d+)\b"),      # Restricted
    re.compile(r"\b(VHD\d+)\b"),      # Danger
    re.compile(r"\b(ZJ\(D\)\d+)\b"),  # Other danger
]

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5,
    "JUNE": 6, "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10,
    "NOVEMBER": 11, "DECEMBER": 12,
}
# Longest-first so "MARCH" wins over "MAR"
MONTH_NAMES_SORTED = sorted(MONTH_MAP, key=len, reverse=True)

# --------------------------------------------------------------------------
# Hard-coded named areas
#
# Some NOTAMs name an area instead of giving its coordinates -- "FIRING EXER
# AT VHD5" carries no lat/long at all, so without a lookup the NOTAM has no
# geometry and silently never appears on the map.  These polygons fill that
# gap.
#
# NOTE ON THE REFERENCE: `named_locations` at reference line 5213 defined VHD5
# as a 4-point box at (22.2333, 113.5667)-(22.2667, 113.6000).  That is ~22.7
# NM from the Q-line centre published in the live VHD5 NOTAM (C0298/26, Q-line
# 2225N11357E radius 003), so it was plotting the danger area in open sea well
# south-west of where it actually is.  The polygon below sits 0.87 NM from that
# Q-line centre with every vertex inside the declared 3 NM radius, so it agrees
# with the published NOTAM.
# --------------------------------------------------------------------------
# Vertices are stored as a proper boundary ring, walked counter-clockwise.
#
# The source list was not in boundary order: its second entry jumped to the far
# north while entries 3 and 4 sat in the south-west, which drew a deep notch
# across the northern edge.  The ring below is the same nine points sorted by
# bearing around the centroid (longitude scaled by cos(lat) so the angles are
# geometric rather than skewed by the lat/lon aspect ratio).
#
# The result is a simple, non-self-intersecting polygon of ~22.1 km^2 -- 11%
# larger than the unsorted order, which had been cutting a chunk out of the
# danger area.  It is very slightly concave: (22.413333, 113.935833) is the one
# vertex not on the convex hull, so a convex-hull fit would have been wrong too.
VHD5_AREA = [
    (22.393611, 113.924167), (22.378611, 113.927500), (22.375833, 113.932500),
    (22.374444, 113.939722), (22.399444, 113.958889), (22.436944, 113.978889),
    (22.448055, 113.961666), (22.413333, 113.935833), (22.415000, 113.922778),
]

SAN_WAI_TAI_LING = [
    (22.51515, 114.13711), (22.51765, 114.14065), (22.51727, 114.14419),
    (22.51281, 114.14389), (22.51190, 114.14546), (22.51038, 114.14340),
]

# (label, polygon, trigger pattern).
#
# The source data listed six San Wai / Tai Ling aliases ("SAN WAI/TAI LING",
# "TAI LING / SAN WAI", "TAI LING RANGE", ...) all carrying identical
# coordinates.  One alternation covers every spelling -- including spacing and
# slash variations -- so the polygon is stored once rather than six times and
# cannot drift between copies.
#
# Matching is on word boundaries, not substrings, so "TAI LINGERING" or a
# hypothetical "VHD50" cannot trigger a false overlay.
NAMED_AREAS: list[tuple[str, list, "re.Pattern[str]"]] = [
    ("VHD5", VHD5_AREA, re.compile(r"\bVHD\s?5\b")),
    ("San Wai / Tai Ling Range", SAN_WAI_TAI_LING,
     re.compile(r"\b(?:SAN\s+WAI|TAI\s+LING)\b")),
]

STATUS_ACTIVE, STATUS_FUTURE, STATUS_INACTIVE = "ACTIVE", "FUTURE", "INACTIVE"
STATUS_ORDER = {STATUS_ACTIVE: 0, STATUS_FUTURE: 1, STATUS_INACTIVE: 2}

# --------------------------------------------------------------------------
# Urgency buckets -- drive both the map colour and the toggle bar.
#   NOW   : active at the evaluation instant
#   SOON  : becomes active within the next `soon_hours` (default 2 h)
#   TODAY : active somewhere in the HK local day, but not NOW or SOON
#   OTHER : outside today entirely
# --------------------------------------------------------------------------
URGENCY_NOW, URGENCY_SOON, URGENCY_TODAY, URGENCY_OTHER = \
    "NOW", "SOON", "TODAY", "OTHER"

SOON_HOURS_DEFAULT = 2

URGENCY_ORDER = {URGENCY_NOW: 0, URGENCY_SOON: 1,
                 URGENCY_TODAY: 2, URGENCY_OTHER: 3}

# Red = happening now, pink = imminent, amber = later today, grey = other.
URGENCY_COLOURS = {
    URGENCY_NOW:   "#e60000",   # red
    URGENCY_SOON:  "#ff7d7d",   # light red / pink
    URGENCY_TODAY: "#ffb300",   # amber
    URGENCY_OTHER: "#9e9e9e",   # grey
}
URGENCY_BADGES = {
    URGENCY_NOW:   "🔴 ACTIVE NOW",
    URGENCY_SOON:  "🌸 WITHIN 2 H",
    URGENCY_TODAY: "🟠 LATER TODAY",
    URGENCY_OTHER: "⚪ NOT TODAY",
}
URGENCY_SHORT = {
    URGENCY_NOW: "NOW", URGENCY_SOON: "≤2H",
    URGENCY_TODAY: "TODAY", URGENCY_OTHER: "—",
}

# Toggle-bar options. "All" is deliberately NOT a bucket union -- it means
# every C-NOTAM currently published on the website, unfiltered.
FILTER_MODES = ("All", "Active Today", "Next 2 Hours", "Active Now")

# ICAO NOTAM field markers, used to lay the raw text out one field per line.
# Single letters only, so numbered sub-areas "1)" "2)" inside E) stay inline.
RE_FIELD_MARKER = re.compile(r"(?<![A-Z0-9])([QABCDEFG])\)")


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Geometry:
    """One drawable shape: 'polygon' | 'circle' | 'point'."""
    kind: str
    coordinates: list = field(default_factory=list)   # [(lat, lon), ...]
    radius_m: float | None = None
    source: str = "E-field coordinates"

    def centroid(self) -> tuple[float, float] | None:
        if not self.coordinates:
            return None
        lats = [c[0] for c in self.coordinates]
        lons = [c[1] for c in self.coordinates]
        return sum(lats) / len(lats), sum(lons) / len(lons)

    def bounds(self) -> list[list[float]] | None:
        """[[min_lat, min_lon], [max_lat, max_lon]] for map fitting."""
        if not self.coordinates:
            return None
        lats = [c[0] for c in self.coordinates]
        lons = [c[1] for c in self.coordinates]
        if self.kind == "circle" and self.radius_m:
            dlat = self.radius_m / 111_320.0
            clat = lats[0]
            dlon = self.radius_m / (111_320.0 * max(math.cos(math.radians(clat)), 1e-6))
            return [[clat - dlat, lons[0] - dlon], [clat + dlat, lons[0] + dlon]]
        return [[min(lats), min(lons)], [max(lats), max(lons)]]


@dataclass
class Notam:
    id: str
    text: str
    valid_from: datetime
    valid_to: datetime
    d_field: str | None = None
    is_perm: bool = False
    geometries: list[Geometry] = field(default_factory=list)

    @property
    def has_location(self) -> bool:
        return bool(self.geometries)

    def bounds(self) -> list[list[float]] | None:
        boxes = [g.bounds() for g in self.geometries]
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return [
            [min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)],
            [max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)],
        ]

    def view(self) -> tuple[tuple[float, float], int] | None:
        """
        (centre, zoom) that frames this NOTAM's whole extent.

        The Kivy app used hard-coded thresholds and read keys that its own
        multi-polygon branch had deleted (a KeyError risk). Deriving zoom
        from the real bounding box covers every geometry type uniformly.
        """
        box = self.bounds()
        if not box:
            return None
        (min_lat, min_lon), (max_lat, max_lon) = box
        centre = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
        span = max(max_lat - min_lat,
                   (max_lon - min_lon) * math.cos(math.radians(centre[0])))
        for threshold, zoom in ((0.0025, 14), (0.006, 13), (0.012, 12),
                                (0.025, 11), (0.05, 10), (0.12, 9),
                                (0.25, 8), (0.6, 7)):
            if span <= threshold:
                return centre, zoom
        return centre, 6


# --------------------------------------------------------------------------
# Coordinate parsing
# --------------------------------------------------------------------------
def parse_coordinate_parts(lat_str: str, lon_str: str) -> tuple[float, float] | None:
    """'222900N', '1135800E'  ->  (22.4833, 113.9667)   [DDMMSS / DDDMMSS]"""
    try:
        lat = int(lat_str[0:2]) + int(lat_str[2:4]) / 60 + int(lat_str[4:6]) / 3600
        lon = int(lon_str[0:3]) + int(lon_str[3:5]) / 60 + int(lon_str[5:7]) / 3600
        if lat_str[6] == "S":
            lat = -lat
        if lon_str[7] == "W":
            lon = -lon
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return (lat, lon)
    except (ValueError, IndexError):
        return None


def find_coords(text: str) -> list[tuple[float, float]]:
    """Pull every 'DDMMSSN DDDMMSSE' pair out of a block of NOTAM text.

    The AIS feed uses '>' as a hard line separator, and it can land *between*
    the latitude and longitude of a single coordinate -- C0334/26 contains
    "222430N >1135340E".  Left in place, the lat/long pattern does not match
    and that corner is silently dropped, so a 4-corner area is drawn as a
    triangle.  Normalising '>' to whitespace first avoids that.
    """
    cleaned = text.replace(">", " ")
    out = [parse_coordinate_parts(a, b) for a, b in RE_COORD.findall(cleaned)]
    return [c for c in out if c]


def _radius_to_metres(value: float, unit: str) -> float:
    unit = unit.upper()
    return {"NM": value * 1852.0, "KM": value * 1000.0,
            "FT": value * 0.3048, "M": value}.get(unit, value)


# --------------------------------------------------------------------------
# Geometry extraction  (port of extract_location_data)
# --------------------------------------------------------------------------
def extract_geometries(notam_text: str, prd_db: dict | None = None) -> list[Geometry]:
    """
    Resolution order, highest priority first:
      1. Multi-area E) field  ("1) ... 2) ...")  -> several polygons
      2. PRD area reference   (VHP/VHR/VHD/ZJ(D)) -> looked-up shape
      3. "BOUNDED BY" polygon inside E)
      4. Whole-text: RADIUS circle / polygon / single point
      5. Named area lookup (VHD5, San Wai / Tai Ling) -- last resort

    Coordinates the NOTAM states itself always win over a name lookup; see the
    comment at step 5 for the case that forces this ordering.
    """
    e_match = RE_E_FIELD.search(notam_text)
    e_field = e_match.group(1) if e_match else ""

    # --- 1. multi-area -----------------------------------------------------
    if e_field:
        markers = list(RE_AREA_MARKER.finditer(e_field))
        if len(markers) > 1:
            geos: list[Geometry] = []
            for i, m in enumerate(markers):
                start = m.start()
                end = markers[i + 1].start() if i + 1 < len(markers) else len(e_field)
                coords = find_coords(e_field[start:end])
                if len(coords) >= 3:
                    geos.append(Geometry("polygon", coords,
                                         source=f"E) area {m.group(1)}"))
            if geos:
                return geos

    # --- 2. PRD lookup -----------------------------------------------------
    if prd_db:
        for pat in PRD_PATTERNS:
            hit = pat.search(notam_text)
            if not hit:
                continue
            area = prd_db.get(hit.group(1))
            if not area:
                continue
            code = hit.group(1)
            gtype = area.get("geometry_type")
            if gtype == "circle" and area.get("center"):
                r = _radius_to_metres(area["radius"], area.get("radius_unit", "NM"))
                return [Geometry("circle", [tuple(area["center"])], r,
                                 source=f"PRD area {code}")]
            if gtype == "polygon" and area.get("coordinates"):
                return [Geometry("polygon", list(area["coordinates"]),
                                 source=f"PRD area {code}")]
            if gtype == "sectors" and area.get("center") and area.get("sectors"):
                return [Geometry("polygon",
                                 _sectors_to_polygon(area),
                                 source=f"PRD area {code} (sector)")]

    upper = notam_text.upper()

    # --- 3. BOUNDED BY inside E) ------------------------------------------
    # A NOTAM may describe SEVERAL areas in one E) field by repeating the
    # phrase "BOUNDED BY", without numbering them "1) 2)".  C0334/26 does
    # exactly that: a helicopter area north of VHD5, then a firing area
    # surrounding VHD5.
    #
    # Collecting every coordinate in the field into a single ring -- which is
    # what a naive "find all coords" does -- splices the two boundaries
    # together into one zig-zagging polygon that covers airspace belonging to
    # neither.  Split on each "BOUNDED BY" and build one polygon per clause.
    if e_field and "BOUNDED BY" in e_field.upper():
        clauses = RE_BOUNDED_BY.split(e_field)[1:]   # drop the preamble
        geos = []
        for idx, clause in enumerate(clauses, 1):
            coords = find_coords(clause)
            if len(coords) >= 3:
                label = ("E) BOUNDED BY" if len(clauses) == 1
                         else f"E) BOUNDED BY (area {idx} of {len(clauses)})")
                geos.append(Geometry("polygon", coords, source=label))
        if geos:
            return geos

    # --- 4. whole-text coordinates ----------------------------------------
    coords = find_coords(notam_text)
    if coords:
        r = RE_RADIUS.search(notam_text)
        if r:
            radius_m = _radius_to_metres(float(r.group(1)), r.group(2))
            return [Geometry("circle", [coords[0]], radius_m, source="RADIUS")]
        if "BOUNDED BY" in upper or len(coords) >= 3:
            return [Geometry("polygon", coords, source="Coordinate list")]
        return [Geometry("point", [coords[0]], source="Single coordinate")]

    # --- 5. named area (last resort) --------------------------------------
    # Deliberately LAST.  A named area is a lookup standing in for coordinates
    # the NOTAM never gave, so it must not override coordinates the NOTAM did
    # give.
    #
    # C0334/26 is exactly why: "HELICOPTER FLYING IN AREA NORTH OF DANGER AREA
    # VHD5 BOUNDED BY <4 coords>".  It mentions VHD5 but describes a different
    # area just north of it.  Resolved earlier, the mere mention of "VHD5"
    # would replace its real 4-point polygon with the VHD5 box and draw the
    # wrong airspace.  Resolved last, it keeps its own coordinates, and
    # C0298/26 ("FIRING EXER AT VHD5", no coordinates anywhere) still gets the
    # hard-coded polygon.
    for label, polygon, pattern in NAMED_AREAS:
        if pattern.search(upper):
            return [Geometry("polygon", list(polygon),
                             source=f"Named area ({label})")]

    return []


def _sectors_to_polygon(area: dict) -> list[tuple[float, float]]:
    """Expand a pie-slice PRD definition into a polygon ring."""
    clat, clon = area["center"]
    radius_m = _radius_to_metres(area["radius"], area.get("radius_unit", "NM"))
    pts: list[tuple[float, float]] = []
    for sector in area["sectors"]:
        a0, a1 = sector["start_angle"], sector["end_angle"]
        if a1 < a0:
            a1 += 360
        pts.append((clat, clon))
        steps = max(10, int(abs(a1 - a0) / 5))
        for i in range(steps + 1):
            ang = math.radians(90 - (a0 + (a1 - a0) * i / steps))
            pts.append((
                clat + math.sin(ang) * radius_m / 111_320.0,
                clon + math.cos(ang) * radius_m /
                (111_320.0 * max(math.cos(math.radians(clat)), 1e-6)),
            ))
    return pts


# --------------------------------------------------------------------------
# Time parsing  (port of parse_d_field / is_notam_active_at_time)
# --------------------------------------------------------------------------
def parse_bc_times(section: str) -> tuple[datetime, datetime, bool]:
    """B) YYMMDDHHMM  C) YYMMDDHHMM  (all UTC). Handles C) PERM."""
    now = datetime.now(timezone.utc)
    m = RE_BC_FIELD.search(section)
    if m:
        try:
            f, t = m.groups()
            vf = datetime(2000 + int(f[0:2]), int(f[2:4]), int(f[4:6]),
                          int(f[6:8]), int(f[8:10]), tzinfo=timezone.utc)
            vt = datetime(2000 + int(t[0:2]), int(t[2:4]), int(t[4:6]),
                          int(t[6:8]), int(t[8:10]), tzinfo=timezone.utc)
            return vf, vt, False
        except ValueError:
            pass
    if RE_C_PERM.search(section):
        # PERM = permanent. Reference app defaulted to +30 days (a bug).
        bm = re.search(r"B\)\s*(\d{10})", section)
        vf = now
        if bm:
            s = bm.group(1)
            try:
                vf = datetime(2000 + int(s[0:2]), int(s[2:4]), int(s[4:6]),
                              int(s[6:8]), int(s[8:10]), tzinfo=timezone.utc)
            except ValueError:
                pass
        return vf, datetime(2099, 12, 31, tzinfo=timezone.utc), True
    return now, now + timedelta(days=30), False


def parse_d_field(d_field: str | None,
                  valid_from: datetime,
                  valid_to: datetime) -> list[tuple[datetime, datetime]] | None:
    """
    Turn a D) schedule into concrete UTC windows.

    Supported:
      "DLY 1030-1500"                      -> every day in B)..C)
      "DLY 2230-1030"                      -> crosses midnight
      "MARCH 02-07 09-14 0000-1300"        -> day ranges
      "MAR 2 30 APR 13 0900-1300"          -> individual days
    Unsupported (returns None -> treated as always-on): HJ, HN, SR/SS.
    """
    if not d_field:
        return None

    d = d_field.upper().strip()
    tm = RE_TIME_RANGE.search(d)
    if not tm:
        return None                                  # HJ / HN / freeform

    sh, sm = int(tm.group(1)[:2]), int(tm.group(1)[2:])
    eh, em = int(tm.group(2)[:2]), int(tm.group(2)[2:])
    if not (0 <= sh <= 23 and 0 <= eh <= 24):
        return None

    windows: list[tuple[datetime, datetime]] = []

    # ---- DLY -------------------------------------------------------------
    if "DLY" in d or "DAILY" in d:
        crosses_midnight = (sh * 60 + sm) > (eh * 60 + em)
        day = valid_from.replace(hour=0, minute=0, second=0, microsecond=0)
        last = valid_to.replace(hour=23, minute=59, second=59, microsecond=0)
        while day <= last:
            try:
                start = day.replace(hour=sh, minute=sm)
                end = ((day + timedelta(days=1)) if crosses_midnight else day) \
                    .replace(hour=eh % 24, minute=em)
                if end > valid_from and start < valid_to:
                    windows.append((max(start, valid_from), min(end, valid_to)))
            except ValueError:
                pass
            day += timedelta(days=1)
        return windows or None

    # ---- explicit dates --------------------------------------------------
    positions: list[tuple[int, str]] = []
    for name in MONTH_NAMES_SORTED:
        for m in re.finditer(r"\b" + name + r"\b", d):
            if not any(p <= m.start() < p + len(n) for p, n in positions):
                positions.append((m.start(), name))
    positions.sort(key=lambda x: x[0])

    base_year, base_month = valid_from.year, valid_from.month
    dates: set[tuple[int, int, int]] = set()

    def collect(chunk: str, month: int, year: int) -> None:
        for a, b in RE_DAY_RANGE.findall(chunk):
            a, b = int(a), int(b)
            if 1 <= a <= 31 and 1 <= b <= 31 and a <= b:
                for day in range(a, b + 1):
                    dates.add((year, month, day))
        for day_s in RE_DAY_SINGLE.findall(RE_DAY_RANGE.sub(" ", chunk)):
            day = int(day_s)
            if 1 <= day <= 31:
                dates.add((year, month, day))

    if positions:
        for i, (pos, name) in enumerate(positions):
            month = MONTH_MAP[name]
            nxt = positions[i + 1][0] if i + 1 < len(positions) else len(d)
            chunk = d[pos + len(name):nxt].replace(tm.group(0), " ")
            # Roll the year forward if the schedule wraps Dec -> Jan
            year = base_year + 1 if month < base_month - 6 else base_year
            collect(chunk, month, year)
    else:
        # No month named (e.g. "02 03 04 09 10 2315-0600"). The days belong
        # to the B)..C) period, so walk each month it spans and keep only
        # the day numbers that actually fall inside that window.
        chunk = d.replace(tm.group(0), " ")
        if not RE_DAY_RANGE.search(chunk) and not RE_DAY_SINGLE.search(chunk):
            return None
        probe_year, probe_month = base_year, base_month
        while (probe_year, probe_month) <= (valid_to.year, valid_to.month):
            collect(chunk, probe_month, probe_year)
            probe_month += 1
            if probe_month > 12:
                probe_month, probe_year = 1, probe_year + 1

    for y, mo, dy in sorted(dates):
        try:
            start = datetime(y, mo, dy, sh, sm, tzinfo=timezone.utc)
            end = datetime(y, mo, dy, eh % 24, em, tzinfo=timezone.utc)
            if eh >= 24 or (eh * 60 + em) <= (sh * 60 + sm):
                end += timedelta(days=1)
            if end > valid_from and start < valid_to:
                windows.append((start, end))
        except ValueError:
            continue                                  # e.g. 30 FEB

    windows.sort(key=lambda w: w[0])
    return windows or None


def status_at(notam: Notam, when: datetime | None = None) -> str:
    """ACTIVE / FUTURE / INACTIVE at `when`, honouring the D) schedule."""
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if when < notam.valid_from:
        return STATUS_FUTURE
    if when > notam.valid_to:
        return STATUS_INACTIVE
    if not notam.d_field:
        return STATUS_ACTIVE

    try:
        windows = parse_d_field(notam.d_field, notam.valid_from, notam.valid_to)
    except Exception:
        return STATUS_ACTIVE
    if not windows:
        return STATUS_ACTIVE            # unparseable -> assume on (fail safe)

    for start, end in windows:
        if start <= when <= end:
            return STATUS_ACTIVE
    return STATUS_FUTURE if when < windows[0][0] else STATUS_INACTIVE


def hk_day_bounds(when: datetime) -> tuple[datetime, datetime]:
    """The HK local day containing `when`, returned as a UTC window."""
    local = when.astimezone(HKT)
    start = datetime(local.year, local.month, local.day, 0, 0, 0, tzinfo=HKT)
    end = datetime(local.year, local.month, local.day, 23, 59, 59, tzinfo=HKT)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def next_activation(notam: Notam, when: datetime,
                    horizon: timedelta) -> datetime | None:
    """
    The next moment this NOTAM switches on, within `when .. when+horizon`.
    Returns None if it does not start in that horizon.
    """
    limit = when + horizon

    # Simple case: no recurring schedule, so B) is the switch-on moment.
    if not notam.d_field:
        return notam.valid_from if when < notam.valid_from <= limit else None

    windows = parse_d_field(notam.d_field, notam.valid_from, notam.valid_to)
    if not windows:
        return notam.valid_from if when < notam.valid_from <= limit else None

    for start, _end in windows:
        if when < start <= limit:
            return start
    return None


def classify_urgency(notam: Notam, when: datetime | None = None,
                     soon_hours: int = SOON_HOURS_DEFAULT) -> str:
    """
    Bucket a NOTAM as NOW / SOON / TODAY / OTHER.

    This is what drives the overlay colour and the toggle bar, so the map
    and the list can never disagree -- both call this one function.

    TODAY covers the *whole* local day 0000-2359 LT, so a NOTAM that was
    active this morning and has already finished still counts as "today".
    """
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if status_at(notam, when) == STATUS_ACTIVE:
        return URGENCY_NOW

    if next_activation(notam, when, timedelta(hours=soon_hours)):
        return URGENCY_SOON

    # Been active, currently active, or will be active at any point in the
    # local calendar day -- the full 0000 LT .. 2359 LT window.
    day_start, day_end = hk_day_bounds(when)
    if active_in_window(notam, day_start, day_end, 15):
        return URGENCY_TODAY

    return URGENCY_OTHER


def active_in_window(notam: Notam, start: datetime, end: datetime,
                     step_minutes: int = 15) -> bool:
    """Sampled overlap test -- used for 'active during my flight' filtering."""
    if end < notam.valid_from or start > notam.valid_to:
        return False
    if not notam.d_field:
        return True
    windows = parse_d_field(notam.d_field, notam.valid_from, notam.valid_to)
    if not windows:
        return True
    if any(ws <= end and we >= start for ws, we in windows):
        return True
    t = start
    while t <= end:
        if status_at(notam, t) == STATUS_ACTIVE:
            return True
        t += timedelta(minutes=step_minutes)
    return status_at(notam, end) == STATUS_ACTIVE


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
class NotamDatabase:
    def __init__(self, prd_db: dict | None = None):
        self.notams: list[Notam] = []
        self.last_updated: datetime | None = None
        self.last_raw_text: str | None = None
        self.prd_db = prd_db or {}

    # ---- ingest ----------------------------------------------------------
    def update_from_json_api(self, url: str = NOTAM_URL, timeout: int = 15,
                             series: str = "C") -> int:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        items = payload.get("notam", [])
        texts = [i["content"] for i in items if i.get("content")]
        if not texts:
            return 0
        # Join with blank lines so the section splitter still works, exactly
        # like the reference app did to mimic its old HTML scraper output.
        self.last_raw_text = "\n\n".join(texts)
        return self.update_from_text(self.last_raw_text, series=series)

    def update_from_text(self, raw: str, series: str = "C") -> int:
        pattern = RE_NOTAM_ID_C if series == "C" else RE_NOTAM_ID_ANY
        out: list[Notam] = []
        for section in re.split(r"\n\n+", raw or ""):
            if not section.strip():
                continue
            m = pattern.search(section)
            if not m:
                continue
            vf, vt, perm = parse_bc_times(section)
            dm = RE_D_FIELD.search(section)
            d_field = " ".join(dm.group(1).split()).rstrip(">") if dm else None
            out.append(Notam(
                id=m.group(1),
                text=section,
                valid_from=vf,
                valid_to=vt,
                d_field=d_field or None,
                is_perm=perm,
                geometries=extract_geometries(section, self.prd_db),
            ))
        self.notams = out
        self.last_updated = datetime.now(timezone.utc)
        return len(out)

    # ---- query -----------------------------------------------------------
    def with_location(self) -> list[Notam]:
        return [n for n in self.notams if n.has_location]

    def filter(self, mode: str = "Active Now", when: datetime | None = None,
               soon_hours: int = SOON_HOURS_DEFAULT) -> list[Notam]:
        """
        Filter by the toggle-bar mode, sorted by urgency then time.

          All          -- every NOTAM currently published, no time filter
          Active Today -- active at any point in the local day 0000-2359 LT
                          (already finished, running now, or still to come)
          Next 2 Hours -- active now, or switching on within `soon_hours`
          Active Now   -- active at this instant
        """
        when = when or datetime.now(timezone.utc)

        if mode == "All":
            picked = list(self.notams)
        elif mode == "Active Today":
            day_start, day_end = hk_day_bounds(when)
            picked = [n for n in self.notams
                      if active_in_window(n, day_start, day_end, 15)]
        elif mode == "Next 2 Hours":
            picked = [n for n in self.notams
                      if classify_urgency(n, when, soon_hours)
                      in (URGENCY_NOW, URGENCY_SOON)]
        else:                                            # Active Now
            picked = [n for n in self.notams
                      if status_at(n, when) == STATUS_ACTIVE]

        return self.sort_by_urgency(picked, when, soon_hours)

    @staticmethod
    def sort_by_urgency(notams: list[Notam], when: datetime | None = None,
                        soon_hours: int = SOON_HOURS_DEFAULT) -> list[Notam]:
        """
        NOW first (soonest to expire = most urgent), then SOON (soonest to
        start), then TODAY (soonest to start), then everything else.
        """
        when = when or datetime.now(timezone.utc)

        def key(n: Notam):
            bucket = classify_urgency(n, when, soon_hours)
            rank = URGENCY_ORDER[bucket]
            if bucket == URGENCY_NOW:
                # Sort on the end of the window actually in force, so a
                # NOTAM stopping in 20 min outranks one valid all month.
                return (rank, current_window_end(n, when).timestamp())
            nxt = next_activation(n, when, timedelta(days=2))
            return (rank, (nxt or n.valid_from).timestamp())

        return sorted(notams, key=key)

    @staticmethod
    def sort_by_status_and_time(notams: list[Notam],
                                when: datetime | None = None) -> list[Notam]:
        """
        Legacy ordering kept for parity with the reference app:
        ACTIVE first (soonest to expire), then FUTURE, then INACTIVE.
        """
        when = when or datetime.now(timezone.utc)

        def key(n: Notam):
            st = status_at(n, when)
            rank = STATUS_ORDER[st]
            if st == STATUS_ACTIVE:
                return (rank, n.valid_to.timestamp())
            if st == STATUS_FUTURE:
                return (rank, n.valid_from.timestamp())
            return (rank, -n.valid_to.timestamp())

        return sorted(notams, key=key)


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------
STATUS_COLOURS = {
    STATUS_ACTIVE:   "#ff6600",   # bright orange, like the Kivy layer
    STATUS_FUTURE:   "#ffcc00",   # amber
    STATUS_INACTIVE: "#996633",   # dark/muted orange
}
STATUS_BADGES = {
    STATUS_ACTIVE: "🟢 ACTIVE",
    STATUS_FUTURE: "🟡 FUTURE",
    STATUS_INACTIVE: "🔴 INACTIVE",
}


def pretty_text(notam: Notam) -> str:
    """
    Lay the raw NOTAM out one ICAO field per line.

    The HK AIS feed uses '>' as a hard line separator, but it wraps mid
    sentence ("...WILL>TAKE PLACE AT...") and packs several fields onto one
    line ("A) VHHK B) 2609281430 C) 2609291730"). So:

      1. turn '>' into spaces and collapse whitespace -> one clean stream
      2. break the stream before every field marker  Q) A) B) ... G)

    The marker regex needs a single letter not preceded by another letter
    or digit, otherwise the numbered sub-areas inside E) ("1)", "2)") and
    tokens like "VHR12)" would also be split onto their own lines.
    """
    stream = " ".join(notam.text.replace(">", " ").split())

    # Insert a newline before each field marker, then tidy up.
    laid_out = RE_FIELD_MARKER.sub(lambda m: "\n" + m.group(1) + ")", stream)
    lines = [ln.strip() for ln in laid_out.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def fmt_utc_hkt(dt: datetime) -> str:
    return (f"{dt.strftime('%d-%b-%Y %H:%M')}Z  "
            f"({dt.astimezone(HKT).strftime('%d-%b %H:%M')} HKT)")


def fmt_delta(delta: timedelta) -> str:
    """'2h 15m' / '45m' / '3d 4h' -- compact human duration."""
    secs = int(abs(delta.total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def current_window_end(notam: Notam, when: datetime) -> datetime:
    """
    When the NOTAM currently in force actually switches off.

    For a NOTAM with a D) schedule this is the end of the *current* window,
    not the far-off C) date -- that is what makes 'most urgent first'
    ordering meaningful.
    """
    if notam.d_field:
        windows = parse_d_field(notam.d_field, notam.valid_from, notam.valid_to)
        if windows:
            for start, end in windows:
                if start <= when <= end:
                    return end
    return notam.valid_to


def timing_note(notam: Notam, when: datetime | None = None,
                soon_hours: int = SOON_HOURS_DEFAULT) -> str:
    """One-line 'why is it this colour' summary for the list rows."""
    when = when or datetime.now(timezone.utc)
    bucket = classify_urgency(notam, when, soon_hours)

    if bucket == URGENCY_NOW:
        if notam.is_perm and not notam.d_field:
            return "active now · permanent"
        end = current_window_end(notam, when)
        return f"active now · ends in {fmt_delta(end - when)}"

    nxt = next_activation(notam, when, timedelta(days=7))
    if nxt:
        return (f"starts in {fmt_delta(nxt - when)} · "
                f"{nxt.astimezone(HKT).strftime('%d-%b %H:%M')} HKT")
    if when > notam.valid_to:
        return f"expired {fmt_delta(when - notam.valid_to)} ago"
    return "not active today"


if __name__ == "__main__":
    db = NotamDatabase()
    n = db.update_from_json_api()
    print(f"Parsed {n} NOTAMs; {len(db.with_location())} have geometry")
    for notam in db.filter("Active")[:5]:
        print(f"  {notam.id:10} {STATUS_BADGES[status_at(notam)]:12} "
              f"geoms={len(notam.geometries)} D)={notam.d_field}")
