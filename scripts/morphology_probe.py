"""CPU-only morphology experiment. Run with the isolated morfeusz2 research environment."""

import argparse
import json
import time

import morfeusz2

_MIN_NAME_WORDS = 2


def nominative(engine, name):
    words = name.split()
    if len(words) < _MIN_NAME_WORDS:
        return None
    normalized = []
    for index, word in enumerate(words):
        role = "nazwisko" if index == len(words) - 1 else "imię"
        analyses = engine.analyse(word)
        final_node = max((end for _, end, _ in analyses), default=0)
        candidates = {
            data[1].split(":", 1)[0]
            for start, end, data in analyses
            if start == 0 and end == final_node and role in data[3] and data[2].startswith("subst:sg:")
        }
        if len(candidates) != 1:
            return None
        normalized.append(next(iter(candidates)))
    return " ".join(normalized)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    engine = morfeusz2.Morfeusz()
    samples = [
        ("Janowi Kowalskiemu", "Jan Kowalski"),
        ("Annę Malinowską", "Anna Malinowska"),
        ("Janowi Lisowi", "Jan Lis"),
        ("Stanisława Nowaka", None),
        ("Emmy Noether", None),
    ]
    results = [
        {"source": text, "result": nominative(engine, text), "expected": expected}
        for text, expected in samples
    ]
    assert all(row["result"] == row["expected"] for row in results)
    started = time.perf_counter()
    for _ in range(args.iterations):
        nominative(engine, samples[0][0])
    print(
        json.dumps(
            {
                "package": "morfeusz2==1.99.15",
                "cpu_only": True,
                "results": results,
                "iterations": args.iterations,
                "wall_seconds": time.perf_counter() - started,
                "warning": "Morphology of supplied candidates only; no entity detection or role selection. "
                "Ambiguous and unknown forms abstain.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
