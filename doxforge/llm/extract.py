"""Ekstrakcja LLM i scalanie z wynikiem reguł.

Przebieg dla dokumentu klasy, która ma sekcję `llm` w konfiguracji:

1. Model dostaje tekst dokumentu, opis klasy, instrukcje z YAML, schemat wyniku (z opisami pól)
   i wartości wstępne z reguł. Zwraca pełny JSON klasy (wymuszony JSON Schema).
2. Każda wartość z modelu jest normalizowana typem pola (`x-type` w schemacie), sprawdzana
   z typem/enum schematu, a pole tekstowe (`x-source: text`) musi dać się znaleźć w dokumencie.
   Wartość, która nie przejdzie sprawdzenia, jest odrzucana (model nie może niczego dopisać).
3. Scalanie pole po polu według `merge`: `rules` (reguła ma pierwszeństwo, LLM uzupełnia braki)
   albo `llm` (LLM ma pierwszeństwo, reguła jest zapasem). Polityka dla ścieżki listy
   (np. `pelnomocnicy[]`) dotyczy całej listy.
"""

import json
import re
from functools import lru_cache
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from rapidfuzz import fuzz

from doxforge.extraction.constraints import enforce_constraints
from doxforge.extraction.values import VALUE_TYPES_BY_NAME
from doxforge.llm.evidence import NORMALIZED, SourceEvidence
from doxforge.text import fold

type MergePolicy = Literal["rules", "llm"]

# Dopasowanie rozmyte (0-100) wartości tekstowej do treści dokumentu: margines na błędy OCR.
_MIN_TEXT_RATIO = 90
_IDENTIFIER_TYPES = frozenset({"nip", "regon", "krs", "pesel", "iban", "email", "bzp"})

_SYSTEM_PROMPT = """Jesteś systemem ekstrakcji danych z polskich dokumentów urzędowych dotyczących \
zamówień publicznych. Wypełniasz JSON zgodny ze schematem na podstawie treści JEDNEGO dokumentu.

Zasady:
- Bierz wartości wyłącznie z treści dokumentu. Jeśli informacji nie ma albo nie masz pewności, wpisz null.
  Zła wartość jest gorsza niż null.
- Teksty (nazwy, tytuły, tytuł przelewu) przepisuj dokładnie tak jak w dokumencie, łącznie z wielkością
  liter i interpunkcją. Łącz tekst złamany między liniami pojedynczą spacją.
- Tekst mógł przejść przez OCR: popraw tylko oczywiste błędy polskich znaków
  (np. „Odwotujacy” -> „Odwołujący”, „Poznan” -> „Poznań”), niczego więcej nie zmieniaj.
- Imiona i nazwiska osób podawaj w mianowniku, bez tytułów (adw., r. pr., radca prawny, Pan/Pani).
- Formaty: daty RRRR-MM-DD; NIP, REGON, KRS i PESEL jako cyfry; kwoty jako liczby.
  Rachunki bez spacji, z kodem kraju tylko gdy obecny w dokumencie.
- Opisy pól w schemacie są wiążące.
"""


class LLMConfig(BaseModel):
    """Sekcja `llm` pliku klasy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: list[str] = []  # dodatkowe zasady dla tej klasy dokumentów
    max_chars: int = Field(default=15000, gt=0)  # początek dokumentu wysyłany do modelu
    default_policy: MergePolicy = "llm"
    policies: dict[str, MergePolicy] = {}  # ścieżka pola albo listy -> polityka


def llm_schema(output_schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Schemat dla modelu: rozwiązane $ref, bez słów kluczowych `x-*`, `pattern`, `format`.

    Wszystkie pola są wymagane (tryb strict), a puste wartości wyrażamy przez null.
    """
    defs = output_schema.get("$defs", {})

    def convert(node: JsonValue) -> JsonValue:
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and isinstance(defs, dict):
            target = defs[ref.removeprefix("#/$defs/")]
            node = {**target, **node} if isinstance(target, dict) else node
        converted: dict[str, JsonValue] = {}
        for key, value in node.items():
            if key.startswith("x-") or key in {"$ref", "$defs", "$schema", "pattern", "format"}:
                continue
            if key == "properties" and isinstance(value, dict):
                converted[key] = {name: convert(child) for name, child in value.items()}
                converted["required"] = list(value)
                converted["additionalProperties"] = False
            elif key == "items":
                converted[key] = convert(value)
            elif key != "required":
                converted[key] = value
        return converted

    result = convert(output_schema)
    assert isinstance(result, dict)
    return result


