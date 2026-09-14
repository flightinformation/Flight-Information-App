# The NOTAM subsystem in `reference_code.txt` — how it works

The reference file is a **38,044-line Kivy desktop app** (`HKFlyingChartsApp`), not a
Streamlit app. The NOTAM feature is spread across five places:

| Lines | Component | Role |
|---|---|---|
| 5203–5830 | `NotamDatabase` | fetch, parse, geometry, time logic |
| 8280–8440 | `NotamMapHandler` | decide what to draw, centre & zoom the map |
| 37879–38040 | `NotamRadiusLayer` / `NotamPolygonLayer` / `NotamPointLayer` | actual canvas drawing |
| 32213–32620 | `sort_notams_by_status_and_time`, `display_c_notams` | sort + filter for the list UI |
| 33955–34035, 36322–36560, 34688 | `toggle_notams`, cache, `setup_notam_updates` | map toggle, disk cache, 4-h refresh |

---

## 1. Where the data comes from

`update_from_json_api()` (line 5275) GETs **`https://www.notam.ais.gov.hk/data`**.
I confirmed this endpoint is live: HTTP 200, ~26 KB, **58 NOTAMs** right now.

The JSON looks like this:

```json
{"snowtam": [],
 "notam": [{"number": "A2169/26",
            "starttime": "2026-09-14 16:00",
            "endtime":   "2026-09-14 19:00",
            "id": 10345,
            "content": "140923 VHHHYNYX>(A2169/26 NOTAMN>Q) VHHK/QPFCA/...>A) VHHK B) 2609141600 C) 2609141900>E) DUE TO SECTOR CAPACITY..."}]}
```

Two important details:

- The code **ignores** the convenient `starttime`/`endtime` fields and instead
  re-parses the raw ICAO `B)`/`C)` fields out of `content`.
- In `content`, **`>` is the line separator**, not `\n`. This is why the `D)` regex
  has to terminate on `>`, and why raw text needs `.replace('>', '\n')` before display.

It then joins all `content` strings with `\n\n` to mimic the old HTML-scraper format
and feeds them to `update_from_text()`.

---

## 2. Splitting and reading the header

`update_from_text()` (line 5320):

```python
notam_sections = re.split(r'\n\n+', notam_text)          # one section per NOTAM
notam_id_match = re.search(r'(C\d{4}/\d{2})', section)   # <-- C-series ONLY
if not notam_id_match: continue                          # everything else dropped
```

> **Bug #1 — silent data loss.** That regex only accepts **C**-series IDs.
> On today's live feed: **33 C-series kept, 25 A-series silently discarded**.
> A-series includes airspace/aerodrome items such as `A2110/26`, a live firing-exercise
> NOTAM *with* a plottable danger area. My port makes the series a user choice.

**Validity window** — `B)` and `C)`, both UTC, format `YYMMDDHHMM`:

```python
date_match = re.search(r'B\)\s*(\d{10})\s*C\)\s*(\d{10})', section)
valid_from = datetime(2000+int(f[0:2]), int(f[2:4]), ..., tzinfo=timezone.utc)
```

> **Bug #2 — `C) PERM`.** Permanent NOTAMs have no 10-digit `C)` value, so the regex
> fails and the bare `except: pass` leaves the default **`now + 30 days`**. A permanent
> restriction silently expires after a month. I handle `PERM` explicitly.

**`D)` field** — the recurring schedule, captured and normalised:

```python
d_match = re.search(r'D\)\s*(.+?)(?:\s*E\)|>|$)', section, re.DOTALL)
d_field = ' '.join(d_match.group(1).split()).rstrip('>')
```

---

## 3. How the AREA is parsed — `extract_location_data()` (line 5375)

Coordinates arrive as ICAO **DDMMSS / DDDMMSS** strings, matched by:

```python
coord_pattern = r'(\d{6}[N|S])\s*(\d{7}[E|W])'     # e.g. 222900N 1135800E
```

and converted in `parse_coordinate_parts()` (degrees + minutes/60 + seconds/3600,
negated for S/W). Verified: `222900N 1135800E` → `22.4833, 113.9667`. ✅

The function tries five strategies **in priority order** and returns on the first hit:

### Priority 1 — Multi-area `E)` field → `multi-polygon`
It isolates `E)` up to `F)`, then finds numbered markers `1)`, `2)`, `3)`:

```python
markers = list(re.finditer(r'\b(\d+)\)', e_field))
if len(markers) > 1:
    for each marker:  slice text from this marker to the next
                      find coords ONLY in that slice
                      if >= 3 coords -> one polygon
```

This slicing is the key idea: it stops one NOTAM's three separate danger zones from
being merged into a single nonsensical polygon. Result is
`{'type': 'multi-polygon', 'geometries': [...]}`.

