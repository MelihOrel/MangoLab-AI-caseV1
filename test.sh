#!/usr/bin/env bash
# Runs your tests. They must pass with no network at all: we run this with
# FX_UPSTREAM_BASE pointing at a closed port.
set -euo pipefail
cd "$(dirname "$0")"

# Every upstream call in the suite is served by httpx.MockTransport, so no
# socket is opened and the value of FX_UPSTREAM_BASE is irrelevant. It is set
# here to a closed port so that a regression which reaches the network fails
# loudly instead of quietly talking to the real API.
export FX_UPSTREAM_BASE="${FX_UPSTREAM_BASE:-http://127.0.0.1:1}"

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
  echo "test.sh: no working Python on PATH (needs 3.10+)" >&2
  exit 1
}

if [ -z "$(venv_python)" ]; then
  echo "test.sh: creating $VENV" >&2
  create_venv
  "$(venv_python)" -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

exec "$(venv_python)" -m pytest "$@"
