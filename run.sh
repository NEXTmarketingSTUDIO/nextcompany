#!/usr/bin/env bash
# Uruchom backend NEXTcompany pod właściwym Pythonem (.venv).
# Omija shimy pyenv, które mogą przechwycić binarkę `uvicorn`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"

if [ ! -x "${VENV_PY}" ]; then
  echo "Brak ${VENV_PY}. Utwórz najpierw venv:"
  echo "  python3 -m venv ${REPO_ROOT}/.venv"
  echo "  ${REPO_ROOT}/.venv/bin/pip install -r ${SCRIPT_DIR}/requirements.txt"
  exit 1
fi

cd "${SCRIPT_DIR}"
exec "${VENV_PY}" -m uvicorn main:app --reload --port 8000 "$@"
