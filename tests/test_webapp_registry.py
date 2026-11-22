import pytest

from doxforge.classify import ClassificationConfig, classify
from doxforge.doc_types import load_doc_types
from doxforge.ingest import Document, Page, TextSource
from webapp.registry import (
    draft_schema,
    placeholder_classification,
    register_type,
    registered_candidates,
    registered_names,
)

MIN_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["foo"],
    "properties": {"foo": {"type": ["string", "null"], "x-source": "text"}},
}


def test_register_and_reload_roundtrip(tmp_path):
    path = register_type("faktura", "Faktura VAT", MIN_SCHEMA, tmp_path)
    assert path.exists()
    assert registered_names(tmp_path) == ["faktura"]
    doc_types = load_doc_types(tmp_path)  # classifier/extractor see it on the next run
    assert "faktura" in doc_types
    assert doc_types["faktura"].config.description == "Faktura VAT"


def test_bad_schema_rejected_and_nothing_written(tmp_path):
    with pytest.raises(ValueError):
        register_type("bad", "x", {"type": "not-a-type"}, tmp_path)
    assert not list(tmp_path.glob("*.yaml"))
    assert registered_names(tmp_path) == []


def test_invalid_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        register_type("Faktura VAT", "x", MIN_SCHEMA, tmp_path)
    assert not list(tmp_path.glob("*.yaml"))


def test_name_collision_rejected(tmp_path):
    register_type("faktura", "a", MIN_SCHEMA, tmp_path)
    with pytest.raises(ValueError):
        register_type("faktura", "b", MIN_SCHEMA, tmp_path)


def test_example_pdf_saved(tmp_path):
    register_type("faktura", "FV", MIN_SCHEMA, tmp_path, example_pdf=b"%PDF-1.4 fake")
    assert (tmp_path / "examples" / "faktura.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_registered_candidates_pairs_name_and_description(tmp_path):
    register_type("faktura", "Faktura VAT", MIN_SCHEMA, tmp_path)
    doc_types = load_doc_types(tmp_path)
    assert registered_candidates(doc_types, tmp_path) == [("faktura", "Faktura VAT")]


def test_placeholder_classification_never_selects():
    config = ClassificationConfig.model_validate(placeholder_classification())
    doc = Document([Page("faktura vat na kwote 100 zl", TextSource.TEXT_LAYER)])
    assert classify(doc, {"faktura": config}) is None


class _StubDraft:
    def complete_json(self, system, user, schema, schema_name, sampling=None):
        return {
            "fields": [
                {"name": "kwota", "type": "string", "x_source": "text", "description": "Kwota faktury"},
                {"name": "data", "type": "string", "x_source": "normalized", "description": "Data ISO"},
            ]
        }

    def close(self):
        pass


def test_draft_schema_builds_house_style_schema(tmp_path):
    out = draft_schema("Faktura ... kwota 100 zł", _StubDraft())
    assert out["type"] == "object"
    assert set(out["required"]) == {"kwota", "data"}
    assert out["properties"]["kwota"]["x-source"] == "text"
    assert out["properties"]["kwota"]["type"] == ["string", "null"]
    assert out["properties"]["data"]["x-source"] == "normalized"
    # the drafted schema must itself be registrable (valid for DocType validation)
    register_type("faktura_ai", "Faktura", out, tmp_path)
