# Publishing to GitHub + Streamlit Cloud — step by step

Everything here is free. Budget about 15 minutes the first time; updates
afterwards take about 30 seconds.

You need a GitHub account (github.com/join) and Git installed
(`git --version` — if that errors, get it from git-scm.com/downloads).

The project folder is already a Git repository with all the code committed, so
you are starting from a good place. Nothing needs to be built or compiled.

---

# Part 1 — Get the code onto GitHub

## Step 1. Unzip the project

Unzip `hk-aviation-briefing.zip` somewhere sensible — Documents is fine, just
not inside a folder that syncs to iCloud or OneDrive, because those sometimes
fight with Git.

Open a terminal **inside that folder**:

- **Windows** — open the folder in File Explorer, type `cmd` in the address
  bar, press Enter.
- **macOS** — right-click the folder → Services → New Terminal at Folder.

Confirm you are in the right place:

```bash
git log --oneline
```

You should see a list of commits ending with `HK Aviation Briefing: NOTAM and
Weather tabs`. If you instead get *"not a git repository"*, you are one level
too high or too low — `cd` into the folder that directly contains `app.py`.

## Step 2. Create an empty repo on GitHub

Go to **github.com/new** and fill in:

| Field | Value |
|---|---|
| Repository name | `hk-aviation-briefing` |
| Public / Private | either works — Streamlit Cloud can read private repos |
| Add a README | **leave unticked** |
| Add .gitignore | **leave unticked** |
| Add a licence | **leave unticked** |

Those three must stay unticked. The repo already has a README and a
`.gitignore`, and letting GitHub create its own makes the first push conflict
for no good reason.

Click **Create repository**. GitHub shows you a setup page — ignore it, the
commands you need are below.

## Step 3. Push

Replace `YOUR-USERNAME` with your GitHub username in both lines:

```bash
git remote add origin https://github.com/YOUR-USERNAME/hk-aviation-briefing.git
git push -u origin main
```

**On the password prompt:** your GitHub account password will *not* work. Git
wants a Personal Access Token:

1. Go to **github.com/settings/tokens** → *Generate new token (classic)*
2. Note: `streamlit deploy`. Expiration: your choice.
3. Tick the **`repo`** scope.
4. *Generate token*, then copy it — it is shown only once.
5. Paste it as the password. (Nothing appears as you paste. That is normal.)

On Windows the Git Credential Manager may pop up a browser window instead —
that is easier, just sign in there.

Refresh your GitHub repo page. All 20 files should now be listed.

> **A note on what is *not* being uploaded:** `.gitignore` deliberately
> excludes `reference_code.txt` (your original 38k-line Kivy app) and every
> `*.log`. Your reference code stays on your machine. Worth knowing, since
> the repo may be public.

---

# Part 2 — Deploy on Streamlit Cloud

## Step 4. Sign in

Go to **share.streamlit.io** and click *Continue with GitHub*, then authorise
Streamlit. If you made the repo **private**, grant repo access when asked —
otherwise your repo will not appear in the next step.

## Step 5. Create the app

Click **Create app**, then *Deploy a public app from GitHub*, and fill in:

| Field | Value |
|---|---|
| Repository | `YOUR-USERNAME/hk-aviation-briefing` |
| Branch | `main` |
| Main file path | `app.py` |
| App URL | choose a subdomain, e.g. `hk-aviation-briefing` |

**Main file path must be `app.py`** — that is the router that draws the NOTAM
and Weather tabs. Pointing it at `views/notam.py` would load a single page with
no tab bar and no shared styling.

## Step 5a. Set the Python version — do this BEFORE deploying

Still on the deploy screen, click **Advanced settings** and set
**Python version** to **3.11**.

This matters. Community Cloud currently defaults to a very new Python (3.13 or
3.14), and it does **not** read a `runtime.txt` file — that trick works on
Heroku and Render, but Streamlit ignores it. The UI dropdown is the only thing
that has any effect.

