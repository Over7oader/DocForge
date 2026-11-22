"""Odczyt treści PDF: warstwa tekstowa, a dla stron bez niej (skany) OCR."""

import hashlib
import json
import logging
import re
import tempfile
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pypdfium2 as pdfium

from doxforge import ocr, settings
from doxforge.text import collapse_spaced_letters

logger = logging.getLogger(__name__)


class TextSource(StrEnum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    SKIPPED = "skipped"  # strona bez warstwy tekstu poza limitem OCR


@dataclass(frozen=True)
class Page:
    text: str
    source: TextSource


@dataclass(frozen=True)
class Document:
    pages: list[Page]

    @property
    def digital_text(self) -> str:
        return "\n".join(page.text for page in self.pages if page.source is TextSource.TEXT_LAYER)

    @property
    def text(self) -> str:
        """Cała treść; strony rozdziela znak nowej strony."""
        return "\n\f\n".join(page.text for page in self.pages)


# PDFium nie jest bezpieczne wątkowo (dokumentacja pypdfium2): każde użycie jest szeregowane.
_PDFIUM_LOCK = threading.Lock()


def pdf_page_texts(pdf: Path) -> list[str]:
    """Tekst warstwy tekstowej każdej strony; strona bez warstwy (np. skan) daje pusty string."""
    texts = []
    with _PDFIUM_LOCK, pdfium.PdfDocument(pdf) as document:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            # pdfium zwraca końce linii "\r\n"; ujednolicamy, żeby "$" we wzorcach działał.
            texts.append(textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n"))
            textpage.close()
            page.close()
    return texts


def _has_text_layer(text: str) -> bool:
    return sum(not char.isspace() for char in text) >= settings.MIN_TEXT_LAYER_CHARS


def _ocr_pages(pdf: Path, indexes: list[int], pace: ocr.OCRPace | None) -> dict[int, ocr.PageOCR]:
    images = {}
    with _PDFIUM_LOCK, pdfium.PdfDocument(pdf) as document:
        for index in indexes:
            page = document[index]
            bitmap = page.render(scale=settings.OCR_DPI / 72)
            images[index] = bitmap.to_numpy().copy()  # kopia: pamięć bitmapy zwalnia close()
            bitmap.close()
            page.close()
    return {index: ocr.recognize_page(image, pace) for index, image in images.items()}


def _cached_ocr(pdf: Path, indexes: list[int], pace: ocr.OCRPace | None) -> dict[int, str]:
    """OCR z pamięcią podręczną na dysku (klucz: treść pliku + wersja ustawień OCR)."""
    key = hashlib.sha256(pdf.read_bytes() + ocr.cache_version().encode()).hexdigest()
    cache_file = settings.OCR_CACHE_DIR / f"{key}.json"
    cached: dict[str, str] = (
        json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.is_file() else {}
    )
    missing = [i for i in indexes if str(i) not in cached]
    if missing:
        logger.info("OCR of %d page(s) of %s", len(missing), pdf.name)
        recognized = _ocr_pages(pdf, missing, pace)
        cached |= {
            str(i): page.text for i, page in recognized.items() if not page.failed and not page.degraded
        }
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=cache_file.parent, delete=False, mode="w", encoding="utf-8", suffix=".tmp"
        ) as staged:
            json.dump(cached, staged, ensure_ascii=False)
        Path(staged.name).replace(cache_file)
        # Partial ensemble is useful now, but must not masquerade as a successful cached ensemble.
        return {i: recognized[i].text if i in recognized else cached[str(i)] for i in indexes}
    return {i: cached[str(i)] for i in indexes}


# Stopka z numerem strony („Strona 1 z 11”, „- 2 -”, „3/10”) przerywa treść między stronami.
_PAGE_NUMBER = re.compile(
    r"^\s*(?:strona\s+\d+\s*(?:z|/)\s*\d+|-?\s*\d{1,3}\s*-?|\d{1,3}\s*/\s*\d{1,3})\s*$", re.I
)


def _clean(text: str) -> str:
    """Usuwa stopki z numerem strony i scala tytuły pisane rozstrzelonymi literami ('O D W O Ł A N I E')."""
    return "\n".join(
        collapse_spaced_letters(line) for line in text.split("\n") if not _PAGE_NUMBER.match(line)
    )


# Znaki spoza polskiego alfabetu, które OCR zwraca zamiast polskich (GDAÑSK, Lubl\u0131n, prowadzonègo).
_OCR_CHARS = str.maketrans({"Ñ": "Ń", "ñ": "ń", "\u0131": "i", "è": "e", "È": "E"})
# Forma prawna z cyframi zamiast liter: „sp. z 0.0.”, „Sp. z o.0.”
_OCR_LLC = re.compile(r"\b([zZ])\s?[0oO]\.\s?[0oO]\.")


# Account number (IBAN/NRB shape: country code, 2 check digits, 6-7 groups of 4) in which OCR read
# a digit as a look-alike letter („PLO2 9855 …”) or put a stray dot after the country code
# („PL.64 6217 …”). Only letters O/o, I/l in digit positions and that punctuation change.
_ACCOUNT_LOOKALIKE = re.compile(
    r"(?<!\w)((?:[A-Z]{2})?)(?:[.,:;]\s?)?([0-9OoIl]{2}(?:\s?[0-9OoIl]{4}){6,7})(?!\w)"
)
_DIGIT_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})
_MIN_ACCOUNT_DIGITS = 22  # przynajmniej tyle prawdziwych cyfr: to na pewno numer, nie wyraz


