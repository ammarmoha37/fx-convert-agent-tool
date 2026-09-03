#!/usr/bin/env bash
# Runs pytest. Tests fake the upstream and must pass with no network.
# Reviewers set FX_UPSTREAM_BASE to a closed port; we never call that host.
set -euo pipefail

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
else
  PYTHON="python"
fi

# Closed port if they did not set one. Tests still use MockTransport.
export FX_UPSTREAM_BASE="${FX_UPSTREAM_BASE:-http://127.0.0.1:1}"

exec "$PYTHON" -m pytest tests/ -q
