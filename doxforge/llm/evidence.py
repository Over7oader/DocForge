"""Weryfikacja kandydatów z LLM na podstawie tekstu jednego dokumentu."""

import re

from rapidfuzz import fuzz

from doxforge.extraction.values import VALUE_TYPES_BY_NAME
from doxforge.text import fold

# Typy, których wartość kanoniczna może różnić się od zapisu źródłowego.
NORMALIZED = frozenset({"date", "amount", "ted"})
_MIN_COPY_RATIO = 90
# Linia nagłówka/podpisu: do 4 słów przed datą („Kraków, dnia”), po dacie najwyżej „r.”/„roku”.
_HEADER_MAX_WORDS = 4
_FILE_OR_URL = re.compile(r"(?:://|www\.|\.(?:pdf|docx?|xlsx?|odt|html?|jpe?g|png|tiff?|txt)\b)")
_HEADER_TAIL = re.compile(r"\s*(?:r\.?|roku)?\s*[.,]?\s*")
# Rdzeń nazwiska przymiotnikowego i końcówka mianownika: męska -i, żeńska -a (tekst złożony: ą -> a).
_ADJECTIVAL_SURNAME = re.compile(r"(\w+(?:sk|ck|dzk))([ia])")
# Końcówki przypadków w tekście złożonym: męskie ski/skiego/skiemu/skim, żeńskie ska/ską/skiej.
_GENDER_ENDINGS = {"i": {"i", "iego", "iemu", "im"}, "a": {"a", "iej"}}
_OTHER_GENDER = str.maketrans({"i": "a", "a": "i", "I": "A", "A": "I"})


def _flip_gender(word: str) -> str:
    """Kowalski <-> Kowalska (ostatnia litera nazwiska przymiotnikowego, z zachowaniem wielkości)."""
    return word[:-1] + word[-1].translate(_OTHER_GENDER)


