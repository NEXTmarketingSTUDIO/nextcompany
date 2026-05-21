#!/usr/bin/env bash
# Produkcja (Render / VPS): bez --reload, port z $PORT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec python -m uvicorn main:app --host "${HOST}" --port "${PORT}"
