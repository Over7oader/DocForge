"""Layout-shift stress test: digital train documents with paragraphs re-wrapped at another width.

Usage: python -m scripts.stress_reflow --source runs/ensemble-ownership-14b --out runs/stress-reflow-1 \
           --seed 1 [--reference runs/refined-8b/gold-source-format] [--no-llm]

Real documents from other templates break lines elsewhere. Short lines (headings, "Miejscowość,
dnia …", signatures) keep their own line; lines that fill most of the page width are treated as one
paragraph and re-wrapped at a random width (70-110 chars, fixed seed). Values do not change, so the
gold applies. A diagnostic of rule/LLM robustness; never tune rules on a single seed.
"""

import argparse
import json
import random
import re
import shutil
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from doxforge import pipeline, settings
from doxforge.classify import classify
from doxforge.doc_types import load_doc_types
from doxforge.evaluation.scorer import DISCORD, score_run, summarize
from doxforge.extraction.engine import build_result, extract_values
from doxforge.ingest import Document, Page, TextSource, load_document
from doxforge.llm.client import load_llm_settings
from doxforge.llm.ensemble import build_client

_FULL_LINE = 0.6  # a line at least this share of the page's longest line continues its paragraph


# A real document starts a new line for titles in capitals, list items and "Label: …" blocks, ends a
# paragraph at a line ending with ":", and keeps whole lines for titles, table rows (column gap of
# 3+ spaces) and the place/date line ("Gdańsk, dnia 3 września 2026 r.").
_STARTS_LINE = re.compile(
    r"^\s*(?:[^a-ząćęłńóśźż]+$|(?:\d+(?:\.\d+)*[.)]|[a-z]\)|[-•§])\s|[^\W\d_][\w\- ]{0,30}:(?:\s|$))"
)
_WHOLE_LINE = re.compile(
    r"^[^a-ząćęłńóśźż]+$|\S {3,}\S|,\s*(?:dnia\s+)?\d{1,2}[ .]\S+[ .]\d{4}\s*(?:r\.|roku)?\s*$"
)


def _fill(paragraph: list[str], width: int) -> str:
    return textwrap.fill(" ".join(paragraph), width, break_long_words=False, break_on_hyphens=False)


def reflow(text: str, width: int) -> str:
    lines = text.split("\n")
    longest = max((len(line) for line in lines), default=0)
    out: list[str] = []
    paragraph: list[str] = []
    for line in lines:
        if paragraph and (_STARTS_LINE.search(line) or _WHOLE_LINE.search(line)):
            out.append(_fill(paragraph, width))
            paragraph = []
        if _WHOLE_LINE.search(line):
            out.append(line)
            continue
        paragraph.append(line.strip())
        if len(line) < _FULL_LINE * longest or not line.strip() or line.rstrip().endswith(":"):
            out.append(_fill(paragraph, width))
            paragraph = []
    if paragraph:
        out.append(_fill(paragraph, width))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    load_dotenv(settings.ENV_FILE)
    manifest = json.loads((args.source / "manifest.json").read_text())
    digital = {k: v for k, v in manifest.items() if v["source"] == "digital"}
    types = load_doc_types()
    config = None if args.no_llm else load_llm_settings()
    client = build_client(config) if config else None
    rng = random.Random(args.seed)
    widths = {rel: rng.randint(70, 110) for rel in sorted(digital)}
    gold_dir = args.reference or (args.source / "gold")
    args.out.mkdir(parents=True, exist_ok=False)

    def process(rel: str) -> None:
        source = load_document(args.source / "input" / Path(rel).with_suffix(".pdf"))
        document = Document([Page(reflow(p.text, widths[rel]), TextSource.TEXT_LAYER) for p in source.pages])
        kind = classify(document, {n: t.config.classification for n, t in types.items()})
        result: object = settings.EMPTY_RESULT
        if kind is not None:
            doc_type = types[kind]
            ctx = extract_values(document.text, doc_type.config.extraction, doc_type.config.vocabularies)
            prepared = pipeline.Prepared(
                kind,
                document,
                build_result(doc_type.config.output_schema, ctx.values),
                ctx.text_labels,
                ctx.selected_entities,
            )
            result = pipeline.finish(prepared, doc_type, client)
        target = args.out / "output" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        gold = args.out / "gold" / rel
        gold.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gold_dir / rel, gold)
        (args.out / "text" / rel).with_suffix(".txt").parent.mkdir(parents=True, exist_ok=True)
        (args.out / "text" / rel).with_suffix(".txt").write_text(document.text)

    try:
        with ThreadPoolExecutor(max_workers=settings.PIPELINE_WORKERS) as executor:
            list(executor.map(process, sorted(digital)))
    finally:
        if client is not None:
            client.close()
    (args.out / "manifest.json").write_text(json.dumps(digital, ensure_ascii=False, indent=2) + "\n")
    report = summarize(
        score_run(args.out / "gold", args.out / "output", types, {k: "digital" for k in digital}), DISCORD
    ).as_json()
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "overall": report["overall"]["ignore/discord_ratio80_proxy"],
                "by_type": {k: v["f1"] for k, v in report["by_type"].items()},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
