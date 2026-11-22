"""Group-wise policy selection diagnostic on a replay report, never an unseen-data claim.

Usage: python -m scripts.policy_cv runs/review-after/replay.json
Only four predeclared global policies are compared. No per-field label memorization.
All documents from a case (and conservatively all scans with the same number) share a fold.
"""

import argparse
import json
import random
from pathlib import Path

from doxforge.evaluation.scorer import Counts

POLICIES = ("rules_only", "configured", "rules_first", "llm_first")
FOLD_COUNT = 5


def _group(row):
    return "scan_" + row["case"].rsplit("_", 1)[-1] if row["source"] == "scan" else row["case"]


def _score(rows, policy):
    counts = Counts()
    for row in rows:
        c = row["scores"][policy]
        counts += Counts(c["tp"], c["fp"], c["fn"])
    return counts


def evaluate(rows, seed=42):
    groups = sorted({_group(row) for row in rows})
    if len(groups) < FOLD_COUNT:
        raise ValueError("At least five case groups required")
    random.Random(seed).shuffle(groups)
    folds = {group: i % FOLD_COUNT for i, group in enumerate(groups)}
    validation = Counts()
    results = []
    for fold in range(FOLD_COUNT):
        train = [row for row in rows if folds[_group(row)] != fold]
        test = [row for row in rows if folds[_group(row)] == fold]
        winner = max(POLICIES, key=lambda policy: _score(train, policy).f1)
        score = _score(test, winner)
        validation += score
        results.append(
            {
                "fold": fold,
                "selected": winner,
                "test_documents": len(test),
                "test_groups": sorted({_group(row) for row in test}),
                "score": score.as_dict(),
            }
        )
    rng = random.Random(seed)
    by_group = {group: [row for row in rows if _group(row) == group] for group in groups}
    boot = []
    for _ in range(1000):
        sample = [row for group in rng.choices(groups, k=len(groups)) for row in by_group[group]]
        boot.append(_score(sample, "configured").f1 - _score(sample, "rules_only").f1)
    boot.sort()
    return {
        "warning": "Development diagnostic only: rules and prompts already saw these data. "
        "Partial cache coverage and shared generator can bias CV. Freeze on external data.",
        "documents": len(rows),
        "groups": len(groups),
        "seed": seed,
        "out_of_fold_policy_selection": validation.as_dict(),
        "folds": results,
        "configured_minus_rules_case_bootstrap_95pct": [boot[24], boot[974]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.replay.read_text())["documents"])
    target = args.replay.with_name("policy-cv.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
