from doxforge.doc_types import DocType, DocTypeConfig
from doxforge.ingest import Document, Page, TextSource
from webapp.registry import placeholder_classification, register_type
from webapp.run import CaseResult, assemble, run_pdf


def _doc_type(name="t"):
    cfg = {
        "name": name,
        "description": "d",
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["Odwolujacy"],
            "properties": {
                "Odwolujacy": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["nip"],
                        "properties": {"nip": {"type": ["string", "null"], "x-source": "text"}},
                    },
                }
            },
        },
        "classification": placeholder_classification(),
    }
    return DocType(DocTypeConfig.model_validate(cfg))


class _FakeClient:
    def __init__(self, answers=None, choice=None):
        self.answers = answers or []
        self.choice = choice
        self.closed = False

    @property
    def member_count(self):
        return len(self.answers)

    def complete_json(self, system, user, schema, schema_name, sampling=None):
        return {"choice": self.choice}

    def close(self):
        self.closed = True


def test_assemble_wires_provenance_and_confidence():
    doc_types = {"t": _doc_type()}
    result = {"Odwolujacy": [{"nip": "1234567890"}]}
    pages = ["Odwołujący\nNIP 1234567890"]
    out = assemble(result, "t", doc_types, pages, [result, result])
    assert isinstance(out, CaseResult)
    assert out.provenance["Odwolujacy[0].nip"].status == "source"
    assert out.confidence["Odwolujacy[0].nip"] == "full"
    assert out.member_count == 2


def test_assemble_flags_rules_sourced_field():
    # Members returned null for nip but the rule engine filled it -> honest "rules", not false green.
    doc_types = {"t": _doc_type()}
    result = {"Odwolujacy": [{"nip": "1234567890"}]}
    nulls = {"Odwolujacy": [{"nip": None}]}
    out = assemble(result, "t", doc_types, ["NIP 1234567890"], [nulls, nulls])
    assert out.confidence["Odwolujacy[0].nip"] == "rules"


def test_assemble_unclassified_is_empty():
    out = assemble({}, None, {}, ["x"], [])
    assert out.doc_class is None and out.provenance == {} and out.confidence == {}


def test_assemble_without_answers_has_no_confidence():
    doc_types = {"t": _doc_type()}
    result = {"Odwolujacy": [{"nip": "1234567890"}]}
    out = assemble(result, "t", doc_types, ["NIP 1234567890"], [])
    assert out.confidence == {} and out.member_count == 0
    assert out.provenance["Odwolujacy[0].nip"].status == "source"


def _only_page(text):
    return Document([Page(text, TextSource.TEXT_LAYER)])


def test_run_pdf_falls_back_to_llm_classifier(tmp_path, monkeypatch):
    register_type("t", "Formularz testowy", _doc_type().config.output_schema, tmp_path)
    doc_types = {"t": _doc_type()}
    monkeypatch.setattr("webapp.run.load_document", lambda pdf, pace=None: _only_page("NIP 1234567890"))
    monkeypatch.setattr("webapp.run.classify", lambda document, configs: None)  # pattern finds nothing
    client = _FakeClient(choice="t")  # LLM picks the registered type
    out = run_pdf(tmp_path / "x.pdf", doc_types, tmp_path, client)
    assert out.doc_class == "t"


def test_run_pdf_registered_type_has_no_bogus_confidence(tmp_path, monkeypatch):
    # A registered type extracts with rules only (llm=None); the classify-fallback answers on the
    # client must not leak into confidence/member_count.
    register_type("t", "Formularz testowy", _doc_type().config.output_schema, tmp_path)
    doc_types = {"t": _doc_type()}
    monkeypatch.setattr("webapp.run.load_document", lambda pdf, pace=None: _only_page("NIP 1234567890"))
    monkeypatch.setattr("webapp.run.classify", lambda document, configs: None)
    out = run_pdf(tmp_path / "x.pdf", doc_types, tmp_path, _FakeClient(choice="t"))
    assert out.doc_class == "t"
    assert out.confidence == {} and out.member_count == 0


def test_run_pdf_unknown_stays_empty(tmp_path, monkeypatch):
    doc_types = {"t": _doc_type()}
    monkeypatch.setattr("webapp.run.load_document", lambda pdf, pace=None: _only_page("cos"))
    monkeypatch.setattr("webapp.run.classify", lambda document, configs: None)
    out = run_pdf(tmp_path / "x.pdf", doc_types, tmp_path, _FakeClient(choice="none"))
    assert out.doc_class is None and out.result == {}
