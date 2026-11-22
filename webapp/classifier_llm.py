"""LLM fallback classifier for user-registered document types.

The pattern/weight classifier (`doxforge.classify`) only recognizes types that ship with
classification patterns. A type registered from the GUI has none, so when pattern scoring finds
no class this asks the LLM to pick among the *registered* types by name + description, with an
explicit "none" so unknown/negative documents still fall through to an empty result. It runs only
as a fallback and only over registered candidates, so the built-in types are never disturbed.
"""

from pydantic import JsonValue

from doxforge.llm.ensemble import JsonCompleter

_MAX_TEXT = 6000  # classification signal is on the first pages; keep the prompt small

_SYSTEM = (
    "Jesteś klasyfikatorem polskich dokumentów prawnych zamówień publicznych. "
    "Na podstawie treści wskaż, do którego z podanych typów należy dokument. "
    'Jeśli nie pasuje do żadnego, wybierz "none".'
)


def classify_llm(text: str, candidates: list[tuple[str, str]], completer: JsonCompleter) -> str | None:
    """Name of the registered type the document matches, or None ("none"/unknown)."""
    if not candidates:
        return None
    names = [name for name, _ in candidates]
    schema: dict[str, JsonValue] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["choice"],
        "properties": {"choice": {"type": "string", "enum": [*names, "none"]}},
    }
    listing = "\n".join(f"- {name}: {description}" for name, description in candidates)
    user = f"Dostępne typy:\n{listing}\n\nTreść dokumentu:\n{text[:_MAX_TEXT]}"
    result = completer.complete_json(_SYSTEM, user, schema, "classify_registered")
    choice = result.get("choice") if isinstance(result, dict) else None
    return choice if choice in names else None
