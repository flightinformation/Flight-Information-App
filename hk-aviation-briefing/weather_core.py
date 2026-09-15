"""
weather_core.py -- UI-free weather engine for the HK Aviation Briefing app.

Ported from the Kivy reference app (`reference_code.txt`), which spread this
logic across several classes:

    WeatherDataManager      (line 4448)  METAR / TAF / ATIS / regional text
      _fetch_metar_from_json_api  (4602)
      _fetch_taf_from_json_api    (4629)
      _fetch_local_forecast...    (4656)
    HKORadarImageManager    (14214) animated radar image sync
    VisibilityDataManager   (8965)  LTMV visibility CSV
    VisibilityOverlay       (9050)  visibility colour bands
    WindDataManager         (9145)  wind parsing from regional text
    GraphicalWindOverlay    (9315)  wind icon selection
    _station_locations      (20036) station coordinates

Everything here is pure data access + parsing, no Streamlit imports, so it can
be unit-tested and reused (see test_weather.py).

Bugs fixed relative to the reference are marked `FIX:`.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
HKT = timezone(timedelta(hours=8))

# Refresh cadences, from the reference app's scheduler constants.
METAR_REFRESH_MINUTES = 15      # reference line 272
RADAR_REFRESH_MINUTES = 3       # reference line 263
ATIS_REFRESH_MINUTES = 5
VIS_REFRESH_MINUTES = 5
WIND_REFRESH_MINUTES = 10

# Endpoints, all confirmed live.
METAR_URL = "https://www.hko.gov.hk/aviat/metar_eng_revamp.json"
TAF_URL = "https://www.hko.gov.hk/aviat/taf_decode_eng_revamp.json"
LOCAL_FCST_URL = "https://www.hko.gov.hk/aviat/100nm_html_e.json"
ATIS_URL = "https://atis.cad.gov.hk/ATIS/ATISweb/atis.php"
RADAR_JSON_URL = "https://www.hko.gov.hk/wxinfo/radars/temp_json/nradar_img.json"
RADAR_BASE_URL = "https://www.hko.gov.hk/wxinfo/radars/"
VIS_URL = ("https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
           "?dataType=LTMV&lang=en&rformat=csv")
REGIONAL_URL = "https://www.hko.gov.hk/dps/wxinfo/ts/text_readings_e.htm"

HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/139.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.hko.gov.hk/en/wxinfo/radars/radar_range.htm",
}

TIMEOUT = 15

# ATIS XPaths, from reference lines 4462-4475.  The two reports live in
# different table cells of the same page: td[1] is arrival, td[3] departure.
ATIS_XPATHS = {
    "arrival": "/html/body/div[1]/table/tbody/tr/td[1]/div/table/tbody"
               "/tr[2]/td/div/table/tbody/tr/td/div",
    "departure": "/html/body/div[1]/table/tbody/tr/td[3]/div/table/tbody"
                 "/tr[2]/td/div/table/tbody/tr/td/div",
}

# Radar ranges.  The reference mapped only range0-2 (line 14237); the feed now
# also carries range3, a 2 km-resolution 64 km product.
# FIX: reference ignored range3 entirely.
RADAR_RANGES = {
    "256 km": "range0",
    "128 km": "range1",
    "64 km": "range2",
    "64 km (2 km res)": "range3",
}
RADAR_DEFAULT = "128 km"

# --------------------------------------------------------------------------
# Station coordinates (reference line 20036).  The reference stored several of
# these as DMS strings run through parse_dms_to_decimal(); they are pre-decimal
# here to keep this module dependency-free.
# --------------------------------------------------------------------------
WIND_STATIONS: dict[str, tuple[float, float]] = {
    "Central Pier": (22.288889, 114.155833),
    "Chek Lap Kok": (22.3265, 113.9021),
    "Cheung Chau": (22.2011, 114.0263),
    "Cheung Chau Beach": (22.2110, 114.0294),
    "Green Island": (22.2849, 114.1128),
    "Hong Kong Sea School": (22.2184, 114.2141),
    "Kai Tak": (22.3100, 114.2130),
    "King's Park": (22.3125, 114.1738),
    "Lamma Island": (22.226111, 114.108611),
    "Lau Fau Shan": (22.468889, 113.983611),
    "Ngong Ping": (22.258611, 113.912778),
    "North Point": (22.294444, 114.199722),
    "Peng Chau": (22.291111, 114.043333),
    "Sai Kung": (22.375556, 114.274444),
    "Sha Chau": (22.345833, 113.891111),
    "Sha Tin": (22.402500, 114.210000),
    "Shek Kong": (22.436111, 114.084722),
    "Stanley": (22.214167, 114.218611),
    "Star Ferry": (22.293056, 114.168611),
    "Ta Kwu Ling": (22.528611, 114.156667),
    "Tai Mei Tuk": (22.475278, 114.237500),
    "Tai Po Kau": (22.442500, 114.183889),
    "Tap Mun": (22.471389, 114.360556),
    "Tate's Cairn": (22.357778, 114.217778),
    "Tseung Kwan O": (22.315833, 114.255556),
    "Tsing Yi": (22.346667, 114.086389),
    "Tuen Mun": (22.390556, 113.976667),
    "Waglan Island": (22.183056, 114.302778),
    "Wetland Park": (22.466667, 114.008889),
    "Wong Chuk Hang": (22.247778, 114.173611),
}

# Visibility stations (reference line 31416).  Only four report LTMV.
VIS_STATIONS: dict[str, tuple[float, float]] = {
    "Central": (22.288889, 114.155833),
    "Chek Lap Kok": (22.309444, 113.921944),
    "Sai Wan Ho": (22.286059, 114.224272),
    "Waglan Island": (22.182222, 114.303333),
}

# Compass name -> bearing the wind is coming FROM (reference line 9149).
# The reference omitted 'Variable' and 'N/A'; both are handled explicitly here.
DIRECTION_TO_DEGREES: dict[str, Optional[float]] = {
    "North": 0.0, "Northeast": 45.0, "East": 90.0, "Southeast": 135.0,
    "South": 180.0, "Southwest": 225.0, "West": 270.0, "Northwest": 315.0,
    "Calm": None, "Variable": None, "N/A": None,
}

# Short codes used for icon filenames (reference line 9322).
DIRECTION_TO_ABBR = {
    "North": "N", "Northeast": "NE", "East": "E", "Southeast": "SE",
    "South": "S", "Southwest": "SW", "West": "W", "Northwest": "NW",
    "Variable": "var", "Calm": "calm",
}

# --------------------------------------------------------------------------
# Regexes
# --------------------------------------------------------------------------
# METAR *or* SPECI -- a SPECI is issued off-schedule when conditions change
# significantly, and the reference correctly accepted both (line 4618).
RE_METAR = re.compile(r"(METAR|SPECI)\s+VHHH\s+\d{6}Z.+?=", re.DOTALL)
# "TAF AMD" is an amended forecast; "TAF COR" is a correction.
# FIX: reference handled AMD but not COR (line 4645).
RE_TAF = re.compile(r"TAF(?:\s+(?:AMD|COR))?\s+VHHH\s+\d{6}Z.+?=", re.DOTALL)
RE_RADAR_PIC = re.compile(r'picture\[\d+\]\[\d+\]\s*=\s*"([^"]+)"')
RE_TAG = re.compile(r"<[^>]+>")
RE_ATIS_BLOCK = re.compile(
    r"VHHH\s+(ARR|DEP)\s+ATIS\s+[A-Z]\s+\d{4}Z.+?(?==ACKNOWLEDGE|$)",
    re.DOTALL)
# Radar filenames carry the timestamp: 2d256nradar_202609142136.jpg
RE_RADAR_TS = re.compile(r"(\d{12})")

# ATIS field extraction (reference parse_qnh_from_atis / parse_oat_from_atis,
# lines 37569 and 37585).
RE_QNH = re.compile(r"QNH\s+(\d+)\s*HPA", re.I)
RE_ATIS_TEMP = re.compile(r"\bT(M?\d{1,2})\b")
RE_ATIS_DEWPT = re.compile(r"\bDP(M?\d{1,2})\b")
RE_ATIS_LETTER = re.compile(r"ATIS\s+([A-Z])\b")
RE_ATIS_TIME = re.compile(r"\b(\d{4})Z\b")
RE_ATIS_RWY = re.compile(r"(?:ARRIVALS|DEPARTURES),\s*RWY\s*([0-9]{2}[LCR]?)", re.I)
RE_ATIS_WIND = re.compile(r"WIND\s+(\S+)")
RE_ATIS_VIS = re.compile(r"\bVIS\s+(\d+(?:\.\d+)?)\s*(KM|M)\b", re.I)


# ==========================================================================
# Dataclasses
# ==========================================================================
@dataclass
class TextReport:
    """A raw aviation text product (METAR, TAF or ATIS)."""
    kind: str                      # "METAR" | "TAF" | "ATIS"
    label: str                     # display label, e.g. "ATIS (Arrival)"
    raw: str = ""
    error: Optional[str] = None
    fetched_at: Optional[datetime] = None

    @property
    def ok(self) -> bool:
        return bool(self.raw) and self.error is None


@dataclass
class WindReading:
    """One station's 10-minute mean wind."""
    station: str
    direction_str: str
    direction_deg: Optional[float]
    speed_kmh: Optional[int]
    gust_kmh: Optional[int]
    lat: Optional[float] = None
    lon: Optional[float] = None

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def speed_kt(self) -> Optional[float]:
        """km/h -> knots. Aviation users think in knots, the feed is km/h."""
        return None if self.speed_kmh is None else self.speed_kmh / 1.852

    @property
    def gust_kt(self) -> Optional[float]:
        return None if self.gust_kmh is None else self.gust_kmh / 1.852

    @property
    def is_unknown(self) -> bool:
        """Station reporting nothing usable (all columns 'N/A')."""
        return self.speed_kmh is None and self.direction_deg is None

    @property
    def is_calm(self) -> bool:
        """Genuinely calm -- not merely missing.

        Careful: ``(self.speed_kmh or 0) == 0`` would call an all-'N/A'
        station calm, because None is falsy.  A station with no reading is
        unknown, which is a different thing from zero wind.
        """
        if self.speed_kmh is None:
            return False
        return self.direction_str == "Calm" or self.speed_kmh == 0

    @property
    def icon_abbr(self) -> Optional[str]:
        """Filename stem for an overlay icon, e.g. 'NE' -> assets/wind/NE.png"""
        return DIRECTION_TO_ABBR.get(self.direction_str)

    def label(self) -> str:
        if self.is_unknown:
            return "no data"
        if self.is_calm:
            return "Calm"
        spd = "?" if self.speed_kmh is None else str(self.speed_kmh)
        direction = ("variable" if self.direction_str in ("N/A", "Variable")
                     else self.direction_str)
        out = f"{direction} {spd} km/h"
        if self.gust_kmh and self.speed_kmh and self.gust_kmh > self.speed_kmh:
            out += f" G{self.gust_kmh}"
        return out