def build_prompt(
    config: LLMConfig, header: str, vocabularies: dict[str, dict[int, str]], text: str, rule_result: JsonValue
) -> tuple[str, str]:
    """Wiadomość systemowa i użytkownika; `header` opisuje rodzaj dokumentu."""
    system = _SYSTEM_PROMPT
    if config.instructions:
        system += "\nZasady dla tego rodzaju dokumentu:\n" + "\n".join(
            f"- {line}" for line in config.instructions
        )
    for name, vocabulary in vocabularies.items():
        system += f"\nSłownik {name}: " + "; ".join(f"{key} = {value}" for key, value in vocabulary.items())
    user = (
        f"{header}\n\n"
        "Wartości wstępne odczytane regułami (mogą być niepełne lub błędne, sprawdź je z treścią):\n"
        f"{json.dumps(rule_result, ensure_ascii=False)}\n\n"
        f"Treść dokumentu:\n<<<\n{text[: config.max_chars]}\n>>>"
    )
    return system, user


def squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", fold(text))


def is_grounded(value: str, value_type: str | None, document: str) -> bool:
    """Czy wartość tekstowa występuje w dokumencie (bez polskich znaków i spacji, z marginesem na OCR).

    Imię i nazwisko w mianowniku może w tekście stać w innym przypadku („Janowi Kowalskiemu”),
    więc dla typu `name` wystarczy, że każdy wyraz występuje w dokumencie bez ostatnich 3 liter.
    """
    squashed = squash(value)
    if not squashed:
        return False
    # Fuzzy matching of identifiers accepts a wrong digit at 90% similarity (and a
    # wrong bank account at >96%). Never repair an identifier by inventing digits.
    if value_type in _IDENTIFIER_TYPES:
        # Not a fragment of a longer number: 9 digits of a 14-digit REGON or of an account.
        return re.search(rf"(?<!\d){re.escape(squashed)}(?!\d)", document) is not None
    if squashed in document:
        return True
    if value_type == "name":
        return all(squash(word)[: max(3, len(squash(word)) - 3)] in document for word in value.split())
    match = fuzz.partial_ratio_alignment(squashed, document)
    # Tolerance is for OCR-damaged letters; a different digit makes a different case number.
    return (
        match is not None
        and match.score >= _MIN_TEXT_RATIO
        and re.sub(r"\D", "", squashed) == _digits(document[match.dest_start : match.dest_end])
    )


# Letters OCR commonly reads in place of a digit next to other digits (folded text: „271l5”).
_DIGIT_LOOKALIKE = re.compile(r"(?<=\d)[oli]|[oli](?=\d)")
_LOOKALIKE_DIGITS = {"o": "0", "l": "1", "i": "1"}


def _digits(text: str) -> str:
    return re.sub(r"\D", "", _DIGIT_LOOKALIKE.sub(lambda m: _LOOKALIKE_DIGITS[m[0]], text))


# A model often stops a name at a line break („DUDEK-TECH” / „Jarosław Dudek”). A rule value read
# from the same source that continues it by a few capitalized words (no digits) is the full name.
_MAX_COMPLETION_WORDS = 4


def _completes(rule_value: str, llm_value: str) -> bool:
    rule_folded, llm_folded = fold(rule_value), fold(llm_value)
    if len(rule_folded) <= len(llm_folded) or not rule_folded.startswith(llm_folded):
        return False
    rest = rule_value[len(llm_value) :]
    words = rest.split()
    # Każde słowo dopisku z wielkiej litery (imię, nazwisko, człon nazwy), a nie „z siedzibą w …”.
    return (
        not rest[0].isalnum()
        and 0 < len(words) <= _MAX_COMPLETION_WORDS
        and all(word[0].isupper() and not any(char.isdigit() for char in word) for word in words)
    )


# Zapisy tej samej treści: formy prawne skrótem lub w pełni, różne myślniki i cudzysłowy.
_LEGAL_FORMS = (
    (r"\bspolka\b", "sp"),
    (r"\bz ograniczona odpowiedzialnoscia\b", "z o o"),
    (r"\bsp akcyjna\b", "s a"),
    (r"\bsp komandytowa\b", "sp k"),
    (r"\bsp jawna\b", "sp j"),
    (r"\bsp partnerska\b", "sp p"),
)


