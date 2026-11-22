import pytest

from doxforge.classify import classify
from doxforge.doc_types import load_doc_types
from doxforge.extraction.engine import build_result, extract_values
from doxforge.ingest import Document, Page, TextSource
from doxforge.llm.evidence import SourceEvidence
from doxforge.llm.extract import LLMConfig, merge_results


@pytest.mark.parametrize(
    ("kind", "good", "bad", "source"),
    [
        ("date", "2024-03-09", "2024-03-10", "Wykonano 9 marca 2024 roku."),
        ("amount", 1520.75, 1520.57, "Rozliczono 1 520,75 zł."),
        ("ted", "123789-2024", "123798-2024", "Ogłoszenie 2024/S 42-123789."),
    ],
)
def test_normalized_value_requires_document_evidence(kind, good, bad, source):
    schema = {"type": ["number" if kind == "amount" else "string", "null"], "x-type": kind}
    assert merge_results(schema, LLMConfig(), source, None, good) == good
    assert merge_results(schema, LLMConfig(), source, None, bad) is None


def test_person_is_a_phrase():
    evidence = SourceEvidence("Janowi Malikowi doręczono odpis. Tomasz Rudnicki podpisał odbiór.")
    assert evidence.same_person("Jan Malik")
    assert not evidence.same_person("Jan Rudnicki")


def test_digital_literal_excludes_label_and_keeps_original_spelling():
    text = "Temat: Uzupełnienie umowy ZR 18A."
    rule = "Uzupełnienie umowy ZR 18A."
    schema = {"type": ["string", "null"], "x-source": "text"}
    ev = SourceEvidence(text, text, {"": ["temat"]})
    for model in [text, "Uzupełnienie umowy ZR 18B."]:
        assert merge_results(schema, LLMConfig(), text, rule, model, evidence=ev) == rule
    assert not SourceEvidence(text).prefer_literal(rule, "Uzupelnienie umowy ZR 18A.", [])


def test_title_signal_distinguishes_document_from_reference():
    configs = {n: dt.config.classification for n, dt in load_doc_types().items()}
    for text, expected in [
        ("Informacja o posiedzeniu\nStrona doręczy brakujące pełnomocnictwo.", None),
        ("PEŁNOMOCNICTWO SZCZEGÓLNE\nDo reprezentacji w postępowaniu.", "pelnomocnictwo"),
    ]:
        assert classify(Document([Page(text, TextSource.TEXT_LAYER)]), configs) == expected


@pytest.mark.parametrize(
    ("source", "model", "expected"),
    [
        ("PL05 4706 8028 7581 5397 2145 5024", "05470680287581539721455024", "PL05470680287581539721455024"),
        ("05 4706 8028 7581 5397 2145 5024", "PL05470680287581539721455024", "05470680287581539721455024"),
        ("DE89 3704 0044 0532 0130 00", "DE89370400440532013000", "DE89370400440532013000"),
        ("GB82 WEST 1234 5698 7654 32", "GB82WEST12345698765432", "GB82WEST12345698765432"),
        ("PL05 4706 8028 7581 5397 2145 5024", "PL05470680287581539721455025", None),
    ],
)
def test_account_follows_source_country_prefix(source, model, expected):
    assert SourceEvidence(source).account(model) == expected


def test_international_account_extraction_and_schema():
    dt = load_doc_types()["przelew"]
    text = (
        "Potwierdzenie przelewu\n"
        "Z rachunku: GB82 WEST 1234 5698 7654 32\n"
        "Na rachunek: DE89 3704 0044 0532 0130 00\nKwota: 20,00 PLN"
    )
    ctx = extract_values(text, dt.config.extraction, dt.config.vocabularies)
    result = build_result(dt.config.output_schema, ctx.values)
    assert result["przelew_bankowy"]["z_rachunku"] == "GB82WEST12345698765432"
    assert result["przelew_bankowy"]["na_rachunek"] == "DE89370400440532013000"
    assert dt.validation_errors(result) == []


def test_explicit_role_anchor_selects_entity_independent_of_order():
    schema = {
        "type": "object",
        "properties": {
            "parties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nazwa": {"type": ["string", "null"], "x-source": "text"}},
                },
            }
        },
    }
    text = "Lider: Pracownia Rzeczna\nPartner: Warsztat Wzgórze"
    rules = {"parties": [{"nazwa": "Pracownia Rzeczna"}]}
    model = {"parties": [{"nazwa": "Warsztat Wzgórze"}, {"nazwa": "Pracownia Rzeczna"}]}
    evidence = SourceEvidence(text, selected_entities={"parties[].nazwa": "Pracownia Rzeczna"})
    assert merge_results(schema, LLMConfig(), text, rules, model, evidence=evidence) == rules
    # Without a role contract, preserve distinct entities.
    assert len(merge_results(schema, LLMConfig(), text, rules, model)["parties"]) == 2


