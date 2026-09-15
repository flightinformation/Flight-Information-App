#!/usr/bin/env bash
# Diagnose and fix "src refspec main does not match any" before pushing.
#
# Run from inside the project folder (the one containing app.py):
#     bash fix_branch.sh
#
# It only touches your LOCAL branch name and remote URL.  It never pushes and
# never deletes anything.

set -u

echo "== checking =="

if [ ! -f app.py ]; then
  echo "x  No app.py here, so this is not the project folder."
  echo "   You are probably one level too high.  Try:"
  echo "       cd hk-aviation-briefing"
  echo "   then run this script again."
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "x  This folder has no .git, so the repository was lost when unzipping."
  echo "   (Some unzip tools silently skip hidden folders.)"
  echo "   Recreate it with:"
  echo "       git init"
  echo "       git add -A"
  echo "       git commit -m \"HK Aviation Briefing\""
  echo "       git branch -M main"
  exit 1
fi

if ! git rev-parse HEAD >/dev/null 2>&1; then
  echo "!  Repository exists but has no commits yet.  Creating one."
  git add -A
  git commit -q -m "HK Aviation Briefing" || true
fi

branch=$(git branch --show-current)
echo "   current branch : ${branch:-<detached>}"
echo "   commits        : $(git rev-list --count HEAD 2>/dev/null || echo 0)"

if [ "$branch" = "main" ]; then
  echo "ok branch is already 'main' - nothing to rename."
else
  echo "!  branch is '$branch', but the deploy steps push 'main'."
  git branch -M main
  echo "ok renamed '$branch' -> 'main'"
fi

echo
echo "== remote =="
if git remote get-url origin >/dev/null 2>&1; then
  echo "   origin is already set to:"
  echo "       $(git remote get-url origin)"
  echo "   If that is wrong, overwrite it (do NOT use 'git remote add' again):"
  echo "       git remote set-url origin https://github.com/YOUR-USERNAME/hk-aviation-briefing.git"
else
  echo "   No origin yet.  Add it:"
  echo "       git remote add origin https://github.com/YOUR-USERNAME/hk-aviation-briefing.git"
fi

echo
echo "== then push =="
echo "       git push -u origin main"
echo
echo "Password prompt needs a token, not your GitHub password:"
echo "github.com/settings/tokens -> Generate new token (classic) -> tick 'repo'"
