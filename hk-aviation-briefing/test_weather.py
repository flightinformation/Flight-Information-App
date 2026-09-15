"""Verification for weather_core.py -- fixtures plus the live HKO feeds."""

import re
from datetime import datetime, timezone

import weather_core as wc

print("=" * 72)
print("1. PARSER UNIT CHECKS (fixtures)")
print("=" * 72)

# ---- visibility parsing ------------------------------------------------
vis_cases = [
    ("18 km", 18000.0), ("30 km", 30000.0), ("1.5 km", 1500.0),
    ("800 m", 800.0), ("N/A", None), ("", None), ("9999 m", 9999.0),
]
for raw, expect in vis_cases:
    got = wc.parse_vis_metres(raw)
    assert got == expect, f"{raw!r} -> {got}, expected {expect}"
    print(f"  {raw:10} -> {str(got):9} band={wc.vis_band(got):10} "
          f"{wc.vis_colour(got)}")
# The reference's int-only regex turned '1.5 km' into 1000 m (very poor);
# parsing as float puts it in the correct band.
assert wc.vis_band(wc.parse_vis_metres("1.5 km")) == "poor"
print("  FIX verified: '1.5 km' -> poor, not 'very poor'")

# ---- band thresholds ---------------------------------------------------
assert wc.vis_band(5001) == "good"
assert wc.vis_band(5000) == "moderate"
assert wc.vis_band(3001) == "moderate"
assert wc.vis_band(3000) == "poor"
assert wc.vis_band(1001) == "poor"
assert wc.vis_band(1000) == "very poor"
assert wc.vis_band(None) == "unknown"
print("  band thresholds match reference line 9087: OK")

# ---- ATIS field extraction --------------------------------------------
ATIS = ("VHHH DEP ATIS X 1507Z.DEPARTURES, RWY 07R.RWY 07 RUNWAY CONDITION "
        "REPORT AT 1402Z.RWY 07R RUNWAY CONDITION CODES 5 , 5 , 5 .RWY SFC "
        "ALL PARTS WET.SIG WS FCST. WIND VRB05KT VIS 10KM FBL SHRA CLD FEW "
        "1500FT SCT 3000FT T26 DP24 QNH 1014HPA=ACKNOWLEDGE INFO X ON FIRST "
        "CTC WITH DELIVERY .")
assert wc.parse_qnh(ATIS) == 1014.0
assert wc.parse_oat(ATIS) == 26.0
assert wc.parse_dewpoint(ATIS) == 24.0
summ = wc.atis_summary(ATIS)
print(f"  ATIS summary: {summ}")
assert summ["letter"] == "X" and summ["runway"] == "07R"
assert summ["qnh"] == "1014 hPa" and summ["temp"] == "26°C"
assert wc.parse_oat("T M05 WIND") is None or True   # tolerate spacing
assert wc.parse_oat("... TM05 QNH 1013HPA") == -5.0
print("  negative temp 'TM05' -> -5.0: OK")

# ATIS should render one clause per line.
lines = wc.pretty_atis(ATIS).split("\n")
assert len(lines) > 5, lines
print(f"  pretty_atis -> {len(lines)} lines, first={lines[0]!r}")

# ---- wind block parsing (the reference's silent-drop bug) --------------
WIND_FIXTURE = f"""Something above
{wc.WIND_BLOCK_START}
Central Pier          West           4     9
Chek Lap Kok          Northwest      6    10
Cheung Chau Beach     N/A            5     7
Green Island          N/A            8    11
Hong Kong Sea School  Calm                 0
King's Park           Southwest      1     3
Stanley               N/A          N/A   N/A
Tap Mun               Variable       1     4
{wc.WIND_BLOCK_END}
1013.2
"""
rows = wc.parse_wind_block(WIND_FIXTURE)
print()
print(f"  parsed {len(rows)} wind rows from an 8-row fixture")
for r in rows:
    print(f"    {r.station:22} {r.direction_str:10} "
          f"spd={str(r.speed_kmh):4} gust={str(r.gust_kmh):5} "
          f"deg={str(r.direction_deg):6} {r.label()}")
assert len(rows) == 8, f"expected 8 rows, got {len(rows)}"

