from pydantic import TypeAdapter

from doxforge.extraction.constraints import enforce_constraints
from doxforge.extraction.engine import ExtractionConfig, build_result, extract_values
from doxforge.extraction.rules import Context, FirstMatchRule, Rule, SectionSpec

RULE = TypeAdapter(Rule)

APPEAL = """Gdańsk, dnia 1 sierpnia 2026 roku
Odwołujący:
Opalit Serwis sp. k.
KRS: 9996902468, NIP: 999-225-17-14, REGON: 999950475
reprezentowana przez: adw. Patrycja Bartoszewicz, r. pr. Jan
Nowak
adres do korespondencji: Kancelaria Prawna i Partnerzy, ul. Błotna 75, 60-339 Poznań
tel. 000 000 000, biuro@kontakt.example
Zamawiający:
Opalit Wyposażenie S.A.
NIP: 9997564636
dotyczy: postępowania pn. „Dostawa
oprogramowania” nr referencyjny sprawy: ZP.621.2026, BZP: 2026/BZP 00999107
ODWOŁANIE
wnoszę odwołanie wobec czynności Zamawiającego polegających na:
1) zaniechaniu odrzucenia oferty wykonawcy X, o którym poinformowano w dniu 27 października 2026 r.;
2) wyborze oferty najkorzystniejszej;
"""

SECTIONS = {
    "odwolujacy": SectionSpec(start=[r"^\s*odwo.ujac\w*\s*:"], end=[r"^\s*zamawiaj"]),
    "zamawiajacy": SectionSpec(start=[r"^\s*zamawiaj\w*\s*:"], end=[r"^\s*dotyczy"]),
    "czynnosc": SectionSpec(start=[r"polegajac\w* na[^:\n]{0,120}:\s*(?:1[.)])"], end=[r"\n\s*2[.)]"]),
    "brak": SectionSpec(start=["nie ma takiego tekstu"]),
}


def run(rule: dict[str, object], text: str = APPEAL) -> object:
    return RULE.validate_python(rule).extract(
        Context(text, SECTIONS, {"powod": {7: "Zaniechanie odrzucenia"}})
    )


def test_labeled_typed_value_in_section():
    assert run({"kind": "labeled", "labels": ["nip"], "type": "nip", "scope": "odwolujacy"}) == "9992251714"
    assert run({"kind": "labeled", "labels": ["nip"], "type": "nip", "scope": "zamawiajacy"}) == "9997564636"


def test_missing_section_gives_null():
    assert run({"kind": "labeled", "labels": ["nip"], "type": "nip", "scope": "brak"}) is None


def test_labeled_text_with_cut():
    rule = {
        "kind": "labeled",
        "labels": ["adres do korespond"],
        "type": "text",
        "cut": [r",\s*(ul|al)\."],
    }
    assert run(rule) == "Kancelaria Prawna i Partnerzy"


def test_labeled_text_continuation_until_stop_line():
    text = "Tytułem\nopłata skarbowa dla Jan\nNowak ZP.1.2026\nStatus: wykonany\n"
    rule = {"kind": "labeled", "labels": ["tytulem"], "type": "text", "continuation": ["status"]}
    assert run(rule, text) == "opłata skarbowa dla Jan Nowak ZP.1.2026"


def test_labeled_skips_signature_dots():
    text = "Mocodawca:\n.............\nJan Nowak\n"
    assert run({"kind": "labeled", "labels": ["mocodawca"], "type": "text"}, text) == "Jan Nowak"


def test_labeled_all_returns_value_per_label():
    text = "KRS 0000111111, NIP 1111111111; KRS 0000222222, NIP 2222222222"
    rule = {"kind": "labeled", "labels": ["krs"], "type": "krs", "window": 20, "all": True}
    assert run(rule, text) == ["0000111111", "0000222222"]


