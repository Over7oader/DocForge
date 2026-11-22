"""Pisownia identyfikatora z OCR wybierana większością jego powtórzeń w dokumencie.

Numer postępowania stoi w części „dotyczy” i w stopce każdej strony. OCR czasem czyta I/l jak 1
albo O jak 0 („D1.529.2026” zamiast „DI.529.2026”), ale rzadko w każdym wystąpieniu tak samo.
Dla pól oznaczonych w schemacie `x-spelling: majority` wybieramy najczęstszą pisownię spośród
wystąpień różniących się tylko takimi znakami. Nie tworzymy nowej pisowni: zawsze jest to
dosłowny token z tekstu.
"""

import re
from collections import Counter

from pydantic import JsonValue

SPELLING_MODES = frozenset({"majority"})
_LOOKALIKES = str.maketrans({"I": "1", "l": "1", "|": "1", "O": "0"})
_TOKEN = re.compile(r"[\w./\-|]+")


def _canonical(token: str) -> str:
    return token.translate(_LOOKALIKES)


def _majority(text: str, value: str) -> str:
    target = _canonical(value)
    counts = Counter(
        token for match in _TOKEN.finditer(text) if _canonical(token := match[0].rstrip(".-/")) == target
    )
    if not counts:
        return value
    best, best_count = counts.most_common(1)[0]
    return best if best_count > counts.get(value, 0) else value


def vote_spelling(text: str, schema: JsonValue, value: JsonValue) -> JsonValue:
    """Kopia wyniku z poprawioną pisownią pól `x-spelling: majority`."""
    if not isinstance(schema, dict):
        return value
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(value, dict):
        return {key: vote_spelling(text, properties.get(key), child) for key, child in value.items()}
    items = schema.get("items")
    if items is not None and isinstance(value, list):
        return [vote_spelling(text, items, child) for child in value]
    if schema.get("x-spelling") == "majority" and isinstance(value, str) and value:
        return _majority(text, value)
    return value
