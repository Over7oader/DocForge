"""Per-field ensemble-agreement confidence, aligned to the final result.

`EnsembleClient` votes then discards the member answers; `webapp.recording_client` keeps them, and
this module turns them into a confidence level per JSON path. The walk is driven by the *result*
(not by any one member), so every level is keyed to a path the viewer actually shows, and array
entities are matched to the members' entities on a key field (NIP/KRS, then fuzzy name) before
voting — order and count differences between members and the final result don't misattribute a
value. A field whose final value is not the ensemble's majority (including values the rule engine
filled while members stayed null) is labelled "rules", so a rules-sourced value never shows a
false "full". With fewer than two members there is no signal.
"""

from collections import Counter
from typing import Literal

from pydantic import JsonValue
from rapidfuzz import fuzz

from doxforge.llm.ensemble import _key
from doxforge.text import fold

# "rules" = the final value did not come from the ensemble majority (rule-filled or overridden).
Level = Literal["full", "partial", "split", "none", "rules"]

_NAME_MATCH = 85.0  # rapidfuzz ratio to treat two entity names as the same entity
_MIN_MEMBERS = 2  # fewer answers than this = no agreement signal
_MAJORITY = 0.5  # winning share above this is a majority (not a split)
_UNANIMOUS = 1.0


def _entity_key(elem: JsonValue) -> str:
    if not isinstance(elem, dict):
        return fold(str(elem))
    for field in ("nip", "krs", "regon"):
        if elem.get(field):
            return f"{field}:{fold(str(elem[field]))}"
    for field in ("nazwa", "name"):
        if elem.get(field):
            return "n:" + fold(str(elem[field]))
    return ""


def _match(items: list[JsonValue], key: str) -> JsonValue:
    """The element of `items` that is the same entity as `key`, or None."""
    for elem in items:
        if _entity_key(elem) == key:
            return elem
    if key.startswith("n:"):
        target = key[2:]
        best = max(
            (elem for elem in items if _entity_key(elem).startswith("n:")),
            key=lambda e: fuzz.ratio(target, _entity_key(e)[2:]),
            default=None,
        )
        if best is not None and fuzz.ratio(target, _entity_key(best)[2:]) >= _NAME_MATCH:
            return best
    return None


def _leaf_level(result_value: JsonValue, members: list[JsonValue]) -> Level:
    if len(members) < _MIN_MEMBERS:
        return "none"
    if result_value is None:
        return "none"  # nothing extracted -> no confidence claim
    counts = Counter(_key(value) for value in members)
    winner_key, winner_n = counts.most_common(1)[0]
    if _key(result_value) != winner_key:
        return "rules"  # final value is not what the ensemble agreed on
    ratio = winner_n / len(members)
    return "full" if ratio == _UNANIMOUS else "partial" if ratio > _MAJORITY else "split"


def _mark_subtree(node: JsonValue, result: JsonValue, level: Level, path: str, out: dict[str, Level]) -> None:
    """Set every result leaf under `node` to `level` (used when members disagree on array length)."""
    properties = node.get("properties") if isinstance(node, dict) else None
    if isinstance(properties, dict):
        for key, child in properties.items():
            child_result = result.get(key) if isinstance(result, dict) else None
            _mark_subtree(child, child_result, level, f"{path}.{key}", out)
        return
    items_schema = node.get("items") if isinstance(node, dict) else None
    if items_schema is not None:
        for i, elem in enumerate(result if isinstance(result, list) else []):
            _mark_subtree(items_schema, elem, level, f"{path}[{i}]", out)
        return
    out[path] = level


def _walk(
    node: JsonValue, result: JsonValue, members: list[JsonValue], path: str, out: dict[str, Level]
) -> None:
    properties = node.get("properties") if isinstance(node, dict) else None
    if isinstance(properties, dict):
        for key, child in properties.items():
            child_result = result.get(key) if isinstance(result, dict) else None
            child_members = [m.get(key) if isinstance(m, dict) else None for m in members]
            _walk(child, child_result, child_members, f"{path}.{key}" if path else key, out)
        return
    items_schema = node.get("items") if isinstance(node, dict) else None
    if items_schema is not None:
        result_list = result if isinstance(result, list) else []
        member_lists = [m if isinstance(m, list) else [] for m in members]
        count_disagreement = len({len(items) for items in member_lists}) > 1
        for i, elem in enumerate(result_list):
            child_path = f"{path}[{i}]"
            if len(members) >= _MIN_MEMBERS and count_disagreement:
                _mark_subtree(items_schema, elem, "split", child_path, out)
                continue
            key = _entity_key(elem)
            matched = [_match(items, key) for items in member_lists]
            _walk(items_schema, elem, matched, child_path, out)
        return
    out[path] = _leaf_level(result, members)


def agreement(schema: JsonValue, result: JsonValue, answers: list[JsonValue]) -> dict[str, Level]:
    """Confidence level per concrete result path, from the ensemble members' answers."""
    out: dict[str, Level] = {}
    _walk(schema, result, list(answers), "", out)
    return out