def _spelling_key(value: str) -> str:
    key = " ".join(re.sub(r"[\W_]+", " ", fold(value)).split())
    for pattern, short in _LEGAL_FORMS:
        key = re.sub(pattern, short, key)
    return key.replace(" ", "")


class _Merger:
    def __init__(self, config: LLMConfig, evidence: SourceEvidence) -> None:
        self.config = config
        self.document = re.sub(r"[\W_]+", "", evidence.folded)
        self.evidence = evidence

    def policy(self, path: str) -> MergePolicy:
        return self.config.policies.get(path, self.config.default_policy)

    def checked(self, node: dict[str, JsonValue], value: JsonValue) -> JsonValue:
        """Wartość z modelu po normalizacji i sprawdzeniu albo None."""
        value_type = node.get("x-type")
        if value is not None and isinstance(value_type, str):
            value = VALUE_TYPES_BY_NAME[value_type].normalize(str(value))  # nazwy sprawdzone przy wczytaniu
        if value is None or not _matches_schema_type(node, value):
            return None
        kind = value_type if isinstance(value_type, str) else None
        if node.get("x-evidence") == "header-line" and not (
            isinstance(value, str | float) and self.evidence.on_header_line(kind, value)
        ):
            return None
        if kind == "iban" and isinstance(value, str):
            return self.evidence.account(value)
        if kind == "name" and isinstance(value, str):
            return self.evidence.person_in_source(value)
        return value if self._supported(node, kind, value) else None

    def _supported(self, node: dict[str, JsonValue], kind: str | None, value: JsonValue) -> bool:
        if kind in NORMALIZED and isinstance(value, str | float):
            return self.evidence.supports(kind, value)
        if node.get("x-source") == "text" and isinstance(value, str):
            return is_grounded(value, kind, self.document) and not self.evidence.only_in_file_or_url(value)
        return True

    def merge(self, node: JsonValue, rules: JsonValue, llm: JsonValue, path: str) -> JsonValue:
        if not isinstance(node, dict):
            raise ValueError(f"Schema node at {path!r} is not an object")
        properties = node.get("properties")
        if isinstance(properties, dict):
            rules_dict = rules if isinstance(rules, dict) else {}
            llm_dict = llm if isinstance(llm, dict) else {}
            return {
                key: self.merge(
                    child, rules_dict.get(key), llm_dict.get(key), f"{path}.{key}" if path else key
                )
                for key, child in properties.items()
            }
        items = node.get("items")
        if items is not None:
            return self._merge_list(items, rules, llm, f"{path}[]")
        rule_value = rules
        llm_value = self.checked(node, llm)
        if self.policy(path) == "rules":
            return rule_value if rule_value is not None else llm_value
        if (
            isinstance(rule_value, str)
            and isinstance(llm_value, str)
            and self._prefer_rule(node, path, rule_value, llm_value)
        ):
            return rule_value
        return llm_value if llm_value is not None else rule_value

    def _prefer_rule(self, node: dict[str, JsonValue], path: str, rule_value: str, llm_value: str) -> bool:
        """Wartość z reguły zamiast z LLM, gdy obie mówią to samo, a reguła ma zapis ze źródła."""
        text_field = node.get("x-source") == "text" and node.get("x-type") in {None, "organization"}
        if text_field and self.evidence.prefer_literal(
            rule_value, llm_value, self.evidence.labels.get(path, [])
        ):
            return True
        if node.get("x-type") == "organization" and _completes(rule_value, llm_value):
            return True
        # Model przepisał zapis tej samej wartości („Spółka z o.o.”, inny myślnik): pole
        # x-source: text ma mieć zapis dosłowny z dokumentu.
        return (
            text_field
            and self.evidence.verbatim(rule_value)
            and not self.evidence.verbatim(llm_value)
            and _spelling_key(rule_value) == _spelling_key(llm_value)
        )

    def _merge_list(self, items: JsonValue, rules: JsonValue, llm: JsonValue, path: str) -> JsonValue:
        rule_list = rules if isinstance(rules, list) else []
        llm_list = llm if isinstance(llm, list) else []
        anchors = {
            key.removeprefix(path + "."): value
            for key, value in self.evidence.selected_entities.items()
            if key.startswith(path + ".") and "[]" not in key.removeprefix(path + ".")
        }
        if anchors:
            chosen = [item for item in llm_list if _matches_selected(item, anchors)]
            if len(chosen) == 1:
                llm_list = chosen
            else:
                fallback = [item for item in rule_list if _matches_selected(item, anchors)]
                if len(fallback) == 1:
                    llm_list = []
            rule_list = [item for item in rule_list if _matches_selected(item, anchors)] or rule_list
        if len(rule_list) == len(llm_list) == 1:
            return [self.merge(items, rule_list[0], llm_list[0], path)]
        # Equal list lengths do not establish identity. Reversed parties used to
        # receive each other's NIP/KRS when a field preferred rules.
        matches = _entity_matches(items, rule_list, llm_list)
        if self.policy(path) == "rules" and rule_list:
            inverse = {r: m for m, r in matches.items()}
            return [
                self.merge(items, rule, llm_list[inverse[i]] if i in inverse else None, path)
                for i, rule in enumerate(rule_list)
            ]
        if self.policy(path) == "llm":
            kept = [
                merged
                for i, element in enumerate(llm_list)
                if _has_value(
                    merged := self.merge(
                        items, rule_list[matches[i]] if i in matches else None, element, path
                    )
                )
            ]
            if kept:
                return kept
        return rule_list


