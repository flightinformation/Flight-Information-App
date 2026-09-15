# The Weather tab — what it does, where the data comes from, and what I fixed

This document is the companion to `NOTAM_EXPLAINED.md`. It covers the second
tab of the Streamlit port: the four text/imagery blocks (METAR, TAF, ATIS,
Weather Radar) and the station map that toggles between visibility and wind.

Everything here was derived from your Kivy reference app in
`reference_code.txt`, and every endpoint was verified live before a single line
of UI was written.

---

## 1. Where the code lives

The weather feature is split across two files, and the split matters.

`weather_core.py` is the engine. It fetches, parses, converts units and hands
back plain Python dataclasses. It imports `requests`, `lxml` and the standard
library — and nothing else. It has no idea Streamlit exists.

`views/weather.py` is the presentation layer. It imports `weather_core as wc`,
arranges the blocks, draws the folium markers and owns all the HTML.

That separation is deliberate, and it is the single most valuable change from
the reference. In the Kivy app the HTTP calls lived inside the widget classes —
`WeatherDataManager` (line 4448) held URLs, parsing and display state together,
and `GraphicalWindOverlay` (line 9315) parsed wind *while* drawing it. The
practical consequence was that nothing could be tested without standing up a
window, so the parsing bugs listed in section 7 sat there unnoticed. Because
the engine is now UI-free, `test_weather.py` drives it directly and asserts on
real responses. That is how those eight bugs were found.

---

## 2. The six data sources

All six were confirmed live, with response sizes, before being wired in.

**METAR** comes from `https://www.hko.gov.hk/aviat/metar_eng_revamp.json`
(reference line 4602). It is a JSON document with the report buried inside it.
The regex `RE_METAR` accepts `METAR` *or* `SPECI`, because a SPECI is the
off-schedule report issued when conditions change significantly — your
reference got this right at line 4618 and I kept it.

**TAF** comes from `taf_decode_eng_revamp.json` (reference line 4629), matched
by `RE_TAF`.

**ATIS** is the odd one out: it is scraped HTML from
`https://atis.cad.gov.hk/ATIS/ATISweb/atis.php`, not JSON. The arrival and
departure broadcasts sit in two different table cells, extracted by the two
XPaths in `ATIS_XPATHS`. Both still resolve to exactly one match each, and the
two cells are genuinely distinct — verified, not assumed.

**Radar** comes from `nradar_img.json`, which is a JavaScript-ish file
containing `picture[n][m] = "filename"` assignments, pulled out with
`RE_RADAR_PIC`. There are four ranges (256 km, 128 km, 64 km, and 64 km at 2 km
resolution) with twenty frames each. The filenames embed a timestamp in **Hong
Kong local time**, not UTC — `radar_frame_time()` parses it accordingly.

**Visibility** is a CSV from the `data.weather.gov.hk` open-data API. Only four
stations report LTMV: Central, Chek Lap Kok, Sai Wan Ho and Waglan Island.

**Wind** is scraped from the regional readings page, which is a fixed-width
plain-text table wrapped in HTML. This one caused the most trouble; see 7.1.

---

## 3. Units: km/h in, knots out

The HKO wind feed reports in **kilometres per hour**. Pilots do not think in
km/h — they think in knots, because that is what the METAR, the ATIS and the
airspeed indicator all use. So `WindReading` exposes both: `speed_kmh` as
fetched, and `speed_kt` as a derived property dividing by 1.852.

The map labels show knots. The readings table shows both columns, so you can
cross-check against the raw feed if a number ever looks wrong.

---

## 4. The four blocks

Each text product is rendered by `report_block()`, which handles three states:
an error, an empty-but-successful response, and actual content. They are
distinguished deliberately — "the fetch failed" and "the fetch worked but the
authority published nothing" are different situations, and collapsing them
would hide an outage.

The raw text is reformatted for readability before display. `pretty_metar()`
breaks before change groups, `pretty_taf()` breaks before each forecast period
(`TEMPO`, `BECMG`, `PROB30`, `FM…`), and `pretty_atis()` puts each
full-stop-separated clause on its own line, mirroring `_format_atis_data` at
reference line 37611 but without the Kivy markup.

