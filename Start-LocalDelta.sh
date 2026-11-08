#!/bin/sh
# Portable GUI convenience launcher; no install or background service.
set -eu
cd "$(dirname "$0")"
if command -v python >/dev/null 2>&1; then
  exec python localdelta.py gui
elif command -v python3 >/dev/null 2>&1; then
  exec python3 localdelta.py gui
else
  printf 'LocalDelta needs Python 3.10+ in PATH.\n' >&2
  exit 1
fi
