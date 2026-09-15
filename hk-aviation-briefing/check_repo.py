"""Verify a deployed checkout has the files st.Page() needs.

StreamlitPageNotFoundError from app.py means Streamlit resolved
"views/notam.py" relative to app.py's own folder and found nothing there.
On Streamlit Cloud that nearly always means views/ was never pushed to GitHub.

Run from the project folder:   python check_repo.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REQUIRED = [
    "app.py",
    "views/notam.py",
    "views/weather.py",
    "notam_core.py",
    "weather_core.py",
    "requirements.txt",
]


def tracked_by_git() -> set[str] | None:
    """Files git will actually push.  None if this is not a git checkout."""
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, check=True).stdout
    except Exception:
        return None
    return {line.strip() for line in out.splitlines() if line.strip()}


def main() -> int:
    here = Path.cwd()
    print(f"checking: {here}\n")

    tracked = tracked_by_git()
    problems = 0

    print("file                     on disk   in git")
    print("-" * 44)
    for rel in REQUIRED:
        on_disk = (here / rel).is_file()
        in_git = "n/a" if tracked is None else ("yes" if rel in tracked else "NO")
        if not on_disk or in_git == "NO":
            problems += 1
        print(f"{rel:<24} {'yes' if on_disk else 'NO ':<9} {in_git}")

    # The specific failure mode behind StreamlitPageNotFoundError.
    print()
    views = here / "views"
    if not views.is_dir():
        print("x  views/ does not exist here at all.")
        problems += 1
    elif tracked is not None:
        tracked_views = sorted(f for f in tracked if f.startswith("views/"))
        if not tracked_views:
            print("x  views/ exists on disk but git is not tracking ANY file in it.")
            print("   This is what makes st.Page() raise StreamlitPageNotFoundError")
            print("   after deploy: the folder never reached GitHub.")
            problems += 1
        else:
            print(f"ok git tracks {len(tracked_views)} file(s) in views/:")
            for f in tracked_views:
                print(f"     {f}")

    if tracked is not None:
        ignored = subprocess.run(["git", "check-ignore", "-v", "views/notam.py"],
                                 capture_output=True, text=True).stdout.strip()
        if ignored:
            print(f"\nx  .gitignore is excluding views/notam.py:\n   {ignored}")
            problems += 1

    print()
    if problems:
        print(f"{problems} problem(s) found.  Fix with:")
        print("    git add -A -f views/")
        print('    git commit -m "add views package"')
        print("    git push")
        return 1

    print("ALL GOOD - every file st.Page() needs is present and tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