Above the blocks is a summary strip of five metrics — ATIS letter and runway
for both arrival and departure, QNH, temperature/dewpoint, and wind. These are
the numbers you want before reading any raw text, and they are extracted by
`atis_summary()`.

### The radar loop

The radar block has a range selector and an animation toggle. When animation is
on, the loop runs **client-side** in a small HTML component: the frame URLs are
baked into a tiny script that preloads the images and cycles them, pausing
slightly longer on the last frame so the sequence reads clearly.

This is done in the browser rather than with a Streamlit rerun loop on purpose.
Driving an animation by rerunning the script would re-execute the whole page on
every tick — refetching, rebuilding the map, and fighting Streamlit's execution
model for a 400 ms frame change. The images are hotlinked straight from HKO
(confirmed: HTTP 200, `image/jpeg`, no `Referer` header required).

---

## 5. The station map

One `st.segmented_control` switches the overlay between Visibility and Wind.
The map is keyed on the layer name (`key=f"wx-map-{layer}"`) so folium fully
rebuilds when you switch rather than reusing stale markers.

**Visibility** markers show the reading above the station name, coloured by
band. The bands and colours are ported from reference line 9087, with the Kivy
RGBA tuples converted to hex: good (>5 km) green, moderate (>3 km) grey, poor
(>1 km) mauve, very poor (≤1 km) red, unknown grey.

**Wind** markers show the speed in knots above an arrow, with a gust suffix
when the gust exceeds the mean. The arrow is rotated by `(direction_deg + 180)
% 360` — that is, it points **downwind**, the way a windsock lies, rather than
into the wind the way a met-chart barb points. `direction_deg` itself is the
bearing the wind blows *from*, following the meteorological convention in the
source data.

Three states are drawn differently and must not be confused: a station with a
real direction gets a rotated arrow; a calm or variable station gets a plain
circle, because an arrow would imply a direction that does not exist; and a
station reporting nothing gets a dashed grey circle labelled "no data".

### Your overlay icons

You mentioned you have overlay icons you'd like to use. The loader is already
wired up and will pick them up automatically — no code changes needed.

Drop files into `assets/icons/wind/` with the stems `N`, `NE`, `E`, `SE`, `S`,
`SW`, `W`, `NW`, plus optionally `var` and `calm`; and into `assets/icons/vis/`
with the stems `good`, `moderate`, `poor`, `very_poor`, `unknown`. PNG, SVG,
JPG, WEBP and GIF all work, and matching is case-insensitive. The sidebar has
an "Overlay icons" expander that lists what it has detected, so you can confirm
the files landed correctly.

Wind icons are rotated by the same downwind rule, so draw your arrow pointing
**north/up** in its natural state and the map will orient it. Until the files
appear, the drawn fallback markers are used, so the page works as-is.

Icons are inlined as base64 data URIs rather than served from a static path.
This is not gratuitous: the folium map renders inside a sandboxed iframe that
cannot resolve Streamlit's own asset URLs, so a normal `src="/app/static/…"`
would silently render as a broken image.

---

## 6. Caching and refresh

`load_weather()` is wrapped in `@st.cache_data` with a TTL of
`METAR_REFRESH_MINUTES * 60` — fifteen minutes, matching reference line 272.
The radar moves faster (three minutes, line 263), but a slightly stale *frame
list* is harmless because the images themselves are fetched live by the
browser.

The sidebar Refresh button increments a `wx_bust` counter that is passed into
the cached function as an argument, which is what forces a genuine re-fetch.
This mirrors the pattern already used on the NOTAM tab.

---

## 7. Bugs found in the reference code

These are real defects in `reference_code.txt`, each confirmed against live
data rather than inferred by reading.

### 7.1 Wind stations silently dropped — the serious one

`WindDataManager.parse_wind_data` (line 9155) used this regex (line 9169):

```
^(.*?)\s+([A-Za-z]+)\s+(\d+)(?:\s+(\d+))?$
```

The direction group is `[A-Za-z]+`, which cannot match `N/A` because of the
slash. Any station reporting `N/A` in the direction column fails the match and
is **silently discarded** — no warning, no log line, it simply vanishes from
the map.

