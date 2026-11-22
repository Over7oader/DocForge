#!/usr/bin/env bash
# Jednorazowa instalacja na maszynie oceniającej (Ubuntu 24.04, Python systemowy 3.12).
# Projekt wymaga Pythona 3.13+, więc instalujemy go przez uv do katalogu projektu: wszystko,
# czego potrzebuje rozwiązanie (interpreter, zależności, modele OCR), zostaje w /opt/solution
# i działa też po restarcie oraz przy uruchomieniu komendy z RUN.md przez sudo.
set -euo pipefail
cd "$(dirname "$0")"

export UV_PYTHON_INSTALL_DIR="$PWD/.python"
export UV_CACHE_DIR="$PWD/.cache/uv"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$PWD/.tools" INSTALLER_NO_MODIFY_PATH=1 sh
    export PATH="$PWD/.tools:$PATH"
fi
uv python install 3.13
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
# Pobranie i sprawdzenie modeli OCR przed oceną (w czasie oceny nic nie jest pobierane).
.venv/bin/python -m scripts.prepare_ocr
mkdir -p input output
[ -f .env ] || echo "Brak .env: skopiuj .env.example do .env i uzupełnij klucz API (albo ustaw DOXFORGE_LLM=none: same reguły)."
