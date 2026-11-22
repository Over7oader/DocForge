"""Centralne ustawienia projektu (stałe; zmiennych środowiskowych jeszcze nie ma)."""

import os
from pathlib import Path

from pydantic import JsonValue

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("DOXFORGE_CACHE_DIR", str(PROJECT_ROOT / ".cache")))
CONFIG_DIR = PROJECT_ROOT / "config"
DOC_TYPES_DIR = CONFIG_DIR / "doc_types"
EVALUATION_CONFIG = CONFIG_DIR / "evaluation.yaml"

DEFAULT_INPUT_DIR = PROJECT_ROOT / "input"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

# Zbiór organizatora pobierany przez scripts/fetch_dataset.sh.
DATASET_DIR = PROJECT_ROOT / "data" / "hacknation-2026" / "dane"

# Strona z mniejszą liczbą znaków (bez spacji) w warstwie tekstowej jest traktowana jak skan.
MIN_TEXT_LAYER_CHARS = 50
# OCR tylko pierwszych stron bez warstwy tekstu: dane są „z reguły na pierwszych trzech stronach”
# (opis zadania), a OCR kosztuje 5-12 s na stronę na CPU (limit: 150 PDF w 45 min).
OCR_MAX_PAGES = 4
OCR_DPI = 200  # skany w zbiorze mają 200 dpi
OCR_MIN_BOX_SCORE = 0.5
OCR_CACHE_DIR = CACHE_DIR / "ocr"
OCR_CACHE_VERSION = "rapidocr-3.10-v6-small-200dpi-1"  # zmień przy zmianie ustawień OCR
# Czas strony zmierzony na 2 vCPU (jak maszyna oceniająca), 12 stron skanów train, bez cache:
# ensemble3 16,4 s, sam PP-OCRv6 6,0 s. Wartości startowe tempa OCR w trybie z budżetem.
OCR_ENSEMBLE_PAGE_S = 16.4
OCR_SINGLE_PAGE_S = 6.0
# Część budżetu zostawiana na etap LLM, który zaczyna się po lokalnym przetworzeniu wszystkich PDF.
# 138 zapytań przez OpenRouter trwało ok. 3 min; zapas na wolniejsze odpowiedzi i ponowienia.
LLM_PHASE_RESERVE_S = 480

# LLM (dostawca i model z .env: DOXFORGE_LLM, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY).
LLM_TIMEOUT_S = 90
LLM_MAX_RETRIES = 7
LLM_RETRY_BASE_DELAY_S = 2.0  # kolejne próby po 2, 4, 8, 16, 30, 30, 30 s
LLM_MAX_RETRY_DELAY_S = 30
# Minimalny odstęp między zapytaniami (wszystkie wątki razem). Przy kluczu użytym w testach
# 4 równoległe zapytania bez odstępu kończyły się masowo statusem 429. 150 dokumentów to przy tym
# odstępie ok. 3 min samego wysyłania, w limicie 45 min.
LLM_MIN_REQUEST_INTERVAL_S = 1.2
LLM_CACHE_DIR = CACHE_DIR / "llm"
# Temperatura próbek przy kilku odpowiedziach jednego modelu w ensemble (przy 1 próbce zawsze 0):
# na tyle niska, żeby odpowiedzi były sensowne, na tyle wysoka, żeby błędy nie powtarzały się 1:1.
LLM_ENSEMBLE_TEMPERATURE = 0.5
ENV_FILE = PROJECT_ROOT / ".env"

# Dokumenty przetwarzane równolegle: czas zajmują głównie zapytania do LLM (I/O), OCR jest szeregowany.
PIPELINE_WORKERS = 4

# Wynik dla dokumentu spoza skonfigurowanych klas. README organizatora mówi o „pustym pliku .json”;
# UZP na Discordzie (13:50, 22:32) potwierdził obiekt `{}`.
EMPTY_RESULT: JsonValue = {}
