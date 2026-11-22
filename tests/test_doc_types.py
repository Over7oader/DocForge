from pathlib import Path

import pytest

from doxforge import settings
from doxforge.doc_types import classify_result, load_doc_types
from doxforge.evaluation.scorer import leaf_pairs

SOURCES = {"text", "normalized", "derived"}


def test_configured_classes():
    assert set(load_doc_types()) == {"odwolanie", "pelnomocnictwo", "przelew"}


def test_reason_vocabulary_covers_ids_1_to_12():
    vocabulary = load_doc_types()["odwolanie"].config.vocabularies["czynnosc_powod"]
    assert sorted(vocabulary) == list(range(1, 13))


def _template(schema: object) -> object:
    """Przykładowy wynik z samymi nullami, zbudowany ze schematu (listy z jednym elementem)."""
    assert isinstance(schema, dict)
    if "properties" in schema:
        return {key: _template(child) for key, child in schema["properties"].items()}
    if "items" in schema:
        return [_template(schema["items"])]
    return None


@pytest.mark.parametrize("name", ["odwolanie", "pelnomocnictwo", "przelew"])
def test_every_leaf_field_declares_its_source(name: str):
    doc_type = load_doc_types()[name]
    leaves = {path for path, _, _ in leaf_pairs(_template(doc_type.config.output_schema), None)}
    assert set(doc_type.field_sources) == leaves
    assert set(doc_type.field_sources.values()) <= SOURCES


def test_empty_result_is_not_classified():
    assert classify_result(settings.EMPTY_RESULT, load_doc_types()) is None


def test_result_matching_no_class_is_rejected():
    with pytest.raises(ValueError, match="matches 0 document classes"):
        classify_result({"cos": 1}, load_doc_types())


def test_duplicate_class_name_fails_at_startup(tmp_path: Path):
    for file_name in ("a.yaml", "b.yaml"):
        (tmp_path / file_name).write_text(
            "name: dup\ndescription: x\nschema: {type: object}\n"
            "classification: {min_score: 1, rules: [{pattern: x, weight: 1}]}\n",
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="Duplicate document class 'dup'"):
        load_doc_types(tmp_path)


def test_invalid_schema_fails_at_startup(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        "name: a\ndescription: x\nschema: {type: 5}\n"
        "classification: {min_score: 1, rules: [{pattern: x, weight: 1}]}\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="is not valid"):
        load_doc_types(tmp_path)


def test_extraction_rule_for_unknown_field_fails_at_startup(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        "name: a\ndescription: x\n"
        "schema: {type: object, required: [x], properties: {x: {type: string, x-source: text}}}\n"
        "classification: {min_score: 1, rules: [{pattern: x, weight: 1}]}\n"
        "extraction: {fields: {y: {kind: first, type: nip}}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown field 'y'"):
        load_doc_types(tmp_path)
