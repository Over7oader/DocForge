"""Typy wartości: jak znaleźć kandydata w tekście i jak sprowadzić go do formatu wyniku.

Wzorce działają na tekście złożonym (`text.fold`: małe litery, bez polskich znaków).
Normalizator dostaje fragment oryginału i zwraca wartość w formacie organizatora albo `None`,
gdy fragment nie jest poprawną wartością danego typu.
"""

import datetime
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from doxforge.text import fold, squash_whitespace

type ValueTypeName = Literal[
    "nip",
    "regon",
    "krs",
    "pesel",
    "iban",
    "amount",
    "date",
    "email",
    "bzp",
    "ted",
    "token",
    "text",
    "name",
    "organization",
]

MONTHS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "wrzesnia",
    "pazdziernika",
    "listopada",
    "grudnia",
)

# Tytuły zawodowe i grzecznościowe przy imieniu i nazwisku (bez polskich znaków, więc pasują
# zarówno do oryginału, jak i do tekstu złożonego).
PERSON_TITLES = (
    r"(?:apl\.\s?(?:adw|radc)\.|adw\.|adwokat\w*|r\.\s?pr\.|radc\w*\s+prawn\w*|mec\.|dr|prof\.|pan[ia]?)"
)


@dataclass(frozen=True)
class ValueType:
    pattern: re.Pattern[str] | None  # None: typ tekstowy, wartością jest cały wskazany fragment
    normalize: Callable[[str], JsonValue]


def _digits(expected: int) -> Callable[[str], JsonValue]:
    def normalize(raw: str) -> JsonValue:
        digits = re.sub(r"\D", "", raw)
        return digits if len(digits) == expected else None

    return normalize


# Rozpoznawanie rachunków bez poprawiania ich sum kontrolnych.
_IBAN_LENGTHS = {"pl": 28, "de": 22, "gb": 22, "fr": 27, "nl": 18, "be": 16, "ch": 21}
_IBAN_BODY_ALPHABETS = {"pl": "0-9", "de": "0-9", "be": "0-9"}
_IBAN_SEARCH = re.compile(
    r"(?<![\w])(?:"
    + "|".join(
        rf"{country}\s?\d{{2}}(?:\s?[{_IBAN_BODY_ALPHABETS.get(country, 'a-z0-9')}]){{{length - 4}}}"
        for country, length in _IBAN_LENGTHS.items()
    )
    + r"|[a-z]{2}\d{2}[a-z0-9]{11,30}|\d{2}(?:\s?\d{4}){6})(?![\w])"
)


def _iban(raw: str) -> JsonValue:
    compact = re.sub(r"\s", "", raw).upper()
    if re.fullmatch(r"\d{26}", compact):
        return compact
    if compact[:2] in {"PL", "DE", "BE"} and not compact[2:].isdigit():
        return None
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        required_length = _IBAN_LENGTHS.get(compact[:2].lower())
        if required_length is None or len(compact) == required_length:
            return compact
    return None


def _regon(raw: str) -> JsonValue:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) in {9, 14} else None


def _amount(raw: str) -> JsonValue:
    number = re.sub(r"[\s\u00a0]", "", re.sub(r"(?i)(pln|zł|zl)", "", raw))
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", number):
        number = number.replace(".", "")
    try:
        return float(number)
    except ValueError:
        return None


_DATE_NUMERIC = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})")
_DATE_ISO = re.compile(r"(\d{4})[.\-](\d{2})[.\-](\d{2})")
_DATE_WORDS = re.compile(rf"(\d{{1,2}})\s+({'|'.join(MONTHS)})\s+(\d{{4}})")


def _date(raw: str) -> JsonValue:
    folded = fold(raw)
    try:
        if match := _DATE_ISO.search(folded):
            year, month, day = (int(g) for g in match.groups())
        elif match := _DATE_NUMERIC.search(folded):
            day, month, year = (int(g) for g in match.groups())
        elif match := _DATE_WORDS.search(folded):
            day, month, year = int(match[1]), MONTHS.index(match[2]) + 1, int(match[3])
        else:
            return None
        return datetime.date(year, month, day).isoformat()
    except ValueError:  # np. 31.02 albo miesiąc 14
        return None


def _bzp(raw: str) -> JsonValue:
    return re.sub(r"\s", "", raw).upper()


_TED_OJ = re.compile(r"(\d{4})\s*/\s*s\s*\d{1,3}\s*[-\u2013]\s*(\d{6,9})")
_TED_ID = re.compile(r"(\d{6,9})\s*-\s*(\d{4})")


def _ted(raw: str) -> JsonValue:
    folded = fold(raw)
    if match := _TED_OJ.search(folded):
        return f"{match[2]}-{match[1]}"
    if match := _TED_ID.search(folded):
        return f"{match[1]}-{match[2]}"
    return None


_QUOTED = re.compile(r"^[„“\"](.+?)[”“\"][.,;]?$")