def _repair_account(match: re.Match[str]) -> str:
    body = match[2]
    if sum(char.isdigit() for char in body) < _MIN_ACCOUNT_DIGITS:
        return match[0]
    return match[1] + body.translate(_DIGIT_LOOKALIKES)


# OCR myli wielkość liter o tym samym kształcie: jednoliterowe „z”/„w” w środku zdania
# („wraz Z serwisem”, „sp. Z o.o.”). Wielka litera między małymi nie zaczyna tam zdania.
_LOWERCASE_PREPOSITION = re.compile(
    r"(?<=[a-ząćęłńóśźż,] )([ZW])(?= [a-ząćęłńóśźż])|(?<=\b[sS]p\. )(Z)(?= o\.)"
)


def _clean_ocr(text: str) -> str:
    text = _ACCOUNT_LOOKALIKE.sub(_repair_account, text.translate(_OCR_CHARS))
    text = _OCR_LLC.sub(lambda m: f"{m[1]} o.o.", text)  # najpierw „Z 0.0.” -> „Z o.o.”
    return _LOWERCASE_PREPOSITION.sub(lambda m: m[0].lower(), text)


def ocr_page_count(pdf: Path) -> int:
    """Pages `load_document` would OCR (for time planning); an unreadable PDF counts as none."""
    try:
        layer = [_clean(text) for text in pdf_page_texts(pdf)]
    except pdfium.PdfiumError:
        return 0
    return min(settings.OCR_MAX_PAGES, sum(not _has_text_layer(text) for text in layer))


def load_document(pdf: Path, pace: ocr.OCRPace | None = None) -> Document:
    layer = [_clean(text) for text in pdf_page_texts(pdf)]
    to_ocr = [i for i, text in enumerate(layer) if not _has_text_layer(text)][: settings.OCR_MAX_PAGES]
    ocr_texts = _cached_ocr(pdf, to_ocr, pace) if to_ocr else {}
    pages = []
    for index, text in enumerate(layer):
        if _has_text_layer(text):
            pages.append(Page(text, TextSource.TEXT_LAYER))
        elif index in ocr_texts:
            pages.append(Page(_clean(_clean_ocr(ocr_texts[index])), TextSource.OCR))
        else:
            pages.append(Page("", TextSource.SKIPPED))
    return Document(pages)