### Priority 2 — PRD cross-reference
If the text mentions a charted area (`VHP\d+` prohibited, `VHR\d+` restricted,
`VHD\d+` danger, `ZJ(D)\d+`), the shape is pulled from `PRDDatabase` (line 6279)
instead of the text. Supports `circle`, `polygon`, and `sectors` — where pie-slices
are expanded into a polygon ring with trigonometry (`~111320 m` per degree,
longitude scaled by `cos(lat)`).

### Priority 3 — Hard-coded named range
`SAN WAI` / `TAI LING` (a firing range that appears without coordinates) maps to a
6-point polygon stored in `self.named_locations`.

### Priority 4 — `BOUNDED BY` inside `E)`
All coordinates in `E)`, needs ≥ 3 → polygon.

### Priority 5 — Whole-text fallback
- `RADIUS` match → **circle** (`radius`, `radius_unit`)
- `BOUNDED BY` or ≥ 3 coords → **polygon**
- exactly 1 coord → **point**

> **Bug #3 — broken radius regex:** `r'(\d+)([M|NM]+)\s+RADIUS'`. Inside `[...]`,
> `|` is a literal, so this matches `M`, `N`, `|`, `MN`, `NM`… and accepts junk like
> `5|| RADIUS`. It also misses decimals (`1.5NM`) and never handles `KM`.
> Correct form: `(\d+(?:\.\d+)?)\s*(NM|KM|M|FT)\s+RADIUS`.

**Live result of this pipeline:** 31 of 58 NOTAMs yield geometry —
16 polygons, 13 circles, 2 points; sources: 15 `E) BOUNDED BY`, 13 `RADIUS`,
2 single-coordinate, 1 named location.

---

## 4. How the TIME is sorted out

### `parse_d_field()` (line 5633) — expands `D)` into concrete UTC windows

| `D)` text | Meaning |
|---|---|
| `DLY 1030-1500` | every day 10:30–15:00Z within `B)`–`C)` |
| `DLY 2230-1030` | crosses midnight → ends next day |
| `MARCH 02-07 09-14 0000-1300` | day *ranges* |
| `MAR 2 30 APR 13 0900-1300` | individual days |
| `HJ` / `HN` | sunrise/sunset — **not supported**, returns `None` |

Mechanism: grab `HHMM-HHMM` with `(\d{4})-(\d{4})`; if `DLY`, walk day-by-day from
`B)` to `C)` (detecting midnight crossover by comparing minutes-of-day, and clipping
to the validity period); otherwise locate month names, slice the text after each one,
expand `\b(\d{1,2})-(\d{1,2})\b` ranges, then pick up leftover single days.

> **Bug #4 — year is assumed.** `year = valid_from.year` for every date, so a
> Dec→Jan schedule lands in the wrong year.
> **Bug #5 — `MAR` matches inside `MARCH`**, producing duplicate/garbled months
> (mitigated by the `set()` de-dup, but fragile). Fix: try longest names first.
> **Bug #6 — no month named.** Live NOTAM `A1997/26` has
> `D) 02 03 04 09 10 16 17 23 24 2315-0600` — bare days, no month. The reference
> returns `None` → treated as permanently active. My port infers the month(s) from
> the `B)`–`C)` span and correctly produces 10 overnight windows.

### `is_notam_active_at_time()` (line 5557) — the status decision

```
if check_time < valid_from            -> 'FUTURE'
if check_time > valid_to              -> 'INACTIVE'
if no D) field                        -> 'ACTIVE'
if inside any D) window               -> 'ACTIVE'
if before first window                -> 'FUTURE'
else (gap, or after last)             -> 'INACTIVE'
```

So `B)`/`C)` is the **outer envelope** and `D)` carves **actual active windows**
inside it. A NOTAM valid all month but only firing 09:00–12:00 shows INACTIVE at
15:00 — correct, and the reason the naive `valid_from <= now <= valid_to` check used
elsewhere in the file disagrees with this function.

> **Inconsistency #7.** `get_active_notams()` (5796), `toggle_notams()` (33977) and
> `NotamMapHandler.display_notam()` (8316) all use the *naive* comparison and ignore
> `D)`. So the **map colours a NOTAM "active" while the side list says INACTIVE.**
> My port routes every status decision through one function.

### Sorting — `sort_notams_by_status_and_time()` (line 32213)

Three buckets, then within each:
- **ACTIVE** → soonest to *expire* first (`valid_to` ascending) — most urgent
- **FUTURE** → soonest to *start* (`valid_from` ascending)
- **INACTIVE** → most recently expired first (`valid_to` descending)

Final order: `active + future + inactive`.

### Filtering — `display_c_notams()` (line 32481)
Spinner offers `Active` / `Active Today` / `Inactive`. "Active Today" converts the
HK local day (UTC+8) to a UTC window and keeps any NOTAM whose `D)` windows overlap
it (`start <= today_end and end >= today_start`).

