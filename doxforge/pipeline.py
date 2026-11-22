"""Przetwarzanie katalogu wejściowego: każdy PDF -> plik JSON o tej samej ścieżce w katalogu wyjściowym."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import JsonValue, ValidationError

from doxforge import settings
from doxforge.classify import classify
from doxforge.doc_types import DocType
from doxforge.extraction.engine import apply_vocabularies, build_result, extract_values
from doxforge.extraction.ownership import bind_entities
from doxforge.extraction.spelling import vote_spelling
from doxforge.ingest import Document, load_document
from doxforge.llm.ensemble import JsonCompleter
from doxforge.llm.evidence import SourceEvidence
from doxforge.llm.extract import LLMConfig, build_prompt, llm_schema, merge_results
from doxforge.ocr import OCRPace

logger = logging.getLogger(__name__)


def _llm_result(
    client: JsonCompleter, doc_type: DocType, llm_config: LLMConfig, text: str, rule_result: JsonValue
) -> JsonValue:
    """Odpowiedź modelu albo None, gdy zapytanie się nie udało (zostaje wynik reguł)."""
    config = doc_type.config
    header = f"Rodzaj dokumentu: {config.name}. {config.description}"
    system, user = build_prompt(llm_config, header, config.vocabularies, text, rule_result)
    try:
        return client.complete_json(system, user, llm_schema(config.output_schema), config.name)
    except (httpx.HTTPError, ValidationError, json.JSONDecodeError):
        # Awaria LLM nie przekreśla dokumentu: zapisujemy wynik reguł, a przyczynę w logu.
        logger.exception("%s: LLM extraction failed, using rules only", config.name)
        return None


@dataclass
class Prepared:
    kind: str
    document: Document
    result: JsonValue
    labels: dict[str, list[str]]
    selected_entities: dict[str, str]


def prepare(pdf: Path, doc_types: dict[str, DocType], pace: OCRPace | None = None) -> Prepared | None:
    document = load_document(pdf, pace)
    doc_class = classify(document, {name: t.config.classification for name, t in doc_types.items()})
    if doc_class is None:
        logger.info("%s: no document class", pdf.name)
        return None
    doc_type = doc_types[doc_class]
    ctx = extract_values(document.text, doc_type.config.extraction, doc_type.config.vocabularies)
    result = build_result(doc_type.config.output_schema, ctx.values)
    return Prepared(doc_class, document, result, ctx.text_labels, ctx.selected_entities)


def finish(prepared: Prepared, doc_type: DocType, client: JsonCompleter | None) -> JsonValue:
    document, result = prepared.document, prepared.result
    llm_config = doc_type.config.llm
    if client is not None and llm_config is not None:
        llm_result = _llm_result(client, doc_type, llm_config, document.text, result)
        if llm_result is not None:
            result = merge_results(
                doc_type.config.output_schema,
                llm_config,
                document.text,
                result,
                llm_result,
                evidence=SourceEvidence(
                    document.text, document.digital_text, prepared.labels, prepared.selected_entities
                ),
            )
            apply_vocabularies(result, doc_type.config.extraction, doc_type.config.vocabularies)
    result = constrain_result(prepared, doc_type, result)
    for error in doc_type.validation_errors(result):
        logger.warning("Result does not match schema of %s: %s", doc_type.name, error)
    return result


def constrain_result(prepared: Prepared, doc_type: DocType, result: JsonValue) -> JsonValue:
    config = doc_type.config.extraction
    result = vote_spelling(prepared.document.text, doc_type.config.output_schema, result)
    result, changes = bind_entities(prepared.document.text, result, config.entities, config.sections)
    if changes:
        logger.info("Source ownership: %d corrections in %s", len(changes), doc_type.name)
    return result


def process_document(pdf: Path, doc_types: dict[str, DocType], client: JsonCompleter | None) -> JsonValue:
    prepared = prepare(pdf, doc_types)
    if prepared is None:
        return settings.EMPTY_RESULT
    result = finish(prepared, doc_types[prepared.kind], client)
    logger.info("%s: %s", pdf.name, prepared.kind)
    return result


def _process_to_file(
    pdf: Path, target: Path, doc_types: dict[str, DocType], client: JsonCompleter | None
) -> None:
    try:
        result = process_document(pdf, doc_types, client)
    except Exception:
        # Błąd jednego dokumentu (uszkodzony PDF, błąd OCR) nie może zatrzymać całej partii:
        # zapisujemy pusty wynik, a pełny traceback trafia do logu.
        logger.exception("%s: processing failed, writing empty result", pdf)
        result = settings.EMPTY_RESULT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(
    input_dir: Path, output_dir: Path, doc_types: dict[str, DocType], client: JsonCompleter | None = None
) -> int:
    """Przetwarza wszystkie PDF-y z `input_dir` (rekurencyjnie, równolegle) i zwraca ich liczbę."""
    pdfs = sorted(path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
    with ThreadPoolExecutor(max_workers=settings.PIPELINE_WORKERS) as executor:
        futures = [
            executor.submit(
                _process_to_file,
                pdf,
                output_dir / pdf.relative_to(input_dir).with_suffix(".json"),
                doc_types,
                client,
            )
            for pdf in pdfs
        ]
        for future in futures:
            future.result()
    logger.info("Wrote %d JSON files to %s", len(pdfs), output_dir)
    return len(pdfs)
