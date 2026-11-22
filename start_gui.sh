#!/usr/bin/env bash
# Webowy podgląd ekstrakcji (Streamlit), dostępny z zewnątrz na porcie 8080 (PORT nadpisuje).
# Na VM uruchamiany z osobnej kopii /opt/solution/gui: typy rejestrowane w GUI nie zmieniają
# konfiguracji używanej przez ./start.sh przy ocenie.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
[ -x ../.venv/bin/python ] && PYTHON=../.venv/bin/python
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python
exec "$PYTHON" -m streamlit run app.py --server.headless true --server.address 0.0.0.0 \
    --server.port "${PORT:-8080}" --browser.gatherUsageStats false