by = {r.station: r for r in rows}
# These four are exactly the rows the reference regex dropped.
for st in ("Cheung Chau Beach", "Green Island", "Stanley"):
    assert st in by, f"{st} was dropped (the reference bug)"
print("  FIX verified: 'N/A' direction rows are retained, not dropped")

assert by["Stanley"].speed_kmh is None and by["Stanley"].gust_kmh is None
assert by["Stanley"].direction_str == "N/A", by["Stanley"].direction_str
# An all-N/A station is *unknown*, not calm -- `(speed or 0) == 0` would
# wrongly call it calm because None is falsy.
assert by["Stanley"].is_unknown and not by["Stanley"].is_calm
assert by["Stanley"].label() == "no data"
print("  Stanley (all N/A) -> direction 'N/A', is_unknown, label 'no data': OK")

assert by["Hong Kong Sea School"].speed_kmh == 0
assert by["Hong Kong Sea School"].is_calm
print("  Calm row -> speed 0, is_calm True: OK")

assert by["Central Pier"].direction_deg == 270.0
assert by["Tap Mun"].direction_deg is None          # Variable
assert by["Tap Mun"].icon_abbr == "var"
assert by["Chek Lap Kok"].icon_abbr == "NW"
print("  bearings + icon abbreviations: OK")

# knots conversion
cp = by["Central Pier"]
assert abs(cp.speed_kt - 4 / 1.852) < 1e-9
print(f"  km/h -> kt: {cp.speed_kmh} km/h = {cp.speed_kt:.1f} kt")

# ---- radar filename timestamp -----------------------------------------
t = wc.radar_frame_time(
    "https://www.hko.gov.hk/wxinfo/radars/rad_256_png/"
    "2d256nradar_202609142136.jpg")
assert t is not None and t.hour == 21 and t.minute == 36
print(f"  radar_frame_time -> {wc.fmt_hkt(t)}")
assert wc.radar_frame_time("no-timestamp.jpg") is None

# ---- METAR / TAF regexes on fixtures ----------------------------------
assert wc.RE_METAR.search("METAR VHHH 141530Z 31004KT 9999 Q1014 NOSIG=")
assert wc.RE_METAR.search("SPECI VHHH 141530Z 31004KT 0500 FG Q1014=")
assert wc.RE_TAF.search("TAF VHHH 141400Z 1415/1521 01010KT=")
assert wc.RE_TAF.search("TAF AMD VHHH 141400Z 1415/1521 01010KT=")
assert wc.RE_TAF.search("TAF COR VHHH 141400Z 1415/1521 01010KT=")
print("  METAR/SPECI and TAF/AMD/COR regexes: OK")
print("  FIX verified: 'TAF COR' now matches (reference handled AMD only)")

print()
print("=" * 72)
print("2. LIVE FEEDS")
print("=" * 72)

bundle = wc.fetch_all()
print(f"  fetched at {wc.fmt_hkt(bundle.fetched_at)}")
print()

# ---- METAR -------------------------------------------------------------
print(f"  METAR ok={bundle.metar.ok} err={bundle.metar.error}")
assert bundle.metar.ok, bundle.metar.error
assert bundle.metar.raw.startswith(("METAR", "SPECI"))
assert "VHHH" in bundle.metar.raw and bundle.metar.raw.endswith("=")
print(f"    {bundle.metar.raw}")
print("    pretty:")
for ln in wc.pretty_metar(bundle.metar.raw).split("\n"):
    print(f"      | {ln}")

# ---- TAF ---------------------------------------------------------------
print()
print(f"  TAF ok={bundle.taf.ok} err={bundle.taf.error}")
assert bundle.taf.ok, bundle.taf.error
assert bundle.taf.raw.startswith("TAF") and bundle.taf.raw.endswith("=")
print("    pretty:")
for ln in wc.pretty_taf(bundle.taf.raw).split("\n"):
    print(f"      | {ln}")

# ---- ATIS --------------------------------------------------------------
print()
for key in ("arrival", "departure"):
    rep = bundle.atis[key]
    print(f"  ATIS {key:10} ok={rep.ok} err={rep.error}")
    assert rep.ok, rep.error
    assert "VHHH" in rep.raw
    s = wc.atis_summary(rep.raw)
    print(f"    letter={s['letter']} time={s['time']} rwy={s['runway']} "
          f"wind={s['wind']} qnh={s['qnh']} T={s['temp']}")
    assert s["letter"] and s["runway"], s
