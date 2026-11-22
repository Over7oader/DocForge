"""Derive per-field provenance by searching each extracted value in the page text.

Nothing is stored by the pipeline: for each leaf of the result JSON we fold the value and
each page's text and search. `doxforge.text.fold` preserves length and positions, so a match
index points at the same place in the original page text — the span highlights there directly.
Values whose schema `x-source` is not `text` (normalized/derived) get a neutral "derived" badge
instead of a (possibly misleading) highlight; null/empty/not-found leaves are "absent".
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from doxforge.text import fold

Status = Literal["source", "derived", "absent"]

_INDEX = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class Provenance:
    status: Status
    page: int | None = None
    span: tuple[int, int] | None = None


def concrete_to_schema(path: str) -> str:
    """`Odwolujacy[0].pelnomocnik.mail` -> `Odwolujacy[].pelnomocnik.mail` (schema field key)."""
    return _INDEX.sub("[]", path)


def leaves(node: JsonValue, path: str = "") -> list[tuple[str, JsonValue]]:
    """Every scalar leaf as (concrete path, value), e.g. `Odwolujacy[0].nip`."""
    if isinstance(node, dict):
        return [lf for key, child in node.items() for lf in leaves(child, f"{path}.{key}" if path else key)]
    if isinstance(node, list):
        return [lf for i, child in enumerate(node) for lf in leaves(child, f"{path}[{i}]")]
    return [(path, node)]


def _find(value: str, pages: list[str]) -> tuple[int, tuple[int, int]] | None:
    """First page and span where `value` occurs (OCR/diacritic-insensitive via fold)."""
    needle = fold(value)
    for page, text in enumerate(pages):
        index = fold(text).find(needle)
        if index >= 0:
            return page, (index, index + len(value))  # fold preserves length
    return None


def locate(result: JsonValue, field_sources: dict[str, str], pages: list[str]) -> dict[str, Provenance]:
    """Map each concrete leaf path (`Odwolujacy[0].nip`) to where its value came from."""
    found: dict[str, Provenance] = {}
    for path, value in leaves(result):
        source = field_sources.get(concrete_to_schema(path))
        if not isinstance(value, str) or not value.strip():
            found[path] = Provenance("absent")
        elif source not in ("text", None):  # normalized / derived: no literal span to show
            found[path] = Provenance("derived")
        else:
            hit = _find(value, pages)
            if hit is not None:
                found[path] = Provenance("source", hit[0], hit[1])
            else:
                found[path] = Provenance("absent" if source == "text" else "derived")
    return found