def test_first_occurrence_and_all():
    text = "PL65 1632 9461 5868 4543 6827 7984 i PL70 1279 3670 3217 9463 5614 8898"
    assert run({"kind": "first", "type": "iban", "occurrence": 2}, text) == "PL70127936703217946356148898"
    assert run({"kind": "first", "type": "iban", "occurrence": 3}, text) is None


def test_regex_header_place():
    rule = {
        "kind": "regex",
        "regex": r"^\s*(?P<value>[a-z][a-z\- ]{1,40}?),?\s+(?:dnia\s+)?\d{1,2}\s+\w+\s+\d{4}",
        "type": "text",
    }
    assert run(rule) == "Gdańsk"  # dopasowanie na tekście złożonym, wartość z oryginału


def test_first_line_quoted_and_email():
    assert run({"kind": "first_line", "scope": "odwolujacy"}) == "Opalit Serwis sp. k."
    assert run({"kind": "quoted", "labels": [r"pn\."]}) == "Dostawa oprogramowania"
    assert run({"kind": "first", "type": "email", "scope": "odwolujacy"}) == "biuro@kontakt.example"


def test_persons_strip_titles_and_join_broken_lines():
    assert run({"kind": "persons", "scope": "odwolujacy"}) == ["Patrycja Bartoszewicz", "Jan Nowak"]
    assert run({"kind": "persons", "first": True, "scope": "odwolujacy"}) == "Patrycja Bartoszewicz"


def test_persons_uppercase_and_organization_words():
    text = "Kancelaria Adwokacka adw. Mateusz Wierzbicki\nADW. MATEUSZ WIERZBICKI"
    assert run({"kind": "persons"}, text) == ["Mateusz Wierzbicki"]


def test_signatories_below_dotted_line():
    text = (
        "Mocodawca:\n..........\nZa Firma sp. z o.o.\nHalina Mazurkiewicz\nPrezes\n"
        "..........\nr. pr. Jan Nowak\n"
    )
    assert run({"kind": "signatories"}, text) == ["Halina Mazurkiewicz"]


def test_first_match_earliest_vs_first_case():
    cases = [{"pattern": "wzor\\w* umowy", "value": 12}, {"pattern": "swz", "value": 4}]
    text = "tresci SWZ w zakresie warunkow i wzoru umowy"
    assert run({"kind": "first_match", "cases": cases}, text) == 12
    assert run({"kind": "first_match", "cases": cases, "pick": "earliest"}, text) == 4


def test_flag_and_vocabulary():
    flag = {"kind": "flag", "pattern": "reprezentowan", "present": 1, "absent": 0, "scope": "odwolujacy"}
    assert run(flag) == 1
    config = ExtractionConfig.model_validate(
        {
            "sections": SECTIONS,
            "fields": {
                "id": {
                    "kind": "first_match",
                    "scope": "czynnosc",
                    "cases": [{"pattern": "odrzuc", "value": 7}],
                },
                "opis": {"kind": "vocabulary", "vocabulary": "powod", "key_field": "id"},
                "dzien": {"kind": "first", "type": "date", "scope": "czynnosc"},
            },
        }
    )
    ctx = extract_values(APPEAL, config, {"powod": {7: "Zaniechanie odrzucenia"}})
    assert ctx.values == {"id": 7, "opis": "Zaniechanie odrzucenia", "dzien": "2026-10-27"}


def test_fallback_chain_uses_first_non_null_rule():
    config = ExtractionConfig.model_validate(
        {
            "fields": {
                "x": [
                    {"kind": "labeled", "labels": ["nie ma"], "type": "nip"},
                    {"kind": "first", "type": "nip"},
                ]
            }
        }
    )
    assert extract_values("NIP 9997564636", config, {}).values == {"x": "9997564636"}


SCHEMA = {
    "type": "object",
    "properties": {
        "mocodawca": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nazwa": {"type": ["string", "null"]},
                    "podpisujacy": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"imie": {"type": ["string", "null"]}}},
                    },
                },
            },
        },
        "pelnomocnicy": {
            "type": "array",
            "items": {"type": "object", "properties": {"imie": {"type": ["string", "null"]}}},
        },
    },
}


