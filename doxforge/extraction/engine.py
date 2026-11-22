"""Złożenie wyniku: reguły z konfiguracji klasy -> wartości pól -> JSON zgodny ze schematem klasy."""

import logging

from pydantic import BaseModel, ConfigDict, JsonValue

from doxforge.extraction.constraints import enforce_constraints
from doxforge.extraction.ownership import EntityScope
from doxforge.extraction.rules import Context, LabeledRule, Rule, SectionSpec, VocabularyRule
from doxforge.extraction.values import VALUE_TYPES_BY_NAME

logger = logging.getLogger(__name__)


class ExtractionConfig(BaseModel):
    """Sekcja `extraction` pliku klasy: nazwane sekcje tekstu i reguła dla wybranych pól.

    Klucz pola to ścieżka jak w ewaluacji, np. `Odwolujacy[].nip`. Reguły liczą się w kolejności
    z pliku, więc reguła `vocabulary` może użyć pola policzonego wcześniej. Pole bez reguły ma null.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sections: dict[str, SectionSpec] = {}
    fields: dict[str, Rule | list[Rule]] = {}  # lista: reguły zapasowe, pierwsza niepusta wygrywa
    entities: dict[str, EntityScope] = {}

    def rules(self) -> list[tuple[str, Rule]]:
        return [
            (path, rule)
            for path, rules in self.fields.items()
            for rule in (rules if isinstance(rules, list) else [rules])
        ]


def extract_values(text: str, config: ExtractionConfig, vocabularies: dict[str, dict[int, str]]) -> Context:
    ctx = Context(text=text, sections=config.sections, vocabularies=vocabularies)
    for path, rules in config.fields.items():
        value: JsonValue = None
        for rule in rules if isinstance(rules, list) else [rules]:
            value = rule.extract(ctx)
            if value is not None:
                if isinstance(rule, LabeledRule) and rule.type == "text":
                    ctx.text_labels[path] = rule.labels
                if rule.select_entity and isinstance(value, str):
                    ctx.selected_entities[path] = value
                logger.debug("%s = %r (%s)", path, value, rule.kind)
                break
        ctx.values[path] = value
    return ctx


def _innermost_array(path: str) -> str | None:
    return path[: path.rindex("[]") + 2] if "[]" in path else None


def build_result(schema: JsonValue, values: dict[str, JsonValue]) -> JsonValue:
    """Drzewo wyniku według schematu; liście bez wartości to null.

    Lista ma tyle elementów, ile najdłuższa lista wartości pól należących bezpośrednio do niej
    (np. `pelnomocnicy[].imie_nazwisko` z regułą `persons`); domyślnie jeden element.
    """

    def build(node: JsonValue, path: str, indexes: dict[str, int]) -> JsonValue:
        if not isinstance(node, dict):
            raise ValueError(f"Schema node at {path!r} is not an object")
        properties = node.get("properties")
        if isinstance(properties, dict):
            return {
                key: build(child, f"{path}.{key}" if path else key, indexes)
                for key, child in properties.items()
            }
        items = node.get("items")
        if items is not None:
            array = f"{path}[]"
            lengths = [
                len(value)
                for field_path, value in values.items()
                if isinstance(value, list) and _innermost_array(field_path) == array
            ]
            return [build(items, array, indexes | {array: i}) for i in range(max(lengths, default=1))]
        value = values.get(path)
        array_path = _innermost_array(path)
        if isinstance(value, list) and array_path is not None:
            index = indexes.get(array_path, 0)
            value = value[index] if index < len(value) else None
        value_type = node.get("x-type")
        if value is not None and isinstance(value_type, str):
            return VALUE_TYPES_BY_NAME[value_type].normalize(str(value))
        return value

    return enforce_constraints(schema, build(schema, "", {}))


def _get(tree: JsonValue, path: str) -> JsonValue:
    node = tree
    for key in path.split("."):
        node = node.get(key) if isinstance(node, dict) else None
    return node


def apply_vocabularies(
    result: JsonValue, config: ExtractionConfig, vocabularies: dict[str, dict[int, str]]
) -> None:
    """Przelicza pola słownikowe (np. opis czynności) z ostatecznej wartości klucza po scaleniu z LLM.

    Dzięki temu opis zawsze odpowiada identyfikatorowi, niezależnie od tego, czy ten pochodzi z reguł,
    czy z modelu. Obsługiwane są pola poza listami.
    """
    for path, rule in config.rules():
        if not isinstance(rule, VocabularyRule) or "[]" in path:
            continue
        key = _get(result, rule.key_field)
        *parents, leaf = path.split(".")
        target = _get(result, ".".join(parents)) if parents else result
        if isinstance(target, dict):
            target[leaf] = vocabularies[rule.vocabulary].get(key) if isinstance(key, int) else None