### Refresh
`setup_notam_updates()` (34688): load disk cache (`notam_cache.txt`) → fetch after
15 s → then `Clock.schedule_interval` every `NOTAM_REFRESH_MINUTES = 240` (4 h), on a
background thread with UI updates marshalled back via `Clock.schedule_once`.

---

## 5. How it OVERLAYS on the map

Kivy has no geo-widget, so the app does the projection **by hand**. Each layer
subclasses `MapLayer` and implements `reposition()`, which Kivy calls on **every pan
and zoom**. The layer clears its canvas and redraws in screen pixels:

```python
def reposition(self):
    mapview = self.parent
    self.canvas.clear()
    for lat, lon in self.points:
        x, y = mapview.get_window_xy_from(lat, lon, mapview.zoom)   # geo -> pixels
        self.line_points.extend([x, y])
    with self.canvas:
        Color(1, 0.5, 0, 0.3) if self.is_active else Color(0.6, 0.3, 0, 0.2)
        Line(points=self.line_points, width=2, close=True)
```

- **`NotamPolygonLayer`** — converts every vertex, draws a closed `Line`, labels at the
  centroid, and caches `screen_points` for hit-testing via `collide_point()`
  (ray-casting, `is_point_in_polygon`) so clicks open `NotamInfoPanel`.
- **`NotamRadiusLayer`** — converts the centre, then converts a second point one
  radius *north* and takes the pixel distance to get `radius_pixels`; draws a **dashed**
  circle (`dash_length=10`).
- **`NotamPointLayer`** — a 12 px circle marker.
- **`NotamLayer.draw_label()`** — the ID on a semi-transparent black `Rectangle`.

**Colour code (shared by all three):** bright orange `(1, 0.5, 0)` at 30 % fill when
active; dark orange `(0.6, 0.3, 0)` at 20 % when not.

> **Bug #8 — the polygon fill never happens.** `NotamPolygonLayer` sets a 30 %-alpha
> `Color` then calls `Line(...)` — a *stroke*, not a fill. It then sets the solid colour
> and draws the **identical `Line` again**. So you get a double-stroked outline and a
> hollow polygon. A real fill needs `Mesh` with triangulated vertices.

**Two rendering paths:**

1. **`NotamMapHandler.display_notam()`** (dedicated NOTAM screen) — draws **one**
   selected NOTAM, calls `map_view.center_on(...)`, and picks zoom from hard-coded
   thresholds (radius > 50 km → 9, > 20 km → 10 …; or polygon span > 0.5° → 9 …).
   > **Bug #9:** the zoom block reads `location['radius']` / `location['coordinates']`
   > *after* the multi-polygon branch deleted `coordinates` — a `KeyError` risk for
   > multi-area NOTAMs (they fall to the `else` branch and get zoom 12 regardless).
2. **`toggle_notams()`** (main map) — draws **all** active+future NOTAMs at once,
   one layer per shape, and for multi-polygons one layer per part named `ID(1)`, `ID(2)`.
   Refresh is done by calling `toggle_notams(None)` twice (off, then on).

Separately, `update_notam_hover_data()` (36710) precomputes pixel circles/polygons
each time the map moves so hover tooltips can hit-test — a second, duplicated
projection of the same data.

---

## 6. What this means for the Streamlit port

Everything in `reposition()` **disappears**. Leaflet (via `folium` +
`streamlit-folium`) takes lat/lon directly and reprojects itself:

| Kivy | Streamlit + folium |
|---|---|
| `NotamPolygonLayer` + manual pixel maths | `folium.Polygon(locations=[(lat, lon), …])` |
| `NotamRadiusLayer` + pixel radius | `folium.Circle(location=…, radius=metres)` |
| `NotamPointLayer` | `folium.CircleMarker` |
| `draw_label()` on canvas | `folium.DivIcon` |
| `center_on()` + zoom thresholds | `fit_bounds()` |
| `collide_point` ray-casting | `popup=` / `tooltip=` (Leaflet hit-tests) |
| `Clock.schedule_interval` | `@st.cache_data(ttl=…)` |
| `notam_cache.txt` | the same cache decorator |
| `reposition()` on pan/zoom | nothing — Leaflet owns it |

Because Streamlit re-runs top-to-bottom on every interaction, the parser must **not**
re-run per click — hence `@st.cache_data(ttl=NOTAM_REFRESH_MINUTES*60)`, with
selection held in `st.session_state`.

### What I built

- **`notam_core.py`** — the engine, UI-free and importable: `Notam`/`Geometry`
  dataclasses, `extract_geometries()`, `parse_d_field()`, `status_at()`,
  `classify_urgency()`, `hk_day_bounds()`, `pretty_text()`, `Notam.view()`,
  `NotamDatabase.filter()/sort_by_urgency()`. All nine issues above are fixed and
  commented.