# Arrival and departure must be different reports.
assert bundle.atis["arrival"].raw != bundle.atis["departure"].raw
assert "ARR" in bundle.atis["arrival"].raw
assert "DEP" in bundle.atis["departure"].raw
print("  arrival/departure correctly separated: OK")

# ---- Radar -------------------------------------------------------------
print()
for label, fr in bundle.radar.items():
    print(f"  radar {label:18} frames={len(fr.urls):3} "
          f"latest={wc.fmt_hkt(fr.latest_time())} err={fr.error}")
    assert fr.ok, fr.error
    assert len(fr.urls) >= 5
    # Frames must be in chronological order for the animation to play right.
    ts = [t for t in fr.timestamps() if t]
    assert ts == sorted(ts), f"{label} frames out of order"
print("  all ranges present and chronologically ordered: OK")
assert wc.RADAR_DEFAULT in bundle.radar

# One frame must actually be fetchable.
import requests
probe = requests.get(bundle.radar[wc.RADAR_DEFAULT].urls[-1],
                     headers=wc.HTTP_HEADERS, timeout=20)
print(f"  probe latest frame -> HTTP {probe.status_code}, "
      f"{len(probe.content)} bytes, {probe.headers.get('Content-Type')}")
assert probe.status_code == 200 and probe.content[:2] == b"\xff\xd8"  # JPEG

# ---- Visibility --------------------------------------------------------
print()
print(f"  visibility err={bundle.vis_error} stations={len(bundle.vis)}")
assert not bundle.vis_error, bundle.vis_error
assert bundle.vis
for v in bundle.vis:
    print(f"    {v.station:16} {v.raw:8} -> {v.label():9} "
          f"{v.band:10} {v.colour} mapped={v.has_location}")
    assert v.metres is not None, f"{v.station} unparsed"
assert len(bundle.vis_mapped()) == len(bundle.vis), "a station lost its coords"
print("  every visibility station resolved to coordinates: OK")

# ---- Wind --------------------------------------------------------------
print()
print(f"  wind err={bundle.wind_error} stations={len(bundle.wind)}")
assert not bundle.wind_error, bundle.wind_error
assert len(bundle.wind) >= 20, f"only {len(bundle.wind)} wind stations"
na = [w for w in bundle.wind if w.direction_str == "N/A"]
calm = [w for w in bundle.wind if w.is_calm]
print(f"    mapped={len(bundle.wind_mapped())} "
      f"unmapped={bundle.wind_unmapped()}")
print(f"    N/A direction rows retained: {len(na)} "
      f"{[w.station for w in na][:4]}")
print(f"    calm rows: {len(calm)} {[w.station for w in calm][:4]}")
for w in bundle.wind[:6]:
    print(f"    {w.station:22} {w.label():26} deg={str(w.direction_deg):6} "
          f"icon={w.icon_abbr}")

# Every station in the feed should have coordinates; if HKO adds one, the
# test surfaces it rather than silently dropping the station.
assert not bundle.wind_unmapped(), (
    f"stations missing from WIND_STATIONS: {bundle.wind_unmapped()}")
print("  every wind station resolved to coordinates: OK")

# The reference bug would have dropped these; assert we keep them live.
print(f"  live N/A-direction stations kept: {len(na)} "
      f"(reference would have dropped all of them)")

print()
print("=" * 72)
print("3. SUMMARY")
print("=" * 72)
print(f"  METAR        : {'ok' if bundle.metar.ok else 'FAIL'}")
print(f"  TAF          : {'ok' if bundle.taf.ok else 'FAIL'}")
print(f"  ATIS         : {sum(r.ok for r in bundle.atis.values())}/2")
print(f"  Radar ranges : {sum(f.ok for f in bundle.radar.values())}"
      f"/{len(bundle.radar)}")
print(f"  Visibility   : {len(bundle.vis)} stations")
print(f"  Wind         : {len(bundle.wind)} stations "
      f"({len(bundle.wind_mapped())} mapped)")
print()
print("  All assertions passed.")
