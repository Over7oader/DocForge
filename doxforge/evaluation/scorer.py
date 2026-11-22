"""F1 ekstrakcji: porównanie drzew JSON (wynik vs wzorzec) pole po polu.

Organizator podał opis na Discordzie bez skryptu. Raportujemy cztery warianty historyczne
i jawny wariant przybliżony discord_ratio80_proxy:

- `NullMode.IGNORE`: pary null/null pomijane (typowe F1 ekstrakcji; wariant główny),
- `NullMode.AS_VALUE`: poprawny null liczony jako TP, gdy klucz jest obecny po obu stronach,
- `MatchMode.STRICT`: równość wartości JSON (liczby porównywane numerycznie),
- `MatchMode.LENIENT`: teksty po ujednoliceniu białych znaków i wielkości liter.

W historycznych wariantach zła wartość liczy się jako FP i FN.
W proxy Discorda zła obecna wartość daje FP, a brak oczekiwanej wartości FN.
Elementy list (np. pełnomocnicy) są parowane tak, by zmaksymalizować
liczbę zgodnych pól, bo kolejność na liście nie niesie znaczenia.
"""

import json
import math
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import permutations
from pathlib import Path
from typing import TypeIs

from pydantic import JsonValue
from rapidfuzz import fuzz

from doxforge import settings
from doxforge.doc_types import DocType, classify_result


class NullMode(StrEnum):
    IGNORE = "ignore"
    AS_VALUE = "as_value"


class MatchMode(StrEnum):
    STRICT = "strict"
    LENIENT = "lenient"
    DISCORD_RATIO = "discord_ratio80_proxy"


class PredictionStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNPARSABLE = "unparsable"


type Variant = tuple[NullMode, MatchMode]
DISCORD: Variant = (NullMode.IGNORE, MatchMode.DISCORD_RATIO)
VARIANTS: tuple[Variant, ...] = (
    *tuple((null, match) for null in NullMode for match in (MatchMode.STRICT, MatchMode.LENIENT)),
    DISCORD,
)
# UZP wymienia te pola. Miara podobieństwa jest naszym jawnym założeniem.
FUZZY_FIELDS = frozenset(
    {
        "Odwolujacy[].nazwa",
        "Zamawiajacy[].nazwa",
        "Odwolujacy[].pelnomocnik.nazwa",
        "przelew_bankowy.tytul",
        "ZamowieniePubliczne.tytul",
    }
)
DISCORD_THRESHOLD = 80

PRIMARY: Variant = (NullMode.IGNORE, MatchMode.STRICT)

# Wynik niezgodny ze schematem każdej klasy (w macierzy klasyfikacji).
INVALID_TYPE = "<niezgodny ze schematem>"
EMPTY_TYPE = "<pusty>"

# Powyżej tylu permutacji parowanie list jest zachłanne zamiast pełnego przeszukania.
_MAX_PERMUTATIONS = 50_000


