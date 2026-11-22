"""Bind copied values to their source owner, using explicit, configurable document blocks."""

import copy
import re
from dataclasses import dataclass
from functools import partial
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, JsonValue

from doxforge.extraction.rules import Context, LabeledRule, SectionSpec, compile_pattern
from doxforge.extraction.values import ValueTypeName
from doxforge.text import fold


class RelationScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    value_field: str
    start: str  # named capture "owner" is the party name in the section heading
    end: list[str] = []


class FlagScope(BaseModel):
    """0/1 field set from a marker phrase inside one party's numbered list item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    pattern: str


class EntityScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name_field: str
    scope: str
    boundaries: list[str] = []
    identifiers: dict[str, ValueTypeName] = {}
    relations: list[RelationScope] = []
    flags: list[FlagScope] = []


_MIN_PARTIES_FOR_FLAGS = 2  # przy jednym podmiocie flaga zostaje null (x-null-when-singleton)


@dataclass(frozen=True)
class BindingChange:
    path: str
    action: str
    source_start: int
    source_end: int


def _get(value: JsonValue, path: str) -> JsonValue:
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _set(value: JsonValue, path: str, replacement: JsonValue) -> None:
    *parents, leaf = path.split(".")
    target = _get(value, ".".join(parents)) if parents else value
    if isinstance(target, dict):
        target[leaf] = replacement


def _key(value: str) -> str:
    return re.sub(r"[\W_]+", "", fold(value))


def _phrase(value: str) -> re.Pattern[str]:
    words = fold(value).split()
    return re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)")


def _entity_spans(ctx: Context, spec: EntityScope, parties: list[JsonValue]) -> dict[int, tuple[int, int]]:
    scope = ctx.span(spec.scope)
    if scope is None:
        return {}
    anchors = {}
    for index, party in enumerate(parties):
        name = _get(party, spec.name_field)
        if not isinstance(name, str) or not name.strip():
            continue
        matches = list(_phrase(name).finditer(ctx.folded, *scope))
        if len(matches) == 1:
            anchors[index] = matches[0]
    # Nested names and repeated occurrences do not establish a unique source owner.
    anchors = {
        index: match
        for index, match in anchors.items()
        if not any(
            index != other and match.start() < m.end() and m.start() < match.end()
            for other, m in anchors.items()
        )
    }
    ordered = sorted(anchors, key=lambda index: anchors[index].start())
    boundaries = sorted(
        match.start()
        for pattern in spec.boundaries
        for match in compile_pattern(pattern).finditer(ctx.folded, *scope)
    )
    spans = {}
    for offset, index in enumerate(ordered):
        anchor = anchors[index]
        previous = anchors[ordered[offset - 1]].end() if offset else scope[0]
        following = anchors[ordered[offset + 1]].start() if offset + 1 < len(ordered) else scope[1]
        start = max(
            (b for b in boundaries if previous <= b <= anchor.start()),
            default=scope[0] if not offset else anchor.start(),
        )
        end = min([following, *(b for b in boundaries if anchor.end() <= b <= following)])
        spans[index] = start, end
    return spans


def _bind_flags(ctx: Context, spec: EntityScope, parties: list[JsonValue], path: str) -> list[BindingChange]:
    """Marker in exactly one list item -> 1 for its party, 0 for the others.

    Only explicit list boundaries delimit items: a marker between two unnumbered blocks could
    belong to either neighbour, so then nothing changes.
    """
    scope = ctx.span(spec.scope)
    if scope is None or len(parties) < _MIN_PARTIES_FOR_FLAGS:
        return []
    starts = sorted(
        m.start()
        for pattern in spec.boundaries
        for m in compile_pattern(pattern).finditer(ctx.folded, *scope)
    )
    items = list(pairwise([*starts, scope[1]]))
    owner_of_item = {}
    for item, (start, end) in enumerate(items):
        anchored = [
            index
            for index, party in enumerate(parties)
            if isinstance(name := _get(party, spec.name_field), str)
            and name.strip()
            and _phrase(name).search(ctx.folded, start, end)
        ]
        if len(anchored) == 1:
            owner_of_item[item] = anchored[0]
    if len(set(owner_of_item.values())) != len(parties):
        return []
    changes = []
    for flag in spec.flags:
        pattern = compile_pattern(flag.pattern)
        marked = {owner for item, owner in owner_of_item.items() if pattern.search(ctx.folded, *items[item])}
        if len(marked) != 1:
            continue
        for index, party in enumerate(parties):
            value = int(index in marked)
            if _get(party, flag.field) != value:
                _set(party, flag.field, value)
                changes.append(BindingChange(f"{path}[{index}].{flag.field}", "set_flag", *items[0]))
    return changes


def _identifier_values(text: str, kind: ValueTypeName) -> set[str]:
    local = Context(text, {}, {})
    result = LabeledRule(kind="labeled", labels=[kind], type=kind, window=30, all=True).extract(local)
    return {value for value in result if isinstance(value, str)} if isinstance(result, list) else set()


def _bind_identifiers(
    ctx: Context, spec: EntityScope, parties: list[JsonValue], path: str
) -> list[BindingChange]:
    spans = _entity_spans(ctx, spec, parties)
    changes = []
    for field, kind in spec.identifiers.items():
        observed = {
            index: _identifier_values(ctx.text[start:end], kind) for index, (start, end) in spans.items()
        }
        for index, values in observed.items():
            party = parties[index]
            candidate = _get(party, field)
            if isinstance(candidate, str) and candidate in values:
                continue
            # Only a span with exactly one identifier of this kind owns it: a shared block of
            # several identifiers placed after the list of names does not tell which is whose.
            foreign = isinstance(candidate, str) and any(
                other == {candidate} for j, other in observed.items() if j != index
            )
            # Absence alone is not negative evidence. Reject only a value positively owned
            # by another anchored party; preserve explicitly shared identifiers.
            if candidate is not None and foreign:
                _set(party, field, None)
                changes.append(BindingChange(f"{path}[{index}].{field}", "reject_foreign", *spans[index]))
                candidate = None
            if candidate is None and len(values) == 1:
                _set(party, field, next(iter(values)))
                changes.append(BindingChange(f"{path}[{index}].{field}", "copy_owned", *spans[index]))
    return changes


def _relation_blocks(
    ctx: Context, parties: list[JsonValue], spec: EntityScope, relation: RelationScope
) -> list[tuple[int, int, int]]:
    headings = list(compile_pattern(relation.start).finditer(ctx.folded))
    names: dict[str, list[int]] = {}
    for index, party in enumerate(parties):
        name = _get(party, spec.name_field)
        if isinstance(name, str):
            names.setdefault(_key(name), []).append(index)
    blocks = []
    for index, heading in enumerate(headings):
        owner = _key(heading["owner"].strip(" :\t"))
        owners = names.get(owner, [])
        if len(owners) != 1:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(ctx.text)
        for pattern in relation.end:
            if stop := compile_pattern(pattern).search(ctx.folded, start, end):
                end = min(end, stop.start())
        blocks.append((owners[0], start, end))
    return blocks


def _holds(party: JsonValue, relation: RelationScope, key: str) -> bool:
    members = _get(party, relation.field)
    return isinstance(members, list) and any(
        isinstance(name := _get(member, relation.value_field), str) and _key(name) == key
        for member in members
    )


def _bind_relation(
    ctx: Context, spec: EntityScope, parties: list[JsonValue], relation: RelationScope, path: str
) -> list[BindingChange]:
    blocks = _relation_blocks(ctx, parties, spec, relation)
    headed = {index for index, _, _ in blocks}
    candidates: dict[str, tuple[str, JsonValue]] = {}
    for party in parties:
        members = _get(party, relation.field)
        if not isinstance(members, list):
            continue
        for member in members:
            name = _get(member, relation.value_field)
            if isinstance(name, str) and name.strip():
                candidates.setdefault(_key(name), (name, member))
    changes = []
    for key, (name, member) in candidates.items():
        pattern = _phrase(name)
        owners = {
            index: (start, end) for index, start, end in blocks if pattern.search(ctx.folded, start, end)
        }
        if not owners:
            continue
        # A member listed under a party without a heading block may simply sit after the last
        # heading (blocks run to the end of the text); that is not evidence to copy it elsewhere.
        held_unheaded = any(
            index not in headed and _holds(party, relation, key) for index, party in enumerate(parties)
        )
        # A genuine shared signatory can belong to several parties. Unknown relations
        # are kept; a positive heading is required before changing an association.
        for index, party in enumerate(parties):
            members = _get(party, relation.field)
            if not isinstance(members, list):
                continue
            present = _holds(party, relation, key)
            # Remove only from a party that has its own heading block: without one there is no
            # positive evidence that the member is not also (or only) its signatory.
            if present and index not in owners and index in headed:
                _set(
                    party,
                    relation.field,
                    [
                        m
                        for m in members
                        if not (isinstance(n := _get(m, relation.value_field), str) and _key(n) == key)
                    ],
                )
                changes.append(
                    BindingChange(
                        f"{path}[{index}].{relation.field}",
                        "reject_foreign_relation",
                        *next(iter(owners.values())),
                    )
                )
            elif not present and index in owners and not held_unheaded:
                _set(party, relation.field, [*members, copy.deepcopy(member)])
                changes.append(
                    BindingChange(f"{path}[{index}].{relation.field}", "copy_owned_relation", *owners[index])
                )
    # Source order, not the order supplied by the model, is the stable order within a block.
    for index, start, end in blocks:
        members = _get(parties[index], relation.field)
        if isinstance(members, list):
            position = partial(_member_position, ctx.folded, relation.value_field, (start, end))
            _set(parties[index], relation.field, sorted(members, key=position))
    return changes


def _member_position(text: str, field: str, span: tuple[int, int], member: JsonValue) -> int:
    name = _get(member, field)
    match = _phrase(name).search(text, *span) if isinstance(name, str) else None
    return match.start() if match else span[1]


def bind_entities(
    text: str, value: JsonValue, specs: dict[str, EntityScope], sections: dict[str, SectionSpec]
) -> tuple[JsonValue, list[BindingChange]]:
    """A pure postprocessor: no labels, document IDs, model calls or mutable cross-document state."""
    result = copy.deepcopy(value)
    changes = []
    ctx = Context(text, sections, {})
    for path, spec in specs.items():
        parties = _get(result, path)
        if not isinstance(parties, list):
            continue
        changes.extend(_bind_identifiers(ctx, spec, parties, path))
        changes.extend(_bind_flags(ctx, spec, parties, path))
        for relation in spec.relations:
            changes.extend(_bind_relation(ctx, spec, parties, relation, path))
    return result, changes