- **`app.py`** — the router: page config, shared CSS, and the top tab bar.
- **`views/notam.py`** — the NOTAM tab (layout described below).
- **`views/weather.py`** — the Weather tab, currently a blank placeholder.
- **`test_notam.py`** — verification against fixtures *and* the live feed.

### File layout

```
app.py              <- run this:  streamlit run app.py
notam_core.py       <- engine, no Streamlit imports
views/
  notam.py          <- NOTAM tab
  weather.py        <- Weather tab (placeholder)
test_notam.py
```

### Tab navigation

The two tabs use `st.navigation(..., position="top")` rather than `st.tabs`:

```python
notam_page   = st.Page("views/notam.py",   title="NOTAM",   icon="🛩️",
                       url_path="notam", default=True)
weather_page = st.Page("views/weather.py", title="Weather", icon="🌦️",
                       url_path="weather")

st.navigation([notam_page, weather_page], position="top").run()
```

`st.tabs` would render both tab bodies on every run, so the weather page would
execute — and eventually fetch — even while you are looking at NOTAMs. Real pages
give each tab its own URL (`/` and `/weather`), its own sidebar, and its own widget
state, and only the visible page runs. `default=True` makes NOTAM the landing page.

Two implementation notes worth recording:

- **The body needs top clearance.** The top nav occupies the header strip, so
  `.block-container` padding was raised to `3.4rem`. At the original `1.6rem` the
  metrics row rode up underneath the tab bar and the labels were clipped — only the
  numbers were visible.
- **The nav test-id is on the `<a>` itself**, not a wrapper, so the active-tab CSS
  must be `a[data-testid="stTopNavLink"][aria-current="page"]`. A descendant selector
  like `div[data-testid="stTopNavBar"] a` silently matches nothing. Verified by
  reading back the computed style: the active tab resolves to
  `background: rgba(230,0,0,.1)` and `color: rgb(230,0,0)`.

Shared CSS lives in `app.py` because Streamlit re-runs the entry script on every
interaction regardless of which page is showing, so one injection covers both tabs.

---

## 7. Page layout (current version)

Stacked top-to-bottom, with the map pinned in place so it never jumps:

```
┌──────────────────────────────────────────────┐
│  [ 🛩️ NOTAM ] [ 🌦️ Weather ]                 │  <- top tab bar
├──────────────────────────────────────────────┤
│  🔴 now · 🌸 ≤2h · 🟠 today · Published(C) · Mapped │
├──────────────────────────────────────────────┤
│                                              │
│                   MAP                        │  <- fixed position
│                                              │
├──────────────────────────────────────────────┤
│  [ All | Active Today | Next 2 H | Now ]  ●legend │  <- toggle bar
├──────────────────────────────────────────────┤
│  ▓ C0292/26  🔴 ACTIVE NOW                   │
│    active now · ends in 6h 51m               │  <- scrollable list,
│    B) … C) … D) …                            │     one row per NOTAM
│    ┌ 190540 VHHHYNYX (C0292/26 NOTAMN      ┐ │
│    │ Q) VHHK/QRTCA/V /BO /W /000/030/…     │ │  <- one ICAO field
│    │ A) VHHK                               │ │     per line
│    │ B) 2608211000                         │ │
│    │ C) 2610202200                         │ │
│    │ D) DAILY 1000-2200                    │ │
│    │ E) TEMPO RESTRICTED FLYING ZONE …     │ │
│    │ F) SFC                                │ │
│    └ G) 3000FT AMSL)                       ┘ │
│    📍 Show on map   🕑 Windows                │
│  ▓ C0291/26  🔴 ACTIVE NOW               …   │
└──────────────────────────────────────────────┘
```

The map is written into an `st.empty()` slot *before* the toggle bar is drawn, then
filled in afterwards — that keeps it visually on top while still letting it react to
a filter chosen below it.

### Urgency colour scheme

Colour is driven by `classify_urgency()`, not by raw `B)`/`C)` validity:

| Bucket | Meaning | Colour |
|---|---|---|
| `NOW` | active at the evaluation instant | **red** `#e60000`, solid outline |
| `SOON` | switches on within the next N hours (default 2) | **light red / pink** `#ff7d7d` |
| `TODAY` | active later in the HK local day, not now or soon | **amber** `#ffb300` |
| `OTHER` | outside today entirely | grey `#9e9e9e` |

`NOW` also gets a solid outline and heavier fill; the rest are dashed. Shapes are
drawn **least-urgent first**, so red always ends up on top of grey rather than being
buried by a large overlapping polygon.

The "soon" horizon is a sidebar slider (1–12 h), so "Next 2 Hours" re-labels itself if
you change it.

### 7a. Filter semantics (revised)