def _text(raw: str) -> JsonValue:
    value = squash_whitespace(raw)
    if match := _QUOTED.match(value):  # cała wartość w cudzysłowie, np. tytuł przelewu w zdaniu
        value = match[1]
    return value.strip(" \t,;:\u2013-\"'„”“") or None


_LEGAL_FORM = re.compile(
    r"(?:sp\.\s*(?:z\s*o\.\s*o\.|[kjp]\.)|s\.\s*a\.|"
    r"spolka\s+(?:z ograniczona odpowiedzialnoscia|akcyjna|komandytowa|jawna))$"
)
_ADDRESS_START = re.compile(r"\s+z\s+siedziba\s+(?:w|we)\s+")


# A street address or postal code is never part of an organization's name. When a template puts
# the name and address on one line ("Firma sp. z o.o. ul. Polna 1, 00-001 Miasto"), cut it off.
_STREET_OR_POSTCODE = re.compile(
    r"[\s,]+(?:(?:ul|al|pl|os)\.\s*[^\d,;]{2,60}?\s\d+[a-z]?(?:/\d+[a-z]?)?\b|\d{2}-\d{3}\s)"
)


# A one-word remainder ("ul. Polna 1" alone, "Firma ul. …") is too little to call it a name.
_MIN_NAME_WORDS_BEFORE_ADDRESS = 2


def _organization(raw: str) -> JsonValue:
    value = _text(raw)
    if not isinstance(value, str):
        return None
    street = _STREET_OR_POSTCODE.search(fold(value))
    if street and len(value[: street.start()].split()) >= _MIN_NAME_WORDS_BEFORE_ADDRESS:
        value = value[: street.start()].rstrip(" ,;")
    for match in _ADDRESS_START.finditer(fold(value)):
        prefix = value[: match.start()].rstrip()
        if _LEGAL_FORM.search(fold(prefix)):
            return prefix
    return value


def _token(raw: str) -> JsonValue:
    return raw.strip(".,;:") or None


_TITLE_PREFIX = re.compile(rf"^(?:{PERSON_TITLES}\s+)+", re.IGNORECASE)
_TITLE_SUFFIX = re.compile(rf"(?:,?\s+{PERSON_TITLES})+$", re.IGNORECASE)


def _name(raw: str) -> JsonValue:
    """Imię i nazwisko bez tytułów przed i po: 'r. pr. Jan Nowak', 'Jan Nowak, adw.' -> 'Jan Nowak'."""
    value = _text(raw)
    if not isinstance(value, str):
        return None
    return _TITLE_SUFFIX.sub("", _TITLE_PREFIX.sub("", value)).strip(" ,") or None


VALUE_TYPES: dict[ValueTypeName, ValueType] = {
    "nip": ValueType(
        re.compile(r"(?<![\d])(?:pl\s?)?\d{3}[- ]?\d{3}[- ]?\d{2}[- ]?\d{2}(?![\d])"), _digits(10)
    ),
    "regon": ValueType(re.compile(r"(?<!\d)(?:\d{14}|\d{9})(?!\d)"), _regon),
    "krs": ValueType(re.compile(r"(?<!\d)\d{10}(?!\d)"), _digits(10)),
    "pesel": ValueType(re.compile(r"(?<!\d)\d{11}(?!\d)"), _digits(11)),
    "iban": ValueType(_IBAN_SEARCH, _iban),
    "amount": ValueType(
        re.compile(
            r"(?<![\d,.])(?:\d{1,3}(?:[ \u00a0.]\d{3})+(?:,\d{2})?|\d+[.,]\d{2}|\d+(?=\s*(?:pln|zl)))(?![\d])"
        ),
        _amount,
    ),
    "date": ValueType(
        re.compile(
            rf"(?<!\d)(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[.\-/]\d{{1,2}}[.\-/]\d{{4}}|\d{{4}}\.\d{{2}}\.\d{{2}}"
            rf"|\d{{1,2}}\s+(?:{'|'.join(MONTHS)})\s+\d{{4}})(?!\d)"
        ),
        _date,
    ),
    "email": ValueType(re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), _text),
    "bzp": ValueType(re.compile(r"\d{4}\s*/\s*bzp\s*\d{8}(?:\s*/\s*\d{2})?"), _bzp),
    "ted": ValueType(
        re.compile(r"\d{4}\s*/\s*s\s*\d{1,3}\s*[-\u2013]\s*\d{6,9}|(?<!\d)\d{6,9}\s*-\s*\d{4}(?!\d)"), _ted
    ),
    # Identyfikator z co najmniej jedną cyfrą: numer postępowania, numer referencyjny transakcji.
    "token": ValueType(re.compile(r"(?<![\w/.\-])[a-z0-9][\w/.\-]*\d[\w/.\-]*"), _token),
    "text": ValueType(None, _text),
    "name": ValueType(None, _name),
    "organization": ValueType(None, _organization),
}

# Ten sam rejestr z kluczem `str`, do wyszukiwania po nazwie z konfiguracji (np. `x-type` w schemacie).
VALUE_TYPES_BY_NAME: Mapping[str, ValueType] = {
    str(name): value_type for name, value_type in VALUE_TYPES.items()
}
