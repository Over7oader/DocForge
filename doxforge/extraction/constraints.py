"""Local structural constraints declared in class schemas."""

from pydantic import JsonValue


def enforce_constraints(schema: JsonValue, value: JsonValue, *, singleton: bool = False) -> JsonValue:
    """Null annotated fields: in a singleton array, or when a sibling condition does not hold."""
    if not isinstance(schema, dict):
        return value
    if singleton and schema.get("x-null-when-singleton") is True:
        return None
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(value, dict):
        return {
            key: None
            if _null_unless_violated(properties.get(key), value)
            else enforce_constraints(properties.get(key), child, singleton=singleton)
            for key, child in value.items()
        }
    items = schema.get("items")
    if items is not None and isinstance(value, list):
        return [enforce_constraints(items, child, singleton=len(value) == 1) for child in value]
    return value


def _null_unless_violated(schema: JsonValue, siblings: dict[str, JsonValue]) -> bool:
    """`x-null-unless: {field: typ, value: osoba_fizyczna}`: pole ma sens tylko przy tej wartości obok."""
    if not isinstance(schema, dict):
        return False
    condition = schema.get("x-null-unless")
    if not isinstance(condition, dict):
        return False
    field = condition.get("field")
    return isinstance(field, str) and siblings.get(field) != condition.get("value")