def _matches_selected(item: JsonValue, anchors: dict[str, str]) -> bool:
    for key, expected in anchors.items():
        value = item
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if not isinstance(value, str) or squash(value) != squash(expected):
            return False
    return True


def _identity(node: JsonValue, value: JsonValue, path: str = "") -> set[tuple[str, str]]:
    """Exact entity anchors; never use position, dates, flags or gold labels."""
    if not isinstance(node, dict):
        return set()
    properties = node.get("properties")
    if isinstance(properties, dict) and isinstance(value, dict):
        anchors: set[tuple[str, str]] = set()
        for key, child in properties.items():
            # Representatives are relationships, not the party's own identity.
            # The `data` wrapper is used for a principal's own business fields.
            if isinstance(child, dict) and "properties" in child and key != "data":
                continue
            anchors |= _identity(child, value.get(key), f"{path}.{key}")
        return anchors
    if (
        isinstance(value, str)
        and value
        and (node.get("x-type") in {"nip", "regon", "krs", "pesel", "name"} or path.endswith(".nazwa"))
    ):
        return {(path, squash(value))}
    return set()


def _entity_matches(items: JsonValue, rules: list[JsonValue], llm: list[JsonValue]) -> dict[int, int]:
    rule_ids = [_identity(items, item) for item in rules]
    matches = {}
    for i, item in enumerate(llm):
        anchors = _identity(items, item)
        scores = [len(anchors & other) for other in rule_ids]
        best = max(scores, default=0)
        if best and scores.count(best) == 1:
            matches[i] = scores.index(best)
    # An ambiguous many-to-one match must not clone the rule's identifiers.
    return {i: r for i, r in matches.items() if list(matches.values()).count(r) == 1}


@lru_cache(maxsize=256)
def _leaf_validator(schema: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema), format_checker=Draft202012Validator.FORMAT_CHECKER)


def _matches_schema_type(node: dict[str, JsonValue], value: JsonValue) -> bool:
    return _leaf_validator(json.dumps(node, sort_keys=True)).is_valid(value)


def _has_value(node: JsonValue) -> bool:
    if isinstance(node, dict):
        return any(_has_value(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_value(value) for value in node)
    return node is not None


def merge_results(  # noqa: PLR0913 - zachowaj publiczne wywołanie z opcjonalnymi dowodami
    output_schema: dict[str, JsonValue],
    config: LLMConfig,
    document: str,
    rule_result: JsonValue,
    llm_result: JsonValue,
    *,
    evidence: SourceEvidence | None = None,
) -> JsonValue:
    """Wynik końcowy: reguły i LLM scalone według polityk z konfiguracji klasy."""
    schema = _resolve_refs(output_schema)
    result = _Merger(config, evidence or SourceEvidence(document)).merge(schema, rule_result, llm_result, "")
    return enforce_constraints(schema, result)


def _resolve_refs(schema: dict[str, JsonValue]) -> JsonValue:
    defs = schema.get("$defs", {})

    def resolve(node: JsonValue) -> JsonValue:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and isinstance(defs, dict):
            target = defs[ref.removeprefix("#/$defs/")]
            if isinstance(target, dict):
                node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return {key: resolve(value) for key, value in node.items() if key != "$defs"}

    return resolve(schema)
