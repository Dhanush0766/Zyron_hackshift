#!/usr/bin/env bash
# MedGate one-command start (Linux/macOS). First run creates a virtual environment.
set -e
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
echo "Dashboard: http://127.0.0.1:8000   (Ctrl+C to stop)"
exec .venv/bin/python -m uvicorn medgate.api:app --host 0.0.0.0 --port 8000