Measured against the live feed: **25 of 30 stations matched, 5 were missed** —
Cheung Chau Beach, Green Island, Peng Chau, Stanley and Tate's Cairn. A sixth
of your wind network was invisible, and because the failure was silent the map
looked perfectly normal.

The fix abandons regex matching for the fixed-width column slices the page
actually uses (`[:22]`, `[22:36]`, `[36:39]`, `[39:]`), with a re-tokenising
fallback if a row does not fit the expected shape. All 30 stations now parse
and all 30 map.

### 7.2 Fractional visibility rounded down a whole kilometre

`VisibilityDataManager` (line 9082) parsed with `re.search(r'(\d+)')` and
multiplied by 1000 for kilometre readings. Integer-only capture means `1.5 km`
parses as `1`, becomes 1000 m, and is classified **very poor** instead of
**poor** — a visible colour change on the map driven purely by a parsing
artefact. Fixed by capturing `\d+(?:\.\d+)?` and parsing as float.

### 7.3 `TAF COR` never matched

The TAF regex allowed `TAF` and `TAF AMD` but not `TAF COR`. A corrected TAF —
exactly the one you most want to see — would not match, and the block would
show nothing. `RE_TAF` now accepts `(?:AMD|COR)?`.

### 7.4 Radar frames stored in a `set`

`HKORadarImageManager.fetch_url_list` (line 14276) collected frame URLs into a
set, which has no ordering. The animation therefore played the twenty frames in
arbitrary order — the loop would appear to jump backwards in time. Frames are
now sorted on the timestamp embedded in the filename, and the test asserts the
result is chronologically ordered.

### 7.5 Visibility CSV split on commas

The CSV was parsed with a plain `split(',')`, which breaks on any quoted field
containing a comma and leaves the UTF-8 BOM attached to the first column name.
Replaced with the `csv` module and `utf-8-sig` decoding.

### 7.6 ATIS had no fallback

ATIS extraction depended entirely on two hard-coded XPaths into the CAD page's
table structure. A single layout change would break it with no recovery path.
Both XPaths currently work, but `RE_ATIS_BLOCK` is now tried as a regex
fallback if they return nothing.

### 7.7 Hard-coded JSON paths

METAR and TAF were read from fixed key paths into the JSON. Added
`_deep_find_str()`, which searches the whole structure for a value matching the
expected report pattern, so a key rename upstream degrades gracefully.

### 7.8 `Variable` and `N/A` missing from the direction map

`DIRECTION_TO_DEGREES` (reference line 9149) covered the eight compass points
but omitted `Calm`, `Variable` and `N/A`, so those lookups raised or returned
nothing useful. All three are now present and map explicitly to `None`.

---

## 8. One bug of my own, worth recording

While testing, Stanley came back with direction `"N/A          N"` and was
reported as **Calm**. Two separate faults:

The first was my column-slice fallback mis-firing and gluing two fields
together. Fixed with a guard that re-tokenises when the sliced direction still
contains whitespace or a slash.

The second was more interesting. `is_calm` was written as
`(self.speed_kmh or 0) == 0`, which is true when `speed_kmh` is `None` —
because `None` is falsy. So a station reporting *nothing* was being displayed
as reporting *zero wind*. Those are completely different claims, and on a
weather map the difference matters. `is_calm` and `is_unknown` are now separate
properties, and an unknown station is labelled "no data" rather than "Calm".

This is the same class of error as 7.1: treating missing data as though it were
a measurement.

---

## 9. Testing

`test_weather.py` runs in three sections. The first uses fixtures — a captured
wind block including `N/A`, `Calm` and the Stanley row that broke my parser,
plus visibility parsing, band thresholds, ATIS field extraction, radar
timestamp ordering, and the METAR/TAF regexes.

The second hits the live feeds and asserts that METAR and TAF return content,
that both ATIS broadcasts are present and distinct, that all four radar ranges
return twenty chronologically ordered frames (with an actual HTTP probe on one
image to confirm it is a real JPEG), that four visibility stations parse, and
that **all 30 wind stations parse and all 30 have coordinates**.

That last assertion is the regression test for 7.1. If a future change
reintroduces silent station-dropping, the count check fails immediately.

Run it with `python test_weather.py`.
