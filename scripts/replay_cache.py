"""Compare fixed policies on exactly the same cached train documents; never call the API.

Run from repository root: python -m scripts.replay_cache --out runs/review-before
Missing cache entries are reported, not silently replaced with rules. This is a development
diagnostic, NOT unseen validation: prompts/rules have already seen this dataset.
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from doxforge import settings
from doxforge.classify import classify
from doxforge.doc_types import load_doc_types
from doxforge.evaluation.dataset import Split, load_evaluation_config, load_manifest
from doxforge.evaluation.scorer import PRIMARY, Counts, score_document
from doxforge.extraction.engine import apply_vocabularies, build_result, extract_values
from doxforge.ingest import load_document
from doxforge.llm.client import LLMClient, load_llm_settings
from doxforge.llm.evidence import SourceEvidence
from doxforge.llm.extract import build_prompt, llm_schema, merge_results


class CacheMissError(Exception):
    pass


class OfflineClient(LLMClient):
    def _post(self, body):
        raise CacheMissError


def _totals(rows):
    totals = {}
    for row in rows:
        for mode, c in row["scores"].items():
            for group in ("all", row["source"], row["class"]):
                totals.setdefault(f"{group}/{mode}", Counts()).__iadd__(Counts(c["tp"], c["fp"], c["fn"]))
    return {k: v.as_dict() for k, v in totals.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    load_dotenv(settings.ENV_FILE)
    cfg = load_llm_settings()
    if cfg is None:
        raise ValueError("Replay needs the model ID used to populate the cache")
    client = OfflineClient(cfg)
    types = load_doc_types()
    samples = load_manifest(settings.DATASET_DIR, load_evaluation_config())
    rows, missing = [], []
    try:
        for sample in samples:
            # Do not inspect holdout labels or responses while choosing policies.
            if sample.split is not Split.TRAIN:
                continue
            document = load_document(sample.pdf)
            kind = classify(document, {name: t.config.classification for name, t in types.items()})
            if kind is None:
                continue
            dt = types[kind]
            llm_cfg = dt.config.llm
            if llm_cfg is None:
                continue
            ctx = extract_values(document.text, dt.config.extraction, dt.config.vocabularies)
            rules = build_result(dt.config.output_schema, ctx.values)
            header = f"Rodzaj dokumentu: {dt.config.name}. {dt.config.description}"
            system, user = build_prompt(llm_cfg, header, dt.config.vocabularies, document.text, rules)
            try:
                llm = client.complete_json(system, user, llm_schema(dt.config.output_schema), kind)
            except CacheMissError:
                missing.append(str(sample.pdf.relative_to(settings.DATASET_DIR)))
                continue
            gold = json.loads(sample.gold.read_text()) if sample.gold else {}
            policies = {
                "rules_only": None,
                "configured": llm_cfg,
                "rules_first": llm_cfg.model_copy(update={"default_policy": "rules", "policies": {}}),
                "llm_first": llm_cfg.model_copy(update={"default_policy": "llm", "policies": {}}),
            }
            row = {
                "case": sample.case_id,
                "source": sample.source,
                "class": kind,
                "pdf": str(sample.pdf.relative_to(settings.DATASET_DIR)),
                "scores": {},
            }
            for mode, policy in policies.items():
                result = (
                    rules
                    if policy is None
                    else merge_results(
                        dt.config.output_schema,
                        policy,
                        document.text,
                        rules,
                        llm,
                        evidence=SourceEvidence(
                            document.text, document.digital_text, ctx.text_labels, ctx.selected_entities
                        ),
                    )
                )
                apply_vocabularies(result, dt.config.extraction, dt.config.vocabularies)
                counts = Counts()
                for c in score_document(gold, result)[PRIMARY].values():
                    counts += c
                row["scores"][mode] = counts.as_dict()
            rows.append(row)
    finally:
        client.close()
    report = {
        "warning": "Previously seen train subset; not unseen F1. No API calls.",
        "model": cfg.model,
        "cached_documents": len(rows),
        "cache_misses": missing,
        "totals": _totals(rows),
        "documents": rows,
    }
    args.out.mkdir(parents=True)
    (args.out / "replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "documents"}, indent=2))


if __name__ == "__main__":
    main()
