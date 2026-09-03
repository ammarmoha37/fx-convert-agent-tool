#!/usr/bin/env bash
# Starts the convert service on $PORT (default 8080).
# FX_UPSTREAM_BASE is read inside app.py — this script never names the host.
set -euo pipefail

# Run from this file's folder so "app:app" resolves no matter where we were called from.
cd "$(dirname "$0")"

PORT="${PORT:-8080}"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
else
  PYTHON="python"
fi

# exec replaces this shell with uvicorn. --host 0.0.0.0 accepts local and review traffic.
exec "$PYTHON" -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
