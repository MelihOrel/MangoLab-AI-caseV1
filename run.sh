#!/usr/bin/env bash
# Starts the service. It must listen on $PORT (default 8080) and read the
# upstream base URL from $FX_UPSTREAM_BASE — we point that at a fake upstream
# when we review your work, so nothing here may hardcode frankfurter.dev.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

# FX_UPSTREAM_BASE is not touched here: app/config.py reads it from the
# environment and falls back to the documented default. Nothing else in the
# codebase knows the host.

VENV=".venv"

# venv puts the interpreter in bin/ on Unix and Scripts/ on Windows, so this
# runs anywhere bash does.
venv_python() {
  if [ -x "$VENV/bin/python" ]; then
    printf '%s' "$VENV/bin/python"
  elif [ -x "$VENV/Scripts/python.exe" ]; then
    printf '%s' "$VENV/Scripts/python.exe"
  fi
}

create_venv() {
  # Candidates are probed by running one, not just by being on PATH: Windows
  # ships python.exe / python3.exe stubs that resolve fine and then refuse to
  # run. `py` is the Windows launcher, used when neither name works.
  for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c "import sys" >/dev/null 2>&1; then
      "$candidate" -m venv "$VENV"
      return 0
    fi
  done
  if command -v py >/dev/null 2>&1 && py -3 -c "import sys" >/dev/null 2>&1; then
    py -3 -m venv "$VENV"
    return 0
  fi
  echo "run.sh: no working Python on PATH (needs 3.10+)" >&2
  exit 1
}

if [ -z "$(venv_python)" ]; then
  echo "run.sh: creating $VENV" >&2
  create_venv
  "$(venv_python)" -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

exec "$(venv_python)" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
