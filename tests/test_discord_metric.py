import pytest

from doxforge.evaluation.scorer import (
    DISCORD,
    FUZZY_FIELDS,
    PRIMARY,
    Counts,
    DocumentScore,
    MatchMode,
    PredictionStatus,
    score_document,
    summarize,
    values_equal,
)


@pytest.mark.parametrize("path", sorted(FUZZY_FIELDS))
def test_fuzzy_only_applies_to_declared_fields(path):
    assert values_equal("Biuro Obsługi Klienta", "Biuro Obsługi Klientów", MatchMode.DISCORD_RATIO, path)
    assert not values_equal("Biuro Obsługi Klienta", "Biuro Obsługi Klientów", MatchMode.STRICT, path)


@pytest.mark.parametrize("path", ["nip", "data", "pesel", "mocodawca[].data.nazwa", "numer_transakcji"])
def test_other_fields_still_require_exact_strings(path):
    assert not values_equal("1234567890", "1234567891", MatchMode.DISCORD_RATIO, path)


def test_discord_wrong_present_value_is_fp_only():
    scores = score_document(
        {"nip": "1234567890", "krs": "7654321098", "p": None},
        {"nip": "1234567891", "krs": None, "p": None, "extra": "x"},
    )

    def total(variant):
        result = Counts()
        for counts in scores[variant].values():
            result += counts
        return result

    assert total(DISCORD) == Counts(tp=0, fp=2, fn=1)
    assert total(PRIMARY) == Counts(tp=0, fp=2, fn=2)


def test_fuzzy_list_alignment_and_summary_use_selected_metric():
    gold = {"Odwolujacy": [{"nazwa": "Błękitna Drukarnia"}, {"nazwa": "Zielone Ogrody"}]}
    prediction = {"Odwolujacy": [{"nazwa": "Zielone Ogrody!"}, {"nazwa": "Błękitna Drukarnia!"}]}
    score = DocumentScore("a.json", "odwolanie", "odwolanie", PredictionStatus.OK, "digital")
    score.fields = score_document(gold, prediction)
    summary = summarize([score], DISCORD)
    assert summary.primary == Counts(tp=2)
    assert summary.by_type["odwolanie"] == Counts(tp=2)
    assert summary.as_json()["primary_variant"] == "ignore/discord_ratio80_proxy"