def test_explicit_role_anchor_falls_back_when_model_misses_leader():
    schema = {
        "type": "object",
        "properties": {
            "parties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nazwa": {"type": ["string", "null"], "x-source": "text"}},
                },
            }
        },
    }
    text = "Lider: Pracownia Rzeczna\nPartner: Warsztat Wzgórze"
    rules = {"parties": [{"nazwa": "Pracownia Rzeczna"}]}
    model = {"parties": [{"nazwa": "Warsztat Wzgórze"}]}
    evidence = SourceEvidence(text, selected_entities={"parties[].nazwa": "Pracownia Rzeczna"})
    assert merge_results(schema, LLMConfig(), text, rules, model, evidence=evidence) == rules


def test_grounded_value_in_wrong_identifier_format_is_rejected():
    schema = {"type": ["string", "null"], "pattern": r"^\d{4}/BZP\d{8}$", "x-type": "bzp", "x-source": "text"}
    text = "Ogłoszenie TED: 2025/S 155-123456"
    assert merge_results(schema, LLMConfig(), text, None, "2025/S155-123456") is None
    assert (
        merge_results(schema, LLMConfig(), "BZP: 2025/BZP00012345", None, "2025/BZP00012345")
        == "2025/BZP00012345"
    )


def test_leaf_schema_limits_apply_after_model_normalization():
    schema = {"type": ["integer", "null"], "minimum": 1, "maximum": 12, "x-source": "derived"}
    assert merge_results(schema, LLMConfig(), "czynność", None, 13) is None
    assert merge_results(schema, LLMConfig(), "czynność", None, 12) == 12


@pytest.mark.parametrize(
    ("candidate", "text", "expected"),
    [
        ("Anna Kowalska", "udzielam pełnomocnictwa Pani Annie Kowalskiej", True),
        ("Anna Kowalski", "udzielam pełnomocnictwa Pani Annie Kowalskiej", False),
        ("Jan Kowalski", "udzielam pełnomocnictwa Panu Janowi Kowalskiemu", True),
        ("Jan Kowalska", "udzielam pełnomocnictwa Panu Janowi Kowalskiemu", False),
        ("Marta Nowak", "Pani Marcie Nowak", True),  # nazwisko nieprzymiotnikowe: bez sprawdzania płci
        ("Piotr Zawadzki", "podpis: Piotr Zawadzki", True),
    ],
)
def test_same_person_keeps_gender_of_adjectival_surname(candidate, text, expected):
    assert SourceEvidence(text).same_person(candidate) is expected


@pytest.mark.parametrize(
    ("kind", "candidate", "text", "expected"),
    [
        ("date", "2026-05-12", "Kraków, dnia 12.05.2026 r.\nODWOŁANIE", True),
        ("date", "2026-03-16", "Lublin, 16 marca 2026 r.", True),
        (None, "Kraków", "Kraków, dnia 12.05.2026 r.", True),
        # Daty z treści i z tabeli chronologii nie są datą pisma.
        ("date", "2026-08-02", "unieważnieniu postępowania z dnia 2 sierpnia 2026 r.;", False),
        ("date", "2026-05-06", "Data Zdarzenie\n06.05.2026 Zaskarżona czynność Zamawiającego.", False),
        (None, "Kraków", "ul. Miodowa 36, 30-284 Kraków\nZamawiający w dniu 25 maja 2026 roku", False),
    ],
)
def test_header_line_evidence_separates_document_date_from_body_dates(kind, candidate, text, expected):
    assert SourceEvidence(text).on_header_line(kind, candidate) is expected


@pytest.mark.parametrize(
    ("candidate", "text", "expected"),
    [
        ("pkr_trans_details_20260212_974925", "Strona 1/1\npkr_trans_details_20260212_974925.pdf", True),
        ("ZP/1/2026", "https://bank.example/ZP/1/2026/print", True),
        ("CEN2609074908518", "Numer referencyjny CEN2609074908518\nplik_CEN2609074908518.pdf", False),
        ("CEN2609074908518", "Numer referencyjny CEN2609074908518", False),
        ("Biuro Rachunkowe", "Biuro Rachunkowe, www.biuro.example", False),
    ],
)
def test_values_seen_only_inside_file_names_or_urls_are_rejected(candidate, text, expected):
    assert SourceEvidence(text).only_in_file_or_url(candidate) is expected


@pytest.mark.parametrize(
    ("candidate", "text", "expected"),
    [
        (
            "Halina Przybylska",
            "Za mocodawcę:\nHalina Przybylski\nPrezes",
            "Halina Przybylski",
        ),  # forma z dokumentu
        ("Jan Kowalska", "Panu Janowi Kowalskiemu", "Jan Kowalski"),
        ("Anna Kowalska", "Pani Annie Kowalskiej", "Anna Kowalska"),  # zgodna: bez zmian
        ("Ewa Nowicka", "Jan Kowalski", None),  # innej osoby nie ma w tekście
    ],
)
def test_person_keeps_the_surname_gender_written_in_the_document(candidate, text, expected):
    assert SourceEvidence(text).person_in_source(candidate) == expected
