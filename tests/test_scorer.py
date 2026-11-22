import copy
import json
from pathlib import Path

import pytest
from pydantic import JsonValue

from doxforge.doc_types import load_doc_types
from doxforge.evaluation.scorer import (
    EMPTY_TYPE,
    MISSING,
    PRIMARY,
    Counts,
    MatchMode,
    NullMode,
    PredictionStatus,
    leaf_pairs,
    score_run,
    summarize,
    tally,
)

PRZELEW: JsonValue = {
    "przelew_bankowy": {
        "data_operacji": "2026-07-12",
        "kwota": 17.0,
        "na_rachunek": "70127936703217946356148898",
        "numer_transakcji": None,
        "tytul": "opłata skarbowa",
        "z_rachunku": "65163294615868454368277984",
    }
}

PELNOMOCNICTWO: JsonValue = {
    "mocodawca": [
        {
            "data": {
                "imie_nazwisko": None,
                "krs": "0000244633",
                "nazwa": "A",
                "nip": "8361772283",
                "typ": "osoba_prawna",
            },
            "podpisujacy": [{"imie_nazwisko": "Jan Nowak"}, {"imie_nazwisko": "Ewa Lis"}],
        }
    ],
    "pelnomocnicy": [
        {"imie_nazwisko": "Patrycja Bartoszewicz", "pesel": None},
        {"imie_nazwisko": "Aleksandra Gajewicz", "pesel": "85120133547"},
    ],
}


def total(gold: JsonValue, pred: JsonValue, null=NullMode.IGNORE, match=MatchMode.STRICT) -> Counts:
    result = Counts()
    for counts in tally(list(leaf_pairs(gold, pred)), (null, match)).values():
        result += counts
    return result


def test_identical_documents_score_perfectly():
    counts = total(PRZELEW, copy.deepcopy(PRZELEW))
    assert (counts.tp, counts.fp, counts.fn) == (5, 0, 0)
    assert counts.f1 == 1.0


def test_wrong_value_counts_as_false_positive_and_false_negative():
    pred = copy.deepcopy(PRZELEW)
    pred["przelew_bankowy"]["tytul"] = "inny tytuł"
    counts = total(PRZELEW, pred)
    assert (counts.tp, counts.fp, counts.fn) == (4, 1, 1)


def test_null_prediction_costs_only_false_negative():
    pred = copy.deepcopy(PRZELEW)
    pred["przelew_bankowy"]["tytul"] = None
    counts = total(PRZELEW, pred)
    assert (counts.tp, counts.fp, counts.fn) == (4, 0, 1)


def test_null_mode_as_value_rewards_correct_null_only_when_key_present():
    assert total(PRZELEW, PRZELEW, null=NullMode.AS_VALUE).tp == 6
    pred = copy.deepcopy(PRZELEW)
    del pred["przelew_bankowy"]["numer_transakcji"]
    assert total(PRZELEW, pred, null=NullMode.AS_VALUE).tp == 5


def test_numbers_compare_numerically():
    pred = copy.deepcopy(PRZELEW)
    pred["przelew_bankowy"]["kwota"] = 17
    assert total(PRZELEW, pred).fp == 0


def test_lenient_match_ignores_whitespace_and_case_but_strict_does_not():
    pred = copy.deepcopy(PRZELEW)
    pred["przelew_bankowy"]["tytul"] = "  Opłata   SKARBOWA "
    assert total(PRZELEW, pred).fp == 1
    assert total(PRZELEW, pred, match=MatchMode.LENIENT).fp == 0


def test_string_and_number_differ_in_strict_mode_only():
    gold: JsonValue = {"pesel": "85120133547"}
    pred: JsonValue = {"pesel": 85120133547}
    assert total(gold, pred).fp == 1
    assert total(gold, pred, match=MatchMode.LENIENT).fp == 0


def test_list_order_does_not_matter():
    pred = copy.deepcopy(PELNOMOCNICTWO)
    pred["pelnomocnicy"].reverse()
    pred["mocodawca"][0]["podpisujacy"].reverse()
    counts = total(PELNOMOCNICTWO, pred)
    assert (counts.fp, counts.fn) == (0, 0)


def test_extra_and_missing_list_items_are_counted():
    pred = copy.deepcopy(PELNOMOCNICTWO)
    pred["pelnomocnicy"] = [
        {"imie_nazwisko": "Aleksandra Gajewicz", "pesel": "85120133547"},
        {"imie_nazwisko": "Wymyślona Osoba", "pesel": None},
        {"imie_nazwisko": "Druga Wymyślona", "pesel": None},
    ]
    counts = total(PELNOMOCNICTWO, pred)
    # Bartoszewicz sparowana z jedną z wymyślonych osób (FP+FN), druga wymyślona osoba to sam FP.
    assert (counts.fp, counts.fn) == (2, 1)


def test_negative_document_with_filled_prediction_gives_false_positives_only():
    counts = total({}, PRZELEW)
    assert (counts.tp, counts.fp, counts.fn) == (0, 5, 0)


def test_missing_prediction_gives_false_negatives_only():
    counts = total(PRZELEW, MISSING)
    assert (counts.tp, counts.fp, counts.fn) == (0, 0, 5)


def test_container_replaced_by_scalar_is_counted():
    counts = total(PELNOMOCNICTWO, {"mocodawca": "A", "pelnomocnicy": []})
    assert counts.fp == 1
    assert counts.fn == 9  # 6 wartości w mocodawcy + 3 w pełnomocnikach


def _write(path: Path, content: JsonValue | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def test_score_run_reports_statuses_types_and_sources(tmp_path: Path):
    gold, pred = tmp_path / "gold", tmp_path / "pred"
    _write(gold / "c1/a.json", PRZELEW)
    _write(pred / "c1/a.json", PRZELEW)
    _write(gold / "c1/b.json", {})
    _write(pred / "c1/b.json", "")  # plik 0-bajtowy = pusty wynik
    _write(gold / "c2/c.json", PELNOMOCNICTWO)  # brak wyniku
    _write(gold / "c2/d.json", PRZELEW)
    _write(pred / "c2/d.json", "{nie json")

    scores = score_run(gold, pred, load_doc_types(), {"c1/a.json": "digital"})
    by_path = {s.rel_path: s for s in scores}
    assert by_path["c1/a.json"].pred_type == "przelew"
    assert by_path["c1/a.json"].source == "digital"
    assert by_path["c1/b.json"].status is PredictionStatus.OK
    assert by_path["c1/b.json"].pred_type == EMPTY_TYPE
    assert by_path["c2/c.json"].status is PredictionStatus.MISSING
    assert by_path["c2/d.json"].status is PredictionStatus.UNPARSABLE

    summary = summarize(scores)
    assert summary.missing == ["c2/c.json"]
    assert summary.unparsable == ["c2/d.json"]
    assert summary.primary == summary.overall[f"{PRIMARY[0]}/{PRIMARY[1]}"]
    assert summary.primary.tp == 5
    assert summary.classification["przelew -> przelew"] == 1


@pytest.mark.parametrize(("tp", "fp", "fn", "f1"), [(0, 0, 0, 0.0), (1, 1, 0, 2 / 3), (2, 0, 2, 2 / 3)])
def test_f1_formula(tp: int, fp: int, fn: int, f1: float):
    assert Counts(tp, fp, fn).f1 == pytest.approx(f1)
