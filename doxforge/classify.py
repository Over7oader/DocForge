"""Klasyfikacja dokumentu po treści, według reguł z sekcji `classification` pliku klasy.

Każda reguła to wzorzec (tekst złożony: małe litery, bez polskich znaków) z wagą i strefą:
`title` (pierwsze linie pierwszej strony, gdzie stoi tytuł pisma) albo `body` (pierwsze strony).
Wynik klasy to suma wag reguł, które pasują (każda raz). Wygrywa klasa z najwyższym wynikiem,
jeśli osiąga swój próg `min_score`; inaczej dokument nie należy do żadnej klasy (pusty wynik).
Ujemne wagi opisują sygnały innych pism (np. „sygn. akt” w orzeczeniu KIO).
"""

import logging
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz.distance import Levenshtein

from doxforge.extraction.rules import compile_pattern
from doxforge.ingest import Document
from doxforge.text import fold

logger = logging.getLogger(__name__)

TITLE_LINES = 15
BODY_PAGES = 3
# Jeden błędny znak na 10 liter („pehnomocnictwo”); inną formę wyrazu odrzuca też warunek
# tej samej ostatniej litery.
_SIMILAR_TITLE_WORD = 0.9
_TITLE_LINE_MAX_WORDS = 2


class ClassificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str
    weight: float
    zone: Literal["title", "body"] = "body"
    # Tytuł ze skanu z jednym błędem OCR („Pehnomocnictwo”): reguła pasuje też, gdy krótka linia
    # zawiera słowo bardzo podobne do tego (tekst złożony).
    similar: str | None = None

    def matches(self, zone: str) -> bool:
        if compile_pattern(self.pattern).search(zone):
            return True
        if self.similar is None:
            return False
        # Ta sama ostatnia litera: końcówka fleksyjna („pelnomocnictwa” to nie tytuł „pelnomocnictwo”).
        return any(
            word[-1] == self.similar[-1]
            and Levenshtein.normalized_similarity(word, self.similar) >= _SIMILAR_TITLE_WORD
            for line in zone.split("\n")
            if len(words := re.findall(r"[^\W\d_]+|\d+", line)) <= _TITLE_LINE_MAX_WORDS
            for word in words
        )


class ClassificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_score: float
    rules: list[ClassificationRule] = Field(min_length=1)


def _zones(document: Document) -> dict[str, str]:
    first_page = document.pages[0].text if document.pages else ""
    title_lines = [line for line in first_page.split("\n") if line.strip()]
    return {
        "title": fold("\n".join(title_lines[:TITLE_LINES])),
        "body": fold("\n".join(page.text for page in document.pages[:BODY_PAGES])),
    }


def score(document: Document, config: ClassificationConfig) -> float:
    zones = _zones(document)
    return sum(rule.weight for rule in config.rules if rule.matches(zones[rule.zone]))


def classify(document: Document, configs: Mapping[str, ClassificationConfig]) -> str | None:
    """Nazwa klasy dokumentu albo `None`, gdy żadna klasa nie przekroczyła progu."""
    scores = {name: score(document, config) for name, config in configs.items()}
    logger.debug("Classification scores: %s", scores)
    accepted = {name: value for name, value in scores.items() if value >= configs[name].min_score}
    if not accepted:
        return None
    best = max(accepted.values())
    winners = [name for name, value in accepted.items() if value == best]
    if len(winners) > 1:
        logger.warning("Ambiguous classification %s, treating document as unclassified", winners)
        return None
    return winners[0]
