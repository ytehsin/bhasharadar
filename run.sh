#!/usr/bin/env bash
# Start BhashaRadar on http://127.0.0.1:8000 (macOS / Linux)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
  ./.venv/bin/pip install "scikit-learn>=1.4" || echo "scikit-learn unavailable - using built-in matcher"
fi
PORT="${BR_PORT:-8000}"
echo "BhashaRadar → http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