@dataclass
class VisReading:
    """One station's 10-minute mean visibility."""
    station: str
    raw: str
    metres: Optional[float]
    lat: Optional[float] = None
    lon: Optional[float] = None
    observed_at: Optional[datetime] = None

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def colour(self) -> str:
        return vis_colour(self.metres)

    @property
    def band(self) -> str:
        return vis_band(self.metres)

    def label(self) -> str:
        if self.metres is None:
            return "n/a"
        if self.metres >= 1000:
            km = self.metres / 1000
            return f"{km:g} km"
        return f"{self.metres:.0f} m"


@dataclass
class RadarFrames:
    """An animated radar loop for one range."""
    range_label: str
    urls: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.urls) and self.error is None

    def timestamps(self) -> list[Optional[datetime]]:
        return [radar_frame_time(u) for u in self.urls]

    def latest_time(self) -> Optional[datetime]:
        ts = [t for t in self.timestamps() if t]
        return max(ts) if ts else None


# ==========================================================================
# Helpers
# ==========================================================================
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    return s


def strip_tags(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return ""
    return " ".join(RE_TAG.sub(" ", text).split())


def _deep_find_str(obj, needle: re.Pattern) -> Optional[str]:
    """Walk a nested dict/list and return the first string matching `needle`.

    The HKO JSON wrappers occasionally move the payload between keys
    (``content`` vs ``content1``, ``table`` vs ``table1``).  The reference
    hard-coded one path and returned None whenever it changed, which is why
    the Selenium fallback existed at all.
    FIX: search the whole structure instead of one fixed path.
    """
    if isinstance(obj, str):
        return obj if needle.search(obj) else None
    if isinstance(obj, dict):
        for v in obj.values():
            found = _deep_find_str(v, needle)
            if found:
                return found
    if isinstance(obj, (list, tuple)):
        for v in obj:
            found = _deep_find_str(v, needle)
            if found:
                return found
    return None


def parse_vis_metres(value: str) -> Optional[float]:
    """'18 km' -> 18000.0, '800 m' -> 800.0, 'N/A' -> None.

    FIX: the reference used ``re.search(r'(\\d+)')`` then multiplied by 1000
    for km (line 9082), so a fractional reading like '1.5 km' parsed as 1 km
    = 1000 m and was mis-coloured red instead of light purple.  Parsing as a
    float fixes that.
    """
    if not value:
        return None
    txt = str(value).strip()
    m = re.search(r"(\d+(?:\.\d+)?)", txt)
    if not m:
        return None
    num = float(m.group(1))
    return num * 1000 if "km" in txt.lower() else num


def vis_band(metres: Optional[float]) -> str:
    """Visibility band names, thresholds from reference line 9087."""
    if metres is None:
        return "unknown"
    if metres > 5000:
        return "good"
    if metres > 3000:
        return "moderate"
    if metres > 1000:
        return "poor"
    return "very poor"


# Colours converted from the reference's Kivy RGBA tuples (line 9087).
VIS_COLOURS = {
    "good": "#33cc33",        # (0.2, 0.8, 0.2)
    "moderate": "#b3b3b3",    # (0.7, 0.7, 0.7)
    "poor": "#cc99cc",        # (0.8, 0.6, 0.8)
    "very poor": "#cc3333",   # (0.8, 0.2, 0.2)
    "unknown": "#808080",     # (0.5, 0.5, 0.5)
}


def vis_colour(metres: Optional[float]) -> str:
    return VIS_COLOURS[vis_band(metres)]


# Wind speed colours (reference GraphicalWindOverlay fallback, line 9345).
def wind_colour(speed_kmh: Optional[int]) -> str:
    if speed_kmh is None:
        return "#808080"
    if speed_kmh > 40:
        return "#ff3333"      # strong
    if speed_kmh > 20:
        return "#ffcc33"      # moderate
    if speed_kmh > 0:
        return "#33cc33"      # light
    return "#808080"          # calm


def radar_frame_time(url: str) -> Optional[datetime]:
    """Pull the UTC timestamp out of a radar image filename."""
    m = RE_RADAR_TS.search(urlparse(url).path)
    if not m:
        return None
    try:
        # HKO names these files in Hong Kong local time.
        naive = datetime.strptime(m.group(1), "%Y%m%d%H%M")
        return naive.replace(tzinfo=HKT)
    except ValueError:
        return None


def fmt_hkt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.astimezone(HKT).strftime("%d-%b %H:%M") + " HKT"


# ==========================================================================
# METAR / TAF
# ==========================================================================
def fetch_metar(session: Optional[requests.Session] = None) -> TextReport:
    """METAR/SPECI for VHHH (reference _fetch_metar_from_json_api, line 4602)."""
    s = session or _session()
    rep = TextReport("METAR", "METAR / SPECI", fetched_at=datetime.now(timezone.utc))
    try:
        data = s.get(METAR_URL, timeout=TIMEOUT).json()
        blob = _deep_find_str(data, RE_METAR)
        if not blob:
            rep.error = "METAR/SPECI pattern not found in JSON response"
            return rep
        m = RE_METAR.search(blob)
        rep.raw = " ".join(m.group(0).split()) if m else ""
        if not rep.raw:
            rep.error = "METAR/SPECI pattern not found"
    except Exception as exc:
        rep.error = f"{type(exc).__name__}: {exc}"
    return rep


def fetch_taf(session: Optional[requests.Session] = None) -> TextReport:
    """TAF for VHHH (reference _fetch_taf_from_json_api, line 4629)."""
    s = session or _session()
    rep = TextReport("TAF", "TAF", fetched_at=datetime.now(timezone.utc))
    try:
        data = s.get(TAF_URL, timeout=TIMEOUT).json()
        blob = _deep_find_str(data, RE_TAF)
        if not blob:
            rep.error = "TAF pattern not found in JSON response"
            return rep
        m = RE_TAF.search(blob)
        rep.raw = " ".join(m.group(0).split()) if m else ""
        if not rep.raw:
            rep.error = "TAF pattern not found"
    except Exception as exc:
        rep.error = f"{type(exc).__name__}: {exc}"
    return rep


# ==========================================================================
# ATIS
# ==========================================================================
def fetch_atis(session: Optional[requests.Session] = None
               ) -> dict[str, TextReport]:
    """Arrival and departure ATIS for VHHH.

    Primary path is the reference's XPaths (line 4462); if the page layout
    shifts, fall back to a regex over the whole page text.
    FIX: the reference had no fallback -- an XPath change returned None and the
    ATIS silently vanished.
    """
    s = session or _session()
    now = datetime.now(timezone.utc)
    out = {
        "arrival": TextReport("ATIS", "ATIS (Arrival)", fetched_at=now),
        "departure": TextReport("ATIS", "ATIS (Departure)", fetched_at=now),
    }
    try:
        resp = s.get(ATIS_URL, timeout=TIMEOUT)
        resp.raise_for_status()

        from lxml import html as lxml_html
        tree = lxml_html.fromstring(resp.content)

        for key, xp in ATIS_XPATHS.items():
            els = tree.xpath(xp)
            if els:
                txt = " ".join(els[0].text_content().split())
                if txt:
                    out[key].raw = txt

        # Fallback: scan the page text for ARR/DEP ATIS blocks.
        if not all(r.ok for r in out.values()):
            page = " ".join(tree.text_content().split())
            for m in RE_ATIS_BLOCK.finditer(page):
                key = "arrival" if m.group(1) == "ARR" else "departure"
                if not out[key].raw:
                    out[key].raw = " ".join(m.group(0).split())

        for key, rep in out.items():
            if not rep.raw:
                rep.error = f"No {key} ATIS found on page"
    except Exception as exc:
        for rep in out.values():
            rep.error = f"{type(exc).__name__}: {exc}"
    return out


def parse_qnh(atis_text: str) -> Optional[float]:
    """'QNH 1013HPA' -> 1013.0 (reference line 37569)."""
    if not atis_text:
        return None
    m = RE_QNH.search(atis_text)
    return float(m.group(1)) if m else None


def _signed_temp(token: str) -> float:
    """'25' -> 25.0, 'M05' -> -5.0"""
    return -float(token[1:]) if token.upper().startswith("M") else float(token)


def parse_oat(atis_text: str) -> Optional[float]:
    """'T26' -> 26.0, 'TM05' -> -5.0 (reference line 37585)."""
    if not atis_text:
        return None
    m = RE_ATIS_TEMP.search(atis_text)
    if m:
        return _signed_temp(m.group(1))
    m = re.search(r"\b(M\d{1,2})\b", atis_text)
    return -float(m.group(1)[1:]) if m else None


def parse_dewpoint(atis_text: str) -> Optional[float]:
    """'DP24' -> 24.0.  Not in the reference; useful alongside OAT."""
    if not atis_text:
        return None
    m = RE_ATIS_DEWPT.search(atis_text)
    return _signed_temp(m.group(1)) if m else None


def atis_summary(atis_text: str) -> dict[str, Optional[str]]:
    """Pull the headline fields out of an ATIS for a compact summary row."""
    if not atis_text:
        return {}
    letter = RE_ATIS_LETTER.search(atis_text)
    time_m = RE_ATIS_TIME.search(atis_text)
    rwy = RE_ATIS_RWY.search(atis_text)
    wind = RE_ATIS_WIND.search(atis_text)
    vis = RE_ATIS_VIS.search(atis_text)
    qnh = parse_qnh(atis_text)
    oat = parse_oat(atis_text)
    dewpt = parse_dewpoint(atis_text)
    return {
        "letter": letter.group(1) if letter else None,
        "time": f"{time_m.group(1)}Z" if time_m else None,
        "runway": rwy.group(1) if rwy else None,
        "wind": wind.group(1) if wind else None,
        "visibility": (f"{vis.group(1)} {vis.group(2).upper()}"
                       if vis else None),
        "qnh": f"{qnh:.0f} hPa" if qnh is not None else None,
        "temp": f"{oat:.0f}°C" if oat is not None else None,
        "dewpoint": f"{dewpt:.0f}°C" if dewpt is not None else None,
    }


def pretty_atis(atis_text: str) -> str:
    """ATIS is one run-on string of '.'-separated clauses; put each on a line.

    Mirrors the reference _format_atis_data (line 37611) but without Kivy
    markup.  The '=' terminator and the ACKNOWLEDGE trailer are kept on their
    own lines so the body stays readable.
    """
    if not atis_text:
        return ""
    txt = " ".join(atis_text.split())
    txt = txt.replace("=", "=\n")
    parts = [p.strip() for p in txt.split(".")]
    lines = [p + "." if not p.endswith(("=", ".")) else p
             for p in parts if p]
    return "\n".join(lines)


def pretty_metar(metar: str) -> str:
    """Keep the header on line 1, then break before change groups.

    Reference _format_metar_data (line 37629) broke on ' Q' and ' TEMPO'.
    FIX: breaking on ' Q' also split the QNH group away from nothing useful
    and could fire inside remarks; break on real change-group keywords only.
    """
    if not metar:
        return ""
    txt = " ".join(metar.split())
    for kw in ("TEMPO", "BECMG", "NOSIG", "RMK"):
        txt = txt.replace(f" {kw}", f"\n{kw}")
    return txt


def pretty_taf(taf: str) -> str:
    """Break a TAF before each change group so the periods line up."""
    if not taf:
        return ""
    txt = " ".join(taf.split())
    # FROM/TEMPO/BECMG/PROB groups each start a new forecast period.
    txt = re.sub(r"\s+(TEMPO|BECMG|PROB\d{2}|FM\d{6}|TX|TN)", r"\n\1", txt)
    return txt


# ==========================================================================
# Radar
# ==========================================================================
def fetch_radar(session: Optional[requests.Session] = None
                ) -> dict[str, RadarFrames]:
    """Animated radar frame URLs for every range (reference line 14276)."""
    s = session or _session()
    out = {label: RadarFrames(label) for label in RADAR_RANGES}
    try:
        data = s.get(RADAR_JSON_URL, timeout=TIMEOUT).json()
        radar = data.get("radar", {})
        for label, json_key in RADAR_RANGES.items():
            section = radar.get(json_key, {})
            urls: list[str] = []
            for entry in section.get("image", []):
                m = RE_RADAR_PIC.match(entry.strip())
                if m:
                    urls.append(urljoin(RADAR_BASE_URL, m.group(1)))
            # Sort by embedded timestamp so the loop always plays in order.
            # FIX: the reference kept these in a set (line 14282), which
            # destroyed frame order and made the animation jump around.
            urls.sort(key=lambda u: (radar_frame_time(u) or datetime.min
                                     .replace(tzinfo=HKT)))
            out[label].urls = urls
            if not urls:
                out[label].error = "No frames found"
    except Exception as exc:
        for fr in out.values():
            fr.error = f"{type(exc).__name__}: {exc}"
    return out


# ==========================================================================
# Visibility
# ==========================================================================
def fetch_visibility(session: Optional[requests.Session] = None
                     ) -> tuple[list[VisReading], Optional[str]]:
    """10-minute mean visibility per station (reference line 8978).

    The CSV is: "Date time","Automatic Weather Station","10 minute mean
    visibility".  It is UTF-8 with a BOM and quotes any field containing a
    space, so it needs a real CSV reader rather than str.split(',').
    FIX: the reference split on ',' (line 8991), which breaks on any quoted
    station name containing a comma and leaves stray BOM bytes in the header.
    """
    s = session or _session()
    try:
        resp = s.get(VIS_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        text = resp.content.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        out: list[VisReading] = []
        for row in rows[1:]:
            if len(row) < 3:
                continue
            stamp, station, value = (c.strip() for c in row[:3])
            observed = None
            try:
                observed = datetime.strptime(stamp, "%Y%m%d%H%M").replace(
                    tzinfo=HKT)
            except ValueError:
                pass
            lat, lon = VIS_STATIONS.get(station, (None, None))
            out.append(VisReading(
                station=station, raw=value,
                metres=parse_vis_metres(value),
                lat=lat, lon=lon, observed_at=observed))
        if not out:
            return [], "No visibility rows in CSV response"
        return out, None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


# ==========================================================================
# Wind
# ==========================================================================
WIND_BLOCK_START = ("10-Minute Mean Wind Direction, Speed and Maximum Gust "
                    "(km/hour)")
WIND_BLOCK_END = "Mean Sea Level Pressure (hPa)"


def parse_wind_block(regional_text: str) -> list[WindReading]:
    """Parse the fixed-width wind table out of the regional readings page.

    The block looks like this (column positions are stable at 0/22/36/39):

        Central Pier          West           4     9
        Cheung Chau Beach     N/A            5     7
        Hong Kong Sea School  Calm                 0
        Stanley               N/A          N/A   N/A

    FIX: the reference used the regex
    ``^(.*?)\\s+([A-Za-z]+)\\s+(\\d+)(?:\\s+(\\d+))?$`` (line 9169).  The
    ``[A-Za-z]+`` direction group cannot match ``N/A`` because of the slash,
    so every station reporting an unknown direction was silently dropped --
    5 of 30 stations on the sample checked, including Green Island and
    Tate's Cairn.  Slicing by column and validating each field keeps them.
    """
    readings: list[WindReading] = []
    if not regional_text or WIND_BLOCK_START not in regional_text:
        return readings

    block = regional_text.split(WIND_BLOCK_START, 1)[1]
    block = block.split(WIND_BLOCK_END, 1)[0].strip("\n")

    for line in block.split("\n"):
        if not line.strip():
            continue

        # Column slice first; fall back to whitespace tokens if the layout
        # ever changes width.
        name = line[:22].strip()
        direction = line[22:36].strip()
        speed_s = line[36:39].strip()
        gust_s = line[39:].strip()

        # A row reading "Stanley  N/A  N/A  N/A" straddles the 36-char
        # boundary, so the direction slice picks up "N/A          N" and the
        # speed slice gets "/A".  Re-tokenise whenever the slice looks wrong.
        if " " in direction or "/" in speed_s:
            name = None

        if not name or not direction:
            toks = line.split()
            if len(toks) < 2:
                continue
            # Direction is the first token that is a known compass name.
            idx = next((i for i, t in enumerate(toks)
                        if t in DIRECTION_TO_DEGREES or t == "N/A"), None)
            if idx is None:
                continue
            name = " ".join(toks[:idx]).strip()
            direction = toks[idx]
            nums = toks[idx + 1:]
            speed_s = nums[0] if nums else ""
            gust_s = nums[1] if len(nums) > 1 else ""

        def as_int(tok: str) -> Optional[int]:
            return int(tok) if tok.isdigit() else None

        speed = as_int(speed_s)
        gust = as_int(gust_s)

        # "Calm" rows leave the speed column blank and put 0 in the gust
        # column; report that as a 0 kt mean wind rather than an unknown.
        if direction == "Calm" and speed is None:
            speed = 0
            if gust == 0:
                gust = None

        lat, lon = WIND_STATIONS.get(name, (None, None))
        readings.append(WindReading(
            station=name,
            direction_str=direction,
            direction_deg=DIRECTION_TO_DEGREES.get(direction),
            speed_kmh=speed, gust_kmh=gust,
            lat=lat, lon=lon))
    return readings


def fetch_wind(session: Optional[requests.Session] = None
               ) -> tuple[list[WindReading], Optional[str]]:
    """Fetch the regional readings page and parse its wind table."""
    s = session or _session()
    try:
        resp = s.get(REGIONAL_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(resp.content)
        body = tree.xpath("//body")
        text = body[0].text_content() if body else tree.text_content()
        readings = parse_wind_block(text)
        if not readings:
            return [], "Wind block not found in regional readings page"
        return readings, None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


# ==========================================================================
# Aggregate
# ==========================================================================
@dataclass
class WeatherBundle:
    """Everything the Weather tab needs, fetched in one pass."""
    metar: TextReport
    taf: TextReport
    atis: dict[str, TextReport]
    radar: dict[str, RadarFrames]
    vis: list[VisReading]
    vis_error: Optional[str]
    wind: list[WindReading]
    wind_error: Optional[str]
    fetched_at: datetime

    def vis_mapped(self) -> list[VisReading]:
        return [v for v in self.vis if v.has_location]

    def wind_mapped(self) -> list[WindReading]:
        return [w for w in self.wind if w.has_location]

    def wind_unmapped(self) -> list[str]:
        """Stations present in the feed but missing from WIND_STATIONS."""
        return [w.station for w in self.wind if not w.has_location]


def fetch_all() -> WeatherBundle:
    """Fetch every weather product. Each source fails independently."""
    s = _session()
    vis, vis_err = fetch_visibility(s)
    wind, wind_err = fetch_wind(s)
    return WeatherBundle(
        metar=fetch_metar(s),
        taf=fetch_taf(s),
        atis=fetch_atis(s),
        radar=fetch_radar(s),
        vis=vis, vis_error=vis_err,
        wind=wind, wind_error=wind_err,
        fetched_at=datetime.now(timezone.utc),
    )