class SourceEvidence:
    def __init__(
        self,
        text: str,
        digital_text: str = "",
        labels: dict[str, list[str]] | None = None,
        selected_entities: dict[str, str] | None = None,
    ) -> None:
        self.labels = labels or {}
        self.selected_entities = selected_entities or {}
        self.folded = fold(text)
        self._flat = " ".join(text.split())
        self.digital = " ".join(digital_text.split())
        self._forms: dict[str, set[str | float]] = {}

    def supports(self, kind: str, candidate: str | float) -> bool:
        """Indeks normalizowanych wystąpień, budowany raz dla używanego typu."""
        if kind not in self._forms:
            spec = VALUE_TYPES_BY_NAME[kind]
            assert spec.pattern is not None
            self._forms[kind] = {
                value
                for match in spec.pattern.finditer(self.folded)
                if isinstance(value := spec.normalize(match[0]), str | float)
            }
        return candidate in self._forms[kind]

    def verbatim(self, candidate: str) -> bool:
        """Wartość występuje w tekście dosłownie (z dokładnością do odstępów)."""
        flat = " ".join(candidate.split())
        return bool(flat) and flat in self._flat

    def only_in_file_or_url(self, candidate: str) -> bool:
        """Wartość występuje w tekście tylko jako część nazwy pliku lub adresu URL.

        Wydruki z przeglądarki i bankowości mają w stopce adres strony i nazwę pliku
        („…_details_20260212_974925.pdf”); to nigdy nie jest wartość pola.
        """
        folded = fold(candidate).strip()
        if not folded:
            return False
        # Wartość z odstępami nie mieści się w jednym tokenie, więc nigdy nie jest odrzucana.
        tokens = [m for m in re.finditer(r"\S+", self.folded) if folded in m[0]]
        return bool(tokens) and all(_FILE_OR_URL.search(m[0]) for m in tokens)

    def on_header_line(self, kind: str | None, candidate: str | float) -> bool:
        """Wartość stoi w krótkiej linii zakończonej datą, jak „Kraków, dnia 5 maja 2026 r.”.

        Taka linia (nagłówek albo miejsce podpisu) różni się od dat w treści i w tabelach
        chronologii, gdzie data stoi w środku zdania albo na początku wiersza.
        """
        spec = VALUE_TYPES_BY_NAME["date"]
        assert spec.pattern is not None
        for match in spec.pattern.finditer(self.folded):
            start = self.folded.rfind("\n", 0, match.start()) + 1
            end = self.folded.find("\n", match.end())
            before, after = (
                self.folded[start : match.start()],
                self.folded[match.end() : end if end >= 0 else None],
            )
            words = re.findall(r"[^\W\d_]+", before)
            if not _HEADER_TAIL.fullmatch(after) or not 1 <= len(words) <= _HEADER_MAX_WORDS:
                continue
            if kind == "date" and spec.normalize(match[0]) == candidate:
                return True
            if kind != "date" and isinstance(candidate, str):
                squashed = re.sub(r"[\W_]+", "", fold(candidate))
                if squashed and squashed in re.sub(r"[\W_]+", "", before):
                    return True
        return False

    def account(self, candidate: str) -> str | None:
        """Przywróć postać ze źródła tylko przy jednoznacznym dopasowaniu rachunku."""
        spec = VALUE_TYPES_BY_NAME["iban"]
        assert spec.pattern is not None
        found = {
            value
            for match in spec.pattern.finditer(self.folded)
            if isinstance(value := spec.normalize(match[0]), str)
        }
        body = candidate[2:] if candidate[:2].isalpha() else candidate
        matches = {
            value
            for value in found
            if (value[2:] if value[:2].isalpha() else value) == body
            and (not candidate[:2].isalpha() or not value[:2].isalpha() or candidate[:2] == value[:2])
        }
        return next(iter(matches)) if len(matches) == 1 else None

    def same_person(self, candidate: str) -> bool:
        """Szuka kolejnych członów nazwiska i imienia w jednym fragmencie tekstu."""
        words = re.findall(r"[^\W\d_]+", fold(candidate))
        if not words:
            return False
        # Pozostawiamy wspólny początek dla odmiany przez przypadki.
        stems = [re.escape(w[: max(3, len(w) - 3)]) + r"[^\W\d_]*" for w in words]
        if re.search(r"\b" + r"[\s\-]+".join(stems) + r"\b", self.folded) is None:
            return False
        return all(self._same_gender(word) for word in words)

    def person_in_source(self, candidate: str) -> str | None:
        """Osoba z tekstu w pisowni płci jak w dokumencie albo None, gdy jej tam nie ma.

        Mały model „poprawia” nazwisko do płci imienia („Halina Przybylski” -> „Przybylska”),
        a wartość ma być taka jak w dokumencie: wtedy przywracamy końcówkę ze źródła.
        """
        if self.same_person(candidate):
            return candidate
        words = candidate.split()
        flipped = " ".join(
            _flip_gender(word) if _ADJECTIVAL_SURNAME.fullmatch(fold(word)) else word for word in words
        )
        return flipped if flipped != candidate and self.same_person(flipped) else None

    def _same_gender(self, word: str) -> bool:
        """Nazwisko przymiotnikowe (-ski/-ska, -cki/-cka) ma w tekście formę tej samej płci.

        Rdzeń bez 3 liter przepuszcza odmianę, ale też „Kowalski” dla „Pani Kowalskiej”.
        Tu sprawdzamy, czy któreś wystąpienie w tekście ma końcówkę zgodną z płcią kandydata.
        """
        match = _ADJECTIVAL_SURNAME.fullmatch(word)
        if match is None:
            return True
        endings = {m[1] for m in re.finditer(rf"\b{re.escape(match[1])}([^\W\d_]*)\b", self.folded)}
        return not endings or bool(endings & _GENDER_ENDINGS[match[2]])

    def prefer_literal(self, literal: str, model: str, labels: list[str]) -> bool:
        """Przy bliskiej zgodności obu odczytów zachowaj pisownię z warstwy tekstowej."""
        literal = " ".join(literal.split())
        model = " ".join(model.split())
        if not literal or literal == model or literal not in self.digital:
            return False
        if model in self.digital:
            if not model.endswith(literal):
                return False
            prefix = fold(model.removesuffix(literal)).strip(" :")
            return bool(prefix) and any(re.fullmatch(label, prefix) for label in labels)
        return fuzz.ratio(literal, model) >= _MIN_COPY_RATIO