The four toggle options are **not** a simple cumulative stack of the colour buckets.
Two of them are defined independently, which matters:

| Toggle | Definition | Implementation |
|---|---|---|
| **All** | *every* C-NOTAM currently published on the website — no time filtering at all | `list(self.notams)` |
| **Active Today** | every NOTAM that **has been, will be, or is** active between **0000 LT and 2359 LT of the current date** | `active_in_window(n, day_start, day_end)` via `hk_day_bounds()` |
| **Next 2 Hours** | active now, or switching on within the horizon | `NOW ∪ SOON` |
| **Active Now** | active at the evaluation instant | `status_at(n) == ACTIVE` |

Consequences worth noting:

- **"All" is a plain passthrough.** It is the raw publication list, so its count always
  equals `len(db.notams)` (asserted in the tests). It is not the union of the four
  urgency buckets, and it deliberately includes expired and far-future NOTAMs.
- **"Active Today" looks backwards as well as forwards.** A NOTAM whose window ran
  08:00–18:00 local and has already finished is still "active today". Live example:
  `C0324/26` (`D) DLY 0000-1000`) appears under *Active Today* but not *Active Now*.
  On the last run, 9 of the 15 Active-Today NOTAMs were not currently active.
- **"Active Today" is not a superset of "Next 2 Hours".** Near local midnight a NOTAM
  starting in 90 minutes begins *tomorrow*: it is `SOON` but falls outside today's
  0000–2359 window. The tests only assert the relationships that genuinely hold —
  `Active Now ⊆ Next 2 Hours`, `Active Now ⊆ Active Today`, and every mode `⊆ All`.

### 7b. Series: C only

The viewer fetches and displays **C-series NOTAMs only** (area and navigation
warnings). `update_from_json_api(series="C")` is the default and the sidebar series
selector was removed in favour of a fixed caption. The A-series aerodrome NOTAMs are
still parseable by the engine — pass `series="A"` or `series=None` — they are simply
not surfaced in this UI. Current live count: **33 C-NOTAMs, 29 with a plotted area.**

### 7c. Raw-text formatting — `pretty_text()`

The feed uses `>` as its line separator and packs several ICAO fields onto one line,
so the raw record arrives as an unreadable stream:

```
010842 VHHHYNYX (C0320/26 NOTAMN Q) VHHK/QXXXX/IV/NBO/W /000/011/2219N11402E001 A) VHHK B) 2609011400 C) 2609191500 E) SHOW WITH LIGHTING EFFECTS … F) SFC G) 1100FT AMSL)
```

`pretty_text()` normalises the whitespace and then starts a new line at each ICAO
field marker:

```python
RE_FIELD_MARKER = re.compile(r"(?<![A-Z0-9])([QABCDEFG])\)")

def pretty_text(notam: Notam) -> str:
    stream = " ".join(notam.text.replace(">", " ").split())
    laid_out = RE_FIELD_MARKER.sub(lambda m: "\n" + m.group(1) + ")", stream)
    lines = [ln.strip() for ln in laid_out.split("\n")]
    return "\n".join(ln for ln in lines if ln)
```

The negative lookbehind `(?<![A-Z0-9])` is the important part. Without it the regex
would also fire on the numbered sub-schedules inside `E)` — `1) SEP 1-16 1400-2200` —
and on embedded airspace identifiers such as `VHR12)`, shredding the free text. With
it, numbered items stay inline exactly as the user asked:

```
010845 VHHHYNYX (C0323/26 NOTAMN
Q) VHHK/QXXXX/IV/NBO/W /000/011/2219N11402E001
A) VHHK
B) 2609200500
C) 2610171115
E) SHOW WITH LIGHTING EFFECTS AT HONG KONG DISNEYLAND (WI 1000M RADIUS OF 221845N1140227E, IN VHR12) WILL BE CONDUCTED AS FOLLOWS: 1) SEP 1-16 1400-2200 DLY 2) SEP 17-18 0615-0715 … 3) SEP 19 …
F) SFC
G) 1100FT AMSL)
```

Validated across all 33 live C-NOTAMs: *lines not starting with a field marker or the
telex header* = **0**, *orphaned numbered sub-areas* = **0**, no surviving `>`
separators, and no field marker left mid-line.

**Rendering gotcha.** `st.markdown(..., unsafe_allow_html=True)` runs the string
through a markdown pass before it reaches the DOM, and that pass collapses real
newlines *even inside a `<pre>` block* — `white-space: pre-wrap` never gets a chance to
help. The engine output was correct while the browser still showed one long line. Fix
is to escape the text and emit explicit breaks:

```python
def body_html(notam) -> str:
    lines = nc.pretty_text(notam).split("\n")
    return "<br>".join(html.escape(ln) for ln in lines)
```

