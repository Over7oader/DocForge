import copy

import pytest

from doxforge.doc_types import DocType, load_doc_types
from doxforge.extraction.ownership import EntityScope, bind_entities
from doxforge.extraction.rules import SectionSpec


def spec(**changes):
    return EntityScope.model_validate(
        {
            "name_field": "name",
            "scope": "parties",
            "identifiers": {"krs": "krs"},
            "boundaries": [r"^\s*\d+[.)]\s+"],
            **changes,
        }
    )


SECTION = {"parties": SectionSpec(start=[r"^zamawiajacy:"], end=[r"^koniec"], max_chars=2000)}


def test_identifier_belongs_to_source_party_not_model_order():
    text = "Zamawiający:\n1) Biuro Źródlane\nNIP: 1112223344\n2) Warsztat Północny\nKRS: 0000456789\nKoniec"
    value = {
        "parties": [
            {"name": "Warsztat Północny", "krs": "0000456789"},
            {"name": "Biuro Źródlane", "krs": "0000456789"},
        ]
    }
    original = copy.deepcopy(value)
    result, changes = bind_entities(text, value, {"parties": spec()}, SECTION)
    assert result["parties"][0]["krs"] == "0000456789"
    assert result["parties"][1]["krs"] is None
    assert value == original
    assert [change.action for change in changes] == ["reject_foreign"]


def test_shared_identifier_is_preserved_when_explicit_for_both_parties():
    text = "Zamawiający:\nBiuro Alfa\nKRS: 0000456789\nBiuro Beta\nKRS: 0000456789\nKoniec"
    value = {"parties": [{"name": name, "krs": "0000456789"} for name in ["Biuro Beta", "Biuro Alfa"]]}
    result, _ = bind_entities(text, value, {"parties": spec()}, SECTION)
    assert result == value


def test_missing_identifier_is_copied_only_from_its_own_labeled_block():
    text = "Zamawiający:\nBiuro Alfa\nKRS: 0000111122\nBiuro Beta\nKRS: 0000333344\nKoniec"
    value = {"parties": [{"name": name, "krs": None} for name in ["Biuro Beta", "Biuro Alfa"]]}
    result, _ = bind_entities(text, value, {"parties": spec()}, SECTION)
    assert [p["krs"] for p in result["parties"]] == ["0000333344", "0000111122"]


@pytest.mark.parametrize(
    "text",
    [
        "Zamawiający:\nBiuro Alfa\nBiuro Alfa\nKoniec",
        "Nieczytelna sekcja dokumentu",
    ],
)
def test_unknown_or_ambiguous_scope_does_not_erase_candidate(text):
    value = {"parties": [{"name": "Biuro Alfa", "krs": "0000111122"}]}
    result, changes = bind_entities(text, value, {"parties": spec()}, SECTION)
    assert result == value
    assert changes == []


def test_nested_company_names_are_not_treated_as_unique_anchors():
    text = "Zamawiający:\nBiuro Alfa sp. z o.o.\nKRS: 0000111122\nKoniec"
    value = {"parties": [{"name": name, "krs": None} for name in ["Biuro Alfa", "Biuro Alfa sp. z o.o."]]}
    assert bind_entities(text, value, {"parties": spec()}, SECTION)[0] == value


def test_signatures_are_reassigned_by_explicit_heading_without_gold_names():
    relation = {
        "field": "signers",
        "value_field": "person",
        "start": r"^w imieniu (?P<owner>[^\n]+):$",
        "end": [r"^pelnomocnictwo przyjmuje"],
    }
    contract = spec(identifiers={}, relations=[relation])
    text = (
        "W imieniu Biuro Alfa:\n..........\nJulia Żak\nPrezes\n"
        "W imieniu Biuro Beta:\n..........\nŁukasz Gaj\nCzłonek zarządu\n"
        "..........\nAnna Cicha\nProkurent\n"
        "Pełnomocnictwo przyjmuję:\nTomasz Las\n"
    )
    value = {
        "parties": [
            {"name": "Biuro Beta", "signers": [{"person": "Łukasz Gaj"}]},
            {"name": "Biuro Alfa", "signers": [{"person": "Julia Żak"}, {"person": "Anna Cicha"}]},
        ]
    }
    result, changes = bind_entities(text, value, {"parties": contract}, SECTION)
    assert [m["person"] for m in result["parties"][0]["signers"]] == ["Łukasz Gaj", "Anna Cicha"]
    assert [m["person"] for m in result["parties"][1]["signers"]] == ["Julia Żak"]
    assert len(changes) == 2


def test_person_explicitly_signing_for_two_parties_is_kept_for_both():
    contract = spec(
        identifiers={},
        relations=[
            {
                "field": "signers",
                "value_field": "person",
                "start": r"^w imieniu (?P<owner>[^\n]+):$",
            }
        ],
    )
    text = "W imieniu Firma A:\nJan Gaj\nW imieniu Firma B:\nJan Gaj"
    value = {
        "parties": [{"name": name, "signers": [{"person": "Jan Gaj"}]} for name in ["Firma A", "Firma B"]]
    }
    assert bind_entities(text, value, {"parties": contract}, SECTION)[0] == value


