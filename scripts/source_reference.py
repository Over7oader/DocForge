"""Create a separate diagnostic reference with IBAN prefixes taken from source.

Organizer reference JSONs are never overwritten. This implements the documented
format clarification independently of model predictions. It does not repair digits.
"""

import argparse
import json
from pathlib import Path

from doxforge.extraction.values import VALUE_TYPES
from doxforge.ingest import load_document
from doxforge.text import fold


def prepare_reference(run: Path, target: Path) -> list[dict]:
    if target.exists():
        raise FileExistsError(target)
    changes = []
    pattern = VALUE_TYPES["iban"].pattern
    assert pattern is not None
    for source in sorted((run / "gold").rglob("*.json")):
        rel = source.relative_to(run / "gold")
        gold = json.loads(source.read_text())
        transfer = gold.get("przelew_bankowy") if isinstance(gold, dict) else None
        if isinstance(transfer, dict):
            document = fold(load_document(run / "input" / rel.with_suffix(".pdf")).text)
            forms = {
                value
                for m in pattern.finditer(document)
                if isinstance(value := VALUE_TYPES["iban"].normalize(m[0]), str)
            }
            for field in ["z_rachunku", "na_rachunek"]:
                expected = transfer.get(field)
                if not isinstance(expected, str):
                    continue
                compact = "".join(expected.split()).upper()
                body = compact[2:] if compact[:2].isalpha() else compact
                matches = {value for value in forms if (value[2:] if value[:2].isalpha() else value) == body}
                if len(matches) == 1 and (replacement := next(iter(matches))) != expected:
                    transfer[field] = replacement
                    changes.append(
                        {
                            "path": rel.as_posix(),
                            "field": field,
                            "before": expected,
                            "after": replacement,
                            "reason": "same account body, unique country-prefix form in source",
                        }
                    )
        output = target / rel
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    changes = prepare_reference(args.run, args.out)
    report = {
        "warning": "Diagnostic reference for Discord account-format clarification; "
        "not the unchanged organizer reference. Model predictions were not read.",
        "source": str(args.run / "gold"),
        "changes": changes,
    }
    args.out.with_name(args.out.name + "-provenance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print("Source-based format corrections:", len(changes))


if __name__ == "__main__":
    main()
