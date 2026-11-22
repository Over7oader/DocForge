"""Measure a frozen train run end-to-end, retaining original and optional diagnostic reference scores.

Warm cache: --source runs/typed-14b --out runs/new-quality
Cold contents: --source runs/openrouter-cold150 --out runs/new-cold150 --cold-cache
OCR weights must already be installed. No holdout runs are accepted by this development harness.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from doxforge import settings
from doxforge.doc_types import load_doc_types
from doxforge.evaluation.scorer import DISCORD, score_run, summarize
from doxforge.llm.client import load_llm_settings


def _hashes() -> dict[str, str]:
    files = [
        file
        for tree in ["doxforge", "config"]
        for file in (settings.PROJECT_ROOT / tree).rglob("*")
        if file.suffix in {".py", ".yaml"}
    ]
    files += [settings.PROJECT_ROOT / "requirements.txt"]
    return {
        str(p.relative_to(settings.PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cold-cache", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--budget-seconds", type=int, default=2640)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    if any(item["split"] != "train" for item in manifest.values()):
        raise ValueError("This harness is restricted to known train data")
    out.mkdir(parents=True, exist_ok=False)
    (out / "input").symlink_to((source / "input").resolve(), target_is_directory=True)
    (out / "gold").symlink_to((source / "gold").resolve(), target_is_directory=True)
    shutil.copyfile(source / "manifest.json", out / "manifest.json")
    load_dotenv(settings.ENV_FILE)
    cfg = load_llm_settings()
    cache = out / "cache" if args.cold_cache else settings.CACHE_DIR
    environment = {**os.environ, "DOXFORGE_CACHE_DIR": str(cache)}
    snapshot = {
        "hashes": _hashes(),
        "cache": str(cache),
        "cold_contents": args.cold_cache,
        "weights_preinstalled": True,
        "model": cfg.model if cfg else None,
        "base_url": cfg.base_url if cfg else None,
        "provider": cfg.provider if cfg else None,
        "ocr": environment.get("DOXFORGE_OCR", "ensemble3"),
        "ocr_threads": environment.get("DOXFORGE_OCR_THREADS", "1"),
    }
    (out / "snapshot.json").write_text(json.dumps(snapshot, indent=2))
    command = [
        sys.executable,
        "-m",
        "doxforge",
        "run",
        "--input",
        str(out / "input"),
        "--output",
        str(out / "output"),
        "--budget-seconds",
        str(args.budget_seconds),
    ]
    started = time.monotonic()
    with (out / "run.log").open("w") as log:
        result = subprocess.run(
            command,
            cwd=settings.PROJECT_ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    elapsed = time.monotonic() - started
    unchanged = snapshot["hashes"] == _hashes()
    result.check_returncode()
    sources = {path: row["source"] for path, row in manifest.items()}
    totals = {}
    for name, reference in {"original": source / "gold", "diagnostic": args.reference}.items():
        if reference is None:
            continue
        selected_reference = reference
        if name == "diagnostic":
            # Reference directories may cover a larger run. Score exactly this input manifest,
            # never count unselected documents as missing predictions.
            subset = out / "gold-diagnostic"
            for relative in manifest:
                target = subset / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(reference / relative, target)
            selected_reference = subset
        report = summarize(
            score_run(selected_reference, out / "output", load_doc_types(), sources), DISCORD
        ).as_json()
        (out / f"report-{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        totals[name] = report["overall"]["ignore/discord_ratio80_proxy"]
    log = (out / "run.log").read_text()
    costs = [
        json.loads(line.split("LLM usage: ", 1)[1]).get("cost", 0)
        for line in log.splitlines()
        if "LLM usage: " in line
    ]
    summary = {
        "warning": "Known train; proxy scorer; diagnostic reference is not original gold.",
        "seconds": elapsed,
        "documents": len(manifest),
        "code_unchanged": unchanged,
        "completed_json_files": len(list((out / "output").rglob("*.json"))),
        "new_llm_responses": log.count("LLM usage:"),
        "reported_cost": sum(c or 0 for c in costs),
        "engine_failures": log.count("failed; retaining"),
        "deadline_exhausted": "time budget exhausted" in log,
        "scores": totals,
    }
    (out / "measurement.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not unchanged:
        raise RuntimeError("Source changed during measurement; do not treat this as a frozen benchmark")


if __name__ == "__main__":
    main()
