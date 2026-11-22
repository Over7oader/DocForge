"""Pomocnicze operacje na tekście."""

import re
import unicodedata

_SPECIAL = str.maketrans({"ł": "l", "Ł": "L"})
_SPACED_LETTERS = re.compile(r"^\s*(?:\w\s){3,}\w\s*$")


def fold(text: str) -> str:
    """Małe litery bez polskich znaków, znak po znaku.

    Długość i pozycje znaków są zachowane, więc indeks dopasowania w tekście złożonym wskazuje
    to samo miejsce w oryginale. Dzięki temu wzorce piszemy bez diakrytyków, a OCR, który
    gubi ogonki (`ą`->`a`), nadal pasuje.
    """
    folded = []
    for char in text.translate(_SPECIAL):
        base = unicodedata.normalize("NFD", char)[0].lower()
        folded.append(base if len(base) == 1 else char)
    return "".join(folded)


def collapse_spaced_letters(line: str) -> str:
    """'P E Ł N O M O C N I C T W O' -> 'PEŁNOMOCNICTWO' (tytuły pisane rozstrzelonymi literami)."""
    return re.sub(r"\s+", "", line) if _SPACED_LETTERS.match(line) else line


def squash_whitespace(text: str) -> str:
    return " ".join(text.split())
