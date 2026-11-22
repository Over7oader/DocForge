"""Summarize a measured run without rewriting reference labels.

Usage: python -m scripts.audit_run runs/openrouter-train
The account-prefix diagnostic records source evidence separately from raw reference F1.
"""

import argparse
import json
from pathlib import Path

from doxforge.doc_types import load_doc_types
from doxforge.evaluation.scorer import DISCORD, Counts, leaf_pairs, score_run, summarize
from doxforge.extraction.values import VALUE_TYPES
from doxforge.ingest import load_document
from doxforge.text import fold

ACCOUNT_FIELDS = frozenset({"przelew_bankowy.z_rachunku", "przelew_bankowy.na_rachunek"})


def audit(run: Path) -> dict:
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    scores = score_run(
        run / "gold", run / "output", load_doc_types(), {k: v["source"] for k, v in manifest.items()}
    )
    non_account = Counts()
    for doc in scores:
        for path, counts in doc.fields[DISCORD].items():
            if path not in ACCOUNT_FIELDS:
                non_account += counts
    prefix_mismatches = []
    pattern = VALUE_TYPES["iban"].pattern
    assert pattern is not None
    for gold in sorted((run / "gold").rglob("*.json")):
        rel = gold.relative_to(run / "gold")
        prediction = run / "output" / rel
        if not prediction.exists():
            continue
        g, p = json.loads(gold.read_text()), json.loads(prediction.read_text())
        for path, expected, actual in leaf_pairs(g, p):
            if path not in ACCOUNT_FIELDS or not isinstance(expected, str) or not isinstance(actual, str):
                continue
            # Diagnose the known missing-PL convention without changing any reference JSON.
            if actual == "PL" + expected:
                pdf = run / "input" / rel.with_suffix(".pdf")
                document = fold(load_document(pdf).text)
                forms = {VALUE_TYPES["iban"].normalize(m[0]) for m in pattern.finditer(document)}
                prefix_mismatches.append(
                    {
                        "path": rel.as_posix(),
                        "field": path,
                        "prefix_in_source": actual in forms,
                    }
                )
    log = (run / "run.log").read_text() if (run / "run.log").exists() else ""
    usage = [
        json.loads(line.split("LLM usage: ", 1)[1]) for line in log.splitlines() if "LLM usage: " in line
    ]
    timing = json.loads((run / "timing.json").read_text()) if (run / "timing.json").exists() else None
    return {
        "run": str(run),
        "warning": "Development/reference diagnostics. Discord ratio80 is an approximation. "
        "Reference labels are unchanged; non-account F1 omits both account fields.",
        "raw_reference": summarize(scores, DISCORD).as_json(),
        "non_account_fields": non_account.as_dict(),
        "prefix_only_mismatches": prefix_mismatches,
        "timing": timing,
        "api_usage": {
            "responses_with_usage": len(usage),
            "prompt_tokens": sum(item["prompt_tokens"] for item in usage),
            "completion_tokens": sum(item["completion_tokens"] for item in usage),
            "reported_cost": (
                sum(item["cost"] or 0 for item in usage)
                if any(item["cost"] is not None for item in usage)
                else None
            ),
            "responses_with_cost": sum(item["cost"] is not None for item in usage),
            "cache_hits_not_billed": "Requests served from local cache are absent from API usage.",
        },
        "failures": {
            "http_429": log.count("HTTP/1.1 429"),
            "llm_fallbacks": log.count("LLM extraction failed"),
            "document_failures": log.count("processing failed"),
            "schema_warnings": log.count("does not match schema"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    report = audit(args.run)
    (args.run / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"raw_reference", "prefix_only_mismatches"}
            },
            indent=2,
        )
    )
    print("raw F1:", report["raw_reference"]["overall"])
    print("prefix-only mismatches:", len(report["prefix_only_mismatches"]))


if __name__ == "__main__":
    main()
