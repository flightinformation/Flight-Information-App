# Publishing to GitHub + Streamlit Cloud — step by step

Total time: about 10 minutes, most of it waiting for the first build.

You need a GitHub account (free) at <https://github.com/signup> if you don't
have one.

---

# Part 1 — Get the code onto GitHub

## Step 1. Unzip the project

Download `hk-aviation-briefing.zip` and unzip it somewhere sensible — your
Documents folder is fine. You should end up with a folder called
`hk-aviation-briefing` containing `app.py`, `views/`, `requirements.txt` and so
on.

**The git repository is already set up inside it, with your first commit
already made.** You do not need to run `git init` or `git commit`. Skip
straight to creating the GitHub repo.

## Step 2. Create an empty repo on GitHub

Go to <https://github.com/new> and fill in:

- **Repository name:** `hk-aviation-briefing`
- **Public** or **Private** — both work with Streamlit Cloud. Private is the
  safer default if you're unsure about the data licensing.
- **Do NOT tick** "Add a README file"
- **Do NOT add** a .gitignore or licence

Those last two matter. The project already contains a README and a .gitignore,
and if GitHub creates its own the histories conflict and your first push gets
rejected.

Click **Create repository**. You'll land on a page showing setup commands —
leave it open, you need the URL from it.

## Step 3. Push

Open a terminal (macOS: Terminal; Windows: Git Bash, installed with
<https://git-scm.com/download/win>), then `cd` into the folder:

```bash
cd ~/Documents/hk-aviation-briefing      # adjust to wherever you unzipped it
```

Set your identity — the repo currently has a placeholder:

```bash
git config user.name "Your Name"
git config user.email "your@email.com"
```

Then connect it to GitHub and push, replacing `YOURNAME` with your GitHub
username:

```bash
git remote add origin https://github.com/YOURNAME/hk-aviation-briefing.git
git push -u origin main
```

**On the password prompt:** GitHub does not accept your account password here.
You need a Personal Access Token:

1. Go to <https://github.com/settings/tokens>
2. **Generate new token** → **classic**
3. Give it a name, set an expiry, tick the **repo** checkbox
4. **Generate token**, then copy it — it is shown only once
5. Paste it as the password (the field stays blank as you paste; that's normal)

Refresh your GitHub repo page and you should see all 17 files.

---

# Part 2 — Deploy on Streamlit Cloud

## Step 4. Sign in

Go to <https://share.streamlit.io> and **Continue with GitHub**. Authorise it
when asked.

If you made the repo **private**, you must grant repository access when
prompted, or Streamlit won't be able to see it.

## Step 5. Create the app

Click **Create app**, then choose **Deploy a public app from GitHub**.

Fill in:

- **Repository:** `YOURNAME/hk-aviation-briefing`
- **Branch:** `main`
- **Main file path:** `app.py`

Optionally click **Custom subdomain** to pick your URL.

Click **Deploy**.

## Step 6. Wait for the build

You'll see a log streaming. It installs the five packages from
`requirements.txt`, which takes roughly 2–5 minutes on first build.

When it finishes, the app opens. Check that the NOTAM map draws and the
Weather tab shows a current METAR.

**Your URL** looks like:

```
https://hk-aviation-briefing.streamlit.app
```

Share it with anyone.

---

# Updating the app later

Streamlit Cloud watches the repo, so pushing is all it takes:

```bash
git add -A
git commit -m "describe what changed"
git push
```

The app rebuilds automatically in a minute or two. This is also how you add
your overlay icons — drop the files into `assets/icons/wind/` and
`assets/icons/vis/`, then commit and push.

---

# If something goes wrong

**Push rejected, "fetch first" or "non-fast-forward"** — GitHub created a
README when you made the repo. Fix:
```bash
git pull --rebase origin main
git push -u origin main
```

**"Authentication failed"** — you used your password instead of a Personal
Access Token. See Step 3.

**"ModuleNotFoundError" in the build log** — something is missing from
`requirements.txt`. It should list `streamlit`, `streamlit-folium`, `folium`,
`requests` and `lxml`.

**App builds but weather is empty** — HKO or CAD is unreachable or temporarily
down. Click **Refresh now** in the sidebar. To confirm the feeds are fine,
run `python test_weather.py` locally; it hits all six live.

**"This app has gone to sleep"** — normal on the free tier after about a week
of no visitors. Anyone can click to wake it, though the first load is slow.

**Repo not listed in Streamlit** — it's private and you didn't grant access.
Go to <https://github.com/settings/installations>, find Streamlit, and add the
repository.

---

# Two things worth doing before you share it widely

**Check the data licensing.** You are redistributing HKO and CAD data and
hotlinking HKO radar images. Fine for personal or internal use; if this is
going properly public, check their terms and credit them.

**Keep the disclaimer visible.** The README says it already, but if colleagues
are going to use this, make sure they understand it is not for operational
flight use — the authoritative sources are the CAD AIS briefing system and the
HKO aviation weather service.
