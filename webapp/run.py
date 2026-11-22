"""Run one PDF through the existing pipeline and attach provenance + confidence.

Reuses `doxforge` pieces (`load_document`, `classify`, `extract_values`/`build_result`, `finish`)
without modifying them. When the pattern classifier finds no class and an LLM client is available,
it falls back to `classify_llm` over the GUI-registered types. `assemble` is the pure part — given
the result, pages and a client whose `.answers` are already populated — so it is unit-testable
without a PDF.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import JsonValue, ValidationError

from doxforge import settings
from doxforge.classify import classify
from doxforge.doc_types import DocType
from doxforge.extraction.engine import build_result, extract_values
from doxforge.ingest import load_document
from doxforge.pipeline import Prepared, finish
from webapp.classifier_llm import classify_llm
from webapp.confidence import Level, agreement
from webapp.provenance import Provenance, locate
from webapp.recording_client import RecordingClient
from webapp.registry import registered_candidates

_LLM_FAILURES = (httpx.HTTPError, ValidationError, json.JSONDecodeError)


@dataclass(frozen=True)
class CaseResult:
    result: JsonValue
    doc_class: str | None
    pages: list[str]
    provenance: dict[str, Provenance]
    confidence: dict[str, Level]
    member_count: int


def assemble(
    result: JsonValue,
    doc_class: str | None,
    doc_types: dict[str, DocType],
    pages: list[str],
    answers: list[JsonValue],
) -> CaseResult:
    """Build a CaseResult from an already-extracted result and the ensemble members' answers."""
    if doc_class is None or doc_class not in doc_types:
        return CaseResult(result, None, pages, {}, {}, 0)
    config = doc_types[doc_class].config
    provenance = locate(result, doc_types[doc_class].field_sources, pages)
    confidence = agreement(config.output_schema, result, answers) if answers else {}
    return CaseResult(result, doc_class, pages, provenance, confidence, len(answers))


def run_pdf(
    pdf: Path, doc_types: dict[str, DocType], directory: Path, client: RecordingClient | None
) -> CaseResult:
    """Classify, extract (rules + optional LLM), then attach provenance and confidence."""
    document = load_document(pdf)
    pages = [page.text for page in document.pages]
    doc_class = classify(document, {name: t.config.classification for name, t in doc_types.items()})
    if doc_class is None and client is not None:
        try:
            doc_class = classify_llm(document.text, registered_candidates(doc_types, directory), client)
        except _LLM_FAILURES:
            doc_class = None  # a failed fallback classify leaves the document unclassified
    if doc_class is None or doc_class not in doc_types:
        return CaseResult(settings.EMPTY_RESULT, None, pages, {}, {}, 0)

    doc_type = doc_types[doc_class]
    ctx = extract_values(document.text, doc_type.config.extraction, doc_type.config.vocabularies)
    result = build_result(doc_type.config.output_schema, ctx.values)
    prepared = Prepared(doc_class, document, result, ctx.text_labels, ctx.selected_entities)
    result = finish(prepared, doc_type, client)
    # Only the extraction LLM's answers are ensemble votes; a rules-only type (llm=None) has none,
    # and any classify-fallback answers on the client are not extraction votes.
    answers = list(client.answers) if client is not None and doc_type.config.llm is not None else []
    return assemble(result, doc_class, doc_types, pages, answers)