Confirmed in the DOM: 8 `<br>` elements and 9 rendered lines for `C0292/26`.

### 7d. Removed from each row

The `Area: 1 shape(s) — circle · RADIUS` summary line was dropped from `.nmeta` at the
user's request; the geometry is already self-evident from the map overlay and the
**📍 Show on map** button. Verified absent from the rendered page
(`anyAreaLine: false`).

### Click-to-pan

Each row has a **📍 Show on map** button. It calls `select()`, which stores the target
in `st.session_state` and passes it to `st_folium(center=…, zoom=…)`.

Zoom comes from `Notam.view()`, which derives it from the shape's real bounding box
(cosine-corrected for longitude). This matters: the reference app's hard-coded
thresholds would slam a huge offshore danger area to zoom 12 and fill the screen with
flat colour. Measured on live data — a 1.13° offshore polygon → zoom 6, a 1000 m
radius circle → zoom 11. Rows with no parseable coordinates show a disabled
**📍 No area** button instead of failing silently.

### Verified in the browser

- top tab bar renders both tabs: `🛩️ NOTAM -> /` and `🌦️ Weather -> /weather`
- NOTAM is the default landing page; clicking Weather routes to `/weather` and
  clicking back restores the map iframe, all 6 rows and the 9-line body formatting
- active tab resolves to the red highlight in computed style
- metric labels are no longer clipped by the nav bar
- header reads **"33 NOTAMs — All"** with 33 rows — i.e. every published C-NOTAM
- header reads **"6 NOTAMs — Active Now"** on the tightest filter
- each row's raw text renders as **9 lines / 8 `<br>`**, one ICAO field per line, with
  `1) 2) 3)` sub-schedules kept inline
- no `Area: … shape(s)` line anywhere on the page
- list container is genuinely scrollable; clicking a row highlights exactly one row
  and pans/zooms the map to that shape
- at 13-Sep 22:00Z all four buckets populate (NOW=6 SOON=6 TODAY=3 OTHER=18), and the
  rendered map HTML contains all four colours

### Verified output (live feed, C-series)

```
Toggle counts        : All 33 | Active Today 15 | Next 2 Hours 6 | Active Now 6
'All' == len(db.notams)                        : True
every NOTAM is C-series                        : True
Active Now ⊆ Next 2 Hours ⊆ All                : True
Active Now ⊆ Active Today                      : True
Active Today carries 9 not-currently-active NOTAMs (already ran / yet to run)

Urgency buckets      : NOW 6 | SOON 0 | TODAY 9 | OTHER 18
with geometry        : 29 of 33
out-of-region points : 0

pretty_text() over all 33 NOTAMs:
  lines not a field marker or header     : 0
  orphaned numbered sub-areas (1) 2) 3)) : 0
  '>' separators left / mid-line markers : none

view() zoom from real bbox:
  C0290/26 polygon span=1.1336 -> zoom 6
  C0292/26 polygon span=0.5625 -> zoom 7
  C0320/26 circle  span=0.0194 -> zoom 11
```

Run it:

```bash
pip install streamlit folium streamlit-folium requests
streamlit run app.py
```


---

## 7e. Named areas: VHD5 and San Wai / Tai Ling

Some NOTAMs name an area instead of giving its coordinates. The live example
is C0298/26:

```
E) FIRING EXER AT VHD5
   SAFETY ALT 3000FT AMSL
   ALL ACFT MUST REMAIN CLEAR OF THE AREA
```

There is not a single lat/long in the body. Without a lookup table the
geometry parser finds nothing, the NOTAM gets no shape, and it silently never
appears on the map — which is the worst possible failure for a live firing
exercise. The same applies to C0297/26, which refers to "SAN WAI / TAI LING
RANGE" by name only.

`NAMED_AREAS` in `notam_core.py` fixes this. It holds two hard-coded polygons
with a trigger pattern each, and adding the table took the mapped count from
**28 to 30 of 33** published C-NOTAMs.

### Resolution order matters

The named-area lookup is deliberately the **last** step, after every attempt to
read coordinates out of the text. A name lookup is a stand-in for coordinates
the NOTAM never gave, so it must never override coordinates the NOTAM did give.

C0334/26 is precisely why:

```
E) HELICOPTER FLYING IN AREA NORTH OF DANGER AREA VHD5 BOUNDED BY
   222900N 1135800E 222800N 1140000E 222310N 1135740E AND 222430N 1135340E
```

This NOTAM *mentions* VHD5 but *describes a different area* just north of it,
and states its own four corners. I originally placed the lookup at step 3,
before the `BOUNDED BY` handler, and it drew the VHD5 box instead of the real
area — wrong airspace, plotted confidently. Moving the lookup to last fixes it:
C0334/26 keeps its own polygon, while C0298/26 still gets the hard-coded one.
Both cases are asserted in `test_notam.py` section 8.