def test_build_result_list_lengths_follow_list_values():
    values = {
        "mocodawca[].nazwa": ["A", "B"],
        "mocodawca[].podpisujacy[].imie": ["Jan Nowak"],
        "pelnomocnicy[].imie": ["X Y", "Z W"],
    }
    assert build_result(SCHEMA, values) == {
        "mocodawca": [
            {"nazwa": "A", "podpisujacy": [{"imie": "Jan Nowak"}]},
            {"nazwa": "B", "podpisujacy": [{"imie": "Jan Nowak"}]},
        ],
        "pelnomocnicy": [{"imie": "X Y"}, {"imie": "Z W"}],
    }


def test_build_result_defaults_to_one_element_of_nulls():
    assert build_result(SCHEMA, {}) == {
        "mocodawca": [{"nazwa": None, "podpisujacy": [{"imie": None}]}],
        "pelnomocnicy": [{"imie": None}],
    }


def test_labeled_table_uses_column_instead_of_first_number():
    text = "Kwota   Data operacji   Numer referencyjny transakcji\n8 250,00 PLN   04.06.2024   REF240604910\n"
    assert run({"kind": "labeled", "labels": ["numer referencyjny"], "type": "token"}, text) == "REF240604910"
    assert run({"kind": "labeled", "labels": ["data operacji"], "type": "date"}, text) == "2024-06-04"
    assert run({"kind": "labeled", "labels": ["kwota"], "type": "amount"}, text) == 8250.0


def test_table_column_order_and_tabs():
    text = "Numer operacji\tKwota\tData wykonania\nREF-24-64\t19,50 PLN\t12.05.2024"
    assert run({"kind": "labeled", "labels": ["numer operacji"], "type": "token"}, text) == "REF-24-64"


def test_table_missing_value_does_not_borrow_neighbor_number():
    text = "Data operacji   Numer transakcji\n12.05.2024   brak\n"
    assert run({"kind": "labeled", "labels": ["numer transakcji"], "type": "token"}, text) is None


def test_inline_value_precedes_table_interpretation():
    text = "Kwota   28 000,00 PLN\nTryb realizacji   zwykły\nData wykonania   03.04.2024\nStatus   wykonano"
    assert run({"kind": "labeled", "labels": ["kwota"], "type": "amount"}, text) == 28000.0
    assert run({"kind": "labeled", "labels": ["data wykonania"], "type": "date"}, text) == "2024-04-03"


def test_keyword_rules_ignore_where_a_template_breaks_the_line():
    rule = FirstMatchRule.model_validate(
        {
            "kind": "first_match",
            "pick": "earliest",
            "cases": [
                {"pattern": "opis\\w* przedmiotu zamowienia", "value": 11},
                {"pattern": "specyfikac\\w* warunkow zamowienia", "value": 4},
            ],
        }
    )
    # SWZ stoi pierwsze, ale złamane między liniami; OPZ dalej w jednej linii.
    text = "Zamawiający opublikował Specyfikację Warunków\nZamówienia. Wg Opisu Przedmiotu Zamówienia"
    assert rule.extract(Context(text, {}, {})) == 4


def test_null_unless_constraint_clears_person_of_a_legal_entity():
    schema = {
        "type": "object",
        "properties": {
            "typ": {"enum": ["osoba_fizyczna", "osoba_prawna"]},
            "imie_nazwisko": {
                "type": ["string", "null"],
                "x-null-unless": {"field": "typ", "value": "osoba_fizyczna"},
            },
        },
    }
    assert (
        enforce_constraints(schema, {"typ": "osoba_prawna", "imie_nazwisko": "Jan Gaj"})["imie_nazwisko"]
        is None
    )
    assert (
        enforce_constraints(schema, {"typ": "osoba_fizyczna", "imie_nazwisko": "Jan Gaj"})["imie_nazwisko"]
        == "Jan Gaj"
    )
