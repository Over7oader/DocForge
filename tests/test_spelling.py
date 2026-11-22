from doxforge.doc_types import load_doc_types
from doxforge.extraction.spelling import vote_spelling

SCHEMA = {"type": "object", "properties": {"nr": {"type": ["string", "null"], "x-spelling": "majority"}}}


def test_ocr_lookalike_spelling_is_replaced_by_the_majority_of_repetitions():
    text = "nr referencyjny: D1.529.2026\nOdwołanie - postępowanie nr DI.529.2026\nstopka DI.529.2026."
    assert vote_spelling(text, SCHEMA, {"nr": "D1.529.2026"}) == {"nr": "DI.529.2026"}


def test_single_or_tied_occurrence_keeps_value_and_other_fields_are_untouched():
    assert vote_spelling("nr D1.529.2026, DI.529.2026", SCHEMA, {"nr": "D1.529.2026"}) == {
        "nr": "D1.529.2026"
    }
    other = {"type": "object", "properties": {"nr": {"type": ["string", "null"]}}}
    assert vote_spelling("DI.5 DI.5 D1.5", other, {"nr": "D1.5"}) == {"nr": "D1.5"}


def test_schema_marks_identifier_fields_for_spelling_vote():
    types = load_doc_types()
    appeal = types["odwolanie"].config.output_schema["properties"]["ZamowieniePubliczne"]["properties"]
    assert appeal["numer_postepowania"]["x-spelling"] == "majority"