class _Missing:
    """Klucz nieobecny w drzewie; różni się od jawnego null tylko w wariancie AS_VALUE."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
type Node = JsonValue | _Missing


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __iadd__(self, other: "Counts") -> "Counts":
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        return self

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        denominator = 2 * self.tp + self.fp + self.fn
        return 2 * self.tp / denominator if denominator else 0.0

    @property
    def errors(self) -> int:
        return self.fp + self.fn

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def values_equal(gold: JsonValue, pred: JsonValue, match: MatchMode, path: str = "") -> bool:
    """Równość dwóch niepustych wartości liści."""
    if isinstance(gold, bool) or isinstance(pred, bool):
        return type(gold) is type(pred) and gold == pred
    if isinstance(gold, int | float) and isinstance(pred, int | float):
        return math.isclose(gold, pred, rel_tol=0, abs_tol=1e-9)
    if isinstance(gold, str) and isinstance(pred, str):
        if match is MatchMode.DISCORD_RATIO:
            return (
                fuzz.ratio(_normalize_text(gold), _normalize_text(pred)) >= DISCORD_THRESHOLD
                if path in FUZZY_FIELDS
                else gold == pred
            )
        return gold == pred if match is MatchMode.STRICT else _normalize_text(gold) == _normalize_text(pred)
    if (
        match is MatchMode.LENIENT
        and isinstance(gold, str | int | float)
        and isinstance(pred, str | int | float)
    ):
        return _normalize_text(str(gold)) == _normalize_text(str(pred))
    return False


def _is_null(value: Node) -> TypeIs[_Missing | None]:
    return value is None or isinstance(value, _Missing)


def _wrong_kind(gold: Node, pred: Node, path: str, kind: type) -> Iterator[tuple[str, Node, Node]]:
    """Strona, która nie jest kontenerem oczekiwanego rodzaju, liczy się jako jedna wartość."""
    if not _is_null(gold) and not isinstance(gold, kind):
        yield path, gold, MISSING
    if not _is_null(pred) and not isinstance(pred, kind):
        yield path, MISSING, pred


def leaf_pairs(
    gold: Node, pred: Node, path: str = "", *, match: MatchMode = MatchMode.STRICT
) -> Iterator[tuple[str, Node, Node]]:
    """Pary (ścieżka, wzorzec, wynik) dla wszystkich liści obu drzew, z listami sparowanymi."""
    if isinstance(gold, dict) or isinstance(pred, dict):
        yield from _wrong_kind(gold, pred, path, dict)
        gold_dict = gold if isinstance(gold, dict) else {}
        pred_dict = pred if isinstance(pred, dict) else {}
        keys = [*gold_dict, *(key for key in pred_dict if key not in gold_dict)]
        for key in keys:
            yield from leaf_pairs(
                gold_dict.get(key, MISSING),
                pred_dict.get(key, MISSING),
                f"{path}.{key}" if path else key,
                match=match,
            )
        return
    if isinstance(gold, list) or isinstance(pred, list):
        yield from _wrong_kind(gold, pred, path, list)
        gold_list = gold if isinstance(gold, list) else []
        pred_list = pred if isinstance(pred, list) else []
        for gold_index, pred_index in _align(gold_list, pred_list, path, match):
            yield from leaf_pairs(
                MISSING if gold_index is None else gold_list[gold_index],
                MISSING if pred_index is None else pred_list[pred_index],
                f"{path}[]",
                match=match,
            )
        return
    yield path, gold, pred


def _agreement(gold: JsonValue, pred: JsonValue, path: str, match: MatchMode) -> int:
    return sum(
        1
        for leaf, g, p in leaf_pairs(gold, pred, path, match=match)
        if not _is_null(g) and not _is_null(p) and values_equal(g, p, match, leaf)
    )


def _align(
    gold: list[JsonValue], pred: list[JsonValue], path: str, match: MatchMode
) -> list[tuple[int | None, int | None]]:
    scores = [[_agreement(g, p, f"{path}[]", match) for p in pred] for g in gold]
    assignment = _best_assignment(scores, len(gold), len(pred))
    matched = set(assignment.values())
    return [(i, assignment.get(i)) for i in range(len(gold))] + [
        (None, j) for j in range(len(pred)) if j not in matched
    ]


def _best_assignment(scores: list[list[int]], n: int, m: int) -> dict[int, int]:
    """Przydział wzorzec -> wynik o największej łącznej zgodności (przy remisie kolejność z listy)."""
    if n == 0 or m == 0:
        return {}

    def total(assignment: dict[int, int]) -> int:
        return sum(scores[i][j] for i, j in assignment.items())

    if math.perm(max(n, m), min(n, m)) <= _MAX_PERMUTATIONS:
        if n <= m:
            candidates = ({i: js[i] for i in range(n)} for js in permutations(range(m), n))
        else:
            candidates = ({is_[j]: j for j in range(m)} for is_ in permutations(range(n), m))
        return max(candidates, key=total)
    assignment: dict[int, int] = {}
    for _, i, j in sorted(((scores[i][j], i, j) for i in range(n) for j in range(m)), key=lambda t: -t[0]):
        if i not in assignment and j not in assignment.values():
            assignment[i] = j
    return assignment


def tally(pairs: list[tuple[str, Node, Node]], variant: Variant) -> dict[str, Counts]:
    """Liczniki TP/FP/FN per ścieżka pola."""
    null_mode, match = variant
    counts: dict[str, Counts] = {}
    for path, gold, pred in pairs:
        c = counts.setdefault(path, Counts())
        if _is_null(gold) and _is_null(pred):
            if null_mode is NullMode.AS_VALUE and gold is None and pred is None:
                c.tp += 1
        elif _is_null(gold):
            c.fp += 1
        elif _is_null(pred):
            c.fn += 1
        elif values_equal(gold, pred, match, path):
            c.tp += 1
        else:
            c.fp += 1
            if match is not MatchMode.DISCORD_RATIO:
                c.fn += 1
    return counts


@dataclass
class DocumentScore:
    rel_path: str
    gold_type: str | None
    pred_type: str
    status: PredictionStatus
    source: str
    fields: dict[Variant, dict[str, Counts]] = field(default_factory=dict)

    def total(self, variant: Variant = PRIMARY) -> Counts:
        result = Counts()
        for counts in self.fields[variant].values():
            result += counts
        return result


def _load_prediction(path: Path) -> tuple[Node, PredictionStatus]:
    if not path.is_file():
        return MISSING, PredictionStatus.MISSING
    content = path.read_text(encoding="utf-8")
    if not content.strip():  # plik 0-bajtowy też traktujemy jako pusty wynik
        return settings.EMPTY_RESULT, PredictionStatus.OK
    try:
        prediction: JsonValue = json.loads(content)
    except json.JSONDecodeError:
        return MISSING, PredictionStatus.UNPARSABLE
    return prediction, PredictionStatus.OK


def _pred_type(prediction: Node, doc_types: dict[str, DocType]) -> str:
    if isinstance(prediction, _Missing):
        return EMPTY_TYPE
    try:
        return classify_result(prediction, doc_types) or EMPTY_TYPE
    except ValueError:
        return INVALID_TYPE


def score_document(gold: JsonValue, prediction: Node) -> dict[Variant, dict[str, Counts]]:
    pairs = list(leaf_pairs(gold, prediction))
    result = {variant: tally(pairs, variant) for variant in VARIANTS if variant != DISCORD}
    result[DISCORD] = tally(list(leaf_pairs(gold, prediction, match=MatchMode.DISCORD_RATIO)), DISCORD)
    return result


def score_run(
    gold_dir: Path, pred_dir: Path, doc_types: dict[str, DocType], sources: Mapping[str, str]
) -> list[DocumentScore]:
    """Ocena wszystkich wzorców z `gold_dir` względem plików o tej samej ścieżce w `pred_dir`."""
    scores = []
    for gold_path in sorted(gold_dir.rglob("*.json")):
        rel = gold_path.relative_to(gold_dir).as_posix()
        gold: JsonValue = json.loads(gold_path.read_text(encoding="utf-8"))
        prediction, status = _load_prediction(pred_dir / rel)
        score = DocumentScore(
            rel_path=rel,
            gold_type=classify_result(gold, doc_types),
            pred_type=_pred_type(prediction, doc_types),
            status=status,
            source=sources.get(rel, "unknown"),
        )
        score.fields = score_document(gold, prediction)
        scores.append(score)
    return scores


def _variant_key(variant: Variant) -> str:
    return f"{variant[0]}/{variant[1]}"


def _add(table: dict[str, Counts], key: str, counts: Counts) -> None:
    table.setdefault(key, Counts()).__iadd__(counts)


@dataclass
class RunSummary:
    documents: int
    missing: list[str]
    unparsable: list[str]
    overall: dict[str, Counts]
    by_type: dict[str, Counts]
    by_source: dict[str, Counts]
    by_field: dict[str, Counts]
    classification: dict[str, int]
    worst_documents: list[tuple[str, int]]
    variant: Variant = PRIMARY

    @property
    def primary(self) -> Counts:
        return self.overall[_variant_key(self.variant)]

    def as_json(self) -> dict[str, JsonValue]:
        def table(rows: dict[str, Counts]) -> JsonValue:
            return {name: counts.as_dict() for name, counts in rows.items()}

        return {
            "primary_variant": _variant_key(self.variant),
            "metric_assumptions": {
                "discord_ratio80_proxy": "Approximation of Discord UZP: rapidfuzz.fuzz.ratio >=80 "
                "after whitespace/case normalization ONLY on listed fields. Wrong present value is FP only. "
                "Similarity algorithm, aggregation and assignment are not confirmed by official scorer.",
                "fuzzy_fields": [str(path) for path in sorted(FUZZY_FIELDS)],
            },
            "documents": self.documents,
            "missing": list(self.missing),
            "unparsable": list(self.unparsable),
            "overall": table(self.overall),
            "by_type": table(self.by_type),
            "by_source": table(self.by_source),
            "by_field": table(self.by_field),
            "classification": dict(self.classification),
            "worst_documents": [{"path": path, "errors": errors} for path, errors in self.worst_documents],
        }


def summarize(scores: list[DocumentScore], variant: Variant = PRIMARY) -> RunSummary:
    """Zbiorczy raport: F1 ogólne, per klasa, per źródło, per pole, klasyfikacja, najgorsze pliki."""
    overall: dict[str, Counts] = {}
    by_type: dict[str, Counts] = {}
    by_source: dict[str, Counts] = {}
    by_field: dict[str, Counts] = {}
    confusion: Counter[str] = Counter()
    for score in scores:
        for item_variant in VARIANTS:
            _add(overall, _variant_key(item_variant), score.total(item_variant))
        doc_class = score.gold_type or EMPTY_TYPE
        _add(by_type, doc_class, score.total(variant))
        _add(by_source, score.source, score.total(variant))
        for path, counts in score.fields[variant].items():
            _add(by_field, f"{score.gold_type or score.pred_type}:{path}", counts)
        confusion[f"{doc_class} -> {score.pred_type}"] += 1
    worst = sorted(scores, key=lambda s: -s.total(variant).errors)[:15]
    return RunSummary(
        documents=len(scores),
        missing=[s.rel_path for s in scores if s.status is PredictionStatus.MISSING],
        unparsable=[s.rel_path for s in scores if s.status is PredictionStatus.UNPARSABLE],
        overall=overall,
        by_type=dict(sorted(by_type.items())),
        by_source=dict(sorted(by_source.items())),
        by_field=dict(sorted(by_field.items(), key=lambda kv: (kv[1].f1, kv[0]))),
        classification=dict(sorted(confusion.items())),
        worst_documents=[(s.rel_path, s.total(variant).errors) for s in worst if s.total(variant).errors],
        variant=variant,
    )


def format_report(summary: RunSummary) -> str:
    """Czytelna tabela raportu do wypisania w terminalu."""

    def rows(title: str, table: dict[str, Counts]) -> list[str]:
        lines = [f"\n{title}", f"  {'':62} {'TP':>5} {'FP':>5} {'FN':>5} {'P':>6} {'R':>6} {'F1':>6}"]
        lines += [
            f"  {name:62} {c.tp:>5} {c.fp:>5} {c.fn:>5} {c.precision:>6.3f} {c.recall:>6.3f} {c.f1:>6.3f}"
            for name, c in table.items()
        ]
        return lines

    lines = [f"Dokumenty: {summary.documents}  (wariant główny: {_variant_key(summary.variant)})"]
    if summary.variant == DISCORD:
        lines.append("UWAGA: proxy Discorda, z założoną miarą ratio >=80. To nie jest oficjalny scorer UZP.")
    if summary.missing:
        lines.append(f"Brak pliku wyniku: {summary.missing}")
    if summary.unparsable:
        lines.append(f"Niepoprawny JSON: {summary.unparsable}")
    lines += rows("F1 ogólne (null-mode/match-mode)", summary.overall)
    lines += rows("Per klasa (wzorzec)", summary.by_type)
    lines += rows("Per źródło", summary.by_source)
    lines += rows("Per pole", summary.by_field)
    lines.append("\nKlasyfikacja (wzorzec -> wynik)")
    lines += [f"  {name:62} {count}" for name, count in summary.classification.items()]
    if summary.worst_documents:
        lines.append("\nNajwięcej błędów (FP+FN)")
        lines += [f"  {path:62} {errors}" for path, errors in summary.worst_documents]
    return "\n".join(lines)
