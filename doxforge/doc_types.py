"""Klasy dokumentów definiowane w plikach config/doc_types/*.yaml.

Nowa klasa to nowy plik YAML (nazwa, opis, słowniki, JSON Schema wyniku), bez zmian w kodzie.
"""

from functools import cached_property
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from doxforge import settings
from doxforge.classify import ClassificationConfig
from doxforge.extraction.engine import ExtractionConfig
from doxforge.extraction.rules import VocabularyRule, compile_pattern
from doxforge.extraction.spelling import SPELLING_MODES
from doxforge.extraction.values import VALUE_TYPES_BY_NAME
from doxforge.llm.extract import LLMConfig

# Dodatkowe wymaganie wobec wartości z LLM (llm/evidence.py): header-line = krótka linia „Miejscowość, data”.
_EVIDENCE_KINDS = frozenset({"header-line"})


class DocTypeConfig(BaseModel):
    """Zawartość jednego pliku config/doc_types/<klasa>.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    vocabularies: dict[str, dict[int, str]] = {}
    output_schema: dict[str, JsonValue] = Field(alias="schema")
    classification: ClassificationConfig
    extraction: ExtractionConfig = ExtractionConfig()
    llm: LLMConfig | None = None  # brak sekcji: klasa obsługiwana tylko regułami


class DocType:
    """Klasa dokumentu z walidatorem wyniku zgodnym z jej JSON Schema."""

    def __init__(self, config: DocTypeConfig) -> None:
        Draft202012Validator.check_schema(config.output_schema)
        self.config = config
        self._validator = Draft202012Validator(
            config.output_schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        self._check_extraction()

    def _check_annotations(self) -> None:
        for value_type in _schema_values(self.config.output_schema, "x-type"):
            if value_type not in VALUE_TYPES_BY_NAME:
                raise ValueError(f"Class {self.name!r}: unknown x-type {value_type!r}")
        for evidence in _schema_values(self.config.output_schema, "x-evidence"):
            if evidence not in _EVIDENCE_KINDS:
                raise ValueError(f"Class {self.name!r}: unknown x-evidence {evidence!r}")
        for condition in _schema_values(self.config.output_schema, "x-null-unless"):
            if not isinstance(condition, dict) or set(condition) != {"field", "value"}:
                raise ValueError(f"Class {self.name!r}: x-null-unless needs exactly 'field' and 'value'")
        for mode in _schema_values(self.config.output_schema, "x-spelling"):
            if mode not in SPELLING_MODES:
                raise ValueError(f"Class {self.name!r}: unknown x-spelling {mode!r}")

    def _check_extraction(self) -> None:
        """Błędy w regułach (nieznane pole, sekcja, słownik, zły regex) wychodzą przy starcie."""
        extraction = self.config.extraction
        patterns = [rule.pattern for rule in self.config.classification.rules]
        for section in extraction.sections.values():
            patterns += section.start + section.end
        for path, rule in extraction.rules():
            if path not in self.field_sources:
                raise ValueError(f"Class {self.name!r}: extraction rule for unknown field {path!r}")
            if rule.select_entity and "[]" not in path:
                raise ValueError(f"Entity selection requires an array field: {path!r}")
            if rule.scope is not None and rule.scope not in extraction.sections:
                raise ValueError(f"Class {self.name!r}: field {path!r} uses unknown section {rule.scope!r}")
            if isinstance(rule, VocabularyRule) and rule.vocabulary not in self.config.vocabularies:
                raise ValueError(
                    f"Class {self.name!r}: field {path!r} uses unknown vocabulary {rule.vocabulary!r}"
                )
            patterns += [value for key, value in rule.model_dump().items() if key in {"regex", "pattern"}]
            patterns += list(getattr(rule, "labels", []))
        for pattern in patterns:
            compile_pattern(pattern)
        self._check_ownership()
        self._check_annotations()
        if self.config.llm is not None:
            list_paths = {path[: path.rindex("[]") + 2] for path in self.field_sources if "[]" in path}
            for path in self.config.llm.policies:
                if path not in self.field_sources and path not in list_paths:
                    raise ValueError(f"Class {self.name!r}: LLM policy for unknown field {path!r}")

    def _check_ownership(self) -> None:
        extraction = self.config.extraction
        for path, spec in extraction.entities.items():
            if f"{path}[].{spec.name_field}" not in self.field_sources:
                raise ValueError(f"Class {self.name!r}: invalid entity name field for {path!r}")
            if spec.scope not in extraction.sections:
                raise ValueError(f"Class {self.name!r}: unknown entity scope {spec.scope!r}")
            for pattern in spec.boundaries:
                compile_pattern(pattern)
            for field, kind in spec.identifiers.items():
                if f"{path}[].{field}" not in self.field_sources or VALUE_TYPES_BY_NAME[kind].pattern is None:
                    raise ValueError(f"Class {self.name!r}: invalid owned identifier {path}.{field}")
            for relation in spec.relations:
                full = f"{path}[].{relation.field}[].{relation.value_field}"
                if (
                    full not in self.field_sources
                    or "owner" not in compile_pattern(relation.start).groupindex
                ):
                    raise ValueError(f"Class {self.name!r}: invalid relation scope {full}")
                for pattern in relation.end:
                    compile_pattern(pattern)
            for flag in spec.flags:
                compile_pattern(flag.pattern)
                if f"{path}[].{flag.field}" not in self.field_sources:
                    raise ValueError(f"Class {self.name!r}: invalid entity flag {path}.{flag.field}")

    @property
    def name(self) -> str:
        return self.config.name

    @cached_property
    def top_level_keys(self) -> frozenset[str]:
        required = self.config.output_schema.get("required")
        if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
            raise ValueError(f"Schema of class {self.name!r} must list top-level keys in 'required'")
        return frozenset(key for key in required if isinstance(key, str))

    @cached_property
    def field_sources(self) -> dict[str, str]:
        """Ścieżka pola (format jak w ewaluacji, np. `Odwolujacy[].nip`) -> wartość `x-source` ze schematu."""
        return _field_sources(self.config.output_schema, self.config.output_schema, "")

    def validation_errors(self, result: JsonValue) -> list[str]:
        """Opisy niezgodności wyniku ze schematem klasy (pusta lista = wynik poprawny)."""
        return [f"{error.json_path}: {error.message}" for error in self._validator.iter_errors(result)]


def _schema_values(node: JsonValue, keyword: str) -> list[JsonValue]:
    """Wszystkie wartości danego słowa kluczowego w drzewie schematu."""
    if isinstance(node, dict):
        found = [node[keyword]] if keyword in node else []
        return found + [v for child in node.values() for v in _schema_values(child, keyword)]
    if isinstance(node, list):
        return [v for child in node for v in _schema_values(child, keyword)]
    return []


def _resolve_ref(node: dict[str, JsonValue], root: dict[str, JsonValue]) -> dict[str, JsonValue]:
    ref = node.get("$ref")
    if ref is None:
        return node
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError(f"Only local $ref is supported, got {ref!r}")
    target: JsonValue = root
    for part in ref.removeprefix("#/").split("/"):
        if not isinstance(target, dict):
            raise ValueError(f"Unresolvable $ref {ref!r}")
        target = target[part]
    if not isinstance(target, dict):
        raise ValueError(f"$ref {ref!r} does not point to a schema object")
    return {**target, **{key: value for key, value in node.items() if key != "$ref"}}


def _field_sources(node: JsonValue, root: dict[str, JsonValue], path: str) -> dict[str, str]:
    if not isinstance(node, dict):
        raise ValueError(f"Schema node at {path!r} is not an object")
    node = _resolve_ref(node, root)
    source = node.get("x-source")
    if isinstance(source, str):
        return {path: source}
    sources: dict[str, str] = {}
    properties = node.get("properties")
    if isinstance(properties, dict):
        for key, child in properties.items():
            sources |= _field_sources(child, root, f"{path}.{key}" if path else key)
    items = node.get("items")
    if items is not None:
        sources |= _field_sources(items, root, f"{path}[]")
    return sources


def load_doc_types(directory: Path = settings.DOC_TYPES_DIR) -> dict[str, DocType]:
    """Wczytuje wszystkie klasy z katalogu; błędna konfiguracja przerywa start aplikacji."""
    doc_types: dict[str, DocType] = {}
    for path in sorted(directory.glob("*.yaml")):
        config = DocTypeConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if config.name in doc_types:
            raise ValueError(f"Duplicate document class {config.name!r} in {path}")
        doc_types[config.name] = DocType(config)
    if not doc_types:
        raise ValueError(f"No document classes configured in {directory}")
    return doc_types


def classify_result(result: JsonValue, doc_types: dict[str, DocType]) -> str | None:
    """Nazwa klasy, do której należy wynik (po kluczach najwyższego poziomu); `None` dla pustego wyniku.

    Klasę rozpoznajemy po strukturze, nie po pełnej walidacji: wzorce organizatora zawierają
    pojedyncze wartości spoza formatu (np. rachunek z prefiksem PL), a nadal są danej klasy.

    Raises:
        ValueError: wynik nie pasuje do żadnej klasy albo pasuje do kilku.
    """
    if result == settings.EMPTY_RESULT:
        return None
    keys = set(result) if isinstance(result, dict) else set()
    matching = [name for name, doc_type in doc_types.items() if keys == doc_type.top_level_keys]
    if len(matching) != 1:
        raise ValueError(f"Result matches {len(matching)} document classes: {matching}")
    return matching[0]
