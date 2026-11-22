"""Rodzaje reguł ekstrakcji używane w sekcji `extraction` pliku klasy (config/doc_types/*.yaml).

Każda reguła ma pole `kind` i działa na tekście dokumentu w zakresie (`scope`): nazwanej sekcji
albo całym tekście. Wzorce (regex) piszemy dla tekstu złożonego: małe litery, bez polskich znaków
(`doxforge.text.fold`). Reguła zwraca wartość, listę wartości (pole na liście, np. pełnomocnicy)
albo `None`, gdy nie ma pewnego wyniku: przy F1 brak wartości kosztuje mniej niż zła wartość.
"""

import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from doxforge.extraction.values import PERSON_TITLES, VALUE_TYPES, ValueTypeName
from doxforge.text import fold

FLAGS = re.MULTILINE
_MIN_TABLE_COLUMNS = 2


def compile_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, FLAGS)


class SectionSpec(BaseModel):
    """Nazwany fragment tekstu: od pierwszej pasującej alternatywy `start` do najbliższego `end`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: list[str] = Field(min_length=1)  # alternatywy w kolejności pierwszeństwa
    end: list[str] = []
    include_start: bool = False  # czy dopasowanie `start` należy do sekcji
    max_chars: int | None = None
    # priority: pierwsza alternatywa `start`, która występuje; earliest: alternatywa najwcześniej w tekście
    start_pick: Literal["priority", "earliest"] = "priority"


@dataclass
class Context:
    """Tekst dokumentu, sekcje i wartości pól policzonych wcześniej."""

    text: str
    sections: dict[str, SectionSpec]
    vocabularies: dict[str, dict[int, str]]
    values: dict[str, JsonValue] = field(default_factory=dict)
    selected_entities: dict[str, str] = field(default_factory=dict)

    text_labels: dict[str, list[str]] = field(default_factory=dict)

    @cached_property
    def folded(self) -> str:
        return fold(self.text)

    @cached_property
    def flat(self) -> str:
        """Tekst złożony z końcami linii i stron jako spacjami (ta sama długość i pozycje).

        Dla reguł słów kluczowych układ linii nie ma znaczenia: inny szablon złamie frazę
        („Specyfikację Warunków” / „Zamówienia”) w innym miejscu.
        """
        return self.folded.replace("\n", " ").replace("\f", " ")

    @cached_property
    def _section_spans(self) -> dict[str, tuple[int, int] | None]:
        return {name: self._find_section(spec) for name, spec in self.sections.items()}

    def _find_section(self, spec: SectionSpec) -> tuple[int, int] | None:
        matches = [m for pattern in spec.start if (m := compile_pattern(pattern).search(self.folded))]
        if not matches:
            return None
        match = min(matches, key=lambda m: m.start()) if spec.start_pick == "earliest" else matches[0]
        start = match.start() if spec.include_start else match.end()
        end = len(self.text)
        for pattern in spec.end:
            if stop := compile_pattern(pattern).search(self.folded, match.end()):
                end = min(end, stop.start())
        if spec.max_chars is not None:
            end = min(end, start + spec.max_chars)
        return start, end

    def span(self, scope: str | None) -> tuple[int, int] | None:
        """Zakres (start, koniec) w tekście; `None`, gdy sekcji nie ma w dokumencie."""
        if scope is None:
            return 0, len(self.text)
        return self._section_spans[scope]


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str | None = None  # nazwa sekcji; brak = cały dokument
    select_entity: bool = False  # wartość z tej reguły wskazuje wymaganą rolę na liście


def _distinct(values: list[JsonValue]) -> list[JsonValue]:
    unique: list[JsonValue] = []
    for value in values:
        if value is not None and value not in unique:
            unique.append(value)
    return unique


def _find_typed(ctx: Context, type_name: ValueTypeName, start: int, end: int) -> JsonValue:
    """Pierwsza poprawna wartość danego typu w zakresie."""
    value_type = VALUE_TYPES[type_name]
    if value_type.pattern is None:
        raise ValueError(f"Type {type_name!r} has no search pattern")
    for match in value_type.pattern.finditer(ctx.folded, start, end):
        value = value_type.normalize(ctx.text[match.start() : match.end()])
        if value is not None:
            return value
    return None


def _line_end(text: str, position: int) -> int:
    newline = text.find("\n", position)
    return len(text) if newline == -1 else newline


class LabeledRule(_Rule):
    """Wartość przy etykiecie: w tej samej linii, w następnej albo w oknie `window` znaków za etykietą.

    Dla typów tekstowych wartością jest reszta linii (bez separatorów ':' i kropek); gdy jest pusta,
    następna niepusta linia. `continuation` dokleja kolejne linie, aż trafi na pustą linię albo
    linię zaczynającą się od wzorca końca (np. następnej etykiety).
    """

    kind: Literal["labeled"]
    labels: list[str] = Field(min_length=1)
    type: ValueTypeName
    window: int = 200
    continuation: list[str] | None = None
    max_lines: int = 4
    cut: list[str] = []  # tekst wartości kończy się przed pierwszym dopasowaniem (np. adres po nazwie)
    all: bool = False  # lista wartości przy każdym wystąpieniu etykiety (np. KRS członków konsorcjum)

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        if self.all:
            matches = sorted(
                (
                    match
                    for label in self.labels
                    for match in compile_pattern(label).finditer(ctx.folded, *span)
                ),
                key=lambda match: match.start(),
            )
            return _distinct([self._value_at(ctx, match.end(), span[1]) for match in matches]) or None
        start, end = span
        for label in self.labels:
            for match in compile_pattern(label).finditer(ctx.folded, start, end):
                value = self._value_at(ctx, match.end(), end)
                if value is not None:
                    return value
        return None

    def _table_value(self, ctx: Context, position: int, end: int) -> tuple[bool, JsonValue]:
        """Read the cell below a multi-column header before searching a flat window."""
        line_start = ctx.text.rfind("\n", 0, position) + 1
        line_end = min(_line_end(ctx.text, position), end)
        if (
            VALUE_TYPES[self.type].pattern is not None
            and _find_typed(ctx, self.type, position, line_end) is not None
        ):
            return False, None
        cells = list(re.finditer(r"\S.*?(?=(?: {2,}|\t+)|$)", ctx.text[line_start:line_end]))
        selected = next(
            (i for i, c in enumerate(cells) if c.start() < position - line_start <= c.end()), None
        )
        if len(cells) < _MIN_TABLE_COLUMNS or selected is None or line_end >= end:
            return False, None
        row_start = line_end + 1
        row_end = min(_line_end(ctx.text, row_start), end)
        row = list(re.finditer(r"\S.*?(?=(?: {2,}|\t+)|$)", ctx.text[row_start:row_end]))
        if len(row) != len(cells):
            return False, None
        cell = row[selected]
        start, stop = row_start + cell.start(), row_start + cell.end()
        value_type = VALUE_TYPES[self.type]
        value = (
            _find_typed(ctx, self.type, start, stop)
            if value_type.pattern is not None
            else value_type.normalize(ctx.text[start:stop])
        )
        return True, value

    def _value_at(self, ctx: Context, position: int, end: int) -> JsonValue:
        table, value = self._table_value(ctx, position, end)
        if table:
            return value
        if VALUE_TYPES[self.type].pattern is None:
            return self._text_value(ctx, position, end)
        return _find_typed(ctx, self.type, position, min(end, position + self.window))

    def _text_value(self, ctx: Context, position: int, end: int) -> JsonValue:
        lines = ctx.text[position:end].split("\n")
        # reszta wyrazu etykiety (wzorzec bywa skrócony: „korespond”) i separatory przed wartością
        first = re.sub(r"^\w*[\s:.…]*", "", lines[0])
        rest = lines[1:]
        if not first.strip():
            # linia kropek (miejsce na podpis) to nie wartość
            rest = [line for line in rest if re.search(r"\w", line)]
            if not rest:
                return None
            first, rest = rest[0], rest[1:]
        collected = [first]
        if self.continuation is not None:
            stops = [compile_pattern(p) for p in self.continuation]
            for line in rest[: self.max_lines - 1]:
                if not line.strip() or any(stop.match(fold(line).strip()) for stop in stops):
                    break
                collected.append(line)
        value = " ".join(collected)
        for pattern in self.cut:
            if match := compile_pattern(pattern).search(fold(value)):
                value = value[: match.start()]
        return VALUE_TYPES[self.type].normalize(value)


class FirstRule(_Rule):
    """N-ta (domyślnie pierwsza) różna poprawna wartość danego typu w zakresie."""

    kind: Literal["first"]
    type: ValueTypeName
    occurrence: int = Field(default=1, ge=1)
    all: bool = False  # lista wszystkich różnych wartości (np. KRS każdego członka konsorcjum)

    @field_validator("type")
    @classmethod
    def _searchable(cls, type_name: ValueTypeName) -> ValueTypeName:
        if VALUE_TYPES[type_name].pattern is None:
            raise ValueError(f"type {type_name!r} cannot be searched for; use first_line or labeled")
        return type_name

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        value_type = VALUE_TYPES[self.type]
        assert value_type.pattern is not None  # sprawdzone przy wczytaniu konfiguracji
        seen = _distinct(
            [
                value_type.normalize(ctx.text[m.start() : m.end()])
                for m in value_type.pattern.finditer(ctx.folded, *span)
            ]
        )
        if self.all:
            return seen or None
        return seen[self.occurrence - 1] if len(seen) >= self.occurrence else None


class RegexRule(_Rule):
    """Wartość z grupy `value` wyrażenia regularnego (na tekście złożonym), znormalizowana typem."""

    kind: Literal["regex"]
    regex: str
    type: ValueTypeName
    all: bool = False  # lista wszystkich różnych dopasowań zamiast pierwszego

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        values = _distinct(
            [
                VALUE_TYPES[self.type].normalize(ctx.text[m.start("value") : m.end("value")])
                for m in compile_pattern(self.regex).finditer(ctx.folded, *span)
            ]
        )
        if self.all:
            return values or None
        return values[0] if values else None


class FirstLineRule(_Rule):
    """Pierwsza niepusta linia zakresu (np. nazwa podmiotu pod etykietą „Odwołujący:”)."""

    kind: Literal["first_line"]
    type: ValueTypeName = "text"
    # nazwa bywa złamana na kilka linii: doklejamy kolejne, aż linia zacznie się od wzorca końca
    continuation: list[str] | None = None
    max_lines: int = 3

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        lines = [line for line in ctx.text[span[0] : span[1]].split("\n") if line.strip(" \t:")]
        if not lines:
            return None
        collected = [lines[0]]
        if self.continuation is not None:
            stops = [compile_pattern(p) for p in self.continuation]
            for line in lines[1 : self.max_lines]:
                if any(stop.match(fold(line).strip()) for stop in stops):
                    break
                collected.append(line)
        return VALUE_TYPES[self.type].normalize(" ".join(collected))


class QuotedRule(_Rule):
    """Tekst w cudzysłowie („…”, “…”, "…") tuż za etykietą, także przez kilka linii."""

    kind: Literal["quoted"]
    labels: list[str] = Field(min_length=1)

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        # OCR często gubi cudzysłów otwierający („), więc jest opcjonalny; zamykający jest wymagany
        quoted = re.compile(r"\s*:?\s*[„“\"]?([^„”“\"\s][^„”“\"]{2,600}?)[”“\"]")
        for label in self.labels:
            for match in compile_pattern(label).finditer(ctx.folded, *span):
                found = quoted.match(ctx.text, match.end(), span[1])
                if found:
                    return VALUE_TYPES["text"].normalize(found[1])
        return None


# Imię i nazwisko: 2-3 wyrazy z wielkiej litery albo pisane wersalikami (nazwiska dwuczłonowe z łącznikiem).
_UPPER = "A-ZŁŚŻŹĆŃÓĘĄ"
_LOWER = "a-ząćęłńóśźż"
_NAME_WORD = rf"(?:[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}][{_LOWER}]+)?|[{_UPPER}]{{2,}}(?:-[{_UPPER}]{{2,}})?)"
_PERSON = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{1,2}}"
# Wyrazy, które w „imieniu i nazwisku” oznaczają nazwę organizacji (np. „Kancelaria Adwokacka adw. …”).
_ORGANIZATION_WORDS = re.compile(
    r"(?i)\b(?:kancelari|adwokack|radc|prawn|spółk|spolk|partner|zarząd|zarzad)\w*|\bbiuro\b"
)


_NAME_WORDS = 2  # imię + nazwisko


def _person_name(raw: str) -> JsonValue:
    """Imię i nazwisko z dopasowania; wersaliki -> 'Imię Nazwisko'; nazwa organizacji -> None."""
    name = VALUE_TYPES["name"].normalize(raw)
    if not isinstance(name, str) or _ORGANIZATION_WORDS.search(name):
        return None
    return name.title() if name.isupper() else name


class PersonsRule(_Rule):
    """Osoby z tytułem zawodowym (adw., r. pr., radca prawny…) w zakresie, bez powtórzeń."""

    kind: Literal["persons"]
    first: bool = False  # True: tylko pierwsza osoba (pole pojedyncze), False: lista

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        before = re.compile(rf"(?<![\w.]){PERSON_TITLES}\s+({_PERSON})", re.IGNORECASE)
        after = re.compile(rf"({_PERSON}),?\s+{PERSON_TITLES}(?![\w])")
        text = ctx.text[span[0] : span[1]]
        found = sorted(
            [(m.start(1), m[1]) for m in before.finditer(text)]
            + [(m.start(1), m[1]) for m in after.finditer(text)]
        )
        names: list[JsonValue] = []
        for _, raw in found:
            # nazwisko bywa przeniesione do następnej linii; trzeci wyraz z nowej linii to już co innego
            lines = raw.split("\n")
            name = _person_name(lines[0] if len(lines[0].split()) >= _NAME_WORDS else " ".join(lines[:2]))
            if name is not None and name not in names:
                names.append(name)
        if self.first:
            return names[0] if names else None
        return names or None


class NameLinesRule(_Rule):
    """Imiona i nazwiska wypisane w osobnych liniach (lista osób bez tytułów zawodowych)."""

    kind: Literal["name_lines"]

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        person = re.compile(rf"^\s*(?:[-•\u2013]\s*)?({_PERSON})\s*[,;.]?\s*$")
        names = [
            _person_name(m[1])
            for line in ctx.text[span[0] : span[1]].split("\n")
            if (m := person.match(line))
        ]
        return _distinct(names) or None


class SignatoriesRule(_Rule):
    """Osoby podpisane pod linią kropek (`.....`), bez tytułów zawodowych pełnomocników."""

    kind: Literal["signatories"]

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        lines = ctx.text[span[0] : span[1]].split("\n")
        person = re.compile(rf"^\s*({_PERSON})\s*$")
        names: list[JsonValue] = []
        for index, line in enumerate(lines):
            if not re.search(r"[.…_]{5,}", line):
                continue
            following = [ln for ln in lines[index + 1 : index + 4] if ln.strip()]
            # „Za Firma sp. z o.o.” / „W imieniu …” bywa między linią podpisu a nazwiskiem
            if following and re.match(r"(?i)\s*(za|w imieniu)\s", following[0]):
                following = following[1:]
            if following and (match := person.match(following[0])):
                name = _person_name(match[1])
                if name is not None and name not in names:
                    names.append(name)
        return names or None


class FlagRule(_Rule):
    """`present`, gdy wzorzec występuje w zakresie, w przeciwnym razie `absent`."""

    kind: Literal["flag"]
    pattern: str
    present: JsonValue
    absent: JsonValue

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        return self.present if compile_pattern(self.pattern).search(ctx.flat, *span) else self.absent


class MatchCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str
    value: JsonValue


class FirstMatchRule(_Rule):
    """Wartość przypadku, którego wzorzec występuje w zakresie (wybór według `pick`)."""

    kind: Literal["first_match"]
    cases: list[MatchCase] = Field(min_length=1)
    # first_case: pierwszy przypadek z listy, który występuje; earliest: przypadek, którego dopasowanie
    # zaczyna się najwcześniej w tekście (przy remisie wcześniejszy na liście)
    pick: Literal["first_case", "earliest"] = "first_case"

    def extract(self, ctx: Context) -> JsonValue:
        span = ctx.span(self.scope)
        if span is None:
            return None
        found = []
        for order, case in enumerate(self.cases):
            if match := compile_pattern(case.pattern).search(ctx.flat, *span):
                if self.pick == "first_case":
                    return case.value
                found.append((match.start(), order, case.value))
        return min(found)[2] if found else None


class VocabularyRule(_Rule):
    """Opis ze słownika klasy dla wartości innego pola (np. opis czynności dla jej identyfikatora)."""

    kind: Literal["vocabulary"]
    vocabulary: str
    key_field: str

    def extract(self, ctx: Context) -> JsonValue:
        key = ctx.values.get(self.key_field)
        if not isinstance(key, int):
            return None
        return ctx.vocabularies[self.vocabulary].get(key)


type Rule = Annotated[
    LabeledRule
    | FirstRule
    | RegexRule
    | FirstLineRule
    | QuotedRule
    | PersonsRule
    | NameLinesRule
    | SignatoriesRule
    | FlagRule
    | FirstMatchRule
    | VocabularyRule,
    Field(discriminator="kind"),
]
