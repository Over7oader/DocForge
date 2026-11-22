"""Offline comparison of source ownership on saved predictions. Never calls an LLM API.

python -m scripts.replay_ownership runs/typed-14b --out runs/new-review --ocr single
Optionally pass --reference to an independently prepared diagnostic reference, preserving original gold.
"""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from doxforge.doc_types import classify_result, load_doc_types
from doxforge.evaluation.scorer import DISCORD, score_run, summarize
from doxforge.extraction.ownership import bind_entities
from doxforge.ingest import load_document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--ocr", choices=["single", "ensemble3"], default="single")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ["DOXFORGE_OCR"] = args.ocr
    types = load_doc_types()
    changes = []
    for source in sorted((args.run / "output").rglob("*.json")):
        relative = source.relative_to(args.run / "output")
        prediction = json.loads(source.read_text())
        kind = classify_result(prediction, types)
        corrected = prediction
        if kind is not None:
            config = types[kind].config.extraction
            document = load_document(args.run / "input" / relative.with_suffix(".pdf"))
            corrected, events = bind_entities(document.text, prediction, config.entities, config.sections)
            if corrected != prediction:
                changes.append({"file": relative.as_posix(), "changes": [asdict(e) for e in events]})
        target = args.out / "output" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(corrected, ensure_ascii=False, indent=2) + "\n")
    manifest = json.loads((args.run / "manifest.json").read_text())
    sources = {path: item["source"] for path, item in manifest.items()}
    reports = {}
    for name, gold in {"original": args.run / "gold", "diagnostic": args.reference}.items():
        if gold is None:
            continue
        reports[name] = {}
        for side, prediction_dir in {"before": args.run / "output", "after": args.out / "output"}.items():
            report = summarize(score_run(gold, prediction_dir, types, sources), DISCORD).as_json()
            (args.out / f"{name}-{side}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            reports[name][side] = report["overall"]["ignore/discord_ratio80_proxy"]
    result = {
        "warning": "Previously seen development data, postprocessing only, no LLM calls.",
        "reference": str(args.reference) if args.reference else None,
        "reports": reports,
        "changed_documents": changes,
    }
    (args.out / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
