#!/usr/bin/env bash
# Pobiera zbiór treningowy organizatora do data/hacknation-2026 (katalog ignorowany przez git).
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=data/hacknation-2026
if [ -d "$DEST/.git" ]; then
    git -C "$DEST" pull --ff-only
else
    git clone --depth 1 https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026.git "$DEST"
fi
