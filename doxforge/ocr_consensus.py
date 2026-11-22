"""Spatial, line-level OCR voting. Select observed text; never invent characters or digits."""

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import pairwise

from rapidfuzz.distance import Levenshtein

PRIMARY = "ppocr_v6"
_AGREEMENT = 0.9
_MIN_WORDS_FOR_QUALITY = 4
_SINGLETON_ALLOWANCE = 0.35
_PRIMARY_MARGIN = 0.15
_UNSUPPORTED_MIN_SCORE = 0.85
_MIN_SCORE = 0.5
_MAX_ROW_DISTANCE = 0.65
_MIN_HORIZONTAL_OVERLAP = 0.3
_CONSENSUS_CONFIDENCE_MARGIN = 0.1
_PRIORITY = {"ppocr_v6": 2, "tesseract": 1, "ppocr_v5": 0}


@dataclass(frozen=True)
class OCRLine:
    engine: str
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass(frozen=True)
class Decision:
    selected: OCRLine
    candidates: tuple[OCRLine, ...]
    votes: int


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _agree(a: OCRLine, b: OCRLine) -> bool:
    # Similar names may differ in accents/punctuation, but a different digit is a disagreement.
    x, y = _normalized(a.text), _normalized(b.text)
    return re.findall(r"\d", x) == re.findall(r"\d", y) and (
        x == y or Levenshtein.normalized_similarity(x, y) >= _AGREEMENT
    )


def _quality(line: OCRLine) -> float:
    words = re.findall(r"[^\W\d_]+", line.text, re.UNICODE)
    singletons = sum(len(word) == 1 for word in words) / max(1, len(words))
    # Damaged OCR often emits a long row of isolated letters with deceptively high confidence.
    penalty = max(0.0, singletons - _SINGLETON_ALLOWANCE) if len(words) >= _MIN_WORDS_FOR_QUALITY else 0.0
    return line.score * (1 - penalty)


def _choose(lines: list[OCRLine]) -> Decision | None:
    votes = [sum(_agree(line, other) for other in lines) for line in lines]
    best = max(range(len(lines)), key=lambda i: (votes[i], _quality(lines[i]), lines[i].engine == PRIMARY))
    # Prefer the model with full Polish coverage when agreeing confidence estimates are close.
    if votes[best] > 1:
        peers = [
            i
            for i in range(len(lines))
            if votes[i] == votes[best]
            and (_quality(lines[i]) + _CONSENSUS_CONFIDENCE_MARGIN >= _quality(lines[best]))
        ]
        best = max(peers, key=lambda i: (_PRIORITY.get(lines[i].engine, -1), _quality(lines[i])))
    primary = next((i for i, line in enumerate(lines) if line.engine == PRIMARY), None)
    # Confidence is not calibrated across engines. With no agreement keep a credible primary
    # unless another complete observation is substantially better; do not splice alternatives.
    if (
        votes[best] == 1
        and primary is not None
        and _quality(lines[primary]) + _PRIMARY_MARGIN >= _quality(lines[best])
    ):
        best = primary
    chosen = lines[best]
    if chosen.engine != PRIMARY and votes[best] == 1 and _quality(chosen) < _UNSUPPORTED_MIN_SCORE:
        return None
    chosen = _restore_diacritics(chosen, [line for i, line in enumerate(lines) if i != best])
    return Decision(chosen, tuple(lines), votes[best])


# Polish letter -> its base letter. Models without full Polish coverage drop the diacritic
# (Stępień -> Stepień, Łódź -> Lódź) while another engine reads it on the same row.
_POLISH_BASE = dict(zip("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ", strict=True))
_RESTORE_SIMILARITY = 0.8
# E-mail addresses and URLs are ASCII by definition; never add diacritics there.
_ASCII_TOKEN = re.compile(r"\S*(?:@|://|www\.|\w\.[a-z]{2,}\b)\S*", re.IGNORECASE)


