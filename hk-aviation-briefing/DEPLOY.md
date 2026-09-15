# Publishing the app

## Why the one-click "deploy" button won't work

This is not a static website. It is a Python server that fetches live data from
HKO and CAD on every page load. Static hosting (S3, GitHub Pages, Netlify drop)
only serves HTML/CSS/JS files — there is no Python process, so `app.py` would
never run.

You need a host that runs Python.

---

## Option 1 — Streamlit Community Cloud (recommended, free)

Best fit: it is built by the Streamlit team specifically for this, and the free
tier is enough for this app.

1. Put the project on GitHub. From the project folder:

   ```bash
   git init
   git add .
   git commit -m "HK Aviation Briefing"
   git branch -M main
   git remote add origin https://github.com/YOURNAME/hk-aviation-briefing.git
   git push -u origin main
   ```

   `.gitignore` already excludes `reference_code.txt`, logs and caches, so your
   38k-line reference file will not be published.

2. Go to <https://share.streamlit.io> and sign in with GitHub.

3. **New app** → pick the repo → set **Main file path** to `app.py` → Deploy.

It installs from `requirements.txt` automatically. First build takes a few
minutes; after that every `git push` redeploys.

You get a URL like `https://yourname-hk-aviation-briefing.streamlit.app`.

**Note:** free apps sleep after ~7 days of no visitors and wake on the next
visit (slow first load). Anyone with the link can view it unless you set the
app to private in the app settings.

---

## Option 2 — Render / Railway / Fly.io

Use these if you want it always-on, or on your own domain.

The one thing that differs from local: these platforms assign a port via the
`$PORT` environment variable, so the start command must use it.

**Render** — New → Web Service → connect repo:

- Build command: `pip install -r requirements.txt`
- Start command:
  ```
  streamlit run app.py --server.port $PORT --server.address 0.0.0.0
  ```

Railway and Fly.io work the same way with that start command.

---

## Option 3 — Your own server / VPS

```bash
git clone https://github.com/YOURNAME/hk-aviation-briefing.git
cd hk-aviation-briefing
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

`run.sh` pins port 8501. For anything public-facing put nginx or Caddy in front
to terminate TLS, and run it under systemd so it restarts on reboot.

---

## Before you publish — two things to check

**Outbound network access.** The app is useless without it. It needs to reach
`hko.gov.hk`, `data.weather.gov.hk`, `atis.cad.gov.hk` and the AIS NOTAM site.
All of these are plain public HTTPS, so any normal host is fine, but a locked-
down corporate environment may block them.

**Data licensing.** You are redistributing HKO and CAD data, and hotlinking HKO
radar imagery directly. That is fine for personal or internal use, but if this
is going public you should check their terms and add an attribution line. The
radar images already carry the HKO copyright notice burned into them.

**This is not for operational flight use.** Worth stating on the page itself if
other people will see it — the official source is the AIS/CAD briefing system,
not a scraper.

---

## Files that make deployment work

| File | Purpose |
|---|---|
| `requirements.txt` | Dependencies. Every host reads this. |
| `.streamlit/config.toml` | Headless mode, theme, no port pinned so `$PORT` wins. |
| `.gitignore` | Keeps `reference_code.txt`, logs and caches out of the repo. |
| `run.sh` | Local/VPS launch on port 8501. |
