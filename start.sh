#!/usr/bin/env bash
# Przetwarza ./input -> ./output. Wymaga: pip install -r requirements.txt (Python 3.13+).
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python
exec "$PYTHON" -m doxforge run --input input --output output --budget-seconds 2640 "$@"