If you already deployed on the wrong version, you do not need to start over:
**Manage app** → **Settings** → **Python version** → 3.11 → save and reboot.

Click **Deploy**.

## Step 6. Wait for the build

First build takes 2–5 minutes. You will see it install the five packages from
`requirements.txt`, then the app appears.

Check these, in this order:

1. **NOTAM tab** — map loads with red/amber overlays over Hong Kong.
2. **Click through all four filters** — *All*, *Active Today*, *Next 2 Hours*,
   *Active Now*. The map must stay visible in each. This is exactly what the
   0-px iframe bug broke, so it is the one regression worth eyeballing on every
   deploy.
3. **Weather tab** — METAR, TAF and ATIS blocks populate, radar animates.

Your app is live at `https://<your-subdomain>.streamlit.app` and anyone with
the link can open it.

---

# Updating the app later

Streamlit Cloud watches the branch, so a push is a deploy:

```bash
git add -A
git commit -m "describe what changed"
git push
```

The app rebuilds in roughly 30 seconds. No button to press.

If a change does not seem to land, open the app menu (☰, top right) →
**Reboot app** to force a clean restart.

---

# If something goes wrong

**`StreamlitPageNotFoundError` on the `st.Page("views/notam.py", ...)` line.**
The `views/` folder did not reach GitHub. `st.Page()` resolves that path
relative to `app.py`'s own folder, so if `views/` is missing the app dies on
the very first page it tries to register. Open your repo on github.com — if
there is no `views/` folder next to `app.py`, that is the whole problem.

Some Git GUIs and drag-and-drop uploads skip subfolders. From the project
folder, check and fix:

```bash
python check_repo.py          # reports exactly what is missing
git add -A -f views/
git commit -m "add views package"
git push
```

**`error: src refspec main does not match any` when pushing.**
Your local branch is called something else (usually `master`). Run
`bash fix_branch.sh`, or just `git branch -M main`, then push again.

**Repo does not show up in the Streamlit dropdown.**
Private repo without permissions granted. share.streamlit.io → your avatar →
*Settings* → *Connections* → reconnect GitHub and allow repo access.

**`ModuleNotFoundError` in the logs.**
A package is missing from `requirements.txt`. Add it, commit, push — the
rebuild picks it up.

**Build fails on `lxml`, or the app runs on Python 3.13 / 3.14.**
Set the Python version **in the Streamlit UI** — Community Cloud does *not*
read a `runtime.txt` file, whatever other guides say. See Step 5a above.

**App loads but NOTAMs or weather are empty.**
The upstream feed is unreachable or has changed shape. Both pages surface the
error rather than failing silently. Click *Refresh now* in the sidebar first —
results are cached (4 h for NOTAMs, 15 min for weather), so you may be looking
at an empty cached result.

**"This app has gone to sleep."**
Free-tier apps sleep after ~7 days of no visitors. Anyone can wake it with the
button on screen; it takes about 30 seconds.

**Map is blank / has no height.**
Run `python test_map_render.py` locally. It checks the map's generated
JavaScript for the duplicate-declaration fault that caused exactly this, across
all four filters. `NOTAM_EXPLAINED.md` §7g has the full story.

---

# Two things worth doing before you share it widely

**Add the overlay icons.** `assets/icons/wind/` and `assets/icons/vis/` are
still empty, so the Weather map falls back to plain coloured markers. Drop PNGs
in with these exact stems and they are picked up automatically:

- `wind/` → `N NE E SE S SW W NW var calm`
- `vis/` → `good moderate poor very_poor unknown`

Commit and push as usual.

**Be clear about what this is.** The data comes from the Hong Kong AIS and HKO
public feeds, but this is a convenience viewer, not an official briefing tool.
If others will use it, the README should say plainly that flight planning must
be done against the official source. That matters more than anything technical
in this guide.
