#!/usr/bin/env bash
# Build na Render — wymaga Pythona 3.12 (wheel pydantic-core, bez Rust).
set -euo pipefail

PY_VER="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $(python --version) (${PY_VER})"

if [ "${PY_VER}" != "3.12" ]; then
  echo "BŁĄD: Render używa Python ${PY_VER}, a projekt wymaga 3.12."
  echo "W panelu Render → Environment ustaw: PYTHON_VERSION=3.12.8"
  echo "Następnie Manual Deploy."
  exit 1
fi

pip install --upgrade pip
pip install -r requirements.txt
