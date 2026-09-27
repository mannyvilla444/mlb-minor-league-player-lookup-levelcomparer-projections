#!/usr/bin/env bash
# Level Stats - double-click launcher for macOS / Linux.
#
# On macOS, double-clicking a .command file opens it in Terminal and runs it.
# If macOS refuses ("cannot be opened because it is from an unidentified
# developer"), right-click the file and choose Open, once.
#
# If double-clicking does nothing, the executable bit was lost in transit:
#     chmod +x "start-level-stats.command"

set -uo pipefail
cd "$(dirname "$0")" || exit 1

VENV_PY=".venv/bin/python"

echo
echo "  Level Stats"
echo "  Folder: $(pwd)"
echo

fail() {
  echo
  echo "  $1"
  echo
  read -r -p "  Press Return to close. " _
  exit 1
}

if [ ! -f "run.py" ]; then
  fail "ERROR: run.py is not next to this file. Look for a nested project folder."
fi

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,14) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if [ ! -x "$VENV_PY" ]; then
  echo "  First run - setting up. This takes a minute or two."
  echo
  BASE_PY="$(find_python)" || fail "ERROR: need Python 3.11-3.13. Install it from python.org."
  echo "  Using Python: $(command -v "$BASE_PY")"
  echo "  Creating a private environment in .venv ..."
  "$BASE_PY" -m venv .venv || fail "ERROR: could not create .venv in this folder."
fi

# Cheap import probe: catches a half-built venv and any newly added dependency.
if ! "$VENV_PY" -c "import fastapi, uvicorn, jinja2, statsapi, pandas, multipart" >/dev/null 2>&1; then
  echo "  Installing dependencies..."
  echo
  "$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1
  "$VENV_PY" -m pip install -r requirements.txt || fail "ERROR: dependency install failed - see the pip output above."
  echo
  echo "  Setup complete."
  echo
fi

"$VENV_PY" run.py "$@"
status=$?

echo
if [ "$status" -ne 0 ]; then
  echo "  The app stopped with an error (exit $status). The message above says why."
  read -r -p "  Press Return to close. " _
else
  echo "  Level Stats has stopped. You can close this window."
fi
exit "$status"
