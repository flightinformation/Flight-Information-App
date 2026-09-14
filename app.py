"""
HK Aviation Briefing -- Streamlit port of the Kivy reference app.

Run:  streamlit run app.py

This file is the *router* only.  It owns the page config, the shared CSS and
the top tab bar; the actual pages live in views/.

    views/notam.py    -- the NOTAM map + toggle bar + scrollable list
    views/weather.py  -- placeholder, to be built out next

`st.navigation(position="top")` is used rather than `st.tabs` because each tab
is a genuinely separate page: it gets its own URL, its own sidebar controls and
its own widget state, and switching tabs does not re-run the other page's data
fetch.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="HK Aviation Briefing", page_icon="🛩️",
                   layout="wide")

# --------------------------------------------------------------------------
# Shared styling.  Injected here in the entry script so it applies to every
# page -- Streamlit re-runs this file on every interaction, on both tabs.
# --------------------------------------------------------------------------
st.markdown("""
<style>
  /* Top nav bar occupies the header strip, so the body needs clearance or
     it rides up underneath and clips the first row (the metric labels). */
  .block-container {padding-top: 3.4rem; padding-bottom: 1rem;}
  div[data-testid="stMetricValue"] {font-size: 1.35rem;}
  div[data-testid="stMetricLabel"] {font-size: .78rem;}

  /* ---- Top tab bar (st.navigation position="top") -------------------
     The test-id sits on the <a> itself, not on a wrapper, so the selector
     must be a[data-testid="stTopNavLink"] -- not a descendant combinator. */
  a[data-testid="stTopNavLink"] {
      font-weight: 600 !important;
      font-size: .95rem !important;
      padding: .40rem 1.15rem !important;
      border-radius: 8px !important;
      border: 1px solid transparent !important;
  }
  a[data-testid="stTopNavLink"]:hover {
      background: rgba(128,128,128,.12) !important;
  }
  a[data-testid="stTopNavLink"][aria-current="page"] {
      background: rgba(230,0,0,.10) !important;
      border: 1px solid rgba(230,0,0,.35) !important;
      color: #e60000 !important;
  }
  a[data-testid="stTopNavLink"][aria-current="page"] * {
      color: #e60000 !important;
  }

  /* ---- NOTAM list rows ---------------------------------------------- */
  .nrow {
      border-left: 6px solid var(--c);
      background: rgba(128,128,128,.07);
      border-radius: 6px;
      padding: .5rem .7rem;
      margin-bottom: .15rem;
  }
  .nrow-sel {background: rgba(255,170,0,.16); box-shadow: 0 0 0 1px rgba(255,170,0,.5);}
  .nid   {font: 700 1.02rem/1.2 ui-monospace, monospace;}
  .nbadge{
      font: 700 .68rem/1 ui-monospace, monospace; color: #fff;
      background: var(--c); padding: .16rem .45rem;
      border-radius: 10px; margin-left: .4rem; vertical-align: 2px;
      white-space: nowrap;
  }
  .nnote {font-size: .8rem; opacity: .85; margin-top: .18rem;}
  .nmeta {font: .74rem/1.45 ui-monospace, monospace; opacity: .72; margin-top: .28rem;}
  .nbody {
      font: .76rem/1.45 ui-monospace, monospace;
      white-space: pre-wrap; margin: .4rem 0 0;
      max-height: 9rem; overflow: auto;
      background: rgba(128,128,128,.10);
      padding: .4rem .55rem; border-radius: 4px;
  }
  div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stButton"] button {
      padding: .15rem .5rem; font-size: .74rem;
  }

  /* ---- Weather placeholder ------------------------------------------ */
  .wx-hero {
      border: 1px dashed rgba(128,128,128,.45);
      border-radius: 10px;
      padding: 2.4rem 1.6rem;
      text-align: center;
      background: rgba(128,128,128,.05);
  }
  .wx-hero h2 {margin: .2rem 0 .4rem; font-size: 1.5rem;}
  .wx-hero p  {margin: 0; opacity: .75; font-size: .9rem;}
</style>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------
# Tab bar.  `default=True` makes NOTAM the landing page, so "/" shows NOTAM
# and "/weather" shows the weather page.
# --------------------------------------------------------------------------
notam_page = st.Page("views/notam.py", title="NOTAM", icon="🛩️",
                     url_path="notam", default=True)
weather_page = st.Page("views/weather.py", title="Weather", icon="🌦️",
                       url_path="weather")

st.navigation([notam_page, weather_page], position="top").run()