### The reference had VHD5 in the wrong place

`named_locations` at reference line 5213 defined VHD5 as a four-point box at
roughly (22.2333, 113.5667) to (22.2667, 113.6000).

The live VHD5 NOTAM publishes a Q-line of `2225N11357E003` — centre 22.4167 N,
113.95 E, radius 3 NM. The reference box sits **22.7 NM** from that centre,
out in open sea to the south-west. The polygon you supplied has its centroid
**0.87 NM** from the Q-line centre, with every vertex inside the declared 3 NM
radius. Your coordinates agree with the published NOTAM; the reference's did
not.

### Vertex order: sorted into a proper boundary ring

The nine VHD5 coordinates as supplied were **not in boundary order**. The
second entry jumped to the far north while entries 3 and 4 sat in the
south-west, so walking the list in order cut a deep notch across the northern
edge of the area.

They are now stored sorted by bearing around the centroid, with longitude
scaled by cos(latitude) so the angles are geometric rather than skewed by the
lat/lon aspect ratio. That yields a simple, non-self-intersecting ring of about
**22.1 km2**, wound counter-clockwise.

This matters operationally: the unsorted order enclosed **11% less area**, so
the notch was carving a bite out of a live danger area — airspace that is
restricted in reality would have been drawn as clear.

One detail worth recording. The sorted ring is *very slightly concave*:
`(22.413333, 113.935833)` is the single vertex not on the convex hull. So a
convex-hull fit would also have been wrong, just wrong in the other direction
by quietly adding area that isn't part of the danger zone. The angular sort
preserves every vertex exactly as given and only corrects their order.

`test_notam.py` locks this in with four assertions: the ring has no
self-intersections, no duplicate vertices, positive (counter-clockwise) signed
area, and every vertex within the 3 NM radius published in the live NOTAM's
Q-line. If the raw unsorted list is ever pasted back in, the self-intersection
check fails immediately.

### Alias handling

The source data listed six spellings of the San Wai / Tai Ling range — with and
without spaces around the slash, in both name orders, with and without the
trailing "RANGE" — each carrying identical coordinates. Rather than store six
copies that could drift apart, one regex alternation covers every spelling and
the polygon is stored once.

Matching is on **word boundaries**, not substrings. A plain `"TAI LING" in text`
check would fire on "TAI LINGERING ROAD", and `"VHD5"` would fire inside
"VHD51". Both are covered by negative assertions in the test suite.


---

## 7f. NOTAMs that describe more than one area

C0334/26 is a NOTAM with two distinct areas in a single E) field:

```
E) HELICOPTER FLYING IN AREA NORTH OF DANGER AREA VHD5 BOUNDED BY
   222900N 1135800E 222800N 1140000E 222310N 1135740E AND 222430N 1135340E.
   OPERATING ALT A020 OR BLW.
   FIRING EXER IN AREA SURROUNDING DANGER AREA VHD5 BOUNDED BY
   222430N 1135340E 222310N 1135740E 222800N 1140000E 222809N 1135943E
   AND 222648N 1135553E.
   SAFETY LVL A050 OR ABV.
```

A helicopter area north of VHD5 below A020, and a firing area surrounding VHD5
at A050 and above. Two areas, two different altitude bands, one NOTAM.

### What was going wrong

The parser had a multi-area path, but it only recognised *numbered* areas
(`1) ... 2) ...`). This NOTAM separates its areas by simply repeating the
phrase "BOUNDED BY", so that path never fired and the `BOUNDED BY` handler ran
instead — gathering **every** coordinate in the field into one ring.

The result was a single 8-point polygon that ran to the end of the first area,
jumped back to the start of the second, and zig-zagged between them. It covered
airspace belonging to neither area while misrepresenting both.

The fix splits the E) field on each occurrence of "BOUNDED BY" and builds one
polygon per clause, labelled "area 1 of 2" and "area 2 of 2" so the list view
shows which is which.

### A second bug hiding underneath

Once the areas were separated, the first came out as a **triangle** — three
corners, where the text plainly lists four.

The cause is in the raw feed. AIS uses `>` as a hard line separator, and it can
land *between* the latitude and longitude of a single coordinate:

```
222310N 1135740E AND 222430N >1135340E.
```

The coordinate regex expects `DDMMSSN` and `DDDMMSSE` separated by whitespace,
so `222430N >1135340E` did not match and that corner was **silently dropped**.
No error, no warning — the area was simply drawn one corner short.

`find_coords()` now normalises `>` to whitespace before matching. That is a
one-line change, but it is the kind of defect worth taking seriously: a danger
area drawn as a triangle instead of a quadrilateral looks perfectly plausible
on screen, so nothing about the display invites you to doubt it.

### Verification