def _restore_diacritics(chosen: OCRLine, others: list[OCRLine]) -> OCRLine:
    """Add Polish diacritics that another engine observed at the aligned position; nothing else.

    Only a base letter can become its own Polish variant (e -> ę, never e -> a or a digit change),
    so the chosen reading keeps every other character and its digits.
    """
    text = list(chosen.text)
    protected = {i for m in _ASCII_TOKEN.finditer(chosen.text) for i in range(m.start(), m.end())}
    for other in others:
        if Levenshtein.normalized_similarity(_folded(chosen.text), _folded(other.text)) < _RESTORE_SIMILARITY:
            continue
        for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, chosen.text, other.text, autojunk=False
        ).get_opcodes():
            if tag != "replace" or i2 - i1 != j2 - j1:
                continue
            for offset in range(i2 - i1):
                mine, theirs = chosen.text[i1 + offset], other.text[j1 + offset]
                if i1 + offset not in protected and _POLISH_BASE.get(theirs) == mine:
                    text[i1 + offset] = theirs
    restored = "".join(text)
    return chosen if restored == chosen.text else dataclasses.replace(chosen, text=restored)


def _folded(text: str) -> str:
    return "".join(_POLISH_BASE.get(char, char) for char in _normalized(text))


def _join_fragments(fragments: list[OCRLine]) -> OCRLine:
    """Assemble disjoint cells from ONE engine before voting, retaining column spacing."""
    kept: list[OCRLine] = []
    for fragment in sorted(fragments, key=lambda item: (item.left, -len(item.text))):
        overlap = next(
            (
                i
                for i, other in enumerate(kept)
                if (
                    max(0.0, min(other.right, fragment.right) - max(other.left, fragment.left))
                    / min(other.right - other.left, fragment.right - fragment.left)
                    > _MIN_HORIZONTAL_OVERLAP
                )
            ),
            None,
        )
        if overlap is None:
            kept.append(fragment)
        elif (len(fragment.text), fragment.score) > (len(kept[overlap].text), kept[overlap].score):
            kept[overlap] = fragment
    kept.sort(key=lambda item: item.left)
    if len(kept) == 1:
        return kept[0]
    text = kept[0].text
    for previous, current in pairwise(kept):
        char_width = (previous.right - previous.left) / max(1, len(previous.text))
        text += ("   " if current.left - previous.right > 2 * char_width else " ") + current.text
    length = sum(len(fragment.text) for fragment in kept)
    return OCRLine(
        kept[0].engine,
        text,
        sum(f.score * len(f.text) for f in kept) / max(1, length),
        min(f.left for f in kept),
        min(f.top for f in kept),
        max(f.right for f in kept),
        max(f.bottom for f in kept),
    )


def _engine_votes(group: list[OCRLine]) -> list[OCRLine]:
    by_engine: dict[str, list[OCRLine]] = {}
    for fragment in group:
        by_engine.setdefault(fragment.engine, []).append(fragment)
    return [_join_fragments(fragments) for fragments in by_engine.values()]


def consensus(lines: list[OCRLine]) -> list[Decision]:
    """Align physical rows; an engine gets at most one vote per row, even if repeated."""
    groups: list[list[OCRLine]] = []
    seen: set[tuple[str, str, float, float, float, float]] = set()
    for line in sorted(lines, key=lambda item: (item.center, item.left, item.engine)):
        if not line.text.strip() or line.right <= line.left or line.score < _MIN_SCORE:
            continue
        identity = (line.engine, line.text, line.left, line.top, line.right, line.bottom)
        if identity in seen:
            continue
        seen.add(identity)
        matches = []
        for index, group in enumerate(groups):
            anchor = max(group, key=lambda item: item.right - item.left)
            distance = abs(anchor.center - line.center) / min(anchor.height, line.height)
            overlap = max(0.0, min(anchor.right, line.right) - max(anchor.left, line.left))
            if (
                distance < _MAX_ROW_DISTANCE
                and overlap / min(anchor.right - anchor.left, line.right - line.left)
                > _MIN_HORIZONTAL_OVERLAP
            ):
                matches.append((distance, index))
        if matches:
            groups[min(matches)[1]].append(line)
        else:
            groups.append([line])
    decisions = [decision for group in groups if (decision := _choose(_engine_votes(group))) is not None]
    return sorted(decisions, key=lambda item: (item.selected.center, item.selected.left))