def test_unknown_signature_heading_still_bounds_previous_owner():
    contract = spec(
        identifiers={},
        relations=[
            {
                "field": "signers",
                "value_field": "person",
                "start": r"^w imieniu (?P<owner>[^\n]+):$",
            }
        ],
    )
    text = "W imieniu Firma A:\nJan Gaj\nW imieniu Firma X:\nAnna Lis"
    value = {"parties": [{"name": "Firma A", "signers": [{"person": "Jan Gaj"}, {"person": "Anna Lis"}]}]}
    assert bind_entities(text, value, {"parties": contract}, SECTION)[0] == value


def test_ownership_configuration_is_checked_on_startup():
    config = load_doc_types()["pelnomocnictwo"].config
    entity = config.extraction.entities["mocodawca"].model_copy(update={"scope": "missing"})
    extraction = config.extraction.model_copy(update={"entities": {"mocodawca": entity}})
    with pytest.raises(ValueError, match="unknown entity scope"):
        DocType(config.model_copy(update={"extraction": extraction}))


def test_signer_of_party_without_heading_is_not_moved_to_last_heading_block():
    # Blok „W imieniu Alfa” sięga do końca tekstu, a podpis Beta nie ma nagłówka w tym formacie.
    contract = spec(
        identifiers={},
        relations=[{"field": "signers", "value_field": "person", "start": r"^w imieniu (?P<owner>[^\n]+):$"}],
    )
    text = "W imieniu Alfa sp. z o.o.:\nJan Nowak\nZa Beta S.A.\nAnna Wiśniewska"
    value = {
        "parties": [
            {"name": "Alfa sp. z o.o.", "signers": [{"person": "Jan Nowak"}]},
            {"name": "Beta S.A.", "signers": [{"person": "Anna Wiśniewska"}]},
        ]
    }
    result, changes = bind_entities(text, value, {"parties": contract}, SECTION)
    assert result == value
    assert changes == []


def test_identifiers_listed_after_all_names_are_not_rejected_as_foreign():
    text = (
        "Zamawiający:\n1) Alfa sp. z o.o., ul. Polna 1\n2) Beta S.A., ul. Leśna 2\n"
        "KRS: 0000111111, KRS: 0000222222\nKoniec"
    )
    value = {
        "parties": [
            {"name": "Alfa sp. z o.o.", "krs": "0000111111"},
            {"name": "Beta S.A.", "krs": "0000222222"},
        ]
    }
    result, changes = bind_entities(text, value, {"parties": spec()}, SECTION)
    assert result == value
    assert changes == []


FLAG = {"flags": [{"field": "lead", "pattern": r"prowadzac\w* postepowani"}]}


def test_marker_after_first_numbered_party_belongs_to_that_party():
    # Dopisek stoi pod blokiem 1), tuż przed 2): należy do punktu 1, nie do sąsiada.
    text = (
        "Zamawiający:\n1) Zarząd Dróg w Brzozowcu\nNIP: 999-239-80-73\n"
        "- Zamawiający prowadzący postępowanie\n2) Zakład Komunalny w Zielonkowie\nREGON: 999806152\nKoniec"
    )
    value = {
        "parties": [
            {"name": "Zarząd Dróg w Brzozowcu", "lead": 0},
            {"name": "Zakład Komunalny w Zielonkowie", "lead": 1},
        ]
    }
    result, _ = bind_entities(text, value, {"parties": spec(identifiers={}, **FLAG)}, SECTION)
    assert [p["lead"] for p in result["parties"]] == [1, 0]


@pytest.mark.parametrize(
    "text",
    [
        # Bez numeracji dopisek mógłby należeć do sąsiada: nic nie zmieniamy.
        "Zamawiający:\nZarząd Dróg w Brzozowcu\n- prowadzący postępowanie\n"
        "Zakład Komunalny w Zielonkowie\nKoniec",
        # Dopisek przy obu punktach: niejednoznaczne.
        "Zamawiający:\n1) Zarząd Dróg w Brzozowcu, prowadzący postępowanie\n"
        "2) Zakład Komunalny w Zielonkowie, prowadzący postępowanie\nKoniec",
    ],
)
def test_ambiguous_flag_marker_keeps_model_values(text):
    value = {
        "parties": [
            {"name": "Zarząd Dróg w Brzozowcu", "lead": 0},
            {"name": "Zakład Komunalny w Zielonkowie", "lead": 1},
        ]
    }
    result, _ = bind_entities(text, value, {"parties": spec(identifiers={}, **FLAG)}, SECTION)
    assert result == value