Both areas now parse to the corner counts the text states (4 and 5), neither
ring self-intersects, and both sit comfortably inside the 5 NM radius published
in the NOTAM's own Q-line (`2226N11357E005`) — centroids 0.35 and 0.38 NM from
that centre, furthest vertex 3.43 NM.

Across the full live corpus the parser now yields 14 `BOUNDED BY` polygons, 13
radius circles, 2 single points and 2 named areas, with no polygon having fewer
than three points.

Covered by `test_notam.py` sections 8g and 8h.

---

## 7g. The disappearing map on "All" and "Active Today"

### Symptom

Selecting **All** or **Active Today** made the map vanish completely. The
metrics row ran straight into the toggle bar with nothing between them. The
other two filters, **Next 2 Hours** and **Active Now**, were unaffected. There
was no error on screen, no traceback in the server log, and no red exception
box — the map was simply absent.

### What it was not

The absence of any error message made this look like a layout or caching
problem, and three plausible explanations had to be ruled out first.

It was not payload size. Rendering each filter offline gave 76 KB of map HTML
for "All" against 20 KB for "Active Now" — larger, but nowhere near a limit
that would break an iframe.

It was not the `st_folium` component key. The map is drawn with a fixed
`key="notam-map"` while the Weather tab varies its key per layer, which made
stale-component reuse an obvious suspect. A standalone probe that changed the
map's contents while holding the key constant rendered correctly every time.

It was not the deferred `st.empty()` slot. The map's position is reserved
before the toggle bar is drawn and filled in afterwards, once the filter is
known. A probe reproducing that exact pattern also rendered correctly.

### What it actually was

Inspecting the live DOM in the broken state gave the decisive clue:

```
outerH=0  attrH=0  innerMap=520  bodyLen=93033
```

The iframe had loaded 93 KB of content and the Leaflet map inside it had laid
itself out correctly at 520 px. Only the **outer** iframe had collapsed to
zero height. The map was being drawn — it was being drawn in a box with no
height.

`streamlit-folium` does not ship folium's rendered HTML to the browser. It
re-writes the map into a standalone JavaScript string, and the component
`eval()`s that string and then calls `Streamlit.setFrameHeight()`. That
ordering matters: if the script throws, the height call is never reached and
the iframe keeps its initial height of zero. The exception happens inside a
sandboxed component iframe, which is why nothing surfaced in the server log.

Dumping the generated JavaScript for each filter and comparing revealed a
duplicate variable declaration present in exactly the two broken modes:

```
all.js           2 x  var html_ed7c5d3188e6e0fd68062abb881f1857
active_today.js  2 x  var html_52acdb62aaf378c12648f615f6745d1a
active_now.js    0 duplicates
```

Both duplicates belonged to **C0334/26** — the helicopter/firing NOTAM whose
two `BOUNDED BY` clauses were split into two separate areas in section 7f. It
is the only NOTAM in the corpus with more than one geometry, and it is active
later today rather than right now, so it appears in "All" and "Active Today"
and in neither of the working filters. That is the whole pattern: the bug was
never about how many shapes were drawn, only about *which* NOTAM was present.

The cause was in `build_map()`. One `folium.Popup` was built per NOTAM and
then attached to every one of its geometries:

```python
popup = folium.Popup(popup_html, max_width=380)
for gi, g in enumerate(n.geometries):
    folium.Polygon(..., popup=popup)      # same object attached twice
```

A folium `Element` may only have one parent. Attaching one popup to two shapes
makes folium emit the same `var html_<hash>` declaration twice, and the second
declaration throws. For every NOTAM with a single area this was harmless, so
the latent defect sat unnoticed until 7f split C0334/26 into two areas and gave
it a second shape to share the popup with.

### The fix

Build a fresh `Popup` inside the geometry loop:

```python
for gi, g in enumerate(n.geometries):
    popup = folium.Popup(popup_html, max_width=380)
```

The HTML string is still built once per NOTAM; only the wrapper object is new
each time. Both areas of C0334/26 keep identical popup content.

A controlled A/B test confirmed cause and effect directly:

| Popup handling | Duplicate declarations | Rendered iframe height |
|---|---|---|
| One shared object | 1 | **0 px — map invisible** |
| Fresh per geometry | 0 | 400 px — map visible |

### Verification

In the running app every filter now reports a 520 px map iframe, where "All"
and "Active Today" previously reported 0:

```
All           height=520    Next 2 Hours  height=520
Active Today  height=520    Active Now    height=520
```

### Guarding against a repeat

The failure was invisible from the server side, so the regression test asserts
on the generated JavaScript rather than on the page. `test_map_render.py`
builds the map for all four filters and fails if any variable is declared more
than once. It also re-tests C0334/26 in isolation, and includes a negative
control that deliberately shares one popup between two shapes and asserts the
duplicate *is* detected — without that control a test like this could pass
simply by failing to look in the right place.
