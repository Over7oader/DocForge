"""Testy danych: audyt zbioru organizatora (ANALIZA.md §12) jako testy regresyjne.

Pilnują założeń, na których opieramy reguły ekstrakcji, i wykrywają zmiany w zbiorze
po aktualizacji repozytorium organizatora.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import JsonValue

from doxforge import settings
from doxforge.doc_types import DocType, classify_result, load_doc_types
from doxforge.evaluation.dataset import Sample, Source, Split, load_evaluation_config, load_manifest
from doxforge.evaluation.scorer import leaf_pairs
from doxforge.ingest import pdf_page_texts

pytestmark = pytest.mark.dataset

# Katalog skanów -> klasa dokumentu.
SCAN_CLASSES = {"odwolania": "odwolanie", "pelnomocnictwa": "pelnomocnictwo", "potwierdzenia": "przelew"}


@pytest.fixture(scope="module")
def samples() -> list[Sample]:
    if not settings.DATASET_DIR.is_dir():
        pytest.skip("Brak zbioru organizatora: uruchom scripts/fetch_dataset.sh")
    return load_manifest(settings.DATASET_DIR, load_evaluation_config())


@pytest.fixture(scope="module")
def doc_types() -> dict[str, DocType]:
    return load_doc_types()


def _gold(sample: Sample) -> JsonValue:
    assert sample.gold is not None
    result: JsonValue = json.loads(sample.gold.read_text(encoding="utf-8"))
    return result


def _expected_class(sample: Sample) -> str:
    assert sample.gold is not None
    if sample.source is Source.SCAN:
        return SCAN_CLASSES[sample.pdf.parent.parent.name]
    return sample.gold.stem


def _labelled(samples: list[Sample]) -> list[Sample]:
    return [s for s in samples if s.gold is not None]


def test_manifest_composition(samples: list[Sample]):
    composition = Counter((s.source, s.split) for s in samples)
    assert composition == {
        (Source.DIGITAL, Split.TRAIN): 180,
        (Source.SCAN, Split.TRAIN): 15,
        (Source.SCAN, Split.HOLDOUT): 15,
        (Source.NEGATIVE, Split.TRAIN): 12,  # 8 z katalogu negatywne + 4 wplecione w sprawy
        (Source.NEGATIVE, Split.HOLDOUT): 4,
    }


def test_holdout_has_no_copy_in_train(samples: list[Sample]):
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    train = {digest(s.pdf) for s in samples if s.split is Split.TRAIN}
    leaked = [s.pdf.name for s in samples if s.split is Split.HOLDOUT and digest(s.pdf) in train]
    assert leaked == []


# Dopuszczamy prefiks kraju na podstawie doprecyzowania UZP z Discorda, 21:17 i 23:00.
KNOWN_LABEL_NOISE = {}  # Aktualny schemat dopuszcza prefiks kraju zgodnie z Discordem UZP.


def test_labels_match_their_class_schema(samples: list[Sample], doc_types: dict[str, DocType]):
    violations: dict[str, list[str]] = {}
    for sample in _labelled(samples):
        assert sample.gold is not None
        gold = _gold(sample)
        expected = _expected_class(sample)
        assert classify_result(gold, doc_types) == expected, sample.gold
        errors = [e.split(":")[0] for e in doc_types[expected].validation_errors(gold)]
        if errors:
            violations[sample.gold.relative_to(settings.DATASET_DIR).as_posix()] = errors
    assert violations == KNOWN_LABEL_NOISE


def test_reason_description_matches_vocabulary(samples: list[Sample], doc_types: dict[str, DocType]):
    vocabulary = doc_types["odwolanie"].config.vocabularies["czynnosc_powod"]
    for sample in _labelled(samples):
        gold = _gold(sample)
        if not isinstance(gold, dict) or "OdwolanieKIO" not in gold:
            continue
        appeal = gold["OdwolanieKIO"]
        assert isinstance(appeal, dict)
        reason = appeal["czynnosc_powod_id"]
        assert isinstance(reason, int), sample.gold
        assert vocabulary[reason] == appeal["czynnosc_opis"], sample.gold


def test_letter_date_and_place_are_null_together(samples: list[Sample]):
    """Brak linii „Miejscowość, dnia …” oznacza null w obu polach (D3/D9 w ANALIZA.md)."""
    for sample in _labelled(samples):
        gold = _gold(sample)
        if isinstance(gold, dict) and isinstance(appeal := gold.get("OdwolanieKIO"), dict):
            assert (appeal["data"] is None) == (appeal["miejscowosc"] is None), sample.gold


def _squash(text: str) -> str:
    return re.sub(r"[\s\-\u2013]", "", text).casefold()


def test_text_fields_appear_verbatim_in_digital_pdfs(samples: list[Sample], doc_types: dict[str, DocType]):
    """Pola `x-source: text` da się znaleźć w warstwie tekstowej PDF (podstawa weryfikacji źródła)."""
    missing = []
    for sample in _labelled(samples):
        if sample.source is not Source.DIGITAL:
            continue
        gold = _gold(sample)
        sources = doc_types[_expected_class(sample)].field_sources
        text = _squash("".join(pdf_page_texts(sample.pdf)))
        for path, value, _ in leaf_pairs(gold, None):
            if sources[path] == "text" and isinstance(value, str) and _squash(value) not in text:
                missing.append(f"{sample.case_id}/{sample.pdf.name}: {path}={value!r}")
    assert missing == []


def test_appeal_parties_agree_with_power_of_attorney(samples: list[Sample]):
    """Odwołujący = mocodawca, a pełnomocnik z odwołania jest na liście pełnomocników."""
    by_case: dict[str, dict[str, JsonValue]] = {}
    for sample in _labelled(samples):
        if sample.source is Source.DIGITAL:
            by_case.setdefault(sample.case_id, {})[_expected_class(sample)] = _gold(sample)
    for case, labels in by_case.items():
        appeal, power = labels["odwolanie"], labels["pelnomocnictwo"]
        assert isinstance(appeal, dict) and isinstance(power, dict)
        appellants, principals, attorneys = appeal["Odwolujacy"], power["mocodawca"], power["pelnomocnicy"]
        assert isinstance(appellants, list) and isinstance(principals, list) and isinstance(attorneys, list)
        appellant = appellants[0]
        assert isinstance(appellant, dict)
        principal_nips = [
            p["data"]["nip"] for p in principals if isinstance(p, dict) and isinstance(p["data"], dict)
        ]
        assert appellant["nip"] in principal_nips, case
        attorney = appellant["pelnomocnik"]
        assert isinstance(attorney, dict)
        if attorney["imie_nazwisko"] is not None:
            names = [a["imie_nazwisko"] for a in attorneys if isinstance(a, dict)]
            assert attorney["imie_nazwisko"] in names, case
