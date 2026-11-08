#!/usr/bin/env bash
# Publish only code after creating an EMPTY PUBLIC GitHub repository.
set -euo pipefail
cd "$(dirname "$0")/.."
OWNER="${1:-subhb-hub}"
REPO="${2:-LocalDelta}"
command -v git >/dev/null || { echo "Install Git first" >&2; exit 1; }
command -v python3 >/dev/null || { echo "Install Python 3.10+ first" >&2; exit 1; }
if [ ! -d .git ]; then git init -b main; fi
git add -A
python3 scripts/check_public_tree.py
printf '\nStaged for PUBLIC upload:\n'
git diff --cached --name-only
printf '\nType PUBLISH to confirm: '
read -r ANSWER
[ "$ANSWER" = 'PUBLISH' ] || { echo 'Cancelled, nothing pushed.'; exit 1; }
[ "$(git branch --show-current)" = 'main' ] || { echo 'Expected main branch'; exit 1; }
REMOTE="https://github.com/$OWNER/$REPO.git"
EXISTING="$(git remote get-url origin 2>/dev/null || true)"
if [ -n "$EXISTING" ] && [ "$EXISTING" != "$REMOTE" ]; then
  echo "origin points to unexpected remote $EXISTING" >&2; exit 1
fi
[ -n "$EXISTING" ] || git remote add origin "$REMOTE"
if ! git diff --cached --quiet; then git commit -m 'feat: initial local-first AI incremental update engine'; fi
git push -u origin main
printf 'Published: https://github.com/%s/%s\n' "$OWNER" "$REPO"
