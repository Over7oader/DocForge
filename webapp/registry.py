"""Self-service document-type registration: write one validated config/doc_types/<name>.yaml.

Config is the single source of truth, so adding a type needs no code change — `load_doc_types`
reads the new YAML on the next run. A registered type is validated here by constructing its
`DocType` (the same checks the app runs at start), so a bad schema is rejected in the form, not
at next launch. Registered types carry a placeholder classification that never fires; the LLM
fallback classifier (`webapp.classifier_llm`) is what recognizes them. Names of GUI-registered
types are tracked in a `.registered.json` sidecar so the fallback ranks only those.
"""

import json
from pathlib import Path

import yaml
from pydantic import JsonValue

from doxforge.doc_types import DocType, DocTypeConfig
from doxforge.llm.ensemble import JsonCompleter

_REGISTERED = ".registered.json"

_DRAFT_SYSTEM = (
    "Projektujesz listę pól do ekstrakcji danych z podanego dokumentu. Dla każdego pola podaj: "
    "name (krótki klucz, małe litery i podkreślenia), type, x_source ('text' = występuje dosłownie, "
    "'normalized' = sprowadzone do formatu np. data ISO, 'derived' = wywnioskowane) oraz krótki "
    "description po polsku. Zaproponuj pola najważniejsze dla tego typu dokumentu."
)

# A strict schema the provider can fill reliably (unlike a free-form schema, which models return as
# prose/fenced text). draft_schema turns the returned field list into a house-style JSON Schema.
_FIELDS_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "type", "x_source", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["string", "integer", "number", "boolean", "array"]},
                    "x_source": {"type": "string", "enum": ["text", "normalized", "derived"]},
                    "description": {"type": "string"},
                },
            },
        }
    },
}


def placeholder_classification() -> dict[str, JsonValue]:
    """A classification config that never selects the type (LLM fallback classifies it instead)."""
    return {"min_score": 999.0, "rules": [{"pattern": "zzzz_never_match_zzzz", "weight": 0.0}]}


def registered_names(directory: Path) -> list[str]:
    sidecar = directory / _REGISTERED
    if not sidecar.is_file():
        return []
    names = json.loads(sidecar.read_text(encoding="utf-8"))
    return [name for name in names if isinstance(name, str)]


def registered_candidates(doc_types: dict[str, DocType], directory: Path) -> list[tuple[str, str]]:
    """(name, description) for each registered type currently loaded — input to the LLM fallback."""
    return [
        (name, doc_types[name].config.description)
        for name in registered_names(directory)
        if name in doc_types
    ]


def register_type(
    name: str,
    description: str,
    schema: dict[str, JsonValue],
    directory: Path,
    example_pdf: bytes | None = None,
) -> Path:
    """Validate and write `<directory>/<name>.yaml`; register the name. Raises ValueError if invalid."""
    target = directory / f"{name}.yaml"
    if target.exists():
        raise ValueError(f"Document type {name!r} already exists")
    config = {
        "name": name,
        "description": description,
        "schema": schema,
        "classification": placeholder_classification(),
    }
    try:
        DocType(DocTypeConfig.model_validate(config))  # same validation as app start
    except Exception as error:  # schema/pydantic/rule errors -> a message for the form
        raise ValueError(f"Invalid document type: {error}") from error

    directory.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    names = registered_names(directory)
    if name not in names:
        names.append(name)
        (directory / _REGISTERED).write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
    if example_pdf is not None:
        examples = directory / "examples"
        examples.mkdir(parents=True, exist_ok=True)
        (examples / f"{name}.pdf").write_bytes(example_pdf)
    return target


def draft_schema(text: str, completer: JsonCompleter) -> dict[str, JsonValue]:
    """Mode A: ask the LLM for a field list, then build a house-style JSON Schema from it."""
    result = completer.complete_json(_DRAFT_SYSTEM, text[:12000], _FIELDS_SCHEMA, "draft_schema")
    fields = result.get("fields") if isinstance(result, dict) else None
    if not isinstance(fields, list) or not fields:
        raise ValueError("Model did not propose any fields")
    properties: dict[str, JsonValue] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not isinstance(name, str):
            continue
        properties[name] = {
            "type": [field.get("type", "string"), "null"],
            "x-source": field.get("x_source", "text"),
            "description": field.get("description", ""),
        }
    if not properties:
        raise ValueError("Model did not propose any usable fields")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }
